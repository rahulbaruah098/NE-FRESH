"""Product unit normalization and quantity helpers.

This module is dependency-light so product/unit behavior can be tested without Flask.
"""

from services.product_pricing import _calculate_product_pricing_from_form

UNIT_OPTIONS = {
    "WEIGHT": ["kg", "gram"],
    "VOLUME": ["liter", "ml"],
    "COUNT": [
        "piece",
        "packet",
        "bottle",
        "box",
        "tray",
        "dozen",
        "bunch",
        "bundle",
        "set",
        "jar",
        "can",
        "pouch",
        "tin",
        "bag",
        "crate",
        "roll",
        "custom",
    ],
}

UNIT_TYPE_LABELS = {
    "WEIGHT": "Weight",
    "VOLUME": "Volume",
    "COUNT": "Count / Unit",
}

def normalize_unit_type(value):
    value = (value or "").strip().upper()

    if value in UNIT_OPTIONS:
        return value

    return "WEIGHT"

def normalize_unit_label(unit_type, unit_label, custom_unit_label=None):
    unit_type = normalize_unit_type(unit_type)

    unit_label = (unit_label or "").strip().lower()
    custom_unit_label = (custom_unit_label or "").strip().lower()

    allowed_labels = UNIT_OPTIONS.get(unit_type, [])

    if unit_label == "custom" and custom_unit_label:
        return custom_unit_label

    if unit_label in allowed_labels and unit_label != "custom":
        return unit_label

    if unit_type == "VOLUME":
        return "liter"

    if unit_type == "COUNT":
        return "piece"

    return "kg"

def unit_quantity_rules(unit_type, unit_label):
    unit_type = normalize_unit_type(unit_type)
    unit_label = (unit_label or "").strip().lower()

    if unit_label == "kg":
        return {
            "min": 0.25,
            "step": 0.25,
            "message": "Minimum 0.25 kg",
        }

    if unit_label == "gram":
        return {
            "min": 50,
            "step": 50,
            "message": "Minimum 50 gram",
        }

    if unit_label == "liter":
        return {
            "min": 0.25,
            "step": 0.25,
            "message": "Minimum 0.25 liter",
        }

    if unit_label == "ml":
        return {
            "min": 50,
            "step": 50,
            "message": "Minimum 50 ml",
        }

    return {
        "min": 1,
        "step": 1,
        "message": f"Minimum 1 {unit_label or 'unit'}",
    }

def normalize_quantity_by_unit(quantity, unit_type, unit_label):
    unit_type = normalize_unit_type(unit_type)
    unit_label = (unit_label or "").strip().lower()

    try:
        quantity = float(quantity or 0)
    except (TypeError, ValueError):
        quantity = 0

    rules = unit_quantity_rules(unit_type, unit_label)
    min_value = float(rules["min"])
    step_value = float(rules["step"])

    if quantity < min_value:
        return None, rules["message"]

    if step_value > 0:
        quantity = round(round(quantity / step_value) * step_value, 2)

    if unit_type == "COUNT":
        quantity = int(round(quantity))

        if quantity < 1:
            return None, rules["message"]

    return quantity, None

def product_unit_type(product):
    return normalize_unit_type(product.get("unit_type") or "WEIGHT")

def product_unit_label(product):
    unit_type = product_unit_type(product)

    return normalize_unit_label(
        unit_type,
        product.get("unit_label") or "kg",
        product.get("custom_unit_label")
    )

def product_price_per_unit(product):
    try:
        return float(product.get("price_per_unit") or 0)
    except (TypeError, ValueError):
        return 0.0

def product_original_price_per_unit(product):
    value = product.get("original_price_per_unit")

    if value is None:
        value = product.get("price_per_unit")

    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0

def product_mrp_per_unit(product):
    try:
        return float(product.get("mrp_per_unit") or product.get("old_price") or 0)
    except (TypeError, ValueError):
        return 0.0

def product_stock_quantity(product):
    try:
        return float(product.get("stock_quantity") or 0)
    except (TypeError, ValueError):
        return 0.0

def hydrate_product_unit_fields(product):
    unit_type = product_unit_type(product)
    unit_label = product_unit_label(product)
    rules = unit_quantity_rules(unit_type, unit_label)

    price_per_unit = product_price_per_unit(product)
    original_price_per_unit = product_original_price_per_unit(product)
    mrp_per_unit = product_mrp_per_unit(product)
    stock_quantity = product_stock_quantity(product)

    default_min = float(rules["min"])
    quantity_step = float(rules["step"])

    try:
        custom_min = float(
            product.get("quantity_min")
            if product.get("quantity_min") is not None
            else product.get("min_order_quantity")
            if product.get("min_order_quantity") is not None
            else default_min
        )
    except (TypeError, ValueError):
        custom_min = default_min

    if custom_min < default_min:
        custom_min = default_min

    if unit_type == "COUNT":
        custom_min = int(round(custom_min))

        if custom_min < 1:
            custom_min = 1

    product["unit_type"] = unit_type
    product["unit_type_label"] = UNIT_TYPE_LABELS.get(unit_type, unit_type.title())
    product["unit_label"] = unit_label
    product["price_per_unit"] = price_per_unit
    product["original_price_per_unit"] = original_price_per_unit
    product["mrp_per_unit"] = mrp_per_unit
    product["stock_quantity"] = stock_quantity
    product["quantity_min"] = custom_min
    product["quantity_step"] = quantity_step
    product["quantity_message"] = f"Minimum {custom_min:g} {unit_label or 'unit'}"

    return product

def cart_item_quantity(cart_item):
    value = cart_item.get("cart_quantity")

    if value is None:
        value = cart_item.get("quantity")

    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0

def build_unit_product_update_from_form(form, fallback_original_price=0):
    unit_type = normalize_unit_type(form.get("unit_type"))

    unit_label = normalize_unit_label(
        unit_type,
        form.get("unit_label"),
        form.get("custom_unit_label")
    )

    pricing = _calculate_product_pricing_from_form(
        form,
        fallback_original_price=fallback_original_price
    )

    original_price_per_unit = float(pricing.get("original_price_per_unit") or 0)
    price_per_unit = float(pricing.get("price_per_unit") or 0)
    discount_amount_per_unit = float(pricing.get("discount_amount_per_unit") or 0)

    try:
        mrp_per_unit = float(form.get("mrp_per_unit") or 0)
    except (TypeError, ValueError):
        mrp_per_unit = 0.0

    try:
        stock_quantity = float(form.get("stock_quantity") or 0)
    except (TypeError, ValueError):
        stock_quantity = 0.0

    rules = unit_quantity_rules(unit_type, unit_label)
    default_min = float(rules["min"])
    quantity_step = float(rules["step"])

    try:
        quantity_min = float(
            form.get("quantity_min")
            or form.get("min_order_quantity")
            or default_min
        )
    except (TypeError, ValueError):
        quantity_min = default_min

    if quantity_min < default_min:
        quantity_min = default_min

    if unit_type == "COUNT":
        quantity_min = int(round(quantity_min))

        if quantity_min < 1:
            quantity_min = 1

    return {
        "unit_type": unit_type,
        "unit_label": unit_label,
        "original_price_per_unit": round(original_price_per_unit, 2),
        "price_per_unit": round(price_per_unit, 2),
        "mrp_per_unit": round(mrp_per_unit, 2),
        "stock_quantity": round(stock_quantity, 2),
        "quantity_min": quantity_min,
        "quantity_step": quantity_step,
        "quantity_message": f"Minimum {quantity_min:g} {unit_label or 'unit'}",

        "discount_enabled": pricing.get("discount_enabled", False),
        "discount_type": pricing.get("discount_type", "percent"),
        "discount_value": pricing.get("discount_value", 0),
        "discount_amount_per_unit": round(discount_amount_per_unit, 2),
        "discount_percent": pricing.get("discount_percent", 0),
    }
