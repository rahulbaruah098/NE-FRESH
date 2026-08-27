"""Pure finance transition builders for Admin settlement actions.

Step 6 keeps Flask request/redirect/flash and Mongo write orchestration inside the
existing route handlers, while moving the protected money calculations and state
payloads here.  The functions in this module do not perform database writes.
"""

from services.finance_reconciliation import finance_reconciliation_snapshot
from datetime import datetime




def _delivery_monthly_period_label(period):
    try:
        return datetime.strptime(str(period) + "-01", "%Y-%m-%d").strftime("%B %Y")
    except Exception:
        return str(period or "")


def settlement_money(value, default=0.0):
    """Mirror the legacy Admin settlement money coercion exactly."""
    try:
        if value is None or str(value).strip() == "":
            return float(default)
        return round(float(value), 2)
    except Exception:
        return float(default)


def build_store_platform_fee_received_state(
    order_id,
    net_platform_fee,
    admin_user,
    payment_mode,
    reference,
    note,
    now,
):
    net_platform_fee = settlement_money(net_platform_fee, 0)
    event = {
        "action": "STORE_PLATFORM_FEE_RECEIVED_BY_ADMIN",
        "order_id": str(order_id),
        "amount_received": net_platform_fee,
        "payment_mode": payment_mode,
        "reference_no": reference,
        "note": note,
        "created_by": str(admin_user.get("id") or admin_user.get("_id") or ""),
        "created_by_name": admin_user.get("name") or admin_user.get("email") or "Admin",
        "created_by_role": "admin",
        "created_at": now,
    }
    update_data = {
        "platform_fee_status": "RECEIVED",
        "platform_fee_received_at": now,
        "platform_fee_received_amount": net_platform_fee,
        "platform_fee_received_reference": reference,
        "platform_fee_received_mode": payment_mode,
        "admin_platform_fee_status": "RECEIVED",
        "store_platform_fee_remittance_status": "RECEIVED",
        "store_platform_fee_remitted_at": now,
        "store_platform_fee_payment_mode": payment_mode,
        "store_platform_fee_reference": reference,
        "store_payout_status": "NOT_REQUIRED",
        "store_settlement_status": "DIRECT_COLLECTION_RECONCILED",
        "order_settlement_status": "BUSINESS_RECONCILED",
        "settlement_status": "BUSINESS_RECONCILED",
        "updated_at": now,
    }
    event_note = (
        f"Admin received Store-remitted Platform Fee ₹{net_platform_fee:.2f} "
        f"via {payment_mode}. Reference: {reference or '-'}."
    )
    return {
        "event": event,
        "order_update": update_data,
        "transaction_update": {**update_data, "status": "PAID"},
        "event_note": event_note,
    }


def build_upi_delivery_verified_state(order, order_id, admin_user, note, now):
    order = order or {}
    reference = (order.get("upi_delivery_reference") or "").strip()
    platform_fee = settlement_money(order.get("platform_fee"))
    store_payout_amount = settlement_money(order.get("store_payout_amount"))
    amount_received = settlement_money(
        order.get("cod_collected_amount"),
        order.get("total_payable") or order.get("total_amount") or 0,
    )
    actor_id = str(admin_user.get("id") or admin_user.get("_id") or "")
    actor_name = admin_user.get("name") or admin_user.get("email") or "Admin"

    event = {
        "action": "UPI_AT_DELIVERY_VERIFIED_BY_ADMIN",
        "order_id": str(order_id),
        "amount_received": amount_received,
        "payment_mode": "UPI",
        "reference_no": reference,
        "upi_reference": reference,
        "settlement_impact": "UPI_VERIFIED_STORE_PAYOUT_UNLOCKED",
        "platform_fee": platform_fee,
        "store_payout_amount": store_payout_amount,
        "created_by": actor_id,
        "created_by_name": actor_name,
        "created_by_role": "admin",
        "note": note,
        "created_at": now,
    }
    order_update = {
        "payment_status": "PAID",
        "payment_collection_status": "PAID",
        "payment_reconciliation_status": "VERIFIED",
        "cod_collection_status": "UPI_VERIFIED",
        "upi_delivery_reconciliation_status": "VERIFIED",
        "upi_delivery_verified_at": now,
        "upi_delivery_verified_by": actor_id,
        "upi_delivery_verified_by_name": actor_name,
        "upi_delivery_reconciliation_note": note,
        "platform_fee_status": "RECEIVED",
        "platform_fee_received_at": now,
        "admin_platform_fee_status": "RECEIVED",
        "rider_cash_settlement_status": "NOT_REQUIRED",
        "rider_cash_to_submit": 0.0,
        "expected_rider_cash_to_submit": 0.0,
        "store_payout_status": "PENDING_AFTER_DELIVERY",
        "store_settlement_status": "PAYOUT_PENDING",
        "order_settlement_status": "STORE_PAYOUT_PENDING",
        "settlement_status": "STORE_PAYOUT_PENDING",
        "last_settlement_event": event,
        "updated_at": now,
    }
    transaction_update = {
        "status": "PAID",
        "amount": amount_received,
        "payment_status": "PAID",
        "payment_received_by": "ADMIN_PLATFORM",
        "payment_collection_status": "PAID",
        "payment_reconciliation_status": "VERIFIED",
        "payment_collection_channel": "UPI",
        "cod_collection_status": "UPI_VERIFIED",
        "upi_delivery_reference": reference,
        "upi_delivery_reconciliation_status": "VERIFIED",
        "upi_delivery_verified_at": now,
        "platform_fee_status": "RECEIVED",
        "platform_fee_received_at": now,
        "admin_platform_fee_status": "RECEIVED",
        "rider_cash_settlement_status": "NOT_REQUIRED",
        "rider_cash_to_submit": 0.0,
        "expected_rider_cash_to_submit": 0.0,
        "store_payout_status": "PENDING_AFTER_DELIVERY",
        "store_settlement_status": "PAYOUT_PENDING",
        "order_settlement_status": "STORE_PAYOUT_PENDING",
        "settlement_status": "STORE_PAYOUT_PENDING",
        "updated_at": now,
    }
    event_note = (
        f"Admin verified UPI payment ₹{amount_received:.2f} received by NE FRESH. "
        f"Reference {reference}. Store payout is now pending."
    )
    return {
        "reference": reference,
        "platform_fee": platform_fee,
        "store_payout_amount": store_payout_amount,
        "amount_received": amount_received,
        "event": event,
        "order_update": order_update,
        "transaction_update": transaction_update,
        "event_note": event_note,
    }


def build_rider_cash_received_state(order, order_id, admin_user, note, now):
    order = order or {}
    rider_cash_to_submit = settlement_money(order.get("rider_cash_to_submit"))
    platform_fee = settlement_money(order.get("platform_fee"))
    store_payout_amount = settlement_money(order.get("store_payout_amount"))
    actor_id = str(admin_user.get("id") or admin_user.get("_id") or "")
    actor_name = admin_user.get("name") or admin_user.get("email") or "Admin"

    event = {
        "action": "RIDER_CASH_RECEIVED_BY_ADMIN",
        "order_id": str(order_id),
        "amount_received": rider_cash_to_submit,
        "platform_fee": platform_fee,
        "store_payout_amount": store_payout_amount,
        "created_by": actor_id,
        "created_by_name": actor_name,
        "created_by_role": "admin",
        "note": note,
        "created_at": now,
    }
    order_update = {
        "rider_cash_settlement_status": "RECEIVED",
        "rider_cash_received_at": now,
        "rider_cash_received_by": actor_id,
        "rider_cash_received_by_name": actor_name,
        "rider_cash_settlement_note": note,
        "platform_fee_status": "RECEIVED",
        "platform_fee_received_at": now,
        "admin_platform_fee_status": "RECEIVED",
        "store_payout_status": "PENDING_AFTER_DELIVERY",
        "store_settlement_status": "PAYOUT_PENDING",
        "order_settlement_status": "STORE_PAYOUT_PENDING",
        "settlement_status": "STORE_PAYOUT_PENDING",
        "last_settlement_event": event,
        "updated_at": now,
    }
    transaction_update = {
        "rider_cash_settlement_status": "RECEIVED",
        "rider_cash_received_at": now,
        "rider_cash_received_by": actor_id,
        "rider_cash_settlement_note": note,
        "platform_fee_status": "RECEIVED",
        "platform_fee_received_at": now,
        "admin_platform_fee_status": "RECEIVED",
        "store_payout_status": "PENDING_AFTER_DELIVERY",
        "store_settlement_status": "PAYOUT_PENDING",
        "order_settlement_status": "STORE_PAYOUT_PENDING",
        "settlement_status": "STORE_PAYOUT_PENDING",
        "updated_at": now,
    }
    event_note = (
        f"Admin received rider cash ₹{rider_cash_to_submit:.2f}. "
        f"Platform fee ₹{platform_fee:.2f} marked received. "
        f"Store payout ₹{store_payout_amount:.2f} is pending."
    )
    return {
        "rider_cash_to_submit": rider_cash_to_submit,
        "platform_fee": platform_fee,
        "store_payout_amount": store_payout_amount,
        "event": event,
        "order_update": order_update,
        "transaction_update": transaction_update,
        "event_note": event_note,
    }


def calculate_refund_finance_state(
    row,
    refund_items_amount,
    refund_delivery_fee,
    refund_platform_fee,
    refund_tip_amount,
):
    """Calculate refund impact without writing to MongoDB."""
    row = row or {}
    refund_items_amount = settlement_money(refund_items_amount, 0)
    refund_delivery_fee = settlement_money(refund_delivery_fee, 0)
    refund_platform_fee = settlement_money(refund_platform_fee, 0)
    refund_tip_amount = settlement_money(refund_tip_amount, 0)
    refund_amount = round(
        refund_items_amount + refund_delivery_fee + refund_platform_fee + refund_tip_amount,
        2,
    )

    total_payable = settlement_money(row.get("total_payable"), 0)
    gross_platform_fee = settlement_money(row.get("platform_fee"), 0)
    net_platform_fee_after_refund = round(max(gross_platform_fee - refund_platform_fee, 0), 2)
    prior_platform_fee_status = (row.get("platform_fee_status") or "").strip().upper()

    if net_platform_fee_after_refund <= 0:
        next_platform_fee_status = "ADJUSTED" if gross_platform_fee > 0 else "NOT_REQUIRED"
    elif prior_platform_fee_status == "RECEIVED":
        next_platform_fee_status = "RECEIVED"
    else:
        next_platform_fee_status = prior_platform_fee_status or "PENDING_PAYMENT_RECONCILIATION"

    status = (row.get("status") or "").upper()
    store_payout_status = (row.get("store_payout_status") or "").upper()
    store_payout_amount = settlement_money(row.get("store_payout_amount"), 0)
    is_cancel_refund = status == "CANCELLED"
    store_refund_deduction = 0.0 if is_cancel_refund else refund_items_amount
    finance_state = finance_reconciliation_snapshot(row)
    store_already_received_order_money = bool(
        store_payout_status == "PAID"
        or (
            not finance_state.get("store_payout_required")
            and finance_state.get("customer_payment_reconciled")
        )
    )

    if is_cancel_refund:
        adjusted_store_payout = 0.0
        store_adjustment_due = 0.0
        settlement_impact = "CANCEL_REFUND_NO_STORE_PAYOUT"
        next_store_payout_status = "NOT_REQUIRED"
    elif store_already_received_order_money:
        adjusted_store_payout = store_payout_amount
        store_adjustment_due = store_refund_deduction
        settlement_impact = "ADJUST_FROM_NEXT_PAYOUT" if store_adjustment_due > 0 else "NO_ADJUSTMENT"
        next_store_payout_status = store_payout_status or "NOT_REQUIRED"
    else:
        adjusted_store_payout = round(max(store_payout_amount - store_refund_deduction, 0), 2)
        store_adjustment_due = 0.0
        settlement_impact = "DEDUCT_FROM_PENDING_PAYOUT" if store_refund_deduction > 0 else "NO_DEDUCTION"
        next_store_payout_status = "PENDING_AFTER_DELIVERY" if adjusted_store_payout > 0 else "ADJUSTED"

    payment_status_after_refund = "REFUNDED" if refund_amount >= total_payable else "PARTIALLY_REFUNDED"

    return {
        "refund_items_amount": refund_items_amount,
        "refund_delivery_fee": refund_delivery_fee,
        "refund_platform_fee": refund_platform_fee,
        "refund_tip_amount": refund_tip_amount,
        "refund_amount": refund_amount,
        "total_payable": total_payable,
        "gross_platform_fee": gross_platform_fee,
        "net_platform_fee_after_refund": net_platform_fee_after_refund,
        "prior_platform_fee_status": prior_platform_fee_status,
        "next_platform_fee_status": next_platform_fee_status,
        "status": status,
        "store_payout_status": store_payout_status,
        "store_payout_amount": store_payout_amount,
        "is_cancel_refund": is_cancel_refund,
        "store_refund_deduction": store_refund_deduction,
        "store_already_received_order_money": store_already_received_order_money,
        "adjusted_store_payout": adjusted_store_payout,
        "store_adjustment_due": store_adjustment_due,
        "settlement_impact": settlement_impact,
        "next_store_payout_status": next_store_payout_status,
        "payment_status_after_refund": payment_status_after_refund,
    }


def calculate_store_payout_base(order):
    order = order or {}
    original_store_payout_amount = settlement_money(
        order.get("original_store_payout_amount"),
        order.get("store_earning") or order.get("items_subtotal") or 0,
    )
    store_refund_deduction = settlement_money(
        order.get("store_refund_deduction")
        if order.get("store_refund_deduction") is not None
        else order.get("refund_deduction"),
        0,
    )
    adjusted_store_payout = settlement_money(
        order.get("adjusted_store_payout"),
        max(original_store_payout_amount - store_refund_deduction, 0),
    )
    store_adjustment_due = settlement_money(order.get("store_adjustment_due"), 0)
    settlement_impact = order.get("settlement_impact") or (
        "DEDUCT_FROM_PENDING_PAYOUT" if store_refund_deduction > 0 else "NO_DEDUCTION"
    )
    return {
        "original_store_payout_amount": original_store_payout_amount,
        "store_refund_deduction": store_refund_deduction,
        "adjusted_store_payout": adjusted_store_payout,
        "store_adjustment_due": store_adjustment_due,
        "settlement_impact": settlement_impact,
    }


def calculate_delivery_monthly_gross(hydrated_orders):
    gross_amount = round(sum(
        settlement_money(
            row.get("delivery_boy_payout_amount")
            if row.get("delivery_boy_payout_amount") is not None
            else row.get("delivery_boy_earning"),
            settlement_money(row.get("delivery_fee")) + settlement_money(row.get("tip_amount")),
        )
        for row in (hydrated_orders or [])
    ), 2)
    return max(gross_amount, 0.0)


def build_delivery_monthly_batch_doc(
    hydrated_orders,
    raw_orders,
    rider_id,
    period,
    payout_mode,
    reference_no,
    note,
    admin_user,
    now,
):
    hydrated_orders = list(hydrated_orders or [])
    raw_orders = list(raw_orders or [])
    gross_amount = calculate_delivery_monthly_gross(hydrated_orders)
    rider_name = next(
        (row.get("delivery_partner_name") for row in hydrated_orders if row.get("delivery_partner_name")),
        "Delivery Partner",
    )
    rider_phone = next(
        (row.get("delivery_partner_phone") for row in hydrated_orders if row.get("delivery_partner_phone")),
        "",
    )
    order_ids = [str(row.get("_id")) for row in raw_orders]
    actor_id = str(admin_user.get("id") or admin_user.get("_id") or "")
    actor_name = admin_user.get("name") or admin_user.get("email") or "Admin"

    return gross_amount, {
        "delivery_partner_id_str": rider_id,
        "delivery_partner_name": rider_name,
        "delivery_partner_phone": rider_phone,
        "period": period,
        "period_label": _delivery_monthly_period_label(period),
        "status": "PAID",
        "order_count": len(raw_orders),
        "order_ids": order_ids,
        "gross_earning": gross_amount,
        "amount_paid": gross_amount,
        "payment_mode": payout_mode,
        "reference_no": reference_no,
        "note": note,
        "paid_at": now,
        "paid_by": actor_id,
        "paid_by_name": actor_name,
        "updated_at": now,
    }
