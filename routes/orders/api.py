"""Orders api route section.

Route decorators, endpoint names, URL paths, HTTP methods and function bodies are
preserved from the Step 6 source. Shared legacy helpers are imported from
``routes.orders.shared`` during this transitional decomposition.
"""

from routes.orders.shared import *

@app.route("/api/orders/<oid>/status", methods=["GET"], endpoint="api_order_status")
@login_required()
def api_order_status(oid):
    u = current_user()

    data = get_order_full(
        oid,
        for_user_id=u["id"] if u["role"] == "customer" else None
    )

    if not data:
        return jsonify({
            "ok": False,
            "error": "not found"
        }), 404

    o = normalize_order_money_fields(data["order"])
    o = decorate_order_delivery_mode_display(o)

    events = []
    for e in data.get("events", []):
        events.append({
            "id": str(e.get("_id")) if e.get("_id") else e.get("id"),
            "status": e.get("status"),
            "note": e.get("note", ""),
            "created_at": e.get("created_at"),
            "created_at_display": _format_customer_datetime(e.get("created_at"))
        })

    return jsonify({
        "ok": True,
        "id": o.get("id"),
        "order_number": o.get("order_number") or "",
        "status": o.get("status"),
        "payment_status": o.get("payment_status"),

        "total_amount": float(o.get("total_amount") or 0),
        "items_subtotal": float(o.get("items_subtotal") or o.get("total_amount") or 0),
        "delivery_fee": float(o.get("delivery_fee") or 0),
        "delivery_fee_amount": float(o.get("delivery_fee_amount") or o.get("delivery_fee") or 0),
        "delivery_fee_source": o.get("delivery_fee_source") or "",
        "delivery_fee_slab": o.get("delivery_fee_slab") or {},
        "delivery_fee_details": o.get("delivery_fee_details") or {},
        "active_delivery_mode": o.get("active_delivery_mode") or DELIVERY_MODE_IN_HOUSE,
        "delivery_mode_label": o.get("delivery_mode_label") or "",
        "delivery_mode_short_label": o.get("delivery_mode_short_label") or "",
        "delivery_fee_label": o.get("delivery_fee_label") or "Delivery Charge",
        "delivery_provider_label": o.get("delivery_provider_label") or "",
        "external_delivery_enabled_at_order": bool(o.get("external_delivery_enabled_at_order", False)),
        "external_delivery_provider": o.get("external_delivery_provider") or "",
        "external_delivery_provider_type": o.get("external_delivery_provider_type") or "",
        "external_delivery_status": o.get("external_delivery_status") or "",
        "external_delivery_status_label": o.get("external_delivery_status_label") or "",
        "external_delivery_booking_status": o.get("external_delivery_booking_status") or "",
        "external_order_id": o.get("external_order_id") or "",
        "external_shipment_id": o.get("external_shipment_id") or "",
        "external_awb": o.get("external_awb") or "",
        "external_tracking_url": o.get("external_tracking_url") or "",
        "external_tracking_code": o.get("external_tracking_code") or "",
        "external_tracking_available": bool(o.get("external_tracking_available")),
        "external_delivery_eta_minutes": o.get("external_delivery_eta_minutes"),
        "free_delivery_above_applied": bool(o.get("free_delivery_above_applied")),
        "free_delivery_above": float(o.get("free_delivery_above") or 0),
        "original_delivery_fee": float(o.get("original_delivery_fee") or o.get("delivery_fee") or 0),
        "free_delivery_savings": float(o.get("free_delivery_savings") or 0),

        "platform_fee": float(o.get("platform_fee") or 0),
        "tip_amount": float(o.get("tip_amount") or 0),
        "total_payable": float(o.get("total_payable") or o.get("total_amount") or 0),
        "admin_platform_fee_status": o.get("admin_platform_fee_status") or "",
        "platform_fee_source": o.get("platform_fee_source") or "disabled",

        "delivery_partner_id": str(o.get("delivery_partner_id")) if o.get("delivery_partner_id") else "",
        "delivery_partner_name": o.get("delivery_partner_name") or "",
        "delivery_partner_phone": o.get("delivery_partner_phone") or "",
        "needs_reassignment": bool(o.get("needs_reassignment")),
        "delivery_cancelled_by_partner": bool(o.get("delivery_cancelled_by_partner")),
        "delivery_reassigned_at": o.get("delivery_reassigned_at"),

        "delivery_failed_reason": o.get("delivery_failed_reason") or "",
        "delivery_failed_note": o.get("delivery_failed_note") or "",
        "delivery_failed_at": o.get("delivery_failed_at") or "",
        "delivery_failed_requires_store_action": bool(o.get("delivery_failed_requires_store_action", False)),
        "delivery_failed_store_decision": o.get("delivery_failed_store_decision") or "",

        "delivery_rescheduled": bool(o.get("delivery_rescheduled", False)),
        "delivery_rescheduled_for": o.get("delivery_rescheduled_for") or "",
        "delivery_rescheduled_note": o.get("delivery_rescheduled_note") or "",

        "assigned_at": o.get("assigned_at"),
        "ready_for_pickup_at": o.get("ready_for_pickup_at"),
        "reached_store_at": o.get("reached_store_at"),
        "picked_up_at": o.get("picked_up_at"),
        "out_for_delivery_at": o.get("out_for_delivery_at"),
        "delivered_at": o.get("delivered_at"),

        "events": events
    })


@app.route('/api/orders', methods=['GET'])
@api_login_required
def api_orders_list(user_id):
    orders = list(
        mongo.orders.find({"user_id": str(user_id)}).sort("created_at", -1)
    )

    result = []

    for o in orders:
        o = normalize_order_money_fields(o)
        o = decorate_order_delivery_mode_display(o)
        result.append({
            "id": str(o["_id"]),
            "order_number": o.get("order_number") or "",
            "store_name": o.get("store_name", ""),
            "total_amount": float(o.get("total_amount") or 0),
            "items_subtotal": float(o.get("items_subtotal") or o.get("total_amount") or 0),

            "delivery_fee": float(o.get("delivery_fee") or 0),
            "delivery_fee_amount": float(o.get("delivery_fee_amount") or o.get("delivery_fee") or 0),
            "delivery_fee_source": o.get("delivery_fee_source") or "",
            "delivery_fee_slab": o.get("delivery_fee_slab") or {},
            "delivery_fee_details": o.get("delivery_fee_details") or {},
            "free_delivery_above_applied": bool(o.get("free_delivery_above_applied")),
            "free_delivery_above": float(o.get("free_delivery_above") or 0),
            "original_delivery_fee": float(o.get("original_delivery_fee") or o.get("delivery_fee") or 0),
            "free_delivery_savings": float(o.get("free_delivery_savings") or 0),

            "platform_fee": float(o.get("platform_fee") or 0),
            "admin_platform_earning": float(o.get("admin_platform_earning") or o.get("platform_fee") or 0),
            "platform_fee_source": o.get("platform_fee_source") or "disabled",

            "tip_amount": float(o.get("tip_amount") or 0),
            "delivery_tip_amount": float(o.get("delivery_tip_amount") or o.get("tip_amount") or 0),

            "store_earning": float(o.get("store_earning") or o.get("total_amount") or 0),

            "total_payable": float(o.get("total_payable") or o.get("total_amount") or 0),

            "admin_platform_fee_status": o.get("admin_platform_fee_status") or "",
            "settlement_status": o.get("settlement_status") or "",
            "status": o.get("status", ""),
            "payment_status": o.get("payment_status", ""),
            "created_at": o.get("created_at", ""),

            "needs_reassignment": bool(o.get("needs_reassignment")),
            "delivery_cancelled_by_partner": bool(o.get("delivery_cancelled_by_partner")),
            "delivery_reassigned_at": o.get("delivery_reassigned_at"),

            "delivery_failed_reason": o.get("delivery_failed_reason") or "",
            "delivery_failed_note": o.get("delivery_failed_note") or "",
            "delivery_failed_at": o.get("delivery_failed_at") or "",
            "delivery_failed_requires_store_action": bool(o.get("delivery_failed_requires_store_action", False)),
            "delivery_failed_store_decision": o.get("delivery_failed_store_decision") or "",

            "delivery_rescheduled": bool(o.get("delivery_rescheduled", False)),
            "delivery_rescheduled_for": o.get("delivery_rescheduled_for") or "",
            "delivery_rescheduled_note": o.get("delivery_rescheduled_note") or ""
        })

    return jsonify({
        "success": True,
        "orders": result
    })


@app.route("/api/customer/order-alerts", methods=["GET"], endpoint="api_customer_order_alerts")
@login_required()
def api_customer_order_alerts():
    u = current_user()

    if not u:
        return jsonify({
            "ok": True,
            "alerts": [],
            "count": 0,
            "unread_count": 0
        })

    role = (u.get("role") or "").strip().lower()
    user_key = _notification_user_key(u)
    state = _notification_state_for(user_key)

    read_keys = set(state.get("read_keys") or [])
    cleared_keys = set(state.get("cleared_keys") or [])

    active_statuses = [
        "PLACED",
        "CONFIRMED",
        "PREPARING",
        "READY_FOR_PICKUP",
        "ASSIGNED_TO_DELIVERY",
        "REACHED_STORE",
        "PICKED_UP",
        "OUT_FOR_DELIVERY",
        "DELIVERY_FAILED"
    ]

    def _id_variants(value):
        values = []

        if value in (None, ""):
            return values

        values.append(value)
        raw = str(value).strip()

        if raw:
            values.append(raw)

            try:
                if ObjectId.is_valid(raw):
                    values.append(ObjectId(raw))
            except Exception:
                pass

        unique = []
        seen = set()

        for item in values:
            marker = f"{type(item).__name__}:{item}"

            if marker not in seen:
                seen.add(marker)
                unique.append(item)

        return unique

    order_query = {
        "status": {"$in": active_statuses}
    }

    if role == "customer":
        # Customer sees only their own active-order notifications.
        order_query["user_id"] = u["id"]

    elif role == "delivery":
        delivery_values = _id_variants(u.get("id") or u.get("_id"))

        if not delivery_values:
            return jsonify({
                "ok": True,
                "alerts": [],
                "count": 0,
                "unread_count": 0
            })

        order_query["delivery_partner_id"] = {"$in": delivery_values}

    elif role == "store":
        store_values = []
        store_values.extend(_id_variants(u.get("store_id")))

        if not store_values:
            user_values = _id_variants(u.get("id") or u.get("_id"))

            if user_values:
                store_doc = mongo.stores.find_one(
                    {
                        "$or": [
                            {"user_id": {"$in": user_values}},
                            {"owner_id": {"$in": user_values}},
                            {"store_user_id": {"$in": user_values}},
                            {"account_id": {"$in": user_values}},
                            {"created_by_user_id": {"$in": user_values}}
                        ]
                    },
                    {"_id": 1}
                )

                if store_doc:
                    store_values.extend(_id_variants(store_doc.get("_id")))

        if not store_values:
            return jsonify({
                "ok": True,
                "alerts": [],
                "count": 0,
                "unread_count": 0
            })

        order_query["store_id"] = {"$in": store_values}

    elif role == "admin":
        # Admin sees current active platform-order notifications.
        pass

    else:
        return jsonify({
            "ok": True,
            "alerts": [],
            "count": 0,
            "unread_count": 0
        })

    scoped_orders = list(
        mongo.orders.find(order_query).sort("updated_at", -1).limit(12)
    )

    alerts = []

    for o in scoped_orders:
        oid = str(o["_id"])
        status = (o.get("status") or "").strip().upper()
        public_order_number = (
            o.get("order_number")
            or o.get("display_order_number")
            or f"#{oid[-6:]}"
        )

        needs_reassignment = bool(
            o.get("needs_reassignment")
            or o.get("delivery_cancelled_by_partner")
        )

        if role == "customer":
            if status == "DELIVERY_FAILED":
                title = "Delivery attempt failed"
                message = f"Order #{oid[-6:]} could not be delivered. The store will reschedule or contact you shortly."
                alert_type = "delivery_failed"

            elif needs_reassignment:
                title = "Delivery partner is being reassigned"
                message = f"Order #{oid[-6:]} is safe. The store is assigning another delivery partner."
                alert_type = "reassigning"

            elif status == "ASSIGNED_TO_DELIVERY":
                title = "Delivery partner assigned"
                message = f"Order #{oid[-6:]} has been assigned to {o.get('delivery_partner_name') or 'a delivery partner'}."
                alert_type = "assigned"

            elif status == "OUT_FOR_DELIVERY":
                title = "Order is out for delivery"
                message = f"Order #{oid[-6:]} is on the way."
                alert_type = "out_for_delivery"

            elif status == "READY_FOR_PICKUP":
                title = "Order ready for pickup"
                message = f"Order #{oid[-6:]} is ready and waiting for delivery assignment."
                alert_type = "ready"

            else:
                continue

        elif role == "store":
            if status == "DELIVERY_FAILED":
                title = "Delivery attempt failed"
                message = f"{public_order_number} needs store attention after a failed delivery attempt."
                alert_type = "delivery_failed"

            elif needs_reassignment:
                title = "Delivery partner needs reassignment"
                message = f"{public_order_number} is waiting for another delivery partner."
                alert_type = "reassigning"

            elif status == "PLACED":
                title = "New order received"
                message = f"{public_order_number} has been placed."
                alert_type = "new_order"

            elif status == "READY_FOR_PICKUP":
                title = "Order ready for delivery"
                message = f"{public_order_number} is ready for delivery assignment."
                alert_type = "ready"

            elif status == "OUT_FOR_DELIVERY":
                title = "Order out for delivery"
                message = f"{public_order_number} is currently out for delivery."
                alert_type = "out_for_delivery"

            else:
                continue

        elif role == "delivery":
            if status == "DELIVERY_FAILED":
                title = "Delivery attempt failed"
                message = f"{public_order_number} is marked as a failed delivery attempt."
                alert_type = "delivery_failed"

            elif status == "ASSIGNED_TO_DELIVERY":
                title = "Order assigned to you"
                message = f"{public_order_number} has been assigned for delivery."
                alert_type = "assigned"

            elif status == "REACHED_STORE":
                title = "Store reached"
                message = f"{public_order_number} is currently at the store stage."
                alert_type = "reached_store"

            elif status == "PICKED_UP":
                title = "Order picked up"
                message = f"{public_order_number} has been picked up for delivery."
                alert_type = "picked_up"

            elif status == "OUT_FOR_DELIVERY":
                title = "Order out for delivery"
                message = f"{public_order_number} is currently out for delivery."
                alert_type = "out_for_delivery"

            else:
                continue

        else:  # admin
            if status == "DELIVERY_FAILED":
                title = "Delivery attempt failed"
                message = f"{public_order_number} has a failed delivery attempt."
                alert_type = "delivery_failed"

            elif needs_reassignment:
                title = "Delivery reassignment required"
                message = f"{public_order_number} is waiting for another delivery partner."
                alert_type = "reassigning"

            elif status == "PLACED":
                title = "New order placed"
                message = f"{public_order_number} has been placed."
                alert_type = "new_order"

            elif status == "READY_FOR_PICKUP":
                title = "Order ready for delivery"
                message = f"{public_order_number} is ready for delivery assignment."
                alert_type = "ready"

            elif status == "ASSIGNED_TO_DELIVERY":
                title = "Delivery partner assigned"
                message = f"{public_order_number} has a delivery partner assigned."
                alert_type = "assigned"

            elif status == "OUT_FOR_DELIVERY":
                title = "Order out for delivery"
                message = f"{public_order_number} is currently out for delivery."
                alert_type = "out_for_delivery"

            else:
                continue

        # A status/reassignment change creates a different key, so a genuinely
        # new order event becomes visible even if an older alert was cleared.
        alert_key = f"{role}:{oid}:{status}:{alert_type}"

        if alert_key in cleared_keys:
            continue

        alerts.append({
            "id": oid,
            "key": alert_key,
            "title": title,
            "message": message,
            "type": alert_type,
            "status": status,
            "is_read": alert_key in read_keys,
            "track_url": url_for("order_track", oid=oid),
            "created_at": o.get("updated_at") or o.get("created_at") or ""
        })

    unread_count = sum(1 for alert in alerts if not alert.get("is_read"))

    return jsonify({
        "ok": True,
        "alerts": alerts,
        "count": len(alerts),
        "unread_count": unread_count
    })


@app.route(
    "/api/customer/order-alerts/read",
    methods=["POST"],
    endpoint="api_customer_order_alerts_read"
)
@login_required()
def api_customer_order_alerts_read():
    u = current_user()

    if not u:
        return jsonify({
            "ok": False,
            "message": "Authentication required."
        }), 401

    payload = request.get_json(silent=True) or {}
    keys = payload.get("keys") or []

    _save_notification_keys(
        _notification_user_key(u),
        "read_keys",
        keys
    )

    return jsonify({
        "ok": True
    })


@app.route(
    "/api/customer/order-alerts/clear",
    methods=["POST"],
    endpoint="api_customer_order_alerts_clear"
)
@login_required()
def api_customer_order_alerts_clear():
    u = current_user()

    if not u:
        return jsonify({
            "ok": False,
            "message": "Authentication required."
        }), 401

    payload = request.get_json(silent=True) or {}
    keys = payload.get("keys") or []

    user_key = _notification_user_key(u)

    _save_notification_keys(
        user_key,
        "cleared_keys",
        keys
    )

    # A cleared notification is also treated as read.
    _save_notification_keys(
        user_key,
        "read_keys",
        keys
    )

    return jsonify({
        "ok": True
    })


@app.route('/api/orders/<oid>', methods=['GET'])
@api_login_required
def api_order_detail(user_id, oid):
    data = get_order_full(oid, for_user_id=str(user_id))

    if not data:
        return jsonify({
            "success": False,
            "error": "Order not found"
        }), 404

    o = normalize_order_money_fields(data["order"])

    items = []

    for item in data.get("items", []):
        quantity = float(item.get("quantity") or item.get("cart_quantity") or 0)
        unit_label = item.get("unit_label") or "unit"
        unit_type = item.get("unit_type") or "COUNT"
        price_per_unit = float(
            item.get("price_per_unit")
            or item.get("unit_price")
            or 0
        )

        items.append({
            "product_id": str(item.get("product_id")) if item.get("product_id") else "",
            "name": item.get("product_name") or item.get("name", ""),
            "quantity": quantity,
            "cart_quantity": quantity,
            "unit_type": unit_type,
            "unit_label": unit_label,
            "price_per_unit": price_per_unit,
            "unit_price": price_per_unit,
            "line_total": float(item.get("line_total") or (quantity * price_per_unit)),
            "image_path": item.get("image_path", "")
        })

    address = data.get("address") or {}

    if address and address.get("_id"):
        address["id"] = str(address["_id"])
        address.pop("_id", None)

    events = []

    for e in data.get("events", []):
        events.append({
            "id": str(e.get("_id")) if e.get("_id") else e.get("id", ""),
            "status": e.get("status", ""),
            "note": e.get("note", ""),
            "created_at": e.get("created_at", "")
        })

    return jsonify({
        "success": True,
        "order": {
            "id": o.get("id") or str(o.get("_id")),
            "store_name": o.get("store_name", ""),
            "total_amount": float(o.get("total_amount") or 0),
            "items_subtotal": float(o.get("items_subtotal") or o.get("total_amount") or 0),

            "delivery_fee": float(o.get("delivery_fee") or 0),
            "delivery_fee_amount": float(o.get("delivery_fee_amount") or o.get("delivery_fee") or 0),
            "delivery_fee_source": o.get("delivery_fee_source") or "",
            "delivery_fee_slab": o.get("delivery_fee_slab") or {},
            "delivery_fee_details": o.get("delivery_fee_details") or {},
            "free_delivery_above_applied": bool(o.get("free_delivery_above_applied")),
            "free_delivery_above": float(o.get("free_delivery_above") or 0),
            "original_delivery_fee": float(o.get("original_delivery_fee") or o.get("delivery_fee") or 0),
            "free_delivery_savings": float(o.get("free_delivery_savings") or 0),

            "platform_fee": float(o.get("platform_fee") or 0),
            "admin_platform_earning": float(o.get("admin_platform_earning") or o.get("platform_fee") or 0),
            "platform_fee_source": o.get("platform_fee_source") or "disabled",

            "tip_amount": float(o.get("tip_amount") or 0),
            "delivery_tip_amount": float(o.get("delivery_tip_amount") or o.get("tip_amount") or 0),

            "store_earning": float(o.get("store_earning") or o.get("total_amount") or 0),

            "total_payable": float(o.get("total_payable") or o.get("total_amount") or 0),

            "admin_platform_fee_status": o.get("admin_platform_fee_status") or "",
            "settlement_status": o.get("settlement_status") or "",
            "status": o.get("status", ""),
            "payment_status": o.get("payment_status", ""),
            "created_at": o.get("created_at", ""),
            "delivery_partner_id": str(o.get("delivery_partner_id")) if o.get("delivery_partner_id") else "",
            "delivery_partner_name": o.get("delivery_partner_name", ""),
            "delivery_partner_phone": o.get("delivery_partner_phone", ""),

            "assigned_at": o.get("assigned_at"),
            "ready_for_pickup_at": o.get("ready_for_pickup_at"),
            "reached_store_at": o.get("reached_store_at"),
            "picked_up_at": o.get("picked_up_at"),
            "out_for_delivery_at": o.get("out_for_delivery_at"),
            "delivered_at": o.get("delivered_at"),

            "items": items,
            "address": address,
            "events": events
        }
    })
