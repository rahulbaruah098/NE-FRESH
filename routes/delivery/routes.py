"""Delivery routes extracted from the updated app.py.

Logic, decorators, endpoint names and route paths are intentionally preserved.
Only the file location changed.
"""

from app_core import *


@app.route('/delivery')
@login_required(role='delivery')
def delivery_dashboard():
    u = current_user()

    availability = _get_delivery_availability(u["id"])
    delivery_active = bool(availability.get("active"))
    active_since = availability.get("active_since")

    orders = []

    # Driver OFF = show no order data.
    if delivery_active and active_since:
        raw_orders = list(
            mongo.orders.find({
                "$or": [
                    {
                        "delivery_partner_id": u["id"],
                        "status": {"$in": DELIVERY_ASSIGNED_ACTIVE_STATUSES}
                    },
                    {
                        "$and": [
                            {
                                "$or": [
                                    {"delivery_partner_id": None},
                                    {"delivery_partner_id": {"$exists": False}}
                                ]
                            },
                            {"created_at": {"$gte": active_since}},
                            {"status": {"$in": DELIVERY_ACTIONABLE_STATUSES}}
                        ]
                    }
                ]
            }).sort("created_at", -1)
        )

        for o in raw_orders:
            o = _hydrate_delivery_order(o)
            distance_km = _driver_distance_to_store_km(o, availability)
            o["driver_store_distance_km"] = distance_km
            orders.append(o)

        # Nearby first, unknown distance last.
        orders.sort(
            key=lambda x: (
                0 if x.get("delivery_partner_id") == u["id"] else 1,
                999999 if x.get("driver_store_distance_km") is None else x.get("driver_store_distance_km")
            )
        )

    return render_template(
        'delivery_dashboard.html',
        user=u,
        orders=orders,
        delivery_active=delivery_active,
        delivery_availability=availability,
        delivery_accept_radius_km=DELIVERY_ACCEPT_RADIUS_KM
    )

@app.route('/api/delivery/availability', methods=['POST'])
@login_required(role='delivery')
def api_delivery_availability():
    u = current_user()
    data = request.get_json(silent=True) or {}

    active = bool(data.get("active"))
    now = _delivery_now()

    if active:
        lat = _get_float_or_none(data.get("latitude"))
        lng = _get_float_or_none(data.get("longitude"))

        if lat is None or lng is None:
            return jsonify({
                "ok": False,
                "error": "GPS location is required to go active."
            }), 400

        mongo.delivery_availability.update_one(
            {"user_id": u["id"]},
            {
                "$set": {
                    "user_id": u["id"],
                    "active": True,
                    "active_since": now,
                    "latitude": lat,
                    "longitude": lng,
                    "updated_at": now
                }
            },
            upsert=True
        )

        return jsonify({
            "ok": True,
            "active": True,
            "active_since": now
        })

    mongo.delivery_availability.update_one(
        {"user_id": u["id"]},
        {
            "$set": {
                "user_id": u["id"],
                "active": False,
                "offline_at": now,
                "updated_at": now
            }
        },
        upsert=True
    )

    return jsonify({
        "ok": True,
        "active": False
    })

@app.route('/delivery/order/<oid>/assign', methods=['POST'])
@login_required(role='delivery')
def delivery_assign(oid):
    u = current_user()

    availability = _get_delivery_availability(u["id"])

    if not availability.get("active"):
        flash("Please go active before accepting delivery orders.", "warning")
        return redirect(url_for("delivery_dashboard"))

    try:
        oid_obj = ObjectId(oid)
    except Exception:
        flash("Invalid order.", "danger")
        return redirect(url_for("delivery_dashboard"))

    order = mongo.orders.find_one({"_id": oid_obj})

    if not order:
        flash("Order not found.", "danger")
        return redirect(url_for("delivery_dashboard"))

    if order.get("status") not in DELIVERY_ACTIONABLE_STATUSES:
        flash("This order is no longer available for delivery.", "warning")
        return redirect(url_for("delivery_dashboard"))

    existing_partner = order.get("delivery_partner_id")

    if existing_partner:
        if str(existing_partner) == str(u["id"]):
            flash("This order is already assigned to you.", "info")
        else:
            flash("This order is already assigned to another delivery partner.", "warning")
        return redirect(url_for("delivery_dashboard"))

    distance_km = _driver_distance_to_store_km(order, availability)

    if distance_km is not None and distance_km > DELIVERY_ACCEPT_RADIUS_KM:
        flash(
            f"This order is too far from your current location ({distance_km:.1f} km).",
            "warning"
        )
        return redirect(url_for("delivery_dashboard"))

    result = assign_delivery_partner_to_order(
        order_id=oid_obj,
        delivery_user_id=u["id"],
        actor=u,
        source="rider_self",
        allow_reassign=False
    )

    if not result.get("ok"):
        flash(result.get("error") or "Could not accept this order.", "warning")
        return redirect(url_for("delivery_dashboard"))

    now = _delivery_now()

    mongo.orders.update_one(
        {"_id": oid_obj},
        {
            "$set": {
                "assignment_distance_km": distance_km,
                "updated_at": now
            }
        }
    )

    flash("Order assigned to you.", "success")
    return redirect(url_for("delivery_dashboard"))

@app.route('/delivery/order/<oid>/status', methods=['POST'])
@login_required(role='delivery')
def delivery_status(oid):
    u = current_user()

    try:
        oid_obj = ObjectId(oid)
    except Exception:
        flash("Invalid order.", "danger")
        return redirect(url_for("delivery_dashboard"))

    order = mongo.orders.find_one({
        "_id": oid_obj,
        "delivery_partner_id": u["id"]
    })

    if not order:
        flash("Order not found or not assigned to you.", "danger")
        return redirect(url_for("delivery_dashboard"))

    new_status = (request.form.get('status') or '').strip().upper()
    now = datetime.utcnow().isoformat()

    allowed_statuses = {
        "REACHED_STORE",
        "PICKED_UP",
        "OUT_FOR_DELIVERY",
        "DELIVERED"
    }

    if new_status not in allowed_statuses:
        flash("Invalid delivery status selected.", "warning")
        return redirect(url_for("delivery_dashboard"))

    current_status = (order.get("status") or "").strip().upper()

    allowed_transitions = {
        "ASSIGNED_TO_DELIVERY": {"REACHED_STORE", "PICKED_UP", "OUT_FOR_DELIVERY"},
        "REACHED_STORE": {"PICKED_UP", "OUT_FOR_DELIVERY"},
        "PICKED_UP": {"OUT_FOR_DELIVERY"},
        "OUT_FOR_DELIVERY": {"DELIVERED"},
    }

    if current_status == "DELIVERED":
        flash("This order is already delivered.", "info")
        return redirect(url_for("delivery_dashboard"))

    if new_status not in allowed_transitions.get(current_status, allowed_statuses):
        flash(f"Cannot change order from {current_status} to {new_status}.", "warning")
        return redirect(url_for("delivery_dashboard"))

    update_data = {
        "status": new_status,
        "updated_at": now
    }

    event_note = "Updated by delivery boy"

    if new_status == "REACHED_STORE":
        update_data["reached_store_at"] = now
        event_note = "Delivery boy reached store."

    elif new_status == "PICKED_UP":
        update_data["picked_up_at"] = now
        event_note = "Order picked up from store."

    elif new_status == "OUT_FOR_DELIVERY":
        update_data["out_for_delivery_at"] = now
        event_note = "Order is out for delivery."

    elif new_status == "DELIVERED":
        cod_received = request.form.get('cod_received')

        if cod_received != '1':
            flash('Please confirm that payment (COD) has been received before marking Delivered.', 'warning')
            return redirect(url_for('delivery_dashboard'))

        update_data["payment_status"] = "PAID"
        update_data["delivered_at"] = now
        event_note = "COD received. Order delivered."

    mongo.orders.update_one(
        {"_id": oid_obj},
        {
            "$set": update_data
        }
    )

    if new_status == "DELIVERED":
        payable_amount = (
            float(order.get("total_amount") or 0)
            + float(order.get("delivery_fee") or 0)
            + float(order.get("tip_amount") or 0)
        )

        existing_txn = mongo.transactions.find_one({
            "order_id": oid_obj
        })

        if existing_txn:
            mongo.transactions.update_many(
                {"order_id": oid_obj},
                {
                    "$set": {
                        "status": "PAID",
                        "amount": payable_amount,
                        "updated_at": now
                    }
                }
            )
        else:
            mongo.transactions.insert_one({
                "order_id": oid_obj,
                "store_id": order.get("store_id"),
                "user_id": order.get("user_id"),
                "amount": payable_amount,
                "status": "PAID",
                "method": order.get("payment_method") or "COD",
                "created_at": now,
                "updated_at": now
            })

        mongo.delivery_availability.update_one(
            {
                "user_id": u["id"],
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
        new_status,
        event_note,
        u
    )

    flash('Delivery status updated.', 'success')
    return redirect(url_for('delivery_dashboard'))

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

    oid = data.get("order_id")
    heading = data.get("heading")
    speed = data.get("speed")

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
                "delivery_partner_id": u["id"]
            })

            if not order:
                return jsonify({
                    "ok": False,
                    "error": "order not found or not assigned to you"
                }), 404

        now = datetime.utcnow().isoformat()

    mongo.delivery_locations.insert_one({
        "delivery_partner_id": u["id"],
        "order_id": oid_obj,
        "latitude": lat,
        "longitude": lng,
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
        "order_id": str(oid_obj) if oid_obj else None
    })

@app.route('/api/delivery/orders/<oid>/location', methods=['GET'])
@login_required()
def delivery_api_get_latest(oid):
    try:
        oid_obj = ObjectId(oid)
    except Exception:
        return jsonify({"ok": False, "error": "invalid order id"}), 400

    row = mongo.delivery_locations.find_one(
        {"order_id": oid_obj},
        sort=[("recorded_at", -1)]
    )

    if not row:
        return jsonify({
            "ok": True,
            "has_location": False
        })

    return jsonify({
        "ok": True,
        "has_location": True,
        "data": {
            "latitude": row.get("latitude"),
            "longitude": row.get("longitude"),
            "updated_at": row.get("recorded_at")
        }
    })
