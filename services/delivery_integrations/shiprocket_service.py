"""Shiprocket integration adapter.

The adapter is credential-ready. If credentials are missing, it returns a safe
manual booking response instead of breaking checkout/order flow.
"""

import requests
from .base import manual_booking_response

SHIPROCKET_BASE_URL = "https://apiv2.shiprocket.in"


def _has_shiprocket_credentials(settings):
    return bool(
        settings.get("shiprocket_enabled")
        and settings.get("shiprocket_email")
        and settings.get("shiprocket_password")
    )


def create_shiprocket_booking(payload, settings):
    if not _has_shiprocket_credentials(settings):
        return manual_booking_response(payload, provider="SHIPROCKET")

    try:
        login_res = requests.post(
            f"{SHIPROCKET_BASE_URL}/v1/external/auth/login",
            json={
                "email": settings.get("shiprocket_email"),
                "password": settings.get("shiprocket_password"),
            },
            timeout=20,
        )
        login_json = login_res.json() if login_res.content else {}
        token = login_json.get("token")
        if not token:
            return {
                "ok": False,
                "provider": "SHIPROCKET",
                "status": "AUTH_FAILED",
                "message": login_json.get("message") or "Shiprocket token was not returned.",
                "raw_response": login_json,
            }

        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

        # This payload is intentionally conservative. Live projects should map exact
        # package/order fields against the active Shiprocket account contract.
        sr_payload = {
            "order_id": payload.get("order_id"),
            "order_date": payload.get("created_at"),
            "pickup_location": settings.get("shiprocket_pickup_location") or "Primary",
            "billing_customer_name": payload.get("customer", {}).get("name"),
            "billing_phone": payload.get("customer", {}).get("phone"),
            "billing_address": payload.get("drop", {}).get("line1"),
            "billing_city": payload.get("drop", {}).get("city"),
            "billing_state": payload.get("drop", {}).get("state"),
            "billing_pincode": payload.get("drop", {}).get("pincode"),
            "shipping_is_billing": True,
            "payment_method": "COD" if float(payload.get("cod_amount") or 0) > 0 else "Prepaid",
            "sub_total": payload.get("order_amount"),
            "length": payload.get("package", {}).get("length_cm"),
            "breadth": payload.get("package", {}).get("breadth_cm"),
            "height": payload.get("package", {}).get("height_cm"),
            "weight": payload.get("package", {}).get("weight_kg"),
            "order_items": [
                {
                    "name": item.get("name"),
                    "sku": item.get("name"),
                    "units": item.get("quantity"),
                    "selling_price": item.get("unit_price"),
                }
                for item in payload.get("items") or []
            ],
        }

        create_res = requests.post(
            f"{SHIPROCKET_BASE_URL}/v1/external/orders/create/adhoc",
            json=sr_payload,
            headers=headers,
            timeout=30,
        )
        create_json = create_res.json() if create_res.content else {}

        if create_res.status_code >= 400:
            return {
                "ok": False,
                "provider": "SHIPROCKET",
                "status": "BOOKING_FAILED",
                "message": create_json.get("message") or "Shiprocket booking failed.",
                "raw_response": create_json,
            }

        return {
            "ok": True,
            "provider": "SHIPROCKET",
            "status": "BOOKED",
            "external_order_id": str(create_json.get("order_id") or payload.get("order_id") or ""),
            "external_shipment_id": str(create_json.get("shipment_id") or ""),
            "external_awb": str(create_json.get("awb_code") or ""),
            "external_tracking_url": str(create_json.get("tracking_url") or ""),
            "external_label_url": str(create_json.get("label_url") or ""),
            "external_manifest_url": str(create_json.get("manifest_url") or ""),
            "message": create_json.get("message") or "Shiprocket shipment created.",
            "raw_response": create_json,
        }
    except Exception as exc:
        return {
            "ok": False,
            "provider": "SHIPROCKET",
            "status": "BOOKING_EXCEPTION",
            "message": str(exc),
            "raw_response": {},
        }
