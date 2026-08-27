"""Store notification helpers extracted during Step 4.

The existing MongoDB queries/document shapes are preserved exactly.
"""

from datetime import datetime

from extensions import mongo

def _store_id_values(store_id):
    return [store_id, str(store_id)]

def _store_notification_stats(store_id):
    store_id_values = _store_id_values(store_id)
    today_prefix = datetime.utcnow().date().isoformat()

    total = mongo.store_notifications.count_documents({
        "store_id": {"$in": store_id_values}
    })

    unread = mongo.store_notifications.count_documents({
        "store_id": {"$in": store_id_values},
        "is_read": False
    })

    today = mongo.store_notifications.count_documents({
        "store_id": {"$in": store_id_values},
        "created_at": {"$regex": f"^{today_prefix}"}
    })

    active = mongo.orders.count_documents({
        "store_id": {"$in": store_id_values},
        "status": {"$nin": ["DELIVERED", "CANCELLED"]}
    })

    return {
        "total": total,
        "unread": unread,
        "today": today,
        "active": active
    }

def _create_store_notification(store, title, message, notif_type="system", order=None, event_key=None):
    now = datetime.utcnow().isoformat()
    store_id = store["_id"]

    if event_key:
        existing = mongo.store_notifications.find_one({
            "store_id": {"$in": _store_id_values(store_id)},
            "event_key": event_key
        })

        if existing:
            return existing

    doc = {
        "store_id": store_id,
        "store_name": store.get("store_name", ""),
        "title": title,
        "message": message,
        "type": notif_type,
        "is_read": False,
        "is_active": True,
        "created_at": now,
        "updated_at": now
    }

    if event_key:
        doc["event_key"] = event_key

    if order:
        doc["order_id"] = order.get("_id")
        doc["order_ref"] = str(order.get("_id"))
        doc["order_status"] = order.get("status", "")
        doc["payment_status"] = order.get("payment_status", "")
        doc["customer_name"] = order.get("customer_name", "")
        doc["customer_phone"] = order.get("customer_phone", "")
        doc["total_payable"] = (
            float(order.get("total_amount") or 0)
            + float(order.get("delivery_fee") or 0)
            + float(order.get("tip_amount") or 0)
        )

    mongo.store_notifications.insert_one(doc)
    return doc

def _hydrate_store_notification(n):
    n["id"] = str(n["_id"])
    n["store_id"] = str(n.get("store_id")) if n.get("store_id") else ""
    n["order_id"] = str(n.get("order_id")) if n.get("order_id") else ""
    n["title"] = n.get("title", "Notification")
    n["message"] = n.get("message", "")
    n["type"] = n.get("type", "system")
    n["is_read"] = bool(n.get("is_read"))
    n["is_active"] = bool(n.get("is_active", True))
    return n

def _sync_store_order_notifications(store):
    store_id_values = _store_id_values(store["_id"])

    recent_orders = list(
        mongo.orders.find({
            "store_id": {"$in": store_id_values}
        }).sort("created_at", -1).limit(60)
    )

    for order in recent_orders:
        oid = str(order["_id"])
        status = (order.get("status") or "PLACED").upper()

        total_payable = (
            float(order.get("total_amount") or 0)
            + float(order.get("delivery_fee") or 0)
            + float(order.get("tip_amount") or 0)
        )

        if status not in ["DELIVERED", "CANCELLED"]:
            _create_store_notification(
                store,
                title="Active order needs attention",
                message=f"Order #{oid[-6:]} is currently {status}. Payable amount ₹ {total_payable:.2f}.",
                notif_type="new_order",
                order=order,
                event_key=f"order-active-{oid}"
            )

    recent_events = list(
        mongo.order_events.find({}).sort("created_at", -1).limit(120)
    )

    for event in recent_events:
        order_id = event.get("order_id")

        if not order_id:
            continue

        order = mongo.orders.find_one({
            "_id": order_id,
            "store_id": {"$in": store_id_values}
        })

        if not order:
            continue

        oid = str(order["_id"])
        status = (event.get("status") or order.get("status") or "").upper()
        event_id = str(event.get("_id"))

        _create_store_notification(
            store,
            title="Order status updated",
            message=f"Order #{oid[-6:]} status changed to {status}.",
            notif_type="status",
            order=order,
            event_key=f"order-event-{event_id}"
        )
