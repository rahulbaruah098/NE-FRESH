"""Protected delivery operations extracted during Step 5.

The functions in this module are intentionally kept behavior-compatible with
the Step 4 source.  They cover delivery availability, assignment/reassignment,
order timeline events, distance checks and read-only delivery hydration.
"""

from datetime import datetime
import math

from bson import ObjectId

from extensions import mongo
from helpers.numbers import _delivery_float_or_none, _get_float_or_none

BASE_DELIVERY_FEE_INR = 40

DELIVERY_SURCHARGE_SLABS = [
    (0, 2, 0),       # 0 - 2 km: ₹40
    (2, 5, 15),      # 2 - 5 km: ₹55
    (5, 10, 30),     # 5 - 10 km: ₹70
    (10, 20, 50),    # 10 - 20 km: ₹90
    (20, 50, 80),    # 20 - 50 km: ₹120
    (50, 9999, 120), # 50+ km: ₹160
]

MAX_DELIVERY_KM = None

DELIVERY_MODE = "ASSAM_STATE_WIDE_DISTANCE_FEE"

DELIVERY_ACTIONABLE_STATUSES = ["SHIPMENT_READY", "READY_FOR_PICKUP"]

DELIVERY_ASSIGNED_ACTIVE_STATUSES = [
    "ASSIGNED_TO_DELIVERY",
    "REACHED_STORE",
    "PICKED_UP",
    "OUT_FOR_DELIVERY"
]

DELIVERY_ACCEPT_RADIUS_KM = 15.0

DELIVERY_STORE_ASSIGNABLE_STATUSES = {
    "SHIPMENT_READY",
    "READY_FOR_PICKUP",  # legacy support
    "ASSIGNED_TO_DELIVERY",
    "REACHED_STORE"
}

DELIVERY_REASSIGN_BLOCKED_STATUSES = {
    "PICKED_UP",
    "OUT_FOR_DELIVERY",
    "DELIVERED",
    "CANCELLED"
}

DELIVERY_PROGRESS_STATUSES = {
    "ASSIGNED_TO_DELIVERY",
    "REACHED_STORE",
    "PICKED_UP",
    "OUT_FOR_DELIVERY",
    "DELIVERED"
}

def haversine_km(lat1, lon1, lat2, lon2):
    if None in (lat1, lon1, lat2, lon2):
        return None
    R = 6371.0
    phi1 = math.radians(float(lat1))
    phi2 = math.radians(float(lat2))
    dphi = math.radians(float(lat2) - float(lat1))
    dlmb = math.radians(float(lon2) - float(lon1))
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

def _delivery_now():
    return datetime.utcnow().isoformat()

def _get_delivery_availability(user_id):
    return mongo.delivery_availability.find_one({"user_id": str(user_id)}) or {}

def _is_delivery_active(user_id):
    row = _get_delivery_availability(user_id)
    return bool(row.get("active"))

def _driver_distance_to_store_km(order_doc, availability_doc):
    driver_lat = _get_float_or_none(availability_doc.get("latitude"))
    driver_lng = _get_float_or_none(availability_doc.get("longitude"))

    if driver_lat is None or driver_lng is None:
        return None

    store = None
    if order_doc.get("store_id"):
        store = mongo.stores.find_one({"_id": order_doc.get("store_id")})

    if not store:
        return None

    store_lat = _get_float_or_none(store.get("latitude"))
    store_lng = _get_float_or_none(store.get("longitude"))

    if store_lat is None or store_lng is None:
        return None

    return haversine_km(driver_lat, driver_lng, store_lat, store_lng)

def _hydrate_delivery_order(o):
    store = mongo.stores.find_one({"_id": o.get("store_id")}) if o.get("store_id") else None

    customer = None
    if o.get("user_id"):
        try:
            customer = mongo.users.find_one({"_id": ObjectId(o.get("user_id"))})
        except Exception:
            customer = None

    addr = mongo.order_addresses.find_one({"order_id": o["_id"]})

    o["id"] = str(o["_id"])
    o["store_name"] = store.get("store_name") if store else o.get("store_name", "")
    o["customer_name"] = customer.get("name") if customer else o.get("customer_name", "")
    o["customer_phone"] = customer.get("phone") if customer else o.get("customer_phone", "")

    o["addr_line1"] = addr.get("line1") if addr else ""
    o["addr_line2"] = addr.get("line2") if addr else ""
    o["addr_city"] = addr.get("city") if addr else ""
    o["addr_state"] = addr.get("state") if addr else ""
    o["addr_pincode"] = addr.get("pincode") if addr else ""
    o["addr_lat"] = addr.get("latitude") if addr else None
    o["addr_lng"] = addr.get("longitude") if addr else None

    o["total_amount"] = float(o.get("total_amount") or 0)
    o["delivery_fee"] = float(o.get("delivery_fee") or 0)
    o["tip_amount"] = float(o.get("tip_amount") or 0)
    o["total_payable"] = (
        float(o.get("total_amount") or 0)
        + float(o.get("delivery_fee") or 0)
        + float(o.get("tip_amount") or 0)
    )

    return o

def calculate_delivery_fee_by_distance(km):
    """
    Assam-wide delivery:
    - No distance blocking.
    - If distance is unavailable, charge base fee.
    - If distance is available, add slab surcharge.
    """
    if km is None:
        return float(BASE_DELIVERY_FEE_INR)

    try:
        km = float(km)
    except Exception:
        return float(BASE_DELIVERY_FEE_INR)

    surcharge = 0

    for low, high, fee in DELIVERY_SURCHARGE_SLABS:
        if km >= low and km < high:
            surcharge = fee
            break

    return float(BASE_DELIVERY_FEE_INR + surcharge)

def _delivery_user_id(value):
    if value is None:
        return ""

    try:
        if isinstance(value, ObjectId):
            return str(value)
    except Exception:
        pass

    return str(value).strip()

def _delivery_actor_snapshot(actor=None):
    actor = actor or {}

    return {
        "actor_id": _delivery_user_id(actor.get("_id") or actor.get("id")),
        "actor_role": actor.get("role") or "",
        "actor_name": actor.get("name") or actor.get("full_name") or ""
    }

def add_order_event(order_id, status, note="", actor=None):
    """
    Consistent order timeline insert.
    Works for store, delivery boy, customer and admin events.
    """
    try:
        oid_obj = order_id if isinstance(order_id, ObjectId) else ObjectId(str(order_id))
    except Exception:
        oid_obj = order_id

    now = datetime.utcnow().isoformat()
    actor_data = _delivery_actor_snapshot(actor)

    doc = {
        "order_id": oid_obj,
        "status": (status or "").strip().upper(),
        "note": note or "",
        "created_at": now,
        "actor_id": actor_data.get("actor_id"),
        "actor_role": actor_data.get("actor_role"),
        "actor_name": actor_data.get("actor_name")
    }

    mongo.order_events.insert_one(doc)
    return doc

def get_delivery_partner_snapshot(delivery_user_id):
    """
    Returns safe delivery-boy details for saving inside orders.
    """
    uid = _delivery_user_id(delivery_user_id)

    if not uid:
        return None

    user = None

    try:
        user = mongo.users.find_one({"_id": ObjectId(uid)})
    except Exception:
        user = mongo.users.find_one({"_id": uid})

    if not user:
        return None

    if (user.get("role") or "").strip().lower() != "delivery":
        return None

    return {
        "id": str(user.get("_id")),
        "name": user.get("name") or user.get("full_name") or "Delivery Partner",
        "phone": user.get("phone") or "",
        "email": user.get("email") or "",
        "is_active": int(user.get("is_active", 1) or 0)
    }

def get_online_delivery_people_near_store(store, max_km=None):
    """
    Store-side helper.

    Returns active delivery boys with latest online GPS from delivery_availability.
    If store coordinates are present, distance from store is calculated.
    """
    store = store or {}

    store_lat = _delivery_float_or_none(store.get("latitude"))
    store_lng = _delivery_float_or_none(store.get("longitude"))

    max_km_value = None
    if max_km is not None:
        try:
            max_km_value = float(max_km)
        except Exception:
            max_km_value = None

    users = list(
        mongo.users.find({
            "role": "delivery",
            "$or": [
                {"is_active": 1},
                {"is_active": True},
                {"is_active": {"$exists": False}}
            ]
        }).sort("name", 1)
    )

    output = []

    for user in users:
        uid = str(user.get("_id"))

        availability = mongo.delivery_availability.find_one({
            "user_id": uid,
            "active": True
        })

        if not availability:
            continue

        rider_lat = _delivery_float_or_none(availability.get("latitude"))
        rider_lng = _delivery_float_or_none(availability.get("longitude"))

        distance_km = None

        if (
            store_lat is not None and
            store_lng is not None and
            rider_lat is not None and
            rider_lng is not None
        ):
            distance_km = haversine_km(store_lat, store_lng, rider_lat, rider_lng)

        if max_km_value is not None and distance_km is not None and distance_km > max_km_value:
            continue

        assigned_count = mongo.orders.count_documents({
            "delivery_partner_id": uid,
            "status": {
                "$in": [
                    "ASSIGNED_TO_DELIVERY",
                    "REACHED_STORE",
                    "PICKED_UP",
                    "OUT_FOR_DELIVERY"
                ]
            }
        })

        output.append({
            "id": uid,
            "name": user.get("name") or "Delivery Partner",
            "phone": user.get("phone") or "",
            "email": user.get("email") or "",
            "is_online": True,
            "latitude": rider_lat,
            "longitude": rider_lng,
            "distance_km": round(distance_km, 2) if distance_km is not None else None,
            "current_order_id": availability.get("current_order_id"),
            "currently_assigned_orders": assigned_count,
            "updated_at": availability.get("updated_at"),
            "active_since": availability.get("active_since")
        })

    output.sort(
        key=lambda row: (
            999999 if row.get("distance_km") is None else row.get("distance_km"),
            row.get("currently_assigned_orders", 0),
            row.get("name", "")
        )
    )

    return output

def assign_delivery_partner_to_order(order_id, delivery_user_id, actor=None, source="store_manual", allow_reassign=False):
    """
    Conflict-safe delivery assignment.

    Used by:
    - Store manual assignment
    - Store reassignment when allow_reassign=True
    - Delivery-boy self accept

    Handles:
    - Normal first assignment
    - Store reassignment before pickup
    - Reassignment after delivery boy cancelled delivery
    """

    try:
        oid_obj = order_id if isinstance(order_id, ObjectId) else ObjectId(str(order_id))
    except Exception:
        return {
            "ok": False,
            "error": "Invalid order id."
        }

    partner = get_delivery_partner_snapshot(delivery_user_id)

    if not partner:
        return {
            "ok": False,
            "error": "Delivery boy not found."
        }

    if int(partner.get("is_active") or 0) != 1:
        return {
            "ok": False,
            "error": "This delivery-boy account is disabled."
        }

    availability = mongo.delivery_availability.find_one({
        "user_id": partner["id"],
        "active": True
    })

    if not availability:
        return {
            "ok": False,
            "error": "This delivery boy is currently offline."
        }

    order = mongo.orders.find_one({"_id": oid_obj})

    if not order:
        return {
            "ok": False,
            "error": "Order not found."
        }

    status = (order.get("status") or "").strip().upper()

    if status in DELIVERY_REASSIGN_BLOCKED_STATUSES:
        return {
            "ok": False,
            "error": "Delivery assignment cannot be changed for this order status."
        }

    if status not in DELIVERY_STORE_ASSIGNABLE_STATUSES:
        return {
            "ok": False,
            "error": "Store must mark this order shipment ready before delivery assignment."
        }

    existing_partner = order.get("delivery_partner_id")
    existing_partner_id = _delivery_user_id(existing_partner)
    new_partner_id = _delivery_user_id(partner["id"])

    was_delivery_cancelled = bool(
        order.get("needs_reassignment")
        or order.get("delivery_cancelled_by_partner")
        or order.get("delivery_cancel_reason")
    )

    if existing_partner and not allow_reassign:
        if existing_partner_id == new_partner_id:
            return {
                "ok": True,
                "message": "This order is already assigned to this delivery boy.",
                "order_id": str(oid_obj),
                "delivery_partner": partner
            }

        return {
            "ok": False,
            "error": "This order already has an assigned delivery boy."
        }

    now = datetime.utcnow().isoformat()
    actor_data = _delivery_actor_snapshot(actor)

    old_partner_id = _delivery_user_id(
        order.get("delivery_partner_id")
        or order.get("previous_delivery_partner_id")
    )

    old_partner_name = (
        order.get("delivery_partner_name")
        or order.get("previous_delivery_partner_name")
        or ""
    )

    old_partner_phone = (
        order.get("delivery_partner_phone")
        or order.get("previous_delivery_partner_phone")
        or ""
    )

    previous_cancel_reason = (
        order.get("delivery_cancel_reason")
        or order.get("delivery_status_note")
        or ""
    )

    is_normal_reassign = bool(
        allow_reassign
        and existing_partner_id
        and existing_partner_id != new_partner_id
    )

    update_data = {
        "delivery_partner_id": partner["id"],
        "delivery_partner_name": partner["name"],
        "delivery_partner_phone": partner["phone"],

        "delivery_assigned_by": actor_data.get("actor_id"),
        "delivery_assigned_by_role": actor_data.get("actor_role"),
        "delivery_assigned_by_name": actor_data.get("actor_name"),
        "delivery_assignment_source": source,

        "assigned_at": now,
        "updated_at": now,
        "status": "ASSIGNED_TO_DELIVERY",

        # Clear rider-cancelled state after successful new assignment
        "needs_reassignment": False,
        "delivery_cancelled_by_partner": False,
        "delivery_cancel_reason": "",
        "delivery_cancelled_status_from": "",

        # Reassignment audit fields
        "delivery_reassigned_at": now if (was_delivery_cancelled or is_normal_reassign) else order.get("delivery_reassigned_at"),
        "delivery_reassigned_by": actor_data.get("actor_id") if (was_delivery_cancelled or is_normal_reassign) else order.get("delivery_reassigned_by"),
        "delivery_reassigned_by_name": actor_data.get("actor_name") if (was_delivery_cancelled or is_normal_reassign) else order.get("delivery_reassigned_by_name")
    }

    if old_partner_id and old_partner_id != new_partner_id:
        update_data["previous_delivery_partner_id"] = old_partner_id
        update_data["previous_delivery_partner_name"] = old_partner_name
        update_data["previous_delivery_partner_phone"] = old_partner_phone

    unassigned_filter = {
        "_id": oid_obj,
        "status": {
            "$in": [
                "SHIPMENT_READY",
                "READY_FOR_PICKUP"  # legacy support
            ]
        },
        "$or": [
            {"delivery_partner_id": {"$exists": False}},
            {"delivery_partner_id": None},
            {"delivery_partner_id": ""}
        ]
    }

    reassign_filter = {
        "_id": oid_obj,
        "status": {
            "$in": [
                "SHIPMENT_READY",
                "READY_FOR_PICKUP",  # legacy support
                "ASSIGNED_TO_DELIVERY",
                "REACHED_STORE"
            ]
        },
        "$or": [
            {
                "$and": [
                    {"delivery_partner_id": {"$exists": True}},
                    {"delivery_partner_id": {"$ne": None}},
                    {"delivery_partner_id": {"$ne": ""}}
                ]
            },
            {"needs_reassignment": True},
            {"delivery_cancelled_by_partner": True}
        ]
    }

    if allow_reassign:
        update_filter = reassign_filter
    else:
        update_filter = unassigned_filter

    update_payload = {
        "$set": update_data
    }

    if was_delivery_cancelled or is_normal_reassign:
        history_entry = {
            "action": "reassigned_after_delivery_cancel" if was_delivery_cancelled else "reassigned_by_store",
            "previous_delivery_partner_id": old_partner_id,
            "previous_delivery_partner_name": old_partner_name,
            "previous_delivery_partner_phone": old_partner_phone,
            "previous_cancel_reason": previous_cancel_reason,
            "new_delivery_partner_id": partner["id"],
            "new_delivery_partner_name": partner["name"],
            "new_delivery_partner_phone": partner["phone"],
            "at": now,
            "by": actor_data.get("actor_role") or "store",
            "actor_id": actor_data.get("actor_id"),
            "actor_name": actor_data.get("actor_name")
        }

        update_payload["$push"] = {
            "delivery_history": history_entry
        }

    result = mongo.orders.update_one(
        update_filter,
        update_payload
    )

    if result.modified_count < 1:
        latest = mongo.orders.find_one({"_id": oid_obj}) or {}
        latest_partner = latest.get("delivery_partner_id")
        latest_status = (latest.get("status") or "").strip().upper()

        if latest_partner and not allow_reassign:
            return {
                "ok": False,
                "error": "This order has just been assigned to another delivery boy."
            }

        if latest_status in DELIVERY_REASSIGN_BLOCKED_STATUSES:
            return {
                "ok": False,
                "error": "This order has moved forward and delivery partner cannot be changed now."
            }

        if latest_status not in ["SHIPMENT_READY", "READY_FOR_PICKUP"] and not allow_reassign:
            return {
                "ok": False,
                "error": "This order is no longer available for delivery assignment."
        }

        return {
            "ok": False,
            "error": "Delivery assignment could not be updated. Please refresh and try again."
        }

    # Set new delivery boy current order
    mongo.delivery_availability.update_one(
        {"user_id": partner["id"]},
        {
            "$set": {
                "current_order_id": str(oid_obj),
                "updated_at": now
            }
        },
        upsert=True
    )

    # Clear old delivery boy current order during reassignment
    if old_partner_id and old_partner_id != new_partner_id:
        mongo.delivery_availability.update_one(
            {
                "user_id": old_partner_id,
                "current_order_id": str(oid_obj)
            },
            {
                "$set": {
                    "current_order_id": None,
                    "updated_at": now
                }
            }
        )

    if was_delivery_cancelled:
        add_order_event(
            oid_obj,
            "DELIVERY_REASSIGNED",
            f"Delivery reassigned to {partner['name']} after previous rider cancellation.",
            actor
        )
    elif is_normal_reassign:
        add_order_event(
            oid_obj,
            "DELIVERY_REASSIGNED",
            f"Delivery reassigned to {partner['name']}.",
            actor
        )
    else:
        add_order_event(
            oid_obj,
            "ASSIGNED_TO_DELIVERY",
            f"Assigned to {partner['name']}",
            actor
        )

    return {
        "ok": True,
        "message": "Delivery boy reassigned successfully." if (was_delivery_cancelled or is_normal_reassign) else "Delivery boy assigned successfully.",
        "order_id": str(oid_obj),
        "delivery_partner": partner,
        "was_reassignment": bool(was_delivery_cancelled or is_normal_reassign)
    }

def clear_delivery_assignment(order_id, actor=None, reason="Delivery assignment cleared."):
    """
    Clears delivery assignment before pickup/out-for-delivery.
    Store can use this for correction/reassignment.
    """
    try:
        oid_obj = order_id if isinstance(order_id, ObjectId) else ObjectId(str(order_id))
    except Exception:
        return {
            "ok": False,
            "error": "Invalid order id."
        }

    order = mongo.orders.find_one({"_id": oid_obj})

    if not order:
        return {
            "ok": False,
            "error": "Order not found."
        }

    status = (order.get("status") or "").strip().upper()

    if status in DELIVERY_REASSIGN_BLOCKED_STATUSES:
        return {
            "ok": False,
            "error": "Delivery assignment cannot be cleared after pickup/out-for-delivery/delivery."
        }

    old_partner_id = order.get("delivery_partner_id")
    now = datetime.utcnow().isoformat()

    mongo.orders.update_one(
        {"_id": oid_obj},
        {
            "$set": {
                "status": "SHIPMENT_READY",
                "delivery_partner_id": None,
                "delivery_partner_name": "",
                "delivery_partner_phone": "",
                "delivery_assignment_source": "",
                "delivery_status_note": reason,
                "updated_at": now
            }
        }
    )

    if old_partner_id:
        mongo.delivery_availability.update_one(
            {
                "user_id": str(old_partner_id),
                "current_order_id": str(oid_obj)
            },
            {
                "$set": {
                    "current_order_id": None,
                    "updated_at": now
                }
            }
        )

    add_order_event(
        oid_obj,
        "SHIPMENT_READY",
        reason,
        actor
    )

    return {
        "ok": True,
        "message": "Delivery assignment cleared."
    }
