"""Shiprocket integration adapter.

The adapter is credential-ready. If credentials are missing, it returns a safe
manual booking response instead of breaking checkout/order flow.
"""

import os
import requests
from .base import manual_booking_response

SHIPROCKET_BASE_URL = "https://apiv2.shiprocket.in"


def _shiprocket_credentials(settings):
    """Resolve runtime credentials with environment secrets taking priority.

    This keeps existing Mongo-backed Admin settings backward compatible while
    allowing production EC2 to keep the password outside normal application
    data (for example in /etc/nefresh/nefresh.env or AWS SSM-derived env).
    """
    settings = settings or {}
    email = (os.getenv("SHIPROCKET_EMAIL") or settings.get("shiprocket_email") or "").strip()
    password = (os.getenv("SHIPROCKET_PASSWORD") or settings.get("shiprocket_password") or "").strip()
    return email, password


def _has_shiprocket_credentials(settings):
    email, password = _shiprocket_credentials(settings)
    return bool(settings.get("shiprocket_enabled") and email and password)


def create_shiprocket_booking(payload, settings):
    if not _has_shiprocket_credentials(settings):
        return {
            "ok": False,
            "provider": "SHIPROCKET",
            "status": "SHIPROCKET_CREDENTIALS_MISSING",
            "message": "Shiprocket API is not enabled or credentials are missing. Add API user email/password in Local Fare & Shiprocket Settings, then retry.",
            "raw_response": {},
        }

    try:
        shiprocket_email, shiprocket_password = _shiprocket_credentials(settings)
        login_res = requests.post(
            f"{SHIPROCKET_BASE_URL}/v1/external/auth/login",
            json={
                "email": shiprocket_email,
                "password": shiprocket_password,
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
            **({"channel_id": settings.get("shiprocket_channel_id")} if settings.get("shiprocket_channel_id") else {}),
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



def quote_shiprocket_delivery(payload, settings):
    """Return a checkout delivery-fee quote for Shiprocket/courier mode.

    If live credentials or serviceability mapping is unavailable, this returns a
    safe manual/fallback quote so checkout can still work without breaking the
    existing flow.
    """
    fallback_fee = float(payload.get("fallback_delivery_fee") or settings.get("third_party_base_fee") or 65)

    if not _has_shiprocket_credentials(settings):
        return {
            "ok": True,
            "serviceable": True,
            "provider": "SHIPROCKET",
            "provider_type": "COURIER",
            "delivery_fee": round(fallback_fee, 2),
            "delivery_fee_source": "shiprocket_manual_fallback",
            "quote_status": "MANUAL_FALLBACK",
            "message": "Shiprocket credentials are not configured. Manual courier quote applied.",
            "eta_minutes": None,
            "raw_response": {},
        }

    try:
        shiprocket_email, shiprocket_password = _shiprocket_credentials(settings)
        login_res = requests.post(
            f"{SHIPROCKET_BASE_URL}/v1/external/auth/login",
            json={
                "email": shiprocket_email,
                "password": shiprocket_password,
            },
            timeout=20,
        )
        login_json = login_res.json() if login_res.content else {}
        token = login_json.get("token")

        if not token:
            return {
                "ok": True,
                "serviceable": True,
                "provider": "SHIPROCKET",
                "provider_type": "COURIER",
                "delivery_fee": round(fallback_fee, 2),
                "delivery_fee_source": "shiprocket_auth_fallback",
                "quote_status": "AUTH_FALLBACK",
                "message": login_json.get("message") or "Shiprocket quote auth failed. Manual courier quote applied.",
                "eta_minutes": None,
                "raw_response": login_json,
            }

        headers = {"Authorization": f"Bearer {token}"}
        drop = payload.get("drop") or {}
        pickup = payload.get("pickup") or {}
        package = payload.get("package") or {}
        cod_amount = float(payload.get("cod_amount") or 0)

        pickup_postcode = str(pickup.get("pincode") or settings.get("shiprocket_pickup_pincode") or "").strip()
        delivery_postcode = str(drop.get("pincode") or "").strip()
        weight = float(package.get("weight_kg") or settings.get("default_package_weight_kg") or 1.0)

        if not pickup_postcode or not delivery_postcode:
            return {
                "ok": True,
                "serviceable": True,
                "provider": "SHIPROCKET",
                "provider_type": "COURIER",
                "delivery_fee": round(fallback_fee, 2),
                "delivery_fee_source": "shiprocket_missing_pincode_fallback",
                "quote_status": "MISSING_PINCODE_FALLBACK",
                "message": "Pickup/drop pincode missing for Shiprocket quote. Manual courier quote applied.",
                "eta_minutes": None,
                "raw_response": {},
            }

        params = {
            "pickup_postcode": pickup_postcode,
            "delivery_postcode": delivery_postcode,
            "cod": 1 if cod_amount > 0 else 0,
            "weight": max(weight, 0.1),
        }

        quote_res = requests.get(
            f"{SHIPROCKET_BASE_URL}/v1/external/courier/serviceability/",
            params=params,
            headers=headers,
            timeout=25,
        )
        quote_json = quote_res.json() if quote_res.content else {}
        companies = (((quote_json or {}).get("data") or {}).get("available_courier_companies") or [])

        if quote_res.status_code >= 400 or not companies:
            return {
                "ok": True,
                "serviceable": True,
                "provider": "SHIPROCKET",
                "provider_type": "COURIER",
                "delivery_fee": round(fallback_fee, 2),
                "delivery_fee_source": "shiprocket_serviceability_fallback",
                "quote_status": "QUOTE_FALLBACK",
                "message": (quote_json.get("message") if isinstance(quote_json, dict) else "") or "Shiprocket live quote unavailable. Manual courier quote applied.",
                "eta_minutes": None,
                "raw_response": quote_json,
            }

        def _company_rate(row):
            for key in ["freight_charge", "rate", "estimated_charges", "cod_charges"]:
                try:
                    val = float(row.get(key) or 0)
                    if val > 0:
                        return val
                except Exception:
                    continue
            return fallback_fee

        selected = sorted(companies, key=_company_rate)[0]
        fee = _company_rate(selected)
        etd = selected.get("etd") or selected.get("estimated_delivery_days")

        return {
            "ok": True,
            "serviceable": True,
            "provider": "SHIPROCKET",
            "provider_type": "COURIER",
            "delivery_fee": round(float(fee), 2),
            "delivery_fee_source": "shiprocket_live_quote",
            "quote_status": "LIVE_QUOTE",
            "message": f"Shiprocket courier quote applied{f' ({etd})' if etd else ''}.",
            "eta_minutes": None,
            "courier_company": selected.get("courier_name") or selected.get("name") or "",
            "raw_response": quote_json,
        }
    except Exception as exc:
        return {
            "ok": True,
            "serviceable": True,
            "provider": "SHIPROCKET",
            "provider_type": "COURIER",
            "delivery_fee": round(fallback_fee, 2),
            "delivery_fee_source": "shiprocket_exception_fallback",
            "quote_status": "EXCEPTION_FALLBACK",
            "message": f"Shiprocket quote failed; manual courier quote applied. {exc}",
            "eta_minutes": None,
            "raw_response": {},
        }
