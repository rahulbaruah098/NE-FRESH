"""Platform Fee calculation and order money-breakdown services.

Extracted in Step 6 without changing the established financial formulas.
"""

from extensions import mongo

PLATFORM_FEE_SETTINGS_KEY = "platform_fee"

DEFAULT_PLATFORM_FEE_SETTINGS = {
    "key": PLATFORM_FEE_SETTINGS_KEY,
    "enabled": False,

    # fixed / percent / fixed_plus_percent
    "fee_type": "fixed",

    # Fixed fee amount in INR.
    "fixed_amount": 0.0,

    # Percentage on product subtotal.
    "percent": 0.0,

    # Optional bounds.
    "min_fee": 0.0,
    "max_fee": 0.0,

    "display_name": "Platform Fee",
    "description": "Platform fee supports secure ordering, customer support, and platform operations.",
}

def _platform_fee_safe_float(value, default=0.0):
    try:
        if value is None or str(value).strip() == "":
            return float(default)

        number = float(value)

        if number < 0:
            return float(default)

        return float(number)
    except Exception:
        return float(default)

def get_platform_fee_settings():
    """
    Reads platform fee configuration from MongoDB.

    Collection:
        platform_settings

    Document:
        {
            "key": "platform_fee",
            "enabled": true/false,
            "fee_type": "fixed" / "percent" / "fixed_plus_percent",
            "fixed_amount": 10,
            "percent": 2,
            "min_fee": 5,
            "max_fee": 30,
            "display_name": "Platform Fee"
        }

    If no setting exists, returns safe disabled defaults.
    """
    settings = dict(DEFAULT_PLATFORM_FEE_SETTINGS)

    try:
        row = mongo.platform_settings.find_one({
            "key": PLATFORM_FEE_SETTINGS_KEY
        }) or {}

        if row:
            settings.update(row)
    except Exception:
        pass

    settings["enabled"] = bool(settings.get("enabled"))

    fee_type = (settings.get("fee_type") or "fixed").strip().lower()

    if fee_type not in ["fixed", "percent", "fixed_plus_percent"]:
        fee_type = "fixed"

    settings["fee_type"] = fee_type
    settings["fixed_amount"] = round(_platform_fee_safe_float(settings.get("fixed_amount"), 0), 2)
    settings["percent"] = round(_platform_fee_safe_float(settings.get("percent"), 0), 2)
    settings["min_fee"] = round(_platform_fee_safe_float(settings.get("min_fee"), 0), 2)
    settings["max_fee"] = round(_platform_fee_safe_float(settings.get("max_fee"), 0), 2)
    settings["display_name"] = (settings.get("display_name") or "Platform Fee").strip() or "Platform Fee"
    settings["description"] = (
        settings.get("description")
        or "Platform fee supports secure ordering, customer support, and platform operations."
    ).strip()

    return settings

def calculate_platform_fee(items_total):
    """
    Calculates admin/platform fee from item subtotal.

    Returns:
        {
            "platform_fee": 10.0,
            "admin_platform_earning": 10.0,
            "platform_fee_source": "admin_global_setting",
            "platform_fee_settings": {...}
        }

    If disabled:
        platform_fee = 0
        platform_fee_source = "disabled"
    """
    try:
        items_total = float(items_total or 0)
    except Exception:
        items_total = 0.0

    if items_total < 0:
        items_total = 0.0

    settings = get_platform_fee_settings()

    if not settings.get("enabled"):
        return {
            "platform_fee": 0.0,
            "admin_platform_earning": 0.0,
            "platform_fee_source": "disabled",
            "platform_fee_settings": settings
        }

    fee_type = settings.get("fee_type") or "fixed"
    fixed_amount = _platform_fee_safe_float(settings.get("fixed_amount"), 0)
    percent = _platform_fee_safe_float(settings.get("percent"), 0)
    min_fee = _platform_fee_safe_float(settings.get("min_fee"), 0)
    max_fee = _platform_fee_safe_float(settings.get("max_fee"), 0)

    platform_fee = 0.0

    if fee_type == "fixed":
        platform_fee = fixed_amount

    elif fee_type == "percent":
        platform_fee = items_total * (percent / 100)

    elif fee_type == "fixed_plus_percent":
        platform_fee = fixed_amount + (items_total * (percent / 100))

    if min_fee > 0 and platform_fee < min_fee:
        platform_fee = min_fee

    if max_fee > 0 and platform_fee > max_fee:
        platform_fee = max_fee

    platform_fee = round(platform_fee, 2)

    return {
        "platform_fee": platform_fee,
        "admin_platform_earning": platform_fee,
        "platform_fee_source": "admin_global_setting",
        "platform_fee_settings": settings
    }

def build_order_money_breakdown(items_total, delivery_fee=0, tip_amount=0, payment_method="COD"):
    """
    Central money breakdown for orders.

    Customer pays:
        items_total + delivery_fee + platform_fee + tip_amount

    Ownership:
        items_total => store earning
        platform_fee => admin earning
        delivery_fee/tip => delivery/delivery-settlement logic

    For COD:
        admin_platform_fee_status = DUE

    For online payment:
        admin_platform_fee_status = COLLECTED
    """
    items_total = round(_platform_fee_safe_float(items_total), 2)
    delivery_fee = round(_platform_fee_safe_float(delivery_fee), 2)
    tip_amount = round(_platform_fee_safe_float(tip_amount), 2)

    platform_result = calculate_platform_fee(items_total)
    platform_fee = round(_platform_fee_safe_float(platform_result.get("platform_fee")), 2)

    total_payable = round(items_total + delivery_fee + platform_fee + tip_amount, 2)

    payment_method_normalized = (payment_method or "COD").strip().upper()

    if payment_method_normalized in ["COD", "CASH", "CASH_ON_DELIVERY"]:
        admin_platform_fee_status = "DUE"
    else:
        admin_platform_fee_status = "COLLECTED"

    return {
        "items_subtotal": items_total,
        "total_amount": items_total,

        "delivery_fee": delivery_fee,
        "delivery_fee_amount": delivery_fee,

        "platform_fee": platform_fee,
        "admin_platform_earning": platform_fee,
        "platform_fee_source": platform_result.get("platform_fee_source"),
        "platform_fee_settings_snapshot": platform_result.get("platform_fee_settings"),

        "tip_amount": tip_amount,
        "delivery_tip_amount": tip_amount,

        "store_earning": items_total,
        "total_payable": total_payable,

        "settlement_status": "PENDING",
        "store_settlement_status": "PENDING",
        "admin_platform_fee_status": admin_platform_fee_status,
        "delivery_settlement_status": "PENDING",
    }
