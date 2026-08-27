"""Delivery dashboard route section.

Route decorators, endpoint names, URL paths, HTTP methods and function bodies are
preserved from the Step 6 source. Shared legacy helpers are imported from
``routes.delivery.shared`` during this transitional decomposition.
"""

from routes.delivery.shared import *

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
            o = _decorate_delivery_financials(_hydrate_delivery_order(o))
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
            o = _decorate_delivery_financials(_hydrate_delivery_order(o))
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
        o = _decorate_delivery_financials(_hydrate_delivery_order(o))
        distance_km = _driver_distance_to_store_km(o, availability)
        o["driver_store_distance_km"] = distance_km
        orders.append(o)

    return render_template(
        "delivery_active_orders.html",
        user=u,
        orders=orders,
        delivery_active=delivery_active,
        delivery_availability=availability,
        delivery_accept_radius_km=DELIVERY_ACCEPT_RADIUS_KM,
        pay_on_delivery_upi=_delivery_pay_on_delivery_upi_settings()
    )
