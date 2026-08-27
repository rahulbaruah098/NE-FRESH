"""Delivery tracking route section.

Route decorators, endpoint names, URL paths, HTTP methods and function bodies are
preserved from the Step 6 source. Shared legacy helpers are imported from
``routes.delivery.shared`` during this transitional decomposition.
"""

from routes.delivery.shared import *

@app.route('/delivery/api/location', methods=['POST'])
@login_required(role='delivery')
def delivery_update_location():
    u = current_user()
    data = request.get_json(silent=True) or {}

    lat_raw = data.get("latitude")
    lng_raw = data.get("longitude")

    # Accept frontend aliases also
    if lat_raw is None:
        lat_raw = data.get("lat")

    if lng_raw is None:
        lng_raw = data.get("lng")

    try:
        lat = float(lat_raw)
        lng = float(lng_raw)
    except (TypeError, ValueError):
        return jsonify({
            "ok": False,
            "error": "latitude/longitude required",
            "received": data
        }), 400

    now = datetime.utcnow().isoformat()

    oid = data.get("order_id")
    heading = data.get("heading")
    speed = data.get("speed")
    accuracy = data.get("accuracy")

    try:
        accuracy = float(accuracy) if accuracy is not None and str(accuracy).strip() != "" else None
    except Exception:
        accuracy = None

    oid_obj = None

    if oid:
        try:
            oid_obj = ObjectId(str(oid))
        except Exception:
            # Do not fail live sharing only because frontend sent old/integer order id.
            # Save general delivery location without order_id.
            oid_obj = None

        if oid_obj:
            order = mongo.orders.find_one({
                "_id": oid_obj,
                "$or": [
                    {"delivery_partner_id": u["id"]},
                    {"delivery_partner_id": str(u["id"])}
                ]
            })

            if not order:
                return jsonify({
                    "ok": False,
                    "error": "order not found or not assigned to you"
                }), 404

       

    mongo.delivery_locations.insert_one({
        "delivery_partner_id": u["id"],
        "order_id": oid_obj,
        "latitude": lat,
        "longitude": lng,
        "accuracy": accuracy,
        "heading": heading,
        "speed": speed,
        "recorded_at": now
    })

    mongo.delivery_availability.update_one(
        {"user_id": u["id"]},
        {
            "$set": {
                "user_id": u["id"],
                "active": True,
                "latitude": lat,
                "longitude": lng,
                "accuracy": accuracy,
                "current_order_id": str(oid_obj) if oid_obj else None,
                "updated_at": now
            },
            "$setOnInsert": {
                "active_since": now
            }
        },
        upsert=True
    )

    return jsonify({
        "ok": True,
        "latitude": lat,
        "longitude": lng,
        "accuracy": accuracy,
        "order_id": str(oid_obj) if oid_obj else None
    })


@app.route('/api/delivery/orders/<oid>/location', methods=['GET'])
@login_required()
def delivery_api_get_latest(oid):
    u = current_user()

    try:
        oid_obj = ObjectId(oid)
    except Exception:
        return jsonify({"ok": False, "error": "invalid order id"}), 400

    order = mongo.orders.find_one({"_id": oid_obj})

    if not order:
        return jsonify({
            "ok": False,
            "error": "order not found"
        }), 404

    role = (u.get("role") or "").strip().lower()
    user_id = str(u.get("id") or u.get("_id") or "")

    if role == "customer" and str(order.get("user_id")) != user_id:
        return jsonify({
            "ok": False,
            "error": "not allowed"
        }), 403

    if role == "delivery":
        assigned_delivery_id = str(order.get("delivery_partner_id") or "")

        if assigned_delivery_id != user_id:
            return jsonify({
                "ok": False,
                "error": "not assigned to this delivery user"
            }), 403

    if role == "store":
        store = mongo.stores.find_one({"user_id": u["id"]})

        if not store or str(order.get("store_id")) not in [str(store.get("_id")), store.get("_id")]:
            return jsonify({
                "ok": False,
                "error": "not allowed"
            }), 403

    assigned_delivery_id = str(order.get("delivery_partner_id") or "").strip()
    order_status = (order.get("status") or "").strip().upper()

    active_delivery_statuses = [
        "ASSIGNED_TO_DELIVERY",
        "ACCEPTED_BY_DELIVERY_MAN",
        "REACHED_STORE",
        "PICKED_UP",
        "OUT_FOR_DELIVERY"
    ]

    if not assigned_delivery_id or order_status not in active_delivery_statuses:
        return jsonify({
            "ok": True,
            "has_location": False,
            "delivery_assigned": False,
            "message": "Delivery partner is not assigned or live tracking is not active for this order."
        })

    row = mongo.delivery_locations.find_one(
        {
            "order_id": oid_obj,
            "delivery_partner_id": {
                "$in": [
                    assigned_delivery_id,
                    str(assigned_delivery_id)
                ]
            }
        },
        sort=[("recorded_at", -1)]
    )

    if not row:
        return jsonify({
            "ok": True,
            "has_location": False,
            "delivery_assigned": True,
            "message": "Delivery partner assigned, but live location is not available yet."
        })

    return jsonify({
        "ok": True,
        "has_location": True,
        "delivery_assigned": True,
        "data": {
            "latitude": row.get("latitude"),
            "longitude": row.get("longitude"),
            "accuracy": row.get("accuracy"),
            "updated_at": row.get("recorded_at")
        }
    })
