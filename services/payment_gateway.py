"""Razorpay/payment-gateway configuration shared by customer checkout and Admin.

Secrets remain environment-only. Mongo stores only enable/mode/behavior flags.
"""

import os
import razorpay
from extensions import mongo

PAYMENT_GATEWAY_SETTINGS_KEY = "payment_gateway_settings"

def _get_razorpay_env_keys(mode="TEST"):
    mode = (mode or "TEST").strip().upper()

    if mode == "LIVE":
        return {
            "key_id": (os.getenv("RAZORPAY_LIVE_KEY_ID") or "").strip(),
            "key_secret": (os.getenv("RAZORPAY_LIVE_KEY_SECRET") or "").strip(),
        }

    return {
        "key_id": (os.getenv("RAZORPAY_TEST_KEY_ID") or "").strip(),
        "key_secret": (os.getenv("RAZORPAY_TEST_KEY_SECRET") or "").strip(),
    }

def get_checkout_payment_gateway_settings():
    settings = mongo.platform_settings.find_one({
        "key": PAYMENT_GATEWAY_SETTINGS_KEY
    }) or {}

    mode = (settings.get("mode") or "TEST").strip().upper()

    if mode not in ["TEST", "LIVE"]:
        mode = "TEST"

    gateway = (settings.get("gateway") or "RAZORPAY").strip().upper()

    if gateway not in ["RAZORPAY"]:
        gateway = "RAZORPAY"

    env_keys = _get_razorpay_env_keys(mode)

    return {
        "enabled": bool(settings.get("enabled", False)),
        "gateway": gateway,
        "mode": mode,

        # Public key only. This can go to frontend Razorpay Checkout.
        "razorpay_key_id": env_keys.get("key_id") or "",

        "auto_capture_enabled": bool(settings.get("auto_capture_enabled", True)),
    }

def get_server_payment_gateway_settings():
    settings = mongo.platform_settings.find_one({
        "key": PAYMENT_GATEWAY_SETTINGS_KEY
    }) or {}

    mode = (settings.get("mode") or "TEST").strip().upper()

    if mode not in ["TEST", "LIVE"]:
        mode = "TEST"

    gateway = (settings.get("gateway") or "RAZORPAY").strip().upper()

    if gateway not in ["RAZORPAY"]:
        gateway = "RAZORPAY"

    env_keys = _get_razorpay_env_keys(mode)

    return {
        "enabled": bool(settings.get("enabled", False)),
        "gateway": gateway,
        "mode": mode,

        # Read from .env only. Do not store/use secret from MongoDB.
        "razorpay_key_id": env_keys.get("key_id") or "",
        "razorpay_key_secret": env_keys.get("key_secret") or "",

        "auto_capture_enabled": bool(settings.get("auto_capture_enabled", True)),
    }

def get_razorpay_client_from_settings(settings=None):
    settings = settings or get_server_payment_gateway_settings()

    if not settings.get("enabled"):
        return None, "Online payment is disabled by Admin."

    if settings.get("gateway") != "RAZORPAY":
        return None, "Only Razorpay gateway is supported right now."

    key_id = (settings.get("razorpay_key_id") or "").strip()
    key_secret = (settings.get("razorpay_key_secret") or "").strip()

    if not key_id or not key_secret:
        return None, (
            "Razorpay credentials are missing in .env. "
            "Please set RAZORPAY_TEST_KEY_ID and RAZORPAY_TEST_KEY_SECRET for TEST mode."
        )

    try:
        client = razorpay.Client(auth=(key_id, key_secret))
        return client, ""
    except Exception as exc:
        return None, f"Unable to initialize Razorpay client: {str(exc)}"

def _admin_get_razorpay_env_status(mode="TEST"):
    mode = (mode or "TEST").strip().upper()

    if mode == "LIVE":
        key_id = (os.getenv("RAZORPAY_LIVE_KEY_ID") or "").strip()
        key_secret = (os.getenv("RAZORPAY_LIVE_KEY_SECRET") or "").strip()
    else:
        key_id = (os.getenv("RAZORPAY_TEST_KEY_ID") or "").strip()
        key_secret = (os.getenv("RAZORPAY_TEST_KEY_SECRET") or "").strip()

    return {
        "key_id_configured": bool(key_id),
        "key_secret_configured": bool(key_secret),
        "key_id_masked": (
            key_id[:10] + "..." + key_id[-4:]
            if len(key_id) > 16
            else ("Configured" if key_id else "Not configured")
        ),
    }

def _admin_get_payment_gateway_settings():
    settings = mongo.platform_settings.find_one({
        "key": PAYMENT_GATEWAY_SETTINGS_KEY
    }) or {}

    mode = (settings.get("mode") or "TEST").strip().upper()

    if mode not in ["TEST", "LIVE"]:
        mode = "TEST"

    gateway = (settings.get("gateway") or "RAZORPAY").strip().upper()

    if gateway not in ["RAZORPAY"]:
        gateway = "RAZORPAY"

    env_status = _admin_get_razorpay_env_status(mode)

    return {
        "enabled": bool(settings.get("enabled", False)),
        "gateway": gateway,
        "mode": mode,

        # Keys are now read from .env only.
        "razorpay_key_id": env_status.get("key_id_masked") or "",
        "razorpay_key_id_configured": bool(env_status.get("key_id_configured")),
        "razorpay_key_secret_configured": bool(env_status.get("key_secret_configured")),

        "auto_refund_enabled": bool(settings.get("auto_refund_enabled", False)),
        "auto_capture_enabled": bool(settings.get("auto_capture_enabled", True)),
        "notes": settings.get("notes") or "",
        "updated_at": settings.get("updated_at") or "",
        "updated_by_name": settings.get("updated_by_name") or "",
    }


def verify_razorpay_payment_signature(client, razorpay_order_id, razorpay_payment_id, razorpay_signature):
    """Verify Razorpay signature using the configured server-side client.

    The gateway exception is intentionally allowed to propagate so the route can
    preserve its existing failure audit/update behavior.
    """
    client.utility.verify_payment_signature({
        "razorpay_order_id": razorpay_order_id,
        "razorpay_payment_id": razorpay_payment_id,
        "razorpay_signature": razorpay_signature,
    })
