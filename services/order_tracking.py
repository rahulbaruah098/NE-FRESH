"""Read-only order tracking hydration extracted during Step 5."""

import math
from bson import ObjectId
from extensions import mongo

def get_order_full(oid, for_user_id=None):
    try:
        oid_obj = ObjectId(oid)
    except Exception:
        return None

    query_filter = {"_id": oid_obj}

    if for_user_id is not None:
        query_filter["user_id"] = str(for_user_id)

    order = mongo.orders.find_one(query_filter)

    if not order:
        return None

    order["id"] = str(order["_id"])

    def _track_float(value):
        try:
            if value is None or str(value).strip() == "":
                return None
            number = float(value)
            if not math.isfinite(number):
                return None
            return number
        except Exception:
            return None

    def _clean_lat_lng(lat_value, lng_value):
        """
        Returns clean map-safe coordinates.

        Also fixes accidental swapped lat/lng values:
        - correct Assam example: lat 26.x, lng 92.x
        - wrong swapped example: lat 92.x, lng 26.x
        """
        lat = _track_float(lat_value)
        lng = _track_float(lng_value)

        if lat is None or lng is None:
            return None, None

        # Fix swapped lat/lng when lat looks like longitude and lng looks like latitude.
        if abs(lat) > 90 and abs(lng) <= 90:
            lat, lng = lng, lat

        # Extra swap guard for India/Assam-style values.
        if 65 <= lat <= 100 and 5 <= lng <= 40:
            lat, lng = lng, lat

        if lat < -90 or lat > 90:
            return None, None

        if lng < -180 or lng > 180:
            return None, None

        return round(lat, 7), round(lng, 7)

    def _safe_id(value):
        if value is None:
            return ""
        try:
            if isinstance(value, ObjectId):
                return str(value)
        except Exception:
            pass
        return str(value)

    items = list(mongo.order_items.find({"order_id": oid_obj}))

    for item in items:
        item["id"] = str(item["_id"])

        item_type = (item.get("item_type") or "product").strip().lower()
        is_bundle = item_type == "bundle" or bool(item.get("bundle_id"))

        item["item_type"] = "bundle" if is_bundle else "product"
        item["is_bundle"] = is_bundle

        if is_bundle:
            item["product_id"] = ""
            item["bundle_id"] = _safe_id(item.get("bundle_id") or item.get("bundle_id_str"))
            item["bundle_id_str"] = item.get("bundle_id_str") or item["bundle_id"]
            item["name"] = item.get("bundle_name_snapshot") or item.get("product_name") or "Product Bundle"
            item["product_name"] = item["name"]
            item["bundle_items_snapshot"] = item.get("bundle_items_snapshot") or []
            item["bundle_savings_snapshot"] = float(item.get("bundle_savings_snapshot") or 0)
            item["items_total_snapshot"] = float(item.get("items_total_snapshot") or 0)
        else:
            item["product_id"] = _safe_id(item.get("product_id"))
            item["name"] = item.get("product_name", "")

        item["quantity"] = float(item.get("quantity") or item.get("cart_quantity") or 0)
        item["unit_label"] = item.get("unit_label") or ("bundle" if is_bundle else "unit")
        item["unit_type"] = item.get("unit_type") or ("COUNT" if is_bundle else "COUNT")
        item["price_per_unit"] = float(item.get("price_per_unit") or item.get("unit_price") or 0)
        item["unit_price"] = item["price_per_unit"]
        item["line_total"] = float(item.get("line_total") or (item["quantity"] * item["price_per_unit"]))

    addr = mongo.order_addresses.find_one({"order_id": oid_obj})

    if addr:
        addr["id"] = str(addr["_id"])
    else:
        addr = None

    # ------------------------------------------------------------
    # Customer delivery point
    # Priority:
    # 1. order_addresses final checkout latitude/longitude
    # 2. order.delivery_latitude / order.delivery_longitude snapshot
    # 3. saved address latitude/longitude snapshot
    # ------------------------------------------------------------
    customer_lat, customer_lng = _clean_lat_lng(
        addr.get("latitude") if addr else None,
        addr.get("longitude") if addr else None
    )

    customer_source = (addr.get("location_source") if addr else "") or ""

    if customer_lat is None or customer_lng is None:
        customer_lat, customer_lng = _clean_lat_lng(
            order.get("delivery_latitude"),
            order.get("delivery_longitude")
        )
        customer_source = order.get("delivery_location_source") or "order_delivery_snapshot"

    if (customer_lat is None or customer_lng is None) and addr:
        customer_lat, customer_lng = _clean_lat_lng(
            addr.get("saved_address_latitude"),
            addr.get("saved_address_longitude")
        )
        customer_source = "saved_address_snapshot"

    if addr:
        addr["latitude"] = customer_lat
        addr["longitude"] = customer_lng
        addr["location_source"] = customer_source

    # ------------------------------------------------------------
    # Store pickup point
    # Priority:
    # 1. stores collection current latitude/longitude
    # 2. order store_latitude/store_longitude snapshot
    # ------------------------------------------------------------
    store_doc = None
    store_id = order.get("store_id")

    if store_id:
        try:
            store_doc = mongo.stores.find_one({"_id": store_id})
        except Exception:
            store_doc = None

        if not store_doc:
            try:
                store_doc = mongo.stores.find_one({"_id": ObjectId(str(store_id))})
            except Exception:
                store_doc = mongo.stores.find_one({"_id": str(store_id)})

    store_lat, store_lng = _clean_lat_lng(
        store_doc.get("latitude") if store_doc else None,
        store_doc.get("longitude") if store_doc else None
    )

    if store_lat is None or store_lng is None:
        store_lat, store_lng = _clean_lat_lng(
            order.get("store_latitude"),
            order.get("store_longitude")
        )

    store_view = {
        "id": _safe_id(store_doc.get("_id") if store_doc else store_id),
        "store_name": (
            store_doc.get("store_name")
            if store_doc
            else order.get("store_name")
            or "Store"
        ),
        "address": store_doc.get("address") if store_doc else "",
        "latitude": store_lat,
        "longitude": store_lng,
    }

    order["store_latitude"] = store_lat
    order["store_longitude"] = store_lng

    # ------------------------------------------------------------
    # Rider live point
    # Priority:
    # 1. latest delivery_locations for this order
    # 2. delivery_availability for assigned rider
    # ------------------------------------------------------------
    latest_rider_location = mongo.delivery_locations.find_one(
        {"order_id": oid_obj},
        sort=[("recorded_at", -1)]
    )

    rider_lat = None
    rider_lng = None
    rider_updated_at = ""
    rider_source = ""

    if latest_rider_location:
        rider_lat, rider_lng = _clean_lat_lng(
            latest_rider_location.get("latitude"),
            latest_rider_location.get("longitude")
        )
        rider_updated_at = latest_rider_location.get("recorded_at") or ""
        rider_source = "delivery_locations"

    if rider_lat is None or rider_lng is None:
        delivery_partner_id = _safe_id(order.get("delivery_partner_id"))

        if delivery_partner_id:
            availability = mongo.delivery_availability.find_one({
                "user_id": delivery_partner_id,
                "active": True
            }) or {}

            rider_lat, rider_lng = _clean_lat_lng(
                availability.get("latitude"),
                availability.get("longitude")
            )
            rider_updated_at = availability.get("updated_at") or ""
            rider_source = "delivery_availability"

    rider_view = {
        "id": _safe_id(order.get("delivery_partner_id")),
        "name": order.get("delivery_partner_name") or "",
        "phone": order.get("delivery_partner_phone") or "",
        "latitude": rider_lat,
        "longitude": rider_lng,
        "updated_at": rider_updated_at,
        "source": rider_source,
    }

    tracking_map = {
        "order_id": str(oid_obj),
        "customer": {
            "label": "Delivery Address",
            "latitude": customer_lat,
            "longitude": customer_lng,
            "source": customer_source,
            "address": {
                "line1": addr.get("line1") if addr else "",
                "line2": addr.get("line2") if addr else "",
                "city": addr.get("city") if addr else "",
                "state": addr.get("state") if addr else "",
                "pincode": addr.get("pincode") if addr else "",
            }
        },
        "store": {
            "label": store_view.get("store_name") or "Store",
            "latitude": store_lat,
            "longitude": store_lng,
            "address": store_view.get("address") or "",
        },
        "rider": rider_view
    }

    events = list(mongo.order_events.find({"order_id": oid_obj}).sort("created_at", 1))

    for e in events:
        e["id"] = str(e["_id"])

    return {
        "order": order,
        "items": items,
        "address": addr,
        "events": events,
        "store": store_view,
        "tracking_map": tracking_map,
    }
