"""Site-wide Jinja context processors.

Dynamic business providers are injected from app_core.py after all legacy
helpers have been defined. This prevents circular imports while moving the
presentation infrastructure out of the monolith.
"""
from __future__ import annotations

from datetime import datetime

from flask import session

from config import _env_text

FOOTER_LINKS = [
    {"label": "Privacy", "endpoint": "legal_privacy"},
    {"label": "Security", "endpoint": "legal_security"},
    {"label": "Terms of Service", "endpoint": "legal_terms"},
    {"label": "Help & Support", "endpoint": "legal_help"},
    {"label": "Report a Fraud", "endpoint": "legal_report_fraud"},
]

_providers = {}


def configure_template_context(**providers) -> None:
    _providers.clear()
    _providers.update(providers)


def _provider(name):
    try:
        return _providers[name]
    except KeyError as exc:
        raise RuntimeError(f"Template context provider is not configured: {name}") from exc


def inject_globals():
    get_delivery_mode_settings = _provider("get_delivery_mode_settings")
    get_platform_fee_settings = _provider("get_platform_fee_settings")
    current_user = _provider("current_user")
    order_status_label = _provider("order_status_label")
    get_delivery_mode_ui_context = _provider("get_delivery_mode_ui_context")
    mongo = _provider("mongo")

    delivery_mode_settings = get_delivery_mode_settings()
    try:
        payment_gateway_row = mongo.platform_settings.find_one({"key": "payment_gateway_settings"}) or {}
    except Exception:
        payment_gateway_row = {}
    try:
        platform_fee_settings = get_platform_fee_settings()
    except Exception:
        platform_fee_settings = {"enabled": False}

    online_payment_allowed = bool(delivery_mode_settings.get("allow_online_payment", True))
    pay_on_delivery_allowed = bool(delivery_mode_settings.get("allow_cod_payment", True))
    platform_fee_enabled = bool(platform_fee_settings.get("enabled", False))
    payment_gateway_enabled = bool(payment_gateway_row.get("enabled", False))

    return {
        "datetime": datetime,
        "current_user": current_user(),
        "service_area": session.get("service_area"),
        "order_status_label": order_status_label,
        "delivery_mode_settings": delivery_mode_settings,
        "active_delivery_mode": delivery_mode_settings.get("active_delivery_mode", "IN_HOUSE"),
        "in_house_delivery_enabled": bool(delivery_mode_settings.get("in_house_delivery_enabled", True)),
        "external_delivery_enabled": bool(delivery_mode_settings.get("external_delivery_enabled", False)),
        "external_local_delivery_enabled": bool(delivery_mode_settings.get("external_local_delivery_enabled", False)),
        "third_party_shipping_enabled": bool(delivery_mode_settings.get("third_party_shipping_enabled", False)),
        "return_refund_enabled": bool(delivery_mode_settings.get("return_refund_enabled", True)),
        "online_payment_allowed": online_payment_allowed,
        "pay_on_delivery_allowed": pay_on_delivery_allowed,
        "payment_gateway_enabled": payment_gateway_enabled,
        "platform_fee_enabled": platform_fee_enabled,
        "delivery_mode_ui": get_delivery_mode_ui_context(delivery_mode_settings),
    }


def inject_cart_count():
    try:
        user = _provider("current_user")()
        if not user:
            return {"cart_count": 0}
        cart_id = _provider("get_or_create_cart")(user["id"])
        count = _provider("mongo").cart_items.count_documents({"cart_id": cart_id})
        return {"cart_count": count}
    except Exception:
        return {"cart_count": 0}


def inject_footer_links():
    return {"FOOTER_LINKS": FOOTER_LINKS}


def inject_site_brand_settings():
    return {
        "APP_BRAND_NAME": _env_text("APP_BRAND_NAME", "NELOCALS"),
        "SUPPORT_EMAIL": _env_text("SUPPORT_EMAIL", "support@nelocals.in"),
        "SOCIAL_FACEBOOK_URL": _env_text("SOCIAL_FACEBOOK_URL", ""),
        "SOCIAL_INSTAGRAM_URL": _env_text("SOCIAL_INSTAGRAM_URL", ""),
        "SOCIAL_YOUTUBE_URL": _env_text("SOCIAL_YOUTUBE_URL", ""),
    }


def register_template_context_processors(app) -> None:
    app.context_processor(inject_globals)
    app.context_processor(inject_cart_count)
    app.context_processor(inject_footer_links)
    app.context_processor(inject_site_brand_settings)
