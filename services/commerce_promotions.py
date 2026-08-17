"""Commerce promotion helpers for NE-Fresh.

This module contains reusable, side-effect-free calculations for:

* store-specific free-delivery progress;
* store-configured combination discounts; and
* related-product suggestions for the next eligible discount tier.

It does not write to MongoDB, mutate cart records, calculate external-delivery
quotes, or create orders. Route modules must explicitly pass the current cart
state and persist any resulting snapshots where required.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Iterable, Mapping, MutableMapping, Sequence

from bson import ObjectId


_TWO_PLACES = Decimal("0.01")
_ALLOWED_DISCOUNT_TYPES = {"PERCENT", "FIXED"}
_ALLOWED_DISCOUNT_SCOPES = {
    "TRIGGER_PRODUCT",
    "REQUIRED_PRODUCTS",
    "ALL_MATCHED_PRODUCTS",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _decimal(value: Any, default: Decimal = Decimal("0")) -> Decimal:
    try:
        if value is None or value == "":
            return default
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return default


def _money(value: Any) -> Decimal:
    return _decimal(value).quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)


def _money_float(value: Any) -> float:
    return float(_money(value))


def _object_id(value: Any) -> ObjectId | None:
    if isinstance(value, ObjectId):
        return value
    try:
        return ObjectId(str(value))
    except Exception:
        return None


def _same_id(left: Any, right: Any) -> bool:
    left_id = _object_id(left)
    right_id = _object_id(right)
    return bool(left_id and right_id and left_id == right_id)


def _is_rule_active(rule: Mapping[str, Any], now: datetime) -> bool:
    if int(rule.get("is_active") or 0) != 1:
        return False

    starts_at = rule.get("starts_at")
    ends_at = rule.get("ends_at")

    if isinstance(starts_at, datetime):
        if starts_at.tzinfo is None:
            starts_at = starts_at.replace(tzinfo=timezone.utc)
        if now < starts_at:
            return False

    if isinstance(ends_at, datetime):
        if ends_at.tzinfo is None:
            ends_at = ends_at.replace(tzinfo=timezone.utc)
        if now > ends_at:
            return False

    return True


def build_free_delivery_progress(
    *,
    store: Mapping[str, Any] | None,
    cart_subtotal: Any,
    platform_settings: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return free-delivery progress using store value first.

    Rules:
    * A present store-level ``free_delivery_above`` value is authoritative.
    * Store value ``0`` explicitly disables the offer.
    * The platform value is used only for legacy stores where the field is
      absent, not when the store intentionally saved zero.
    """

    store = store or {}
    platform_settings = platform_settings or {}
    subtotal = max(_money(cart_subtotal), Decimal("0"))

    if "free_delivery_above" in store:
        threshold = max(_money(store.get("free_delivery_above")), Decimal("0"))
        source = "store"
    else:
        threshold = max(
            _money(platform_settings.get("free_delivery_above")),
            Decimal("0"),
        )
        source = "admin_fallback"

    enabled = threshold > 0
    remaining = max(threshold - subtotal, Decimal("0")) if enabled else Decimal("0")
    unlocked = bool(enabled and subtotal >= threshold)
    percentage = Decimal("0")
    if enabled:
        percentage = min((subtotal / threshold) * Decimal("100"), Decimal("100"))

    return {
        "enabled": enabled,
        "threshold": _money_float(threshold),
        "cart_total": _money_float(subtotal),
        "remaining": _money_float(remaining),
        "unlocked": unlocked,
        "progress_percent": float(percentage.quantize(_TWO_PLACES)),
        "source": source,
    }


def validate_combination_rule(rule: Mapping[str, Any], *, store_id: Any) -> list[str]:
    """Validate one combination-discount rule document.

    Expected rule shape::

        {
            "store_id": ObjectId(...),
            "name": "Buy-together offer",
            "trigger_product_id": ObjectId(...),
            "discount_type": "PERCENT" | "FIXED",
            "discount_scope": "TRIGGER_PRODUCT" |
                              "REQUIRED_PRODUCTS" |
                              "ALL_MATCHED_PRODUCTS",
            "tiers": [
                {
                    "required_product_ids": [ObjectId(...)],
                    "discount_value": 5
                }
            ]
        }

    Each higher tier must require more distinct products than the previous
    tier. This prevents ambiguous 5% and 10% rules matching the same cart.
    """

    errors: list[str] = []
    expected_store_id = _object_id(store_id)
    rule_store_id = _object_id(rule.get("store_id"))
    trigger_id = _object_id(rule.get("trigger_product_id"))
    discount_type = str(rule.get("discount_type") or "").upper().strip()
    discount_scope = str(rule.get("discount_scope") or "").upper().strip()

    if not expected_store_id or not rule_store_id or rule_store_id != expected_store_id:
        errors.append("Rule store does not match the current store.")
    if not trigger_id:
        errors.append("A valid trigger product is required.")
    if discount_type not in _ALLOWED_DISCOUNT_TYPES:
        errors.append("Discount type must be PERCENT or FIXED.")
    if discount_scope not in _ALLOWED_DISCOUNT_SCOPES:
        errors.append("A valid discount scope is required.")

    tiers = rule.get("tiers")
    if not isinstance(tiers, Sequence) or isinstance(tiers, (str, bytes)) or not tiers:
        errors.append("At least one discount tier is required.")
        return errors

    previous_requirement_count = 0
    seen_requirement_sets: set[tuple[str, ...]] = set()

    for index, tier in enumerate(tiers, start=1):
        if not isinstance(tier, Mapping):
            errors.append(f"Tier {index} is invalid.")
            continue

        required_ids: list[ObjectId] = []
        for raw_id in tier.get("required_product_ids") or []:
            product_id = _object_id(raw_id)
            if product_id and product_id != trigger_id and product_id not in required_ids:
                required_ids.append(product_id)

        requirement_key = tuple(sorted(str(pid) for pid in required_ids))
        if not required_ids:
            errors.append(f"Tier {index} must require at least one additional product.")
        elif requirement_key in seen_requirement_sets:
            errors.append(f"Tier {index} duplicates another tier.")
        elif len(required_ids) <= previous_requirement_count:
            errors.append(
                f"Tier {index} must require more products than the previous tier."
            )

        seen_requirement_sets.add(requirement_key)
        previous_requirement_count = max(previous_requirement_count, len(required_ids))

        discount_value = _decimal(tier.get("discount_value"))
        if discount_value <= 0:
            errors.append(f"Tier {index} discount must be greater than zero.")
        elif discount_type == "PERCENT" and discount_value > 100:
            errors.append(f"Tier {index} percentage cannot exceed 100.")

    return errors


def _normalize_cart_lines(cart_lines: Iterable[Mapping[str, Any]]) -> dict[ObjectId, dict[str, Decimal]]:
    normalized: dict[ObjectId, dict[str, Decimal]] = {}

    for line in cart_lines:
        if str(line.get("item_type") or "product").lower() != "product":
            # Bundle discounts are already represented by bundle selling price.
            # They are deliberately excluded from combination-discount matching.
            continue

        product_id = _object_id(line.get("product_id"))
        if not product_id:
            continue

        quantity = max(_decimal(line.get("quantity") or line.get("cart_quantity")), Decimal("0"))
        line_total = max(
            _money(
                line.get("line_total")
                if line.get("line_total") is not None
                else _decimal(line.get("price_per_unit")) * quantity
            ),
            Decimal("0"),
        )

        existing = normalized.setdefault(
            product_id,
            {"quantity": Decimal("0"), "line_total": Decimal("0")},
        )
        existing["quantity"] += quantity
        existing["line_total"] += line_total

    return normalized


def _eligible_base(
    *,
    scope: str,
    trigger_id: ObjectId,
    required_ids: Sequence[ObjectId],
    cart: Mapping[ObjectId, Mapping[str, Decimal]],
) -> Decimal:
    if scope == "TRIGGER_PRODUCT":
        eligible_ids = [trigger_id]
    elif scope == "REQUIRED_PRODUCTS":
        eligible_ids = list(required_ids)
    else:
        eligible_ids = [trigger_id, *required_ids]

    return sum((cart[pid]["line_total"] for pid in eligible_ids if pid in cart), Decimal("0"))


def calculate_combination_discounts(
    *,
    rules: Iterable[Mapping[str, Any]],
    store_id: Any,
    cart_lines: Iterable[Mapping[str, Any]],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Calculate the best matching tier for every active rule.

    This function does not stack two rules against the same trigger product.
    When multiple rules target the same trigger product, only the rule producing
    the highest monetary discount is retained. This prevents accidental double
    discounting while still allowing independent offers for different triggers.
    """

    current_time = now or _utcnow()
    expected_store_id = _object_id(store_id)
    cart = _normalize_cart_lines(cart_lines)
    best_by_trigger: dict[ObjectId, dict[str, Any]] = {}

    for rule in rules:
        if not expected_store_id or not _same_id(rule.get("store_id"), expected_store_id):
            continue
        if not _is_rule_active(rule, current_time):
            continue
        if validate_combination_rule(rule, store_id=expected_store_id):
            continue

        trigger_id = _object_id(rule.get("trigger_product_id"))
        if not trigger_id or trigger_id not in cart or cart[trigger_id]["quantity"] <= 0:
            continue

        matched_tier: Mapping[str, Any] | None = None
        matched_required_ids: list[ObjectId] = []

        # Largest requirement set wins, regardless of stored tier order.
        ordered_tiers = sorted(
            rule.get("tiers") or [],
            key=lambda tier: len(tier.get("required_product_ids") or []),
            reverse=True,
        )

        for tier in ordered_tiers:
            required_ids = [
                product_id
                for product_id in (_object_id(raw) for raw in tier.get("required_product_ids") or [])
                if product_id and product_id != trigger_id
            ]
            required_ids = list(dict.fromkeys(required_ids))
            if required_ids and all(
                product_id in cart and cart[product_id]["quantity"] > 0
                for product_id in required_ids
            ):
                matched_tier = tier
                matched_required_ids = required_ids
                break

        if not matched_tier:
            continue

        discount_type = str(rule.get("discount_type") or "").upper().strip()
        discount_scope = str(rule.get("discount_scope") or "").upper().strip()
        discount_value = _decimal(matched_tier.get("discount_value"))
        eligible_base = _eligible_base(
            scope=discount_scope,
            trigger_id=trigger_id,
            required_ids=matched_required_ids,
            cart=cart,
        )

        if discount_type == "PERCENT":
            discount_amount = eligible_base * discount_value / Decimal("100")
        else:
            discount_amount = min(discount_value, eligible_base)

        discount_amount = max(_money(discount_amount), Decimal("0"))
        if discount_amount <= 0:
            continue

        result = {
            "rule_id": str(rule.get("_id") or ""),
            "rule_name": str(rule.get("name") or "Combination discount"),
            "trigger_product_id": str(trigger_id),
            "required_product_ids": [str(pid) for pid in matched_required_ids],
            "matched_product_ids": [str(trigger_id), *[str(pid) for pid in matched_required_ids]],
            "discount_type": discount_type,
            "discount_scope": discount_scope,
            "discount_value": float(discount_value),
            "eligible_base": _money_float(eligible_base),
            "discount_amount": _money_float(discount_amount),
        }

        existing = best_by_trigger.get(trigger_id)
        if not existing or result["discount_amount"] > existing["discount_amount"]:
            best_by_trigger[trigger_id] = result

    applied = list(best_by_trigger.values())
    total_discount = sum((_money(item["discount_amount"]) for item in applied), Decimal("0"))

    return {
        "applied_rules": applied,
        "combination_discount_total": _money_float(total_discount),
    }


def build_related_product_suggestions(
    *,
    rules: Iterable[Mapping[str, Any]],
    store_id: Any,
    cart_lines: Iterable[Mapping[str, Any]],
    products_by_id: Mapping[Any, Mapping[str, Any]],
    minimum_suggestions: int = 2,
    maximum_suggestions: int = 4,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Return missing products for the nearest higher discount tier.

    Suggestions are rule-driven, same-store, active, and in-stock. The service
    does not introduce random or cross-store recommendations.
    """

    current_time = now or _utcnow()
    expected_store_id = _object_id(store_id)
    cart = _normalize_cart_lines(cart_lines)
    suggestions: MutableMapping[ObjectId, dict[str, Any]] = {}

    normalized_products: dict[ObjectId, Mapping[str, Any]] = {}
    for raw_id, product in products_by_id.items():
        product_id = _object_id(raw_id) or _object_id(product.get("_id"))
        if product_id:
            normalized_products[product_id] = product

    for rule in rules:
        if not expected_store_id or not _same_id(rule.get("store_id"), expected_store_id):
            continue
        if not _is_rule_active(rule, current_time):
            continue
        if validate_combination_rule(rule, store_id=expected_store_id):
            continue

        trigger_id = _object_id(rule.get("trigger_product_id"))
        if not trigger_id or trigger_id not in cart:
            continue

        ordered_tiers = sorted(
            rule.get("tiers") or [],
            key=lambda tier: len(tier.get("required_product_ids") or []),
        )

        selected_tier: Mapping[str, Any] | None = None
        missing_ids: list[ObjectId] = []

        for tier in ordered_tiers:
            required_ids = [
                pid
                for pid in (_object_id(raw) for raw in tier.get("required_product_ids") or [])
                if pid and pid != trigger_id
            ]
            required_ids = list(dict.fromkeys(required_ids))
            missing = [pid for pid in required_ids if pid not in cart]
            if missing:
                selected_tier = tier
                missing_ids = missing
                break

        if not selected_tier:
            continue

        for product_id in missing_ids:
            product = normalized_products.get(product_id)
            if not product:
                continue
            if not _same_id(product.get("store_id"), expected_store_id):
                continue
            if int(product.get("is_active") or 0) != 1:
                continue
            if _decimal(product.get("stock_quantity")) <= 0:
                continue

            discount_type = str(rule.get("discount_type") or "").upper().strip()
            discount_value = _decimal(selected_tier.get("discount_value"))
            if discount_type == "PERCENT":
                offer_text = f"Add to unlock {discount_value.normalize()}% off"
            else:
                offer_text = f"Add to unlock ₹{_money(discount_value):.2f} off"

            candidate = {
                "product_id": str(product_id),
                "name": str(product.get("name") or "Product"),
                "image_path": str(product.get("image_path") or ""),
                "price_per_unit": _money_float(product.get("price_per_unit")),
                "unit_label": str(product.get("unit_label") or product.get("unit") or "unit"),
                "stock_quantity": float(_decimal(product.get("stock_quantity"))),
                "rule_id": str(rule.get("_id") or ""),
                "rule_name": str(rule.get("name") or "Combination discount"),
                "discount_type": discount_type,
                "discount_value": float(discount_value),
                "offer_text": offer_text,
                "missing_for_tier": len(missing_ids),
            }

            existing = suggestions.get(product_id)
            if not existing or candidate["discount_value"] > existing["discount_value"]:
                suggestions[product_id] = candidate

    ordered = sorted(
        suggestions.values(),
        key=lambda item: (
            item["missing_for_tier"],
            -item["discount_value"],
            item["name"].lower(),
        ),
    )

    limit = max(int(maximum_suggestions or 0), 0)
    if limit:
        ordered = ordered[:limit]

    # The caller can decide not to open the popup when fewer than the desired
    # minimum exist. We return all valid suggestions instead of fabricating any.
    for item in ordered:
        item["meets_popup_minimum"] = len(ordered) >= max(int(minimum_suggestions or 0), 0)

    return ordered