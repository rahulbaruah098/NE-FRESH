"""Delivery routes extracted from the updated app.py.

Logic, decorators, endpoint names and route paths are intentionally preserved.
Only the file location changed.
"""

from app_core import *


def _delivery_notification_user_values(user_id):
    values = [str(user_id)]

    try:
        user_id_str = str(user_id)
        if ObjectId.is_valid(user_id_str):
            values.append(ObjectId(user_id_str))
    except Exception:
        pass

    return values


def _delivery_notification_order_values(order_id):
    values = [str(order_id)]

    try:
        order_id_str = str(order_id)
        if ObjectId.is_valid(order_id_str):
            values.append(ObjectId(order_id_str))
    except Exception:
        pass

    return values


def _hydrate_delivery_notification(n):
    if not n:
        return {}

    def _safe_str(value):
        if value is None:
            return ""
        try:
            if isinstance(value, ObjectId):
                return str(value)
        except Exception:
            pass
        return str(value)

    return {
        "id": _safe_str(n.get("_id")),
        "delivery_user_id": _safe_str(n.get("delivery_user_id")),
        "title": n.get("title") or "Notification",
        "message": n.get("message") or "",
        "type": n.get("type") or "system",
        "order_id": _safe_str(n.get("order_id")),
        "order_ref": _safe_str(n.get("order_ref") or n.get("order_id")),
        "store_id": _safe_str(n.get("store_id")),
        "store_name": n.get("store_name") or "",
        "is_read": bool(n.get("is_read")),
        "is_active": bool(n.get("is_active", True)),
        "event_key": n.get("event_key") or "",
        "target_url": n.get("target_url") or "",
        "created_at": n.get("created_at") or "",
        "updated_at": n.get("updated_at") or "",
        "read_at": n.get("read_at") or "",
    }


def _create_delivery_notification(delivery_user_id, title, message, notif_type="system", order=None, event_key=None, target_url=None):
    now = datetime.utcnow().isoformat()

    delivery_user_id_str = str(delivery_user_id)

    if event_key:
        existing = mongo.delivery_notifications.find_one({
            "delivery_user_id": {"$in": _delivery_notification_user_values(delivery_user_id)},
            "event_key": event_key
        })

        if existing:
            return existing

    order_id = None
    store_id = None
    store_name = ""

    if order:
        order_id = order.get("_id") or order.get("id")
        store_id = order.get("store_id")
        store_name = order.get("store_name") or ""

    doc = {
        "delivery_user_id": delivery_user_id_str,
        "title": title,
        "message": message,
        "type": notif_type,
        "order_id": order_id,
        "order_ref": str(order_id) if order_id else "",
        "store_id": store_id,
        "store_name": store_name,
        "target_url": target_url or url_for("delivery_available_orders"),
        "event_key": event_key or f"delivery-notification-{delivery_user_id_str}-{now}",
        "is_read": False,
        "is_active": True,
        "created_at": now,
        "updated_at": now
    }

    result = mongo.delivery_notifications.insert_one(doc)
    doc["_id"] = result.inserted_id

    return doc


def _sync_delivery_available_order_notifications(delivery_user, availability):
    """
    Creates notification rows for available READY_FOR_PICKUP orders
    visible to this delivery boy.

    This does not assign the order.
    It only notifies the delivery boy.
    """
    if not delivery_user or not availability or not availability.get("active"):
        return []

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
        }).sort("updated_at", -1).limit(30)
    )

    synced = []

    for order in raw_orders:
        distance_km = _driver_distance_to_store_km(order, availability)

        if distance_km is not None and distance_km > DELIVERY_ACCEPT_RADIUS_KM:
            continue

        hydrated_order = _hydrate_delivery_order(order)

        oid = str(order["_id"])
        store_name = hydrated_order.get("store_name") or "Store"

        notif = _create_delivery_notification(
            delivery_user_id=delivery_user["id"],
            title="New delivery available",
            message=f"Order #{oid[-6:]} is ready for pickup from {store_name}.",
            notif_type="new_available_order",
            order=hydrated_order,
            event_key=f"new-available-order-{str(delivery_user['id'])}-{oid}",
            target_url=url_for("delivery_available_orders")
        )

        synced.append(notif)

    return synced

@app.route('/delivery')
@login_required(role='delivery')
def delivery_dashboard():
    u = current_user()

    availability = _get_delivery_availability(u["id"])
    delivery_active = bool(availability.get("active"))

    orders = []


    cancelled_by_me_count = mongo.orders.count_documents({
        "delivery_history": {
            "$elemMatch": {
                "action": "cancelled_by_delivery_partner",
                "delivery_partner_id": str(u["id"])
            }
        }
    })


    successful_deliveries_count = mongo.orders.count_documents({
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
    })

    active_assigned_count = mongo.orders.count_documents({
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
    })

    all_delivery_records_count = (
        int(active_assigned_count or 0)
        + int(successful_deliveries_count or 0)
        + int(cancelled_by_me_count or 0)
    )

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
        delivery_accept_radius_km=DELIVERY_ACCEPT_RADIUS_KM,
        cancelled_by_me_count=cancelled_by_me_count,
        successful_deliveries_count=successful_deliveries_count,
        all_delivery_records_count=all_delivery_records_count
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

@app.route('/delivery/notifications/poll', methods=['GET'], endpoint='delivery_notifications_poll')
@login_required(role='delivery')
def delivery_notifications_poll():
    u = current_user()

    availability = _get_delivery_availability(u["id"])
    delivery_active = bool(availability.get("active"))

    if delivery_active:
        _sync_delivery_available_order_notifications(u, availability)

    notifications = list(
        mongo.delivery_notifications.find({
            "delivery_user_id": {"$in": _delivery_notification_user_values(u["id"])},
            "is_active": True
        }).sort("created_at", -1).limit(20)
    )

    hydrated_notifications = [
        _hydrate_delivery_notification(n)
        for n in notifications
    ]

    unread_count = mongo.delivery_notifications.count_documents({
        "delivery_user_id": {"$in": _delivery_notification_user_values(u["id"])},
        "is_read": False,
        "is_active": True
    })

    return jsonify({
        "ok": True,
        "delivery_active": delivery_active,
        "notifications": hydrated_notifications,
        "stats": {
            "unread": int(unread_count),
            "total": int(len(hydrated_notifications))
        }
    })


@app.route('/delivery/notifications/<nid>/read', methods=['POST'], endpoint='delivery_notification_mark_read')
@login_required(role='delivery')
def delivery_notification_mark_read(nid):
    u = current_user()

    try:
        nid_obj = ObjectId(str(nid))
    except Exception:
        return jsonify({"ok": False}), 400

    mongo.delivery_notifications.update_one(
        {
            "_id": nid_obj,
            "delivery_user_id": {"$in": _delivery_notification_user_values(u["id"])}
        },
        {
            "$set": {
                "is_read": True,
                "read_at": datetime.utcnow().isoformat(),
                "updated_at": datetime.utcnow().isoformat()
            }
        }
    )

    return jsonify({"ok": True})

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

@app.route('/delivery/cancelled-orders', methods=['GET'], endpoint='delivery_cancelled_orders')
@login_required(role='delivery')
def delivery_cancelled_orders():
    u = current_user()

    availability = _get_delivery_availability(u["id"])
    delivery_active = bool(availability.get("active"))

    q = (request.args.get("q") or "").strip()

    delivery_user_id_values = [
        str(u["id"])
    ]

    try:
        if ObjectId.is_valid(str(u["id"])):
            delivery_user_id_values.append(ObjectId(str(u["id"])))
    except Exception:
        pass

    raw_orders = list(
        mongo.orders.find({
            "delivery_history": {
                "$elemMatch": {
                    "action": "cancelled_by_delivery_partner",
                    "delivery_partner_id": {"$in": delivery_user_id_values}
                }
            }
        }).sort("updated_at", -1)
    )

    cancelled_orders = []

    for o in raw_orders:
        o = _hydrate_delivery_order(o)

        my_cancel_entries = []

        for h in o.get("delivery_history") or []:
            if not isinstance(h, dict):
                continue

            if h.get("action") != "cancelled_by_delivery_partner":
                continue

            if str(h.get("delivery_partner_id") or "") != str(u["id"]):
                continue

            my_cancel_entries.append(h)

        if not my_cancel_entries:
            continue

        latest_cancel = my_cancel_entries[-1]

        o["cancel_reason"] = latest_cancel.get("reason") or o.get("delivery_cancel_reason") or "Cancelled by delivery partner."
        o["cancelled_at"] = latest_cancel.get("at") or o.get("delivery_cancelled_at") or ""
        o["cancelled_status_from"] = latest_cancel.get("status_before_cancel") or o.get("delivery_cancelled_status_from") or ""
        o["cancel_actor_name"] = latest_cancel.get("actor_name") or latest_cancel.get("delivery_partner_name") or u.get("name") or "Delivery Partner"
        o["cancel_count_for_this_order"] = len(my_cancel_entries)

        if q:
            haystack = " ".join([
                str(o.get("id") or ""),
                str(o.get("store_name") or ""),
                str(o.get("customer_name") or ""),
                str(o.get("customer_phone") or ""),
                str(o.get("cancel_reason") or ""),
                str(o.get("cancelled_status_from") or "")
            ]).lower()

            if q.lower() not in haystack:
                continue

        cancelled_orders.append(o)

    return render_template(
        "delivery_cancelled_orders.html",
        user=u,
        orders=cancelled_orders,
        delivery_active=delivery_active,
        delivery_availability=availability,
        q=q,
        total_cancelled=len(cancelled_orders)
    )


@app.route('/delivery/successful-deliveries', methods=['GET'], endpoint='delivery_successful_deliveries')
@login_required(role='delivery')
def delivery_successful_deliveries():
    u = current_user()

    availability = _get_delivery_availability(u["id"])
    delivery_active = bool(availability.get("active"))

    q = (request.args.get("q") or "").strip()
    date_from = (request.args.get("from") or "").strip()
    date_to = (request.args.get("to") or "").strip()

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
                    "status": "DELIVERED"
                }
            ]
        }).sort("delivered_at", -1)
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
        "delivery_successful_deliveries.html",
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

@app.route('/delivery/all-orders', methods=['GET'], endpoint='delivery_all_orders')
@login_required(role='delivery')
def delivery_all_orders():
    u = current_user()

    availability = _get_delivery_availability(u["id"])
    delivery_active = bool(availability.get("active"))

    q = (request.args.get("q") or "").strip()
    status_filter = (request.args.get("status") or "").strip().upper()

    delivery_user_id_values = [str(u["id"])]

    try:
        if ObjectId.is_valid(str(u["id"])):
            delivery_user_id_values.append(ObjectId(str(u["id"])))
    except Exception:
        pass

    rows = []

    # Active + delivered records directly assigned to this rider
    direct_statuses = list(DELIVERY_ASSIGNED_ACTIVE_STATUSES) + ["DELIVERED", "DELIVERY_FAILED", "CANCELLED"]

    direct_orders = list(
        mongo.orders.find({
            "$and": [
                {
                    "$or": [
                        {"delivery_partner_id": u["id"]},
                        {"delivery_partner_id": str(u["id"])}
                    ]
                },
                {
                    "status": {"$in": direct_statuses}
                }
            ]
        }).sort("updated_at", -1)
    )

    for o in direct_orders:
        o = _hydrate_delivery_order(o)

        status = (o.get("status") or "").strip().upper()

        if status == "DELIVERED":
            o["record_type"] = "successful"
            o["record_label"] = "Successful Delivery"
            o["record_at"] = o.get("delivered_at") or o.get("updated_at") or o.get("created_at") or ""

        elif status == "DELIVERY_FAILED":
            o["record_type"] = "failed"
            o["record_label"] = "Failed Delivery"
            o["record_at"] = o.get("delivery_failed_at") or o.get("updated_at") or o.get("created_at") or ""
            o["failed_reason"] = o.get("delivery_failed_reason") or ""
            o["failed_note"] = o.get("delivery_failed_note") or ""

        elif status == "CANCELLED" and str(o.get("delivery_failed_by") or "") == str(u["id"]):
            o["record_type"] = "failed"
            o["record_label"] = "Failed Delivery - Cancelled by Store"
            o["record_at"] = o.get("cancelled_at") or o.get("delivery_failed_at") or o.get("updated_at") or o.get("created_at") or ""
            o["failed_reason"] = o.get("delivery_failed_reason") or o.get("cancel_reason") or ""
            o["failed_note"] = o.get("delivery_failed_note") or o.get("cancel_note") or ""

        elif status == "CANCELLED":
            continue

        else:
            o["record_type"] = "active"
            o["record_label"] = "Active Delivery"
            o["record_at"] = o.get("updated_at") or o.get("created_at") or ""

        rows.append(o)

    # Cancelled assignment records from delivery_history
    cancelled_orders = list(
        mongo.orders.find({
            "delivery_history": {
                "$elemMatch": {
                    "action": "cancelled_by_delivery_partner",
                    "delivery_partner_id": {"$in": delivery_user_id_values}
                }
            }
        }).sort("updated_at", -1)
    )

    for o in cancelled_orders:
        o = _hydrate_delivery_order(o)

        my_cancel_entries = []

        for h in o.get("delivery_history") or []:
            if not isinstance(h, dict):
                continue

            if h.get("action") != "cancelled_by_delivery_partner":
                continue

            if str(h.get("delivery_partner_id") or "") != str(u["id"]):
                continue

            my_cancel_entries.append(h)

        if not my_cancel_entries:
            continue

        latest_cancel = my_cancel_entries[-1]

        o["record_type"] = "cancelled_by_me"
        o["record_label"] = "Cancelled By Me"
        o["record_at"] = latest_cancel.get("at") or o.get("delivery_cancelled_at") or o.get("updated_at") or ""
        o["cancel_reason"] = latest_cancel.get("reason") or o.get("delivery_cancel_reason") or "Cancelled by delivery partner."
        o["cancelled_status_from"] = latest_cancel.get("status_before_cancel") or o.get("delivery_cancelled_status_from") or ""

        rows.append(o)

    if status_filter:
        filtered_rows = []

        for row in rows:
            record_type = (row.get("record_type") or "").upper()
            status = (row.get("status") or "").strip().upper()

            if status_filter == "ACTIVE" and row.get("record_type") == "active":
                filtered_rows.append(row)
            elif status_filter == "SUCCESSFUL" and row.get("record_type") == "successful":
                filtered_rows.append(row)
            elif status_filter == "FAILED" and row.get("record_type") == "failed":
                filtered_rows.append(row)
            elif status_filter == "CANCELLED_BY_ME" and row.get("record_type") == "cancelled_by_me":
                filtered_rows.append(row)
            elif status_filter == status or status_filter == record_type:
                filtered_rows.append(row)

        rows = filtered_rows

    if q:
        q_lower = q.lower()
        filtered_rows = []

        for row in rows:
            haystack = " ".join([
                str(row.get("id") or ""),
                str(row.get("store_name") or ""),
                str(row.get("customer_name") or ""),
                str(row.get("customer_phone") or ""),
                str(row.get("status") or ""),
                str(row.get("record_label") or ""),
                str(row.get("cancel_reason") or "")
            ]).lower()

            if q_lower in haystack:
                filtered_rows.append(row)

        rows = filtered_rows

    rows.sort(
        key=lambda x: str(x.get("record_at") or x.get("updated_at") or x.get("created_at") or ""),
        reverse=True
    )

    stats = {
        "total": len(rows),
        "active": sum(1 for r in rows if r.get("record_type") == "active"),
        "successful": sum(1 for r in rows if r.get("record_type") == "successful"),
        "failed": sum(1 for r in rows if r.get("record_type") == "failed"),
        "cancelled_by_me": sum(1 for r in rows if r.get("record_type") == "cancelled_by_me")
    }

    return render_template(
        "delivery_all_orders.html",
        user=u,
        orders=rows,
        stats=stats,
        delivery_active=delivery_active,
        delivery_availability=availability,
        q=q,
        status_filter=status_filter
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
        "DELIVERED",
        "DELIVERY_FAILED"
    }

    if new_status not in allowed_statuses:
        flash("Invalid delivery status selected.", "warning")
        return redirect(request.referrer or url_for("delivery_active_orders"))

    current_status = (order.get("status") or "").strip().upper()

    allowed_transitions = {
        "ASSIGNED_TO_DELIVERY": {"REACHED_STORE", "PICKED_UP", "OUT_FOR_DELIVERY"},
        "REACHED_STORE": {"PICKED_UP", "OUT_FOR_DELIVERY"},
        "PICKED_UP": {"OUT_FOR_DELIVERY"},
        "OUT_FOR_DELIVERY": {"DELIVERED", "DELIVERY_FAILED"},
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

    elif new_status == "DELIVERY_FAILED":
        failed_reason = (request.form.get("delivery_failed_reason") or "").strip()
        failed_note = (request.form.get("delivery_failed_note") or "").strip()

        if not failed_reason:
            flash("Please select/write the reason for failed delivery.", "warning")
            return redirect(request.referrer or url_for("delivery_active_orders"))

        if len(failed_reason) > 120:
            failed_reason = failed_reason[:120]

        if len(failed_note) > 500:
            failed_note = failed_note[:500]

        update_data["delivery_failed_at"] = now
        update_data["delivery_failed_by"] = str(u["id"])
        update_data["delivery_failed_by_name"] = u.get("name") or "Delivery Partner"
        update_data["delivery_failed_reason"] = failed_reason
        update_data["delivery_failed_note"] = failed_note
        update_data["delivery_failed_requires_store_action"] = True
        update_data["delivery_failed_store_decision"] = ""
        update_data["delivery_failed_resolved_at"] = ""
        event_note = f"Delivery failed. Reason: {failed_reason}"

    mongo.orders.update_one(
        {"_id": oid_obj},
        {
            "$set": update_data
        }
    )

    if new_status == "DELIVERY_FAILED":
        # Clear rider current order because this delivery attempt is now closed
        mongo.delivery_availability.update_one(
            {
                "user_id": str(u["id"]),
                "current_order_id": str(oid_obj)
            },
            {
                "$set": {
                    "current_order_id": None,
                    "updated_at": now
                }
            }
        )

        # Notify store so they can decide: reschedule / reassign / cancel
        try:
            store = None
            store_id = order.get("store_id")

            if store_id:
                store_id_values = [store_id, str(store_id)]

                try:
                    if ObjectId.is_valid(str(store_id)):
                        store_id_values.append(ObjectId(str(store_id)))
                except Exception:
                    pass

                store = mongo.stores.find_one({
                    "_id": {"$in": store_id_values}
                })

            if store:
                mongo.store_notifications.insert_one({
                    "store_id": store["_id"],
                    "store_name": store.get("store_name", ""),
                    "title": "Delivery attempt failed",
                    "message": (
                        f"Order #{str(oid_obj)[-6:]} could not be delivered. "
                        f"Reason: {update_data.get('delivery_failed_reason')}. "
                        "Please reschedule, reassign, or cancel this order."
                    ),
                    "type": "delivery_failed",
                    "order_id": str(oid_obj),
                    "order_ref": str(oid_obj),
                    "order_status": "DELIVERY_FAILED",
                    "customer_name": order.get("customer_name", ""),
                    "customer_phone": order.get("customer_phone", ""),
                    "event_key": f"delivery-failed-{str(oid_obj)}-{now}",
                    "is_read": False,
                    "is_active": True,
                    "created_at": now,
                    "updated_at": now
                })
        except Exception as notify_error:
            print("[DELIVERY FAILED STORE NOTIFICATION ERROR]", notify_error)

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
