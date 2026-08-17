"""Store-managed combination discount routes for NE-Fresh.

This module is intentionally separate from ``routes/store/routes.py`` so the
large existing store route file does not need repeated edits. It manages only
store-owned combination discount rules. Cart calculation and customer popup
rendering consume these saved rules in later files.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from bson import ObjectId

from app_core import *
from services.commerce_promotions import validate_combination_rule


_ALLOWED_DISCOUNT_TYPES = {"PERCENT", "FIXED"}
_ALLOWED_DISCOUNT_SCOPES = {
    "TRIGGER_PRODUCT",
    "REQUIRED_PRODUCTS",
    "ALL_MATCHED_PRODUCTS",
}


def _promotion_object_id(value: Any) -> ObjectId | None:
    try:
        return ObjectId(str(value))
    except Exception:
        return None


def _promotion_money(value: Any, *, minimum: float = 0.0) -> float | None:
    try:
        amount = round(float(value), 2)
    except (TypeError, ValueError):
        return None

    if amount < minimum:
        return None
    return amount


def _promotion_datetime(value: Any):
    value = str(value or "").strip()
    if not value:
        return None

    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _current_store_context():
    user, store = _get_current_store_or_redirect()
    if not store:
        return user, None
    return user, store


def _owned_active_product(store_id: ObjectId, raw_product_id: Any):
    product_id = _promotion_object_id(raw_product_id)
    if not product_id:
        return None

    return mongo.products.find_one({
        "_id": product_id,
        "store_id": store_id,
        "is_active": 1,
    })


def _parse_tiers_from_form(store_id: ObjectId, trigger_product_id: ObjectId):
    required_product_groups = request.form.getlist("tier_required_product_ids[]")
    discount_values = request.form.getlist("tier_discount_values[]")

    tiers = []
    seen_requirement_sets = set()

    for index, raw_group in enumerate(required_product_groups):
        raw_discount = discount_values[index] if index < len(discount_values) else None
        discount_value = _promotion_money(raw_discount, minimum=0.01)

        required_ids = []
        for raw_id in str(raw_group or "").split(","):
            product = _owned_active_product(store_id, raw_id.strip())
            if not product:
                continue

            product_id = product["_id"]
            if product_id == trigger_product_id or product_id in required_ids:
                continue
            required_ids.append(product_id)

        requirement_key = tuple(sorted(str(product_id) for product_id in required_ids))
        if not required_ids or discount_value is None or requirement_key in seen_requirement_sets:
            continue

        seen_requirement_sets.add(requirement_key)
        tiers.append({
            "required_product_ids": required_ids,
            "discount_value": discount_value,
        })

    tiers.sort(key=lambda tier: len(tier["required_product_ids"]))
    return tiers


def _promotion_view(rule):
    result = dict(rule)
    result["id"] = str(rule["_id"])
    result["trigger_product_id"] = str(rule.get("trigger_product_id") or "")

    trigger = mongo.products.find_one(
        {"_id": rule.get("trigger_product_id")},
        {"name": 1, "price": 1, "image_url": 1},
    )
    result["trigger_product"] = trigger or {}

    hydrated_tiers = []
    for tier in rule.get("tiers") or []:
        tier_view = dict(tier)
        required_products = list(mongo.products.find(
            {"_id": {"$in": tier.get("required_product_ids") or []}},
            {"name": 1, "price": 1, "image_url": 1},
        ))
        tier_view["required_products"] = required_products
        hydrated_tiers.append(tier_view)

    result["tiers"] = hydrated_tiers
    return result


@app.route("/store/promotions", methods=["GET"], endpoint="store_promotions")
@login_required(role="store")
def store_promotions_page():
    user, store = _current_store_context()
    if not store:
        flash("Store not found.", "danger")
        return redirect(url_for("store_dashboard"))

    store_id = store["_id"]
    products = list(mongo.products.find(
        {"store_id": store_id, "is_active": 1},
        {"name": 1, "price": 1, "stock": 1, "image_url": 1},
    ).sort("name", 1))

    rules = [
        _promotion_view(rule)
        for rule in mongo.combination_discount_rules.find({"store_id": store_id}).sort("created_at", -1)
    ]

    page_context = _build_store_split_page_context(store)
    # The shared store context already contains a ``products`` key. Replace it
    # with the active-product list required by this page before expanding the
    # context, otherwise render_template receives ``products`` twice and raises
    # ``TypeError: got multiple values for keyword argument 'products'``.
    page_context["products"] = products

    return render_template(
        "store_promotions.html",
        user=user,
        store=store,
        promotion_rules=rules,
        **page_context,
    )


@app.route("/store/promotions/new", methods=["POST"], endpoint="store_promotion_create")
@login_required(role="store")
def store_promotion_create():
    _, store = _current_store_context()
    if not store:
        flash("Store not found.", "danger")
        return redirect(url_for("store_dashboard"))

    store_id = store["_id"]
    name = str(request.form.get("name") or "").strip()[:120]
    trigger_product = _owned_active_product(store_id, request.form.get("trigger_product_id"))
    discount_type = str(request.form.get("discount_type") or "PERCENT").upper().strip()
    discount_scope = str(request.form.get("discount_scope") or "ALL_MATCHED_PRODUCTS").upper().strip()

    if not name:
        flash("Promotion name is required.", "danger")
        return redirect(url_for("store_promotions"))
    if not trigger_product:
        flash("Select a valid active trigger product from your store.", "danger")
        return redirect(url_for("store_promotions"))
    if discount_type not in _ALLOWED_DISCOUNT_TYPES:
        flash("Invalid discount type.", "danger")
        return redirect(url_for("store_promotions"))
    if discount_scope not in _ALLOWED_DISCOUNT_SCOPES:
        flash("Invalid discount scope.", "danger")
        return redirect(url_for("store_promotions"))

    tiers = _parse_tiers_from_form(store_id, trigger_product["_id"])
    now = datetime.utcnow()

    document = {
        "store_id": store_id,
        "name": name,
        "description": str(request.form.get("description") or "").strip()[:500],
        "trigger_product_id": trigger_product["_id"],
        "trigger_product_ids": [trigger_product["_id"]],
        "discount_type": discount_type,
        "discount_scope": discount_scope,
        "tiers": tiers,
        "starts_at": _promotion_datetime(request.form.get("starts_at")),
        "ends_at": _promotion_datetime(request.form.get("ends_at")),
        "is_active": 1 if request.form.get("is_active") in {"1", "true", "on", "yes"} else 0,
        "created_at": now,
        "updated_at": now,
    }

    errors = validate_combination_rule(document, store_id=store_id)
    if errors:
        for error in errors:
            flash(error, "danger")
        return redirect(url_for("store_promotions"))

    mongo.combination_discount_rules.insert_one(document)
    flash("Combination discount created successfully.", "success")
    return redirect(url_for("store_promotions"))


@app.route(
    "/store/promotions/<promotion_id>/toggle",
    methods=["POST"],
    endpoint="store_promotion_toggle",
)
@login_required(role="store")
def store_promotion_toggle(promotion_id):
    _, store = _current_store_context()
    if not store:
        flash("Store not found.", "danger")
        return redirect(url_for("store_dashboard"))

    promotion_object_id = _promotion_object_id(promotion_id)
    rule = mongo.combination_discount_rules.find_one({
        "_id": promotion_object_id,
        "store_id": store["_id"],
    }) if promotion_object_id else None

    if not rule:
        flash("Promotion not found.", "danger")
        return redirect(url_for("store_promotions"))

    next_status = 0 if int(rule.get("is_active") or 0) == 1 else 1
    mongo.combination_discount_rules.update_one(
        {"_id": rule["_id"], "store_id": store["_id"]},
        {"$set": {"is_active": next_status, "updated_at": datetime.utcnow()}},
    )

    flash("Promotion activated." if next_status else "Promotion deactivated.", "success")
    return redirect(url_for("store_promotions"))


@app.route(
    "/store/promotions/<promotion_id>/delete",
    methods=["POST"],
    endpoint="store_promotion_delete",
)
@login_required(role="store")
def store_promotion_delete(promotion_id):
    _, store = _current_store_context()
    if not store:
        flash("Store not found.", "danger")
        return redirect(url_for("store_dashboard"))

    promotion_object_id = _promotion_object_id(promotion_id)
    rule = mongo.combination_discount_rules.find_one({
        "_id": promotion_object_id,
        "store_id": store["_id"],
    }) if promotion_object_id else None

    if not rule:
        flash("Promotion not found.", "danger")
        return redirect(url_for("store_promotions"))

    usage_exists = mongo.orders.find_one(
        {"promotion_rule_ids": rule["_id"]},
        {"_id": 1},
    )

    if usage_exists:
        mongo.combination_discount_rules.update_one(
            {"_id": rule["_id"], "store_id": store["_id"]},
            {"$set": {
                "is_active": 0,
                "deleted_at": datetime.utcnow(),
                "updated_at": datetime.utcnow(),
            }},
        )
        flash("Promotion has order history, so it was safely deactivated.", "warning")
    else:
        mongo.combination_discount_rules.delete_one({
            "_id": rule["_id"],
            "store_id": store["_id"],
        })
        flash("Promotion deleted successfully.", "success")

    return redirect(url_for("store_promotions"))