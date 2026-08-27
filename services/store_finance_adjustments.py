"""Store refund carry-forward adjustment ledger.

The compare-and-set/idempotency behavior is preserved from app_core.py.
"""

from datetime import datetime
from bson import ObjectId
from extensions import mongo
from services.finance_reconciliation import finance_money

FINANCE_STORE_ADJUSTMENT_OPEN = "OPEN"

FINANCE_STORE_ADJUSTMENT_PARTIAL = "PARTIAL"

FINANCE_STORE_ADJUSTMENT_APPLIED = "APPLIED"

def finance_store_id_values(value):
    raw = str(value or "").strip()
    if not raw:
        return []
    values = [raw]
    try:
        if ObjectId.is_valid(raw):
            values.append(ObjectId(raw))
    except Exception:
        pass
    return values

def finance_create_store_adjustment(order, amount, reason="REFUND_AFTER_STORE_RECEIPT", actor=None):
    """Create one idempotent carry-forward Store adjustment for a source order."""
    order = order or {}
    amount = finance_money(amount, 0)
    if amount <= 0:
        return None

    store_id = order.get("store_id")
    source_order_id = str(order.get("_id") or order.get("id") or "")
    if not store_id or not source_order_id:
        return None

    actor = actor or {}
    now = datetime.utcnow().isoformat()
    key = f"STORE_REFUND_ADJUSTMENT:{source_order_id}"
    doc = {
        "adjustment_key": key,
        "store_id": store_id,
        "store_id_str": str(store_id),
        "store_name": order.get("store_name") or "",
        "source_order_id": source_order_id,
        "source_order_number": order.get("order_number") or "",
        "type": "REFUND_RECOVERY",
        "reason": reason,
        "original_amount": amount,
        "remaining_amount": amount,
        "applied_amount": 0.0,
        "status": FINANCE_STORE_ADJUSTMENT_OPEN,
        "applications": [],
        "created_at": now,
        "created_by": str(actor.get("id") or actor.get("_id") or ""),
        "created_by_name": actor.get("name") or actor.get("email") or "Admin",
        "updated_at": now,
    }

    mongo.store_finance_adjustments.update_one(
        {"adjustment_key": key},
        {"$setOnInsert": doc},
        upsert=True,
    )
    return mongo.store_finance_adjustments.find_one({"adjustment_key": key})

def finance_store_outstanding_adjustment_total(store_id):
    values = finance_store_id_values(store_id)
    if not values:
        return 0.0
    docs = mongo.store_finance_adjustments.find({
        "$or": [{"store_id": {"$in": values}}, {"store_id_str": str(store_id)}],
        "status": {"$in": [FINANCE_STORE_ADJUSTMENT_OPEN, FINANCE_STORE_ADJUSTMENT_PARTIAL]},
        "remaining_amount": {"$gt": 0},
    })
    return round(sum(finance_money(d.get("remaining_amount"), 0) for d in docs), 2)

def finance_apply_store_adjustments(store_id, payout_order_id, available_amount, actor=None):
    """
    Apply oldest carry-forward Store adjustments against a current Admin payout.
    Uses compare-and-set on remaining_amount to avoid double application.
    """
    available_amount = finance_money(available_amount, 0)
    if available_amount <= 0:
        return 0.0, []

    values = finance_store_id_values(store_id)
    if not values:
        return 0.0, []

    actor = actor or {}
    total_applied = 0.0
    applications = []
    remaining_budget = available_amount

    candidates = list(mongo.store_finance_adjustments.find({
        "$or": [{"store_id": {"$in": values}}, {"store_id_str": str(store_id)}],
        "status": {"$in": [FINANCE_STORE_ADJUSTMENT_OPEN, FINANCE_STORE_ADJUSTMENT_PARTIAL]},
        "remaining_amount": {"$gt": 0},
    }).sort("created_at", 1))

    for candidate in candidates:
        if remaining_budget <= 0:
            break

        adj_id = candidate.get("_id")
        current_remaining = finance_money(candidate.get("remaining_amount"), 0)
        if current_remaining <= 0:
            continue

        apply_amount = round(min(current_remaining, remaining_budget), 2)
        new_remaining = round(current_remaining - apply_amount, 2)
        new_applied = round(finance_money(candidate.get("applied_amount"), 0) + apply_amount, 2)
        new_status = FINANCE_STORE_ADJUSTMENT_APPLIED if new_remaining <= 0 else FINANCE_STORE_ADJUSTMENT_PARTIAL
        now = datetime.utcnow().isoformat()
        application = {
            "payout_order_id": str(payout_order_id or ""),
            "amount": apply_amount,
            "applied_at": now,
            "applied_by": str(actor.get("id") or actor.get("_id") or ""),
            "applied_by_name": actor.get("name") or actor.get("email") or "Admin",
        }

        result = mongo.store_finance_adjustments.update_one(
            {
                "_id": adj_id,
                "remaining_amount": current_remaining,
                "status": {"$in": [FINANCE_STORE_ADJUSTMENT_OPEN, FINANCE_STORE_ADJUSTMENT_PARTIAL]},
            },
            {
                "$set": {
                    "remaining_amount": new_remaining,
                    "applied_amount": new_applied,
                    "status": new_status,
                    "updated_at": now,
                },
                "$push": {"applications": application},
            },
        )

        if result.modified_count != 1:
            continue

        total_applied = round(total_applied + apply_amount, 2)
        remaining_budget = round(remaining_budget - apply_amount, 2)
        applications.append({
            "adjustment_id": str(adj_id),
            "source_order_id": candidate.get("source_order_id") or "",
            "amount": apply_amount,
            "remaining_after": new_remaining,
        })

        source_oid = candidate.get("source_order_id")
        try:
            source_oid_obj = ObjectId(str(source_oid)) if ObjectId.is_valid(str(source_oid)) else None
        except Exception:
            source_oid_obj = None
        if source_oid_obj:
            mongo.orders.update_one(
                {"_id": source_oid_obj},
                {"$set": {"store_adjustment_due": new_remaining, "updated_at": now}},
            )

    return round(total_applied, 2), applications

def finance_rollback_store_adjustments(applications, payout_order_id):
    """
    Best-effort rollback used only if a Store payout fails before the order itself
    is finalized as PAID. It reverses carry-forward adjustment applications made
    for this payout claim so the adjustment ledger cannot be consumed without a
    matching Store payout.
    """
    if not applications:
        return 0.0

    payout_order_id = str(payout_order_id or "")
    rolled_back = 0.0

    for application in reversed(list(applications)):
        adj_id_raw = application.get("adjustment_id")
        amount = finance_money(application.get("amount"), 0)
        if not adj_id_raw or amount <= 0:
            continue

        try:
            adj_id = ObjectId(str(adj_id_raw)) if ObjectId.is_valid(str(adj_id_raw)) else None
        except Exception:
            adj_id = None
        if not adj_id:
            continue

        doc = mongo.store_finance_adjustments.find_one({"_id": adj_id}) or {}
        apps = doc.get("applications") or []
        matching = [
            a for a in apps
            if isinstance(a, dict)
            and str(a.get("payout_order_id") or "") == payout_order_id
            and abs(finance_money(a.get("amount"), 0) - amount) < 0.001
        ]
        if not matching:
            continue

        current_remaining = finance_money(doc.get("remaining_amount"), 0)
        current_applied = finance_money(doc.get("applied_amount"), 0)
        original_amount = finance_money(doc.get("original_amount"), current_remaining + current_applied)

        new_remaining = round(min(original_amount, current_remaining + amount), 2)
        new_applied = round(max(current_applied - amount, 0), 2)
        new_status = (
            FINANCE_STORE_ADJUSTMENT_OPEN
            if new_applied <= 0
            else FINANCE_STORE_ADJUSTMENT_PARTIAL
        )
        now = datetime.utcnow().isoformat()

        result = mongo.store_finance_adjustments.update_one(
            {
                "_id": adj_id,
                "remaining_amount": current_remaining,
                "applied_amount": current_applied,
            },
            {
                "$set": {
                    "remaining_amount": new_remaining,
                    "applied_amount": new_applied,
                    "status": new_status,
                    "updated_at": now,
                },
                "$pull": {
                    "applications": {
                        "payout_order_id": payout_order_id,
                        "amount": amount,
                    }
                },
            },
        )
        if result.modified_count != 1:
            continue

        rolled_back = round(rolled_back + amount, 2)

        source_oid = doc.get("source_order_id")
        try:
            source_oid_obj = ObjectId(str(source_oid)) if ObjectId.is_valid(str(source_oid)) else None
        except Exception:
            source_oid_obj = None
        if source_oid_obj:
            mongo.orders.update_one(
                {"_id": source_oid_obj},
                {"$set": {"store_adjustment_due": new_remaining, "updated_at": now}},
            )

    return rolled_back
