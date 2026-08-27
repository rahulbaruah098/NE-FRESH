"""Return/refund policy configuration shared by customer and Admin flows."""

from extensions import mongo

RETURN_REFUND_POLICY_SETTINGS_KEY = "return_refund_policy_settings"

def get_return_refund_policy_settings():
    """
    Admin-controlled return/refund policy.

    enabled = False means:
    - no return option visible
    - backend blocks return request
    """
    settings = mongo.platform_settings.find_one({
        "key": RETURN_REFUND_POLICY_SETTINGS_KEY
    }) or {}

    enabled = bool(settings.get("enabled", False))

    try:
        return_window_hours = int(settings.get("return_window_hours") or 24)
    except Exception:
        return_window_hours = 24

    if return_window_hours < 1:
        return_window_hours = 1

    if return_window_hours > 720:
        return_window_hours = 720

    return {
        "enabled": enabled,
        "return_window_hours": return_window_hours,
        "default_refund_items": bool(settings.get("default_refund_items", True)),
        "default_refund_delivery_fee": bool(settings.get("default_refund_delivery_fee", False)),
        "default_refund_platform_fee": bool(settings.get("default_refund_platform_fee", False)),
        "default_refund_tip": bool(settings.get("default_refund_tip", False)),
        "policy_note": settings.get("policy_note") or "",
    }

def _admin_get_return_refund_policy_settings():
    settings = mongo.platform_settings.find_one({
        "key": RETURN_REFUND_POLICY_SETTINGS_KEY
    }) or {}

    try:
        return_window_hours = int(settings.get("return_window_hours") or 24)
    except Exception:
        return_window_hours = 24

    if return_window_hours < 1:
        return_window_hours = 1

    if return_window_hours > 720:
        return_window_hours = 720

    return {
        "enabled": bool(settings.get("enabled", False)),
        "return_window_hours": return_window_hours,
        "default_refund_items": bool(settings.get("default_refund_items", True)),
        "default_refund_delivery_fee": bool(settings.get("default_refund_delivery_fee", False)),
        "default_refund_platform_fee": bool(settings.get("default_refund_platform_fee", False)),
        "default_refund_tip": bool(settings.get("default_refund_tip", False)),
        "policy_note": settings.get("policy_note") or "",
        "updated_at": settings.get("updated_at") or "",
        "updated_by_name": settings.get("updated_by_name") or "",
    }
