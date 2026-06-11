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

    orders = []

    # Driver OFF = show no order data.
    # Driver ON = always show all existing READY_FOR_PICKUP + unassigned orders,
    # even if they were marked ready while the delivery boy was offline.
    if delivery_active:
        raw_orders = list(
            mongo.orders.find({
                "$or": [
                    {
                        "$and": [
                            {
                                "$or": [
                                    {"delivery_partner_id": u["id"]},
                                    {"delivery_partner_id": str(u["id"])}
                                ]
                            },
                            {
                                "status": {"$in": DELIVERY_ASSIGNED_ACTIVE_STATUSES}
                            }
                        ]
                    },
                    {
                        "$and": [
                            {
                                "$or": [
                                    {"delivery_partner_id": None},
                                    {"delivery_partner_id": ""},
                                    {"delivery_partner_id": {"$exists": False}}
                                ]
                            },
                            {
                                "status": {"$in": DELIVERY_ACTIONABLE_STATUSES}
                            }
                        ]
                    }
                ]
            }).sort("updated_at", -1)
        )

        for o in raw_orders:
            o = _hydrate_delivery_order(o)
            distance_km = _driver_distance_to_store_km(o, availability)
            o["driver_store_distance_km"] = distance_km

            # Keep available ready orders visible even if distance cannot be calculated.
            # If distance exists and is outside radius, hide only unassigned available orders.
            if not o.get("delivery_partner_id"):
                if distance_km is not None and distance_km > DELIVERY_ACCEPT_RADIUS_KM:
                    continue

            orders.append(o)

        # Own active orders first, then nearest available ready orders.
        orders.sort(
            key=lambda x: (
                0 if str(x.get("delivery_partner_id") or "") == str(u["id"]) else 1,
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

@app.route('/delivery/available-orders')
@login_required(role='delivery')
def delivery_available_orders():
    u = current_user()

    availability = _get_delivery_availability(u["id"])
    delivery_active = bool(availability.get("active"))

    orders = []

    if delivery_active:
        raw_orders = list(
            mongo.orders.find({
                "$and": [
                    {
                        "$or": [
                            {"delivery_partner_id": None},
                            {"delivery_partner_id": ""},
                            {"delivery_partner_id": {"$exists": False}}
                        ]
                    },
                    {
                        "status": {"$in": DELIVERY_ACTIONABLE_STATUSES}
                    }
                ]
            }).sort("updated_at", -1)
        )

        for o in raw_orders:
            o = _hydrate_delivery_order(o)
            distance_km = _driver_distance_to_store_km(o, availability)
            o["driver_store_distance_km"] = distance_km

            if distance_km is not None and distance_km > DELIVERY_ACCEPT_RADIUS_KM:
                continue

            orders.append(o)

        orders.sort(
            key=lambda x: (
                999999 if x.get("driver_store_distance_km") is None else x.get("driver_store_distance_km")
            )
        )

    return render_template(
        "delivery_available_orders.html",
        user=u,
        orders=orders,
        delivery_active=delivery_active,
        delivery_availability=availability,
        delivery_accept_radius_km=DELIVERY_ACCEPT_RADIUS_KM
    )

@app.route('/delivery/active-orders')
@login_required(role='delivery')
def delivery_active_orders():
    u = current_user()

    availability = _get_delivery_availability(u["id"])
    delivery_active = bool(availability.get("active"))

    raw_orders = list(
        mongo.orders.find({
            "$and": [
                {
                    "$or": [
                        {"delivery_partner_id": u["id"]},
                        {"delivery_partner_id": str(u["id"])}
                    ]
                },
                {
                    "status": {"$in": DELIVERY_ASSIGNED_ACTIVE_STATUSES}
                }
            ]
        }).sort("updated_at", -1)
    )

    orders = []

    for o in raw_orders:
        o = _hydrate_delivery_order(o)
        distance_km = _driver_distance_to_store_km(o, availability)
        o["driver_store_distance_km"] = distance_km
        orders.append(o)

    return render_template(
        "delivery_active_orders.html",
        user=u,
        orders=orders,
        delivery_active=delivery_active,
        delivery_availability=availability,
        delivery_accept_radius_km=DELIVERY_ACCEPT_RADIUS_KM
    )

@app.route('/delivery/history')
@login_required(role='delivery')
def delivery_history():
    u = current_user()

    availability = _get_delivery_availability(u["id"])
    delivery_active = bool(availability.get("active"))

    q = (request.args.get("q") or "").strip()
    status_filter = (request.args.get("status") or "").strip().upper()

    history_statuses = ["DELIVERED", "CANCELLED"]

    query_filter = {
        "$and": [
            {
                "$or": [
                    {"delivery_partner_id": u["id"]},
                    {"delivery_partner_id": str(u["id"])}
                ]
            },
            {
                "status": {"$in": history_statuses}
            }
        ]
    }

    if status_filter in history_statuses:
        query_filter["$and"].append({
            "status": status_filter
        })

    raw_orders = list(
        mongo.orders.find(query_filter).sort("updated_at", -1)
    )

    orders = []

    for o in raw_orders:
        o = _hydrate_delivery_order(o)

        if q:
            haystack = " ".join([
                str(o.get("id") or ""),
                str(o.get("store_name") or ""),
                str(o.get("customer_name") or ""),
                str(o.get("customer_phone") or ""),
                str(o.get("status") or "")
            ]).lower()

            if q.lower() not in haystack:
                continue

        orders.append(o)

    total_cod_collected = 0
    total_delivery_fee = 0
    total_tip = 0

    for o in orders:
        if (o.get("payment_method") or "COD").upper() == "COD":
            total_cod_collected += float(o.get("total_payable") or 0)

        total_delivery_fee += float(o.get("delivery_fee") or 0)
        total_tip += float(o.get("tip_amount") or 0)

    return render_template(
        "delivery_history.html",
        user=u,
        orders=orders,
        delivery_active=delivery_active,
        delivery_availability=availability,
        q=q,
        status_filter=status_filter,
        total_cod_collected=total_cod_collected,
        total_delivery_fee=total_delivery_fee,
        total_tip=total_tip
    )

@app.route('/delivery/current')
@login_required(role='delivery')
def delivery_current():
    u = current_user()

    active_statuses = DELIVERY_ASSIGNED_ACTIVE_STATUSES

    raw_orders = list(
        mongo.orders.find({
            "$and": [
                {
                    "$or": [
                        {"delivery_partner_id": u["id"]},
                        {"delivery_partner_id": str(u["id"])}
                    ]
                },
                {
                    "status": {"$in": active_statuses}
                }
            ]
        }).sort("updated_at", -1)
    )

    if not raw_orders:
        flash("No current active delivery found. Accept a ready order first.", "warning")
        return redirect(url_for("delivery_active_orders"))

    status_priority = {
        "OUT_FOR_DELIVERY": 1,
        "PICKED_UP": 2,
        "REACHED_STORE": 3,
        "ASSIGNED_TO_DELIVERY": 4
    }

    raw_orders.sort(
        key=lambda o: (
            status_priority.get((o.get("status") or "").upper(), 99),
            str(o.get("updated_at") or "")
        )
    )

    current_order = raw_orders[0]

    return redirect(url_for("delivery_order_detail", oid=str(current_order["_id"])))

@app.route('/delivery/order/<oid>')
@login_required(role='delivery')
def delivery_order_detail(oid):
    u = current_user()

    availability = _get_delivery_availability(u["id"])
    delivery_active = bool(availability.get("active"))

    try:
        oid_obj = ObjectId(oid)
    except Exception:
        flash("Invalid order.", "danger")
        return redirect(url_for("delivery_active_orders"))

    order = mongo.orders.find_one({
        "_id": oid_obj,
        "$or": [
            {"delivery_partner_id": u["id"]},
            {"delivery_partner_id": str(u["id"])}
        ]
    })

    if not order:
        flash("Order not found or not assigned to you.", "danger")
        return redirect(url_for("delivery_active_orders"))

    order = _hydrate_delivery_order(order)

    store = None
    if order.get("store_id"):
        store = mongo.stores.find_one({"_id": order.get("store_id")})

    if store:
        store["id"] = str(store["_id"])

    order_items = list(
        mongo.order_items.find({"order_id": oid_obj})
    )

    for item in order_items:
        item["id"] = str(item.get("_id"))
        try:
            item["quantity"] = float(item.get("quantity") or item.get("cart_quantity") or 0)
        except Exception:
            item["quantity"] = 0

        try:
            item["line_total"] = float(item.get("line_total") or 0)
        except Exception:
            item["line_total"] = 0

    events = list(
        mongo.order_events.find({"order_id": oid_obj}).sort("created_at", 1)
    )

    for e in events:
        e["id"] = str(e.get("_id"))

    active_order_rows = list(
        mongo.orders.find({
            "$and": [
                {
                    "$or": [
                        {"delivery_partner_id": u["id"]},
                        {"delivery_partner_id": str(u["id"])}
                    ]
                },
                {
                    "status": {"$in": DELIVERY_ASSIGNED_ACTIVE_STATUSES}
                }
            ]
        }).sort("updated_at", -1)
    )

    active_orders = []

    status_priority = {
        "OUT_FOR_DELIVERY": 1,
        "PICKED_UP": 2,
        "REACHED_STORE": 3,
        "ASSIGNED_TO_DELIVERY": 4
    }

    for ao in active_order_rows:
        ao = _hydrate_delivery_order(ao)
        ao["status_priority"] = status_priority.get((ao.get("status") or "").upper(), 99)
        active_orders.append(ao)

    active_orders.sort(
        key=lambda x: (
            x.get("status_priority", 99),
            str(x.get("updated_at") or "")
        )
    )

    return render_template(
        "delivery_order_detail.html",
        user=u,
        order=order,
        store=store,
        order_items=order_items,
        events=events,
        active_orders=active_orders,
        current_order_id=str(oid_obj),
        delivery_active=delivery_active,
        delivery_availability=availability
    )

@app.route('/delivery/earnings')
@login_required(role='delivery')
def delivery_earnings():
    u = current_user()

    availability = _get_delivery_availability(u["id"])
    delivery_active = bool(availability.get("active"))

    q = (request.args.get("q") or "").strip()
    date_from = (request.args.get("from") or "").strip()
    date_to = (request.args.get("to") or "").strip()

    query_filter = {
        "$and": [
            {
                "$or": [
                    {"delivery_partner_id": u["id"]},
                    {"delivery_partner_id": str(u["id"])}
                ]
            },
            {
                "status": "DELIVERED"
            }
        ]
    }

    raw_orders = list(
        mongo.orders.find(query_filter).sort("delivered_at", -1)
    )

    orders = []

    for o in raw_orders:
        o = _hydrate_delivery_order(o)

        delivered_at = str(o.get("delivered_at") or o.get("updated_at") or "")

        if date_from and delivered_at and delivered_at[:10] < date_from:
            continue

        if date_to and delivered_at and delivered_at[:10] > date_to:
            continue

        if q:
            haystack = " ".join([
                str(o.get("id") or ""),
                str(o.get("store_name") or ""),
                str(o.get("customer_name") or ""),
                str(o.get("customer_phone") or ""),
                str(o.get("payment_method") or ""),
                str(o.get("payment_status") or "")
            ]).lower()

            if q.lower() not in haystack:
                continue

        orders.append(o)

    total_cod_collected = 0
    total_delivery_fee = 0
    total_tip = 0
    total_payable = 0

    for o in orders:
        payable = float(
            o.get("total_payable")
            or (
                float(o.get("total_amount") or 0)
                + float(o.get("delivery_fee") or 0)
                + float(o.get("tip_amount") or 0)
            )
        )

        total_payable += payable
        total_delivery_fee += float(o.get("delivery_fee") or 0)
        total_tip += float(o.get("tip_amount") or 0)

        if (o.get("payment_method") or "COD").upper() == "COD":
            total_cod_collected += payable

    return render_template(
        "delivery_earnings.html",
        user=u,
        orders=orders,
        delivery_active=delivery_active,
        delivery_availability=availability,
        q=q,
        date_from=date_from,
        date_to=date_to,
        total_cod_collected=total_cod_collected,
        total_delivery_fee=total_delivery_fee,
        total_tip=total_tip,
        total_payable=total_payable
    )


def _delivery_active_orders_for_offline_check(delivery_user_id):
    """
    Returns active orders currently assigned to this delivery boy.
    Used to block going offline while delivery work is still active.
    """

    active_statuses = set()

    try:
        active_statuses.update(DELIVERY_ASSIGNED_ACTIVE_STATUSES)
    except Exception:
        pass

    active_statuses.update([
        "ASSIGNED_TO_DELIVERY",
        "REACHED_STORE",
        "PICKED_UP",
        "OUT_FOR_DELIVERY"
    ])

    return list(
        mongo.orders.find({
            "$and": [
                {
                    "$or": [
                        {"delivery_partner_id": delivery_user_id},
                        {"delivery_partner_id": str(delivery_user_id)}
                    ]
                },
                {
                    "status": {"$in": list(active_statuses)}
                }
            ]
        }).sort("updated_at", -1)
    )

@app.route('/api/delivery/availability', methods=['POST'])
@login_required(role='delivery')
def api_delivery_availability():
    u = current_user()
    data = request.get_json(silent=True) or {}

    active = bool(data.get("active"))
    now = _delivery_now()

    # ==============================
    # Going ONLINE
    # ==============================
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
            "active_since": now,
            "message": "You are now online."
        })

    # ==============================
    # Going OFFLINE
    # Block if active assigned orders exist
    # ==============================
    active_orders = _delivery_active_orders_for_offline_check(u["id"])

    if active_orders:
        order_refs = []

        for order in active_orders[:3]:
            order_refs.append("#" + str(order.get("_id"))[-6:])

        return jsonify({
            "ok": False,
            "active": True,
            "blocked": True,
            "active_orders_count": len(active_orders),
            "active_orders": order_refs,
            "error": (
                "You cannot go offline while you have active delivery orders. "
                "Please deliver the order or cancel the delivery first."
            )
        }), 409

    mongo.delivery_availability.update_one(
        {"user_id": u["id"]},
        {
            "$set": {
                "user_id": u["id"],
                "active": False,
                "offline_at": now,
                "current_order_id": None,
                "updated_at": now
            }
        },
        upsert=True
    )

    return jsonify({
        "ok": True,
        "active": False,
        "message": "You are now offline."
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
        {
            "_id": oid_obj,
            "delivery_partner_id": u["id"]
        },
        {
            "$set": {
                "assignment_distance_km": distance_km,
                "updated_at": now
            }
        }
    )

    flash("Order assigned to you.", "success")
    return redirect(url_for("delivery_active_orders"))

@app.route('/delivery/order/<oid>/status', methods=['POST'])
@login_required(role='delivery')
def delivery_status(oid):
    u = current_user()

    try:
        oid_obj = ObjectId(oid)
    except Exception:
        flash("Invalid order.", "danger")
        return redirect(request.referrer or url_for("delivery_active_orders"))

    order = mongo.orders.find_one({
        "_id": oid_obj,
        "$or": [
            {"delivery_partner_id": u["id"]},
            {"delivery_partner_id": str(u["id"])}
        ]
    })

    if not order:
        flash("Order not found or not assigned to you.", "danger")
        return redirect(request.referrer or url_for("delivery_active_orders"))

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
        return redirect(request.referrer or url_for("delivery_active_orders"))

    current_status = (order.get("status") or "").strip().upper()

    allowed_transitions = {
        "ASSIGNED_TO_DELIVERY": {"REACHED_STORE", "PICKED_UP", "OUT_FOR_DELIVERY"},
        "REACHED_STORE": {"PICKED_UP", "OUT_FOR_DELIVERY"},
        "PICKED_UP": {"OUT_FOR_DELIVERY"},
        "OUT_FOR_DELIVERY": {"DELIVERED"},
    }

    if current_status == "DELIVERED":
        flash("This order is already delivered.", "info")
        return redirect(request.referrer or url_for("delivery_active_orders"))

    if new_status not in allowed_transitions.get(current_status, allowed_statuses):
        flash(f"Cannot change order from {current_status} to {new_status}.", "warning")
        return redirect(request.referrer or url_for("delivery_active_orders"))

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
            return redirect(request.referrer or url_for('delivery_active_orders'))

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
    return redirect(request.referrer or url_for('delivery_active_orders'))


@app.route('/delivery/order/<oid>/cancel-delivery', methods=['POST'])
@login_required(role='delivery')
def delivery_cancel_assignment(oid):
    """
    Delivery boy cancels only the delivery assignment.
    The customer order is NOT cancelled.

    Flow:
    - Remove current delivery boy from order
    - Mark order back as READY_FOR_PICKUP
    - Set needs_reassignment = True
    - Add timeline/history event
    - Clear rider current_order_id
    """

    u = current_user()

    try:
        oid_obj = ObjectId(oid)
    except Exception:
        flash("Invalid order.", "danger")
        return redirect(request.referrer or url_for("delivery_active_orders"))

    order = mongo.orders.find_one({
        "_id": oid_obj,
        "$or": [
            {"delivery_partner_id": u["id"]},
            {"delivery_partner_id": str(u["id"])}
        ]
    })

    if not order:
        flash("Order not found or not assigned to you.", "danger")
        return redirect(request.referrer or url_for("delivery_active_orders"))

    current_status = (order.get("status") or "").strip().upper()

    # Safe cancellation rule:
    # Rider can cancel before pickup.
    # After pickup/out-for-delivery, the item is already physically with rider,
    # so cancellation needs store/admin/manual return flow.
    cancellable_statuses = {
        "ASSIGNED_TO_DELIVERY",
        "REACHED_STORE"
    }

    if current_status not in cancellable_statuses:
        flash(
            "This delivery cannot be cancelled from your side after pickup. Please contact the store/admin.",
            "warning"
        )
        return redirect(request.referrer or url_for("delivery_active_orders"))

    reason = (request.form.get("cancel_reason") or "").strip()

    if not reason:
        reason = "Cancelled by delivery partner."

    if len(reason) > 300:
        reason = reason[:300]

    now = datetime.utcnow().isoformat()

    old_partner_id = str(order.get("delivery_partner_id") or u["id"])
    old_partner_name = order.get("delivery_partner_name") or u.get("name") or "Delivery Partner"
    old_partner_phone = order.get("delivery_partner_phone") or u.get("phone") or ""

    history_entry = {
        "action": "cancelled_by_delivery_partner",
        "delivery_partner_id": old_partner_id,
        "delivery_partner_name": old_partner_name,
        "delivery_partner_phone": old_partner_phone,
        "reason": reason,
        "status_before_cancel": current_status,
        "at": now,
        "by": "delivery",
        "actor_id": str(u.get("_id") or u.get("id") or ""),
        "actor_name": u.get("name") or old_partner_name
    }

    mongo.orders.update_one(
        {"_id": oid_obj},
        {
            "$set": {
                # Main order is still valid and ready for store reassignment
                "status": "READY_FOR_PICKUP",

                # Remove current rider assignment
                "delivery_partner_id": None,
                "delivery_partner_name": "",
                "delivery_partner_phone": "",

                # Reassignment markers
                "needs_reassignment": True,
                "delivery_cancelled_by_partner": True,
                "delivery_cancelled_at": now,
                "delivery_cancel_reason": reason,
                "delivery_cancelled_status_from": current_status,

                # Keep old rider info for store visibility
                "previous_delivery_partner_id": old_partner_id,
                "previous_delivery_partner_name": old_partner_name,
                "previous_delivery_partner_phone": old_partner_phone,

                "updated_at": now
            },
            "$push": {
                "delivery_history": history_entry
            }
        }
    )

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
        "DELIVERY_CANCELLED_BY_RIDER",
        f"Delivery cancelled by {old_partner_name}. Reason: {reason}",
        u
    )

    # Notify store immediately so they can reassign another delivery boy
    try:
        store = None
        store_id = order.get("store_id")

        if store_id:
            store_id_values = [store_id]

            try:
                store_id_str = str(store_id)
                if ObjectId.is_valid(store_id_str):
                    store_id_values.append(ObjectId(store_id_str))
            except Exception:
                pass

            store = mongo.stores.find_one({
                "_id": {"$in": store_id_values}
            })

        if store:
            title = "Delivery cancelled by rider"
            message = (
                f"Order #{str(oid_obj)[-6:]} needs reassignment. "
                f"{old_partner_name} cancelled this delivery. Reason: {reason}"
            )

            event_key = f"delivery-cancelled-by-rider-{str(oid_obj)}-{now}"

            existing_notification = mongo.store_notifications.find_one({
                "store_id": {"$in": [store["_id"], str(store["_id"])]},
                "event_key": event_key
            })

            if not existing_notification:
                mongo.store_notifications.insert_one({
                    "store_id": store["_id"],
                    "store_name": store.get("store_name", ""),
                    "title": title,
                    "message": message,
                    "type": "delivery_reassignment",
                    "order_id": oid_obj,
                    "order_ref": str(oid_obj),
                    "order_status": "READY_FOR_PICKUP",
                    "payment_status": order.get("payment_status", ""),
                    "customer_name": order.get("customer_name", ""),
                    "customer_phone": order.get("customer_phone", ""),
                    "total_payable": (
                        float(order.get("total_amount") or 0)
                        + float(order.get("delivery_fee") or 0)
                        + float(order.get("tip_amount") or 0)
                    ),
                    "event_key": event_key,
                    "is_read": False,
                    "is_active": True,
                    "created_at": now,
                    "updated_at": now
                })

    except Exception as notify_error:
        print("[STORE NOTIFICATION ERROR]", notify_error)

    flash("Delivery cancelled. The order has been sent back to the store for reassignment.", "success")
    return redirect(url_for("delivery_active_orders"))

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
