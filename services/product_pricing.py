"""Product pricing helpers extracted during Step 4.

Behavior is intentionally unchanged from the Step 3 compatibility source.
"""

def _safe_float(value, default=0.0):
    try:
        if value is None or str(value).strip() == "":
            return float(default)
        return float(value)
    except Exception:
        return float(default)

def _calculate_product_pricing_from_form(request_form, fallback_original_price=0):
    """
    Product pricing rules:
    - original_price_per_unit = store-entered base price for selected unit
    - price_per_unit = final customer selling price after discount
    - discount can be disabled, percent-based, or fixed-amount based
    """

    original_price = _safe_float(
        request_form.get("original_price_per_unit") or request_form.get("price_per_unit"),
        fallback_original_price
    )

    if original_price < 0:
        original_price = -1

    discount_enabled_raw = (
        request_form.get("discount_enabled")
        or request_form.get("is_discount_enabled")
        or ""
    )

    discount_enabled = str(discount_enabled_raw).strip().lower() in {
        "1",
        "true",
        "on",
        "yes",
        "enabled"
    }

    discount_type = (request_form.get("discount_type") or "percent").strip().lower()

    if discount_type not in {"percent", "amount"}:
        discount_type = "percent"

    discount_value = _safe_float(request_form.get("discount_value"), 0)

    if discount_value < 0:
        discount_value = 0

    discount_amount = 0.0
    discount_percent = 0.0
    final_price = original_price

    if discount_enabled and original_price > 0 and discount_value > 0:
        if discount_type == "percent":
            if discount_value > 100:
                discount_value = 100

            discount_percent = discount_value
            discount_amount = original_price * (discount_percent / 100)
            final_price = original_price - discount_amount

        elif discount_type == "amount":
            if discount_value > original_price:
                discount_value = original_price

            discount_amount = discount_value
            final_price = original_price - discount_amount
            discount_percent = (discount_amount / original_price * 100) if original_price else 0

    if final_price < 0:
        final_price = 0

    return {
        "original_price_per_unit": round(original_price, 2),
        "price_per_unit": round(final_price, 2),
        "discount_enabled": bool(discount_enabled and discount_amount > 0),
        "discount_type": discount_type,
        "discount_value": round(discount_value, 2),
        "discount_amount_per_unit": round(discount_amount, 2),
        "discount_percent": round(discount_percent, 2)
    }
