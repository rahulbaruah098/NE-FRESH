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
