"""Shared helpers for NE FRESH external delivery integrations.

These services intentionally do not change existing in-house delivery logic.
They prepare normalized payloads and safe provider responses for external local
and third-party shipping modes.
"""

from datetime import datetime


def utc_now_iso():
    return datetime.utcnow().isoformat()


def safe_float(value, default=0.0):
    try:
        if value is None or str(value).strip() == "":
            return float(default)
        return float(value)
    except Exception:
        return float(default)


def safe_text(value, fallback=""):
    value = "" if value is None else str(value).strip()
    return value if value else fallback


def build_package_from_order_items(order=None, items=None, settings=None):
    order = order or {}
    items = items or []
    settings = settings or {}

    saved_package = order.get("external_package_snapshot") or {}
    if isinstance(saved_package, dict) and saved_package.get("weight_kg"):
        return {
            "weight_kg": safe_float(saved_package.get("weight_kg"), settings.get("default_package_weight_kg") or 1.0),
            "length_cm": safe_float(saved_package.get("length_cm"), settings.get("default_package_length_cm") or 10.0),
            "breadth_cm": safe_float(saved_package.get("breadth_cm"), settings.get("default_package_breadth_cm") or 10.0),
            "height_cm": safe_float(saved_package.get("height_cm"), settings.get("default_package_height_cm") or 10.0),
            "source": saved_package.get("source") or "order_saved_package",
        }

    default_weight = safe_float(settings.get("default_package_weight_kg"), 1.0)
    default_length = safe_float(settings.get("default_package_length_cm"), 10.0)
    default_breadth = safe_float(settings.get("default_package_breadth_cm"), 10.0)
    default_height = safe_float(settings.get("default_package_height_cm"), 10.0)

    total_weight = 0.0
    max_length = 0.0
    max_breadth = 0.0
    max_height = 0.0
    used_product_dimensions = False

    for item in items:
        qty = max(safe_float(item.get("quantity"), 1.0), 1.0)
        weight = safe_float(item.get("shipping_weight_kg"), 0.0)
        length = safe_float(item.get("shipping_length_cm"), 0.0)
        breadth = safe_float(item.get("shipping_breadth_cm"), 0.0)
        height = safe_float(item.get("shipping_height_cm"), 0.0)

        total_weight += (weight if weight > 0 else default_weight) * qty
        if weight > 0 or length > 0 or breadth > 0 or height > 0:
            used_product_dimensions = True
        max_length = max(max_length, length)
        max_breadth = max(max_breadth, breadth)
        max_height = max(max_height, height)

    if total_weight <= 0:
        total_weight = default_weight

    return {
        "weight_kg": round(max(total_weight, 0.1), 3),
        "length_cm": round(max(max_length, default_length, 1.0), 2),
        "breadth_cm": round(max(max_breadth, default_breadth, 1.0), 2),
        "height_cm": round(max(max_height, default_height, 1.0), 2),
        "source": "product_dimensions" if used_product_dimensions else "admin_default_package",
    }


def build_external_delivery_payload(order, address=None, items=None, settings=None):
    """Builds a provider-neutral payload from an NE FRESH order."""
    order = order or {}
    address = address or {}
    items = items or []
    settings = settings or {}

    order_id = str(order.get("_id") or order.get("id") or "")
    active_mode = order.get("active_delivery_mode") or "EXTERNAL_LOCAL_DELIVERY"

    return {
        "order_id": order_id,
        "active_delivery_mode": active_mode,
        "provider": order.get("external_delivery_provider") or "MANUAL",
        "provider_type": order.get("external_delivery_provider_type") or order.get("external_delivery_partner_type") or "HYPERLOCAL",
        "payment_method": order.get("payment_method") or "COD",
        "payment_flow": order.get("payment_flow") or order.get("official_payment_mode") or "",
        "cod_amount": safe_float(order.get("external_cod_amount"), 0),
        "order_amount": safe_float(order.get("total_payable"), order.get("total_amount") or 0),
        "delivery_charge": safe_float(order.get("external_delivery_charge"), order.get("external_delivery_fee_amount") or 0),
        "customer": {
            "name": safe_text(order.get("customer_name"), "Customer"),
            "phone": safe_text(order.get("customer_phone")),
        },
        "pickup": {
            "store_id": str(order.get("store_id") or ""),
            "store_name": safe_text(order.get("store_name"), "NE FRESH Store"),
            "latitude": order.get("store_latitude"),
            "longitude": order.get("store_longitude"),
        },
        "drop": {
            "line1": safe_text(address.get("line1") or order.get("delivery_location_address")),
            "line2": safe_text(address.get("line2")),
            "city": safe_text(address.get("city") or order.get("delivery_location_city")),
            "state": safe_text(address.get("state") or order.get("delivery_location_state")),
            "pincode": safe_text(address.get("pincode") or order.get("delivery_location_pincode")),
            "latitude": order.get("delivery_latitude"),
            "longitude": order.get("delivery_longitude"),
        },
        "package": build_package_from_order_items(order=order, items=items, settings=settings),
        "items": [
            {
                "name": safe_text(item.get("product_name") or item.get("name"), "Item"),
                "quantity": safe_float(item.get("quantity"), 1),
                "unit_price": safe_float(item.get("unit_price"), item.get("price_per_unit") or 0),
            }
            for item in items
        ],
        "created_at": utc_now_iso(),
    }


def manual_booking_response(payload, provider="MANUAL"):
    """Creates a safe internal booking record when real provider API is not configured yet."""
    order_id = payload.get("order_id") or "ORDER"
    stamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    provider = safe_text(provider, "MANUAL").upper()

    return {
        "ok": True,
        "mode": "MANUAL_BOOKING",
        "provider": provider,
        "external_order_id": f"{provider}-{order_id[-6:]}-{stamp}",
        "external_shipment_id": f"SHIP-{order_id[-6:]}-{stamp}",
        "external_awb": "",
        "external_tracking_url": "",
        "external_label_url": "",
        "external_manifest_url": "",
        "status": "BOOKED_MANUALLY",
        "message": "External delivery booking recorded manually. Add live provider credentials to automate booking.",
        "raw_response": {},
    }
