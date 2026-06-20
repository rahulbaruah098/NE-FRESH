"""Generic hyperlocal delivery adapter for Rapido/Ola/Uber/Shiprocket Quick style flows."""

import requests
from .base import manual_booking_response


def create_hyperlocal_booking(payload, settings):
    provider = (settings.get("hyperlocal_provider") or payload.get("provider") or "MANUAL_HYPERLOCAL").upper()

    if not settings.get("hyperlocal_enabled") or not settings.get("hyperlocal_api_base_url") or not settings.get("hyperlocal_api_key"):
        return manual_booking_response(payload, provider=provider)

    try:
        base_url = settings.get("hyperlocal_api_base_url").rstrip("/")
        response = requests.post(
            f"{base_url}/orders",
            json=payload,
            headers={"Authorization": f"Bearer {settings.get('hyperlocal_api_key')}", "Content-Type": "application/json"},
            timeout=30,
        )
        data = response.json() if response.content else {}
        if response.status_code >= 400:
            return {
                "ok": False,
                "provider": provider,
                "status": "BOOKING_FAILED",
                "message": data.get("message") or "Hyperlocal booking failed.",
                "raw_response": data,
            }

        return {
            "ok": True,
            "provider": provider,
            "status": data.get("status") or "BOOKED",
            "external_order_id": str(data.get("order_id") or data.get("id") or ""),
            "external_shipment_id": str(data.get("shipment_id") or data.get("trip_id") or ""),
            "external_awb": "",
            "external_tracking_url": str(data.get("tracking_url") or ""),
            "external_label_url": "",
            "external_manifest_url": "",
            "message": data.get("message") or "Hyperlocal booking created.",
            "raw_response": data,
        }
    except Exception as exc:
        return {
            "ok": False,
            "provider": provider,
            "status": "BOOKING_EXCEPTION",
            "message": str(exc),
            "raw_response": {},
        }



def quote_hyperlocal_delivery(payload, settings):
    """Return a checkout delivery-fee quote for Rapido/Zomato/hyperlocal mode.

    Uses live generic quote endpoint when configured; otherwise applies a safe
    distance-based manual quote so checkout can work before API onboarding.
    """
    provider = (settings.get("hyperlocal_provider") or payload.get("provider") or "MANUAL_HYPERLOCAL").upper()
    distance_km = float(payload.get("distance_km") or 0)
    base_fee = float(settings.get("external_local_base_fee") or 40)
    per_km_fee = float(settings.get("external_local_per_km_fee") or 8)
    min_fee = float(settings.get("external_local_min_fee") or base_fee)
    fallback_fee = max(min_fee, base_fee + max(distance_km, 0) * per_km_fee)

    if not settings.get("hyperlocal_enabled") or not settings.get("hyperlocal_api_base_url") or not settings.get("hyperlocal_api_key"):
        return {
            "ok": True,
            "serviceable": True,
            "provider": provider,
            "provider_type": "HYPERLOCAL",
            "delivery_fee": round(fallback_fee, 2),
            "delivery_fee_source": "hyperlocal_manual_distance_quote",
            "quote_status": "MANUAL_FALLBACK",
            "eta_minutes": int(max(20, min(90, 20 + distance_km * 5))) if distance_km else None,
            "message": "Hyperlocal API credentials are not configured. Manual distance quote applied.",
            "raw_response": {},
        }

    try:
        base_url = settings.get("hyperlocal_api_base_url").rstrip("/")
        response = requests.post(
            f"{base_url}/quotes",
            json=payload,
            headers={"Authorization": f"Bearer {settings.get('hyperlocal_api_key')}", "Content-Type": "application/json"},
            timeout=25,
        )
        data = response.json() if response.content else {}

        if response.status_code >= 400:
            return {
                "ok": True,
                "serviceable": True,
                "provider": provider,
                "provider_type": "HYPERLOCAL",
                "delivery_fee": round(fallback_fee, 2),
                "delivery_fee_source": "hyperlocal_quote_fallback",
                "quote_status": "QUOTE_FALLBACK",
                "eta_minutes": None,
                "message": data.get("message") or "Hyperlocal live quote unavailable. Manual quote applied.",
                "raw_response": data,
            }

        fee = (
            data.get("delivery_fee")
            or data.get("fee")
            or data.get("price")
            or data.get("amount")
            or fallback_fee
        )
        eta = data.get("eta_minutes") or data.get("eta") or data.get("estimated_time_minutes")

        return {
            "ok": True,
            "serviceable": bool(data.get("serviceable", True)),
            "provider": provider,
            "provider_type": "HYPERLOCAL",
            "delivery_fee": round(float(fee or fallback_fee), 2),
            "delivery_fee_source": "hyperlocal_live_quote",
            "quote_status": "LIVE_QUOTE",
            "eta_minutes": eta,
            "message": data.get("message") or "Hyperlocal quote applied.",
            "raw_response": data,
        }
    except Exception as exc:
        return {
            "ok": True,
            "serviceable": True,
            "provider": provider,
            "provider_type": "HYPERLOCAL",
            "delivery_fee": round(fallback_fee, 2),
            "delivery_fee_source": "hyperlocal_exception_fallback",
            "quote_status": "EXCEPTION_FALLBACK",
            "eta_minutes": int(max(20, min(90, 20 + distance_km * 5))) if distance_km else None,
            "message": f"Hyperlocal quote failed; manual quote applied. {exc}",
            "raw_response": {},
        }
