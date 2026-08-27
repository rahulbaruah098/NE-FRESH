"""Store notifications route section.

Route decorators, endpoint names, URL paths, HTTP methods and function bodies are
preserved from the Step 6 source. Shared legacy helpers are imported from
``routes.store.shared`` during this transitional decomposition.
"""

from routes.store.shared import *

@app.route('/store/notifications', methods=['GET'], endpoint='store_notifications')
@login_required(role='store')
def store_notifications_page():
    u, store = _get_current_store_or_redirect()

    if not store:
        return redirect(url_for("store_dashboard"))

    _sync_store_order_notifications(store)

    store_id_values = _store_id_values(store["_id"])

    notifications = list(
        mongo.store_notifications.find({
            "store_id": {"$in": store_id_values}
        }).sort("created_at", -1).limit(150)
    )

    notifications = [_hydrate_store_notification(n) for n in notifications]

    active_orders = list(
        mongo.orders.find({
            "store_id": {"$in": store_id_values},
            "status": {"$nin": ["DELIVERED", "CANCELLED"]}
        }).sort("created_at", -1).limit(30)
    )

    active_notifications = []

    for order in active_orders:
        oid = str(order["_id"])
        status = (order.get("status") or "PLACED").upper()

        total_payable = (
            float(order.get("total_amount") or 0)
            + float(order.get("delivery_fee") or 0)
            + float(order.get("tip_amount") or 0)
        )

        active_notifications.append({
            "id": oid,
            "title": f"Order #{oid[-6:]} needs attention",
            "message": f"Current status: {status}. Payable amount ₹ {total_payable:.2f}.",
            "type": "active_order",
            "order_id": oid,
            "created_at": order.get("created_at", "")
        })

    notification_settings = mongo.store_notification_settings.find_one({
        "store_id": store["_id"]
    }) or {
        "enabled": False
    }

    page_context = _build_store_split_page_context(store)

    return render_template(
        "store_notifications.html",
        user=u,
        store=store,
        notifications=notifications,
        active_notifications=active_notifications,
        notification_settings=notification_settings,
        notification_stats=_store_notification_stats(store["_id"]),
        **page_context
    )


@app.route('/store/notifications/toggle', methods=['POST'], endpoint='store_notifications_toggle')
@login_required(role='store')
def store_notifications_toggle():
    u, store = _get_current_store_or_redirect()

    if not store:
        return jsonify({"ok": False, "message": "Store not found"}), 404

    data = request.get_json(silent=True) or {}
    enabled = bool(data.get("enabled"))
    now = datetime.utcnow().isoformat()

    mongo.store_notification_settings.update_one(
        {"store_id": store["_id"]},
        {
            "$set": {
                "store_id": store["_id"],
                "enabled": enabled,
                "preferences.dashboard_alert": enabled,
                "updated_at": now
            },
            "$setOnInsert": {
                "created_at": now
            }
        },
        upsert=True
    )

    mongo.stores.update_one(
        {"_id": store["_id"]},
        {
            "$set": {
                "notification_preferences.dashboard_alert": enabled,
                "updated_at": now
            }
        }
    )

    _create_store_notification(
        store,
        title="Notifications enabled" if enabled else "Notifications disabled",
        message="Live order alerts were enabled for this store." if enabled else "Live order alerts were disabled for this store.",
        notif_type="system",
        event_key=f"notification-toggle-{store['_id']}-{now}"
    )

    return jsonify({
        "ok": True,
        "enabled": enabled,
        "stats": _store_notification_stats(store["_id"])
    })


@app.route('/store/notifications/poll', methods=['GET'], endpoint='store_notifications_poll')
@login_required(role='store')
def store_notifications_poll():
    u, store = _get_current_store_or_redirect()

    if not store:
        return jsonify({"ok": False, "notifications": []}), 404

    _sync_store_order_notifications(store)

    notifications = list(
        mongo.store_notifications.find({
            "store_id": {"$in": _store_id_values(store["_id"])}
        }).sort("created_at", -1).limit(20)
    )

    hydrated_notifications = []

    for n in notifications:
        row = _hydrate_store_notification(n)

        # IMPORTANT:
        # _hydrate_store_notification() adds string-safe fields,
        # but the original Mongo "_id": ObjectId(...) still remains in the dict.
        # Flask jsonify cannot serialize ObjectId, so remove the raw Mongo field.
        row.pop("_id", None)

        hydrated_notifications.append(row)

    notification_settings = mongo.store_notification_settings.find_one({
        "store_id": store["_id"]
    }) or {
        "enabled": False
    }

    return jsonify({
        "ok": True,
        "enabled": bool(notification_settings.get("enabled")),
        "notifications": hydrated_notifications,
        "stats": _store_notification_stats(store["_id"])
    })


@app.route('/store/notifications/<nid>/read', methods=['POST'], endpoint='store_notification_mark_read')
@login_required(role='store')
def store_notification_mark_read(nid):
    u, store = _get_current_store_or_redirect()

    if not store:
        return jsonify({"ok": False}), 404

    try:
        nid_obj = ObjectId(nid)
    except Exception:
        return jsonify({"ok": False}), 400

    mongo.store_notifications.update_one(
        {
            "_id": nid_obj,
            "store_id": {"$in": _store_id_values(store["_id"])}
        },
        {
            "$set": {
                "is_read": True,
                "read_at": datetime.utcnow().isoformat(),
                "updated_at": datetime.utcnow().isoformat()
            }
        }
    )

    return jsonify({
        "ok": True,
        "stats": _store_notification_stats(store["_id"])
    })


@app.route('/store/notifications/read-all', methods=['POST'], endpoint='store_notifications_mark_all_read')
@login_required(role='store')
def store_notifications_mark_all_read():
    u, store = _get_current_store_or_redirect()

    if not store:
        return jsonify({"ok": False}), 404

    mongo.store_notifications.update_many(
        {
            "store_id": {"$in": _store_id_values(store["_id"])},
            "is_read": False
        },
        {
            "$set": {
                "is_read": True,
                "read_at": datetime.utcnow().isoformat(),
                "updated_at": datetime.utcnow().isoformat()
            }
        }
    )

    return jsonify({
        "ok": True,
        "stats": _store_notification_stats(store["_id"])
    })
