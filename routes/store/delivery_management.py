"""Store delivery management route section.

Route decorators, endpoint names, URL paths, HTTP methods and function bodies are
preserved from the Step 6 source. Shared legacy helpers are imported from
``routes.store.shared`` during this transitional decomposition.
"""

from routes.store.shared import *

@app.route('/store/delivery', methods=['GET'], endpoint='store_delivery')
@login_required(role='store')
def store_delivery_page():
    u, store = _get_current_store_or_redirect()

    if not store:
        flash("Store not found.", "danger")
        return redirect(url_for("store_dashboard"))

    page_context = _build_store_split_page_context(store) or {}
    available_delivery_people = _hydrate_store_delivery_people_for_template(store)

    store_id = store.get("_id")
    store_id_str = str(store_id)
    store_name = (store.get("store_name") or store.get("name") or "").strip().lower()

    def _safe_float(value, default=0):
        try:
            return float(value or default)
        except Exception:
            return float(default)

    def _order_belongs_to_store(order):
        order_store_id = order.get("store_id")
        order_store_name = (order.get("store_name") or "").strip().lower()

        if order_store_id and str(order_store_id) == store_id_str:
            return True

        if store_name and order_store_name and order_store_name == store_name:
            return True

        return False

    def _hydrate_store_delivery_order(order):
        row = dict(order)

        oid_value = row.get("_id") or row.get("id")
        row["id"] = str(oid_value)

        customer = None
        if row.get("user_id"):
            try:
                customer = mongo.users.find_one({"_id": ObjectId(str(row.get("user_id")))})
            except Exception:
                customer = None

        addr = None
        try:
            addr = mongo.order_addresses.find_one({
                "$or": [
                    {"order_id": row.get("_id")},
                    {"order_id": str(row.get("_id") or row.get("id") or "")}
                ]
            })
        except Exception:
            addr = None

        row["customer_name"] = (
            row.get("customer_name")
            or (customer.get("name") if customer else "")
            or "Customer"
        )

        row["customer_phone"] = (
            row.get("customer_phone")
            or (customer.get("phone") if customer else "")
            or ""
        )

        row["addr_line1"] = row.get("addr_line1") or (addr.get("line1") if addr else "")
        row["addr_line2"] = row.get("addr_line2") or (addr.get("line2") if addr else "")
        row["addr_city"] = row.get("addr_city") or (addr.get("city") if addr else "")
        row["addr_state"] = row.get("addr_state") or (addr.get("state") if addr else "")
        row["addr_pincode"] = row.get("addr_pincode") or (addr.get("pincode") if addr else "")
        row["addr_lat"] = row.get("addr_lat") or (addr.get("latitude") if addr else None)
        row["addr_lng"] = row.get("addr_lng") or (addr.get("longitude") if addr else None)

        row["items_subtotal"] = _safe_float(
            row.get("items_subtotal")
            if row.get("items_subtotal") is not None
            else row.get("total_amount")
        )
        row["total_amount"] = _safe_float(row.get("total_amount"))
        row["delivery_fee"] = _safe_float(row.get("delivery_fee"))
        row["platform_fee"] = _safe_float(row.get("platform_fee"))
        row["tip_amount"] = _safe_float(row.get("tip_amount"))

        if row.get("total_payable") is None:
            row["total_payable"] = (
                row["items_subtotal"]
                + row["delivery_fee"]
                + row["platform_fee"]
                + row["tip_amount"]
            )
        else:
            row["total_payable"] = _safe_float(row.get("total_payable"))

        row = _decorate_store_delivery_order(row)

        return row

    def _delivery_status(order):
        return (order.get("status") or "").strip().upper()

    def _has_delivery_partner(order):
        return bool(order.get("delivery_partner_id"))

    def _is_today(value):
        if not value:
            return False

        try:
            raw = str(value).replace("Z", "")
            dt = datetime.fromisoformat(raw)
            return dt.date() == datetime.utcnow().date()
        except Exception:
            return False

    raw_orders_by_id = {}

    # 1. Add orders already prepared by app_core/store order page context.
    for order in page_context.get("orders") or []:
        oid = str(order.get("_id") or order.get("id") or "")
        if oid:
            raw_orders_by_id[oid] = order

    # 2. Add all orders that match this store by ObjectId/string/name.
    direct_store_orders = list(
        mongo.orders.find({
            "$and": [
                {
                    "$or": [
                        {"store_id": store_id},
                        {"store_id": store_id_str},
                        {"store_name": store.get("store_name")},
                        {"store_name": store.get("name")}
                    ]
                },
                {
                    "status": {
                        "$nin": [
                            "PENDING_PAYMENT",
                            "PAYMENT_PENDING",
                            "ONLINE_PENDING"
                        ]
                    }
                }
            ]
        }).sort("updated_at", -1)
    )

    for order in direct_store_orders:
        oid = str(order.get("_id") or order.get("id") or "")
        if oid:
            raw_orders_by_id[oid] = order

    # 3. Hard safety: scan all DELIVERY_FAILED orders and filter ownership in Python.
    # This is the important part that fixes your current issue.
    failed_candidates = list(
        mongo.orders.find({
            "status": "DELIVERY_FAILED"
        }).sort("updated_at", -1)
    )

    for order in failed_candidates:
        if not _order_belongs_to_store(order):
            continue

        oid = str(order.get("_id") or order.get("id") or "")
        if oid:
            raw_orders_by_id[oid] = order

    orders = [
        _hydrate_store_delivery_order(order)
        for order in raw_orders_by_id.values()
        if _order_belongs_to_store(order) and store_order_visible_to_store(order)
    ]

    delivery_metrics = {
        "total_orders": len(orders),
        "ready_for_pickup": 0,
        "needs_rider": 0,
        "reassignment_needed": 0,
        "failed_delivery": 0,
        "failed_action_required": 0,
        "assigned": 0,
        "reached_store": 0,
        "picked_up": 0,
        "out_for_delivery": 0,
        "active_delivery_orders": 0,
        "delivered_today": 0,
        "cancelled": 0,
        "online_riders": len(available_delivery_people),
    }

    ready_orders = []
    needs_rider_orders = []
    failed_delivery_orders = []
    active_delivery_orders = []
    recent_delivered_orders = []
    attention_orders = []

    for order in orders:
        status = _delivery_status(order)
        has_rider = _has_delivery_partner(order)

        needs_reassignment = bool(
            order.get("needs_reassignment")
            or order.get("delivery_cancelled_by_partner")
        )

        if status == "READY_FOR_PICKUP":
            delivery_metrics["ready_for_pickup"] += 1
            ready_orders.append(order)

        if status == "READY_FOR_PICKUP" and (not has_rider or needs_reassignment):
            delivery_metrics["needs_rider"] += 1

            if needs_reassignment:
                delivery_metrics["reassignment_needed"] += 1

            needs_rider_orders.append(order)
            attention_orders.append(order)

        if status in {"ASSIGNED_TO_DELIVERY", "ACCEPTED_BY_DELIVERY_MAN"}:
            delivery_metrics["assigned"] += 1

        if status == "REACHED_STORE":
            delivery_metrics["reached_store"] += 1

        if status == "PICKED_UP":
            delivery_metrics["picked_up"] += 1

        if status == "OUT_FOR_DELIVERY":
            delivery_metrics["out_for_delivery"] += 1

        if status == "DELIVERY_FAILED":
            delivery_metrics["failed_delivery"] += 1

            if order.get("delivery_failed_requires_store_action", True):
                delivery_metrics["failed_action_required"] += 1

            failed_delivery_orders.append(order)
            attention_orders.append(order)

        if status in {
            "ASSIGNED_TO_DELIVERY",
            "ACCEPTED_BY_DELIVERY_MAN",
            "REACHED_STORE",
            "PICKED_UP",
            "OUT_FOR_DELIVERY"
        }:
            delivery_metrics["active_delivery_orders"] += 1
            active_delivery_orders.append(order)

        if status == "DELIVERED":
            recent_delivered_orders.append(order)

            if _is_today(order.get("delivered_at") or order.get("updated_at") or order.get("created_at")):
                delivery_metrics["delivered_today"] += 1

        if status == "CANCELLED":
            delivery_metrics["cancelled"] += 1

    recent_delivered_orders = recent_delivered_orders[:10]
    attention_orders = attention_orders[:10]

    log_debug(
        "[STORE DELIVERY PAGE DEBUG]",
        "store_id=", store_id_str,
        "store_name=", store_name,
        "all_orders=", len(orders),
        "failed_candidates=", len(failed_candidates),
        "failed_for_store=", len(failed_delivery_orders)
    )

    page_context["available_delivery_people"] = available_delivery_people
    page_context["delivery_accept_radius_km"] = DELIVERY_ACCEPT_RADIUS_KM
    page_context["delivery_metrics"] = delivery_metrics
    page_context["ready_orders"] = ready_orders
    page_context["needs_rider_orders"] = needs_rider_orders
    page_context["failed_delivery_orders"] = failed_delivery_orders
    page_context["active_delivery_orders"] = active_delivery_orders
    page_context["recent_delivered_orders"] = recent_delivered_orders
    page_context["attention_orders"] = attention_orders

    return render_template(
        "store_delivery.html",
        user=u,
        store=store,
        **page_context
    )


@app.route('/store/orders/<oid>/ready-for-pickup', methods=['POST'], endpoint='store_order_ready_for_pickup')
@login_required(role='store')
def store_order_ready_for_pickup(oid):
    return_endpoint = "store_active_orders" if (request.form.get("return_to") or "").strip().lower() == "active_orders" else "store_orders"
    u, store = _get_current_store_or_redirect()

    if not store:
        flash("Store not found.", "danger")
        return redirect(url_for("store_dashboard"))

    oid_obj, order = _get_store_owned_order(store, oid)

    if not oid_obj or not order:
        flash("Order not found for your store.", "danger")
        return redirect(url_for(return_endpoint))

    status = (order.get("status") or "").strip().upper()

    if order.get("delivery_partner_id"):
        flash("This order already has a delivery boy assigned.", "warning")
        return redirect(url_for(return_endpoint))

    allowed_shipment_ready_statuses = {
        "CONFIRMED",
        "PREPARING",   # legacy support
        "PACKAGING"
    }

    shipment_ready_statuses = {
        "SHIPMENT_READY",
        "READY_FOR_PICKUP"  # legacy support
    }

    if status in shipment_ready_statuses:
        flash("This order is already marked shipment ready.", "info")
        return redirect(url_for(return_endpoint))

    if status not in allowed_shipment_ready_statuses:
        flash("Only confirmed/packaging orders can be marked shipment ready.", "warning")
        return redirect(url_for(return_endpoint))

    now = datetime.utcnow().isoformat()

    result = mongo.orders.update_one(
        {
            "_id": oid_obj,
            "status": {"$in": list(allowed_shipment_ready_statuses)},
            "$or": [
                {"delivery_partner_id": {"$exists": False}},
                {"delivery_partner_id": None},
                {"delivery_partner_id": ""}
            ]
        },
        {
            "$set": {
                "status": "SHIPMENT_READY",
                "shipment_ready_at": now,

                # legacy timestamp kept so old pages/reports do not break
                "ready_for_pickup_at": now,

                "updated_at": now
            }
        }
    )

    if result.modified_count < 1:
        flash("This order status changed recently. Please refresh and try again.", "warning")
        return redirect(url_for(return_endpoint))

    add_order_event(
        oid_obj,
        "SHIPMENT_READY",
        "Marked shipment ready by store.",
        u
    )

    _create_store_notification(
        store,
        title="Order shipment ready",
        message=f"Order #{str(oid_obj)[-6:]} is shipment ready for delivery pickup.",
        notif_type="delivery",
        order=order,
        event_key=f"shipment-ready-{str(oid_obj)}-{now}"
    )

    flash("Order marked shipment ready.", "success")
    return redirect(url_for(return_endpoint))


@app.route('/store/orders/<oid>/assign-delivery', methods=['POST'], endpoint='store_order_assign_delivery')
@login_required(role='store')
def store_order_assign_delivery(oid):
    u, store = _get_current_store_or_redirect()

    if not store:
        flash("Store not found.", "danger")
        return redirect(request.referrer or url_for("store_delivery"))

    oid_obj, order = _get_store_owned_order(store, oid)

    if not oid_obj or not order:
        flash("Order not found for your store.", "danger")
        return redirect(request.referrer or url_for("store_delivery"))

    delivery_user_id = (request.form.get("delivery_user_id") or "").strip()

    if not delivery_user_id:
        flash("Please select a delivery boy.", "warning")
        return redirect(request.referrer or url_for("store_delivery"))
    
    status = (order.get("status") or "").strip().upper()

    if status not in ["SHIPMENT_READY", "READY_FOR_PICKUP"]:
        flash("Please mark this order shipment ready before assigning a delivery boy.", "warning")
        return redirect(request.referrer or url_for("store_delivery"))

    result = assign_delivery_partner_to_order(
        order_id=oid_obj,
        delivery_user_id=delivery_user_id,
        actor=u,
        source="store_manual",
        allow_reassign=False
    )

    if not result.get("ok"):
        flash(result.get("error") or "Could not assign delivery boy.", "danger")
        return redirect(request.referrer or url_for("store_delivery"))

    _create_store_notification(
        store,
        title="Delivery boy assigned",
        message=f"Order #{str(oid_obj)[-6:]} assigned to {result.get('delivery_partner', {}).get('name', 'delivery boy')}.",
        notif_type="delivery",
        order=order,
        event_key=f"delivery-assign-{str(oid_obj)}-{delivery_user_id}-{datetime.utcnow().isoformat()}"
    )

    flash("Delivery boy assigned successfully.", "success")
    return redirect(request.referrer or url_for("store_delivery"))


@app.route('/store/orders/<oid>/reassign-delivery', methods=['POST'], endpoint='store_order_reassign_delivery')
@login_required(role='store')
def store_order_reassign_delivery(oid):
    u, store = _get_current_store_or_redirect()

    if not store:
        flash("Store not found.", "danger")
        return redirect(url_for("store_dashboard"))

    oid_obj, order = _get_store_owned_order(store, oid)

    if not oid_obj or not order:
        flash("Order not found for your store.", "danger")
        return redirect(request.referrer or url_for("store_delivery"))

    status = (order.get("status") or "").strip().upper()

    if status in {"PICKED_UP", "OUT_FOR_DELIVERY", "DELIVERED", "CANCELLED"}:
        flash("Delivery boy cannot be changed after pickup/out for delivery.", "warning")
        return redirect(request.referrer or url_for("store_delivery"))

    delivery_user_id = (request.form.get("delivery_user_id") or "").strip()

    if not delivery_user_id:
        flash("Please select a delivery boy.", "warning")
        return redirect(request.referrer or url_for("store_delivery"))

    result = assign_delivery_partner_to_order(
        order_id=oid_obj,
        delivery_user_id=delivery_user_id,
        actor=u,
        source="store_reassign",
        allow_reassign=True
    )

    if not result.get("ok"):
        flash(result.get("error") or "Could not reassign delivery boy.", "danger")
        return redirect(request.referrer or url_for("store_delivery"))

    _create_store_notification(
        store,
        title="Delivery boy reassigned",
        message=f"Order #{str(oid_obj)[-6:]} reassigned to {result.get('delivery_partner', {}).get('name', 'delivery boy')}.",
        notif_type="delivery",
        order=order,
        event_key=f"delivery-reassign-{str(oid_obj)}-{delivery_user_id}-{datetime.utcnow().isoformat()}"
    )

    flash("Delivery boy reassigned successfully.", "success")
    return redirect(request.referrer or url_for("store_delivery"))


@app.route('/store/orders/<oid>/reschedule-failed-delivery', methods=['POST'], endpoint='store_order_reschedule_failed_delivery')
@login_required(role='store')
def store_order_reschedule_failed_delivery(oid):
    u, store = _get_current_store_or_redirect()

    if not store:
        flash("Store not found.", "danger")
        return redirect(url_for("store_dashboard"))

    oid_obj, order = _get_store_owned_order(store, oid)

    if not oid_obj or not order:
        flash("Order not found for your store.", "danger")
        return redirect(request.referrer or url_for("store_delivery"))

    status = (order.get("status") or "").strip().upper()

    if status != "DELIVERY_FAILED":
        flash("Only failed delivery orders can be rescheduled from here.", "warning")
        return redirect(request.referrer or url_for("store_delivery"))

    now = datetime.utcnow().isoformat()

    rescheduled_for = (request.form.get("rescheduled_for") or "").strip()
    reschedule_note = (request.form.get("reschedule_note") or "").strip()

    if len(rescheduled_for) > 80:
        rescheduled_for = rescheduled_for[:80]

    if len(reschedule_note) > 500:
        reschedule_note = reschedule_note[:500]

    old_partner_id = order.get("delivery_partner_id")
    old_partner_name = order.get("delivery_partner_name") or ""
    old_partner_phone = order.get("delivery_partner_phone") or ""

    history_entry = {
        "action": "delivery_failed_rescheduled_by_store",
        "previous_delivery_partner_id": str(old_partner_id) if old_partner_id else "",
        "previous_delivery_partner_name": old_partner_name,
        "previous_delivery_partner_phone": old_partner_phone,
        "failed_reason": order.get("delivery_failed_reason") or "",
        "rescheduled_for": rescheduled_for,
        "reschedule_note": reschedule_note,
        "at": now,
        "by": "store",
        "actor_id": str(u.get("_id") or u.get("id")),
        "actor_name": u.get("name") or "Store User"
    }

    mongo.orders.update_one(
        {"_id": oid_obj},
        {
            "$set": {
                "status": "READY_FOR_PICKUP",

                "delivery_partner_id": None,
                "delivery_partner_name": "",
                "delivery_partner_phone": "",
                "delivery_assignment_source": "",

                "needs_reassignment": True,
                "delivery_cancelled_by_partner": False,

                "delivery_failed_requires_store_action": False,
                "delivery_failed_store_decision": "RESCHEDULED",
                "delivery_failed_resolved_at": now,

                "delivery_rescheduled": True,
                "delivery_rescheduled_at": now,
                "delivery_rescheduled_for": rescheduled_for,
                "delivery_rescheduled_note": reschedule_note,

                "ready_for_pickup_at": now,
                "updated_at": now
            },
            "$push": {
                "delivery_history": history_entry
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
        "READY_FOR_PICKUP",
        "Failed delivery rescheduled by store. Order sent back for delivery assignment.",
        u
    )

    _create_store_notification(
        store,
        title="Failed delivery rescheduled",
        message=f"Order #{str(oid_obj)[-6:]} was rescheduled and is ready for rider assignment.",
        notif_type="delivery",
        order=order,
        event_key=f"failed-delivery-rescheduled-{str(oid_obj)}-{now}"
    )

    flash("Failed delivery has been rescheduled and sent back for rider assignment.", "success")
    return redirect(request.referrer or url_for("store_delivery"))


@app.route('/store/orders/<oid>/cancel-failed-delivery', methods=['POST'], endpoint='store_order_cancel_failed_delivery')
@login_required(role='store')
def store_order_cancel_failed_delivery(oid):
    u, store = _get_current_store_or_redirect()

    if not store:
        flash("Store not found.", "danger")
        return redirect(url_for("store_dashboard"))

    oid_obj, order = _get_store_owned_order(store, oid)

    if not oid_obj or not order:
        flash("Order not found for your store.", "danger")
        return redirect(request.referrer or url_for("store_delivery"))

    status = (order.get("status") or "").strip().upper()

    if status != "DELIVERY_FAILED":
        flash("Only failed delivery orders can be cancelled from here.", "warning")
        return redirect(request.referrer or url_for("store_delivery"))

    cancel_reason = (request.form.get("cancel_reason") or "").strip()
    cancel_note = (request.form.get("cancel_note") or "").strip()

    if not cancel_reason:
        flash("Please select/write a cancellation reason.", "warning")
        return redirect(request.referrer or url_for("store_delivery"))

    if len(cancel_reason) > 120:
        cancel_reason = cancel_reason[:120]

    if len(cancel_note) > 500:
        cancel_note = cancel_note[:500]

    now = datetime.utcnow().isoformat()

    old_partner_id = order.get("delivery_partner_id")
    old_partner_name = order.get("delivery_partner_name") or ""
    old_partner_phone = order.get("delivery_partner_phone") or ""

    history_entry = {
        "action": "delivery_failed_cancelled_by_store",
        "previous_delivery_partner_id": str(old_partner_id) if old_partner_id else "",
        "previous_delivery_partner_name": old_partner_name,
        "previous_delivery_partner_phone": old_partner_phone,
        "failed_reason": order.get("delivery_failed_reason") or "",
        "cancel_reason": cancel_reason,
        "cancel_note": cancel_note,
        "at": now,
        "by": "store",
        "actor_id": str(u.get("_id") or u.get("id")),
        "actor_name": u.get("name") or "Store User"
    }

    mongo.orders.update_one(
        {"_id": oid_obj},
        {
            "$set": {
                "status": "CANCELLED",

                "cancelled_by": "store",
                "cancelled_by_role": "store",
                "cancelled_by_id": str(u.get("_id") or u.get("id")),
                "cancelled_by_name": u.get("name") or "Store User",
                "cancel_reason": cancel_reason,
                "cancellation_reason": cancel_reason,
                "cancel_note": cancel_note,
                "cancelled_at": now,

                "delivery_failed_requires_store_action": False,
                "delivery_failed_store_decision": "CANCELLED",
                "delivery_failed_resolved_at": now,

                "updated_at": now
            },
            "$push": {
                "delivery_history": history_entry
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
        "CANCELLED",
        f"Order cancelled by store after failed delivery. Reason: {cancel_reason}",
        u
    )

    _create_store_notification(
        store,
        title="Order cancelled after failed delivery",
        message=f"Order #{str(oid_obj)[-6:]} was cancelled after failed delivery. Reason: {cancel_reason}",
        notif_type="delivery",
        order=order,
        event_key=f"failed-delivery-cancelled-{str(oid_obj)}-{now}"
    )

    flash("Order cancelled after failed delivery.", "success")
    return redirect(request.referrer or url_for("store_delivery"))


@app.route('/store/orders/<oid>/clear-delivery', methods=['POST'], endpoint='store_order_clear_delivery')
@login_required(role='store')
def store_order_clear_delivery(oid):
    return_endpoint = "store_active_orders" if (request.form.get("return_to") or "").strip().lower() == "active_orders" else "store_orders"
    u, store = _get_current_store_or_redirect()

    if not store:
        flash("Store not found.", "danger")
        return redirect(url_for("store_dashboard"))

    oid_obj, order = _get_store_owned_order(store, oid)

    if not oid_obj or not order:
        flash("Order not found for your store.", "danger")
        return redirect(url_for(return_endpoint))

    result = clear_delivery_assignment(
        order_id=oid_obj,
        actor=u,
        reason="Delivery assignment cleared by store."
    )

    if not result.get("ok"):
        flash(result.get("error") or "Could not clear delivery assignment.", "danger")
        return redirect(url_for(return_endpoint))

    _create_store_notification(
        store,
        title="Delivery assignment cleared",
        message=f"Delivery assignment cleared for order #{str(oid_obj)[-6:]}.",
        notif_type="delivery",
        order=order,
        event_key=f"delivery-clear-{str(oid_obj)}-{datetime.utcnow().isoformat()}"
    )

    flash("Delivery assignment cleared.", "success")
    return redirect(url_for(return_endpoint))


@app.route('/store/orders/<oid>/delivery-options', methods=['GET'], endpoint='store_order_delivery_options')
@login_required(role='store')
def store_order_delivery_options(oid):
    u, store = _get_current_store_or_redirect()

    if not store:
        return jsonify({
            "ok": False,
            "error": "Store not found."
        }), 404

    oid_obj, order = _get_store_owned_order(store, oid)

    if not oid_obj or not order:
        return jsonify({
            "ok": False,
            "error": "Order not found for your store."
        }), 404

    people = _hydrate_store_delivery_people_for_template(store)

    return jsonify({
        "ok": True,
        "order_id": str(oid_obj),
        "delivery_people": people
    })


@app.route('/store/delivery-history', methods=['GET'], endpoint='store_delivery_history')
@login_required(role='store')
def store_delivery_history_page():
    """
    Store Delivery Boy History.

    This keeps the existing route and endpoint:
        /store/delivery-history
        endpoint='store_delivery_history'

    It shows delivery-boy-wise history only for the currently logged-in store.
    It is read-only and does not affect delivery assignment/reassignment/cancel flows.
    """
    u, store = _get_current_store_or_redirect()

    if not store:
        flash("Store not found.", "danger")
        return redirect(url_for("store_dashboard"))

    store_id = store.get("_id")
    store_id_str = str(store_id)
    store_name = (store.get("store_name") or store.get("name") or "").strip().lower()

    q = (request.args.get("q") or "").strip()
    status_filter = (request.args.get("status") or "").strip().upper()
    delivery_user_filter = (request.args.get("delivery_user_id") or "").strip()
    payment_type_filter = (request.args.get("payment_type") or "").strip().upper()
    date_from = (request.args.get("from") or "").strip()
    date_to = (request.args.get("to") or "").strip()

    allowed_history_statuses = {
        "DELIVERED",
        "DELIVERY_FAILED",
        "CANCELLED",
        "READY_FOR_PICKUP",
        "ASSIGNED_TO_DELIVERY",
        "ACCEPTED_BY_DELIVERY_MAN",
        "REACHED_STORE",
        "PICKED_UP",
        "OUT_FOR_DELIVERY"
    }

    def _store_history_float(value, default=0.0):
        try:
            if value is None or str(value).strip() == "":
                return float(default)
            return float(value)
        except Exception:
            return float(default)

    def _store_history_belongs_to_store(order):
        order_store_id = order.get("store_id")
        order_store_name = (order.get("store_name") or "").strip().lower()

        if order_store_id and str(order_store_id) == store_id_str:
            return True

        if store_name and order_store_name and order_store_name == store_name:
            return True

        return False

    def _store_history_entries(order):
        entries = order.get("delivery_history") or []
        return [entry for entry in entries if isinstance(entry, dict)]

    def _store_history_latest_action(order, action_name):
        matched = []

        for entry in _store_history_entries(order):
            if entry.get("action") == action_name:
                matched.append(entry)

        return matched[-1] if matched else {}

    def _store_history_latest_value(order, *keys):
        for entry in reversed(_store_history_entries(order)):
            for key in keys:
                value = entry.get(key)
                if value not in [None, ""]:
                    return value

        return ""

    def _store_history_effective_partner_id(order):
        return (
            order.get("delivery_partner_id")
            or order.get("previous_delivery_partner_id")
            or _store_history_latest_value(
                order,
                "delivery_partner_id",
                "previous_delivery_partner_id",
                "old_delivery_partner_id"
            )
            or ""
        )

    def _store_history_effective_partner_name(order):
        return (
            order.get("delivery_partner_name")
            or order.get("previous_delivery_partner_name")
            or _store_history_latest_value(
                order,
                "delivery_partner_name",
                "previous_delivery_partner_name",
                "old_delivery_partner_name"
            )
            or "Unknown Delivery Boy"
        )

    def _store_history_effective_partner_phone(order):
        return (
            order.get("delivery_partner_phone")
            or order.get("previous_delivery_partner_phone")
            or _store_history_latest_value(
                order,
                "delivery_partner_phone",
                "previous_delivery_partner_phone",
                "old_delivery_partner_phone"
            )
            or ""
        )

    def _store_history_has_rider_cancel(order):
        if order.get("delivery_cancelled_by_partner"):
            return True

        if order.get("delivery_cancelled_at") or order.get("delivery_cancel_reason"):
            return True

        if _store_history_latest_action(order, "cancelled_by_delivery_partner"):
            return True

        return False

    def _store_history_record_at(order):
        rider_cancel_entry = _store_history_latest_action(order, "cancelled_by_delivery_partner")

        return (
            order.get("delivered_at")
            or order.get("delivery_failed_at")
            or rider_cancel_entry.get("at")
            or order.get("delivery_cancelled_at")
            or order.get("out_for_delivery_at")
            or order.get("picked_up_at")
            or order.get("reached_store_at")
            or order.get("delivery_assigned_at")
            or order.get("assigned_at")
            or order.get("updated_at")
            or order.get("created_at")
            or ""
        )

    def _store_history_apply_status_label(row, has_rider_cancel_history):
        status = (row.get("status") or "").strip().upper()

        if has_rider_cancel_history and status in {
            "READY_FOR_PICKUP",
            "CANCELLED"
        }:
            row["history_type"] = "rider_cancelled"
            row["history_label"] = "Rider Cancelled Assignment"

        elif status == "DELIVERED":
            row["history_type"] = "delivered"
            row["history_label"] = "Delivered"

        elif status == "DELIVERY_FAILED":
            row["history_type"] = "failed"
            row["history_label"] = "Delivery Failed"

        elif status in {
            "ASSIGNED_TO_DELIVERY",
            "ACCEPTED_BY_DELIVERY_MAN",
            "REACHED_STORE",
            "PICKED_UP",
            "OUT_FOR_DELIVERY"
        }:
            row["history_type"] = "active"
            row["history_label"] = "Active Delivery"

        elif status == "READY_FOR_PICKUP":
            row["history_type"] = "ready"
            row["history_label"] = "Ready For Pickup"

        elif status == "CANCELLED":
            row["history_type"] = "cancelled"
            row["history_label"] = "Cancelled"

        else:
            row["history_type"] = "record"
            row["history_label"] = status.replace("_", " ").title() if status else "Record"

        return row

    base_query = {
        "$or": [
            {"store_id": store_id},
            {"store_id": store_id_str},
            {"store_name": store.get("store_name")},
            {"store_name": store.get("name")}
        ]
    }

    raw_orders = list(
        mongo.orders.find(base_query).sort("updated_at", -1)
    )

    history_orders = []
    delivery_people_map = {}
    rider_summary_map = {}

    for order in raw_orders:
        if not _store_history_belongs_to_store(order):
            continue

        status = (order.get("status") or "").strip().upper()
        has_rider_cancel_history = _store_history_has_rider_cancel(order)

        has_delivery_activity = bool(
            order.get("delivery_partner_id")
            or order.get("previous_delivery_partner_id")
            or order.get("delivery_history")
            or status in allowed_history_statuses
            or has_rider_cancel_history
        )

        if not has_delivery_activity:
            continue

        if status not in allowed_history_statuses and not has_rider_cancel_history:
            continue

        effective_partner_id = _store_history_effective_partner_id(order)

        if not effective_partner_id:
            continue

        effective_partner_id_str = str(effective_partner_id)

        if delivery_user_filter and effective_partner_id_str != delivery_user_filter:
            continue

        payment_method = (order.get("payment_method") or "COD").strip().upper()

        if payment_type_filter == "COD" and payment_method != "COD":
            continue

        if payment_type_filter == "ONLINE" and payment_method == "COD":
            continue

        row = dict(order)
        row["id"] = str(row.get("_id") or "")
        row["delivery_partner_id"] = effective_partner_id_str
        row["delivery_partner_id_str"] = effective_partner_id_str
        row["delivery_partner_name"] = _store_history_effective_partner_name(order)
        row["delivery_partner_phone"] = _store_history_effective_partner_phone(order)

        customer = None

        if row.get("user_id"):
            try:
                customer = mongo.users.find_one({"_id": ObjectId(str(row.get("user_id")))})
            except Exception:
                customer = None

        row["customer_name"] = (
            row.get("customer_name")
            or (customer.get("name") if customer else "")
            or "Customer"
        )

        row["customer_phone"] = (
            row.get("customer_phone")
            or (customer.get("phone") if customer else "")
            or ""
        )

        row = _decorate_store_delivery_order(row)

        row["delivery_partner_id"] = effective_partner_id_str
        row["delivery_partner_id_str"] = effective_partner_id_str
        row["delivery_partner_name"] = row.get("delivery_partner_name") or _store_history_effective_partner_name(order)
        row["delivery_partner_phone"] = row.get("delivery_partner_phone") or _store_history_effective_partner_phone(order)

        record_at = _store_history_record_at(order)
        row["record_at"] = record_at

        row = _store_history_apply_status_label(row, has_rider_cancel_history)

        if status_filter:
            if status_filter == "RIDER_CANCELLED":
                if row.get("history_type") != "rider_cancelled":
                    continue
            elif status_filter != status and status_filter != (row.get("history_type") or "").upper():
                continue

        if date_from and record_at and str(record_at)[:10] < date_from:
            continue

        if date_to and record_at and str(record_at)[:10] > date_to:
            continue

        if q:
            haystack = " ".join([
                str(row.get("id") or ""),
                str(row.get("customer_name") or ""),
                str(row.get("customer_phone") or ""),
                str(row.get("delivery_partner_name") or ""),
                str(row.get("delivery_partner_phone") or ""),
                str(row.get("history_label") or ""),
                str(row.get("payment_method") or ""),
                str(row.get("payment_status") or ""),
                str(row.get("delivery_failed_reason") or ""),
                str(row.get("delivery_failed_note") or ""),
                str(row.get("rider_cancel_reason") or "")
            ]).lower()

            if q.lower() not in haystack:
                continue

        delivery_people_map[effective_partner_id_str] = {
            "id": effective_partner_id_str,
            "name": row.get("delivery_partner_name") or "Delivery Boy",
            "phone": row.get("delivery_partner_phone") or ""
        }

        if effective_partner_id_str not in rider_summary_map:
            rider_summary_map[effective_partner_id_str] = {
                "delivery_partner_id": effective_partner_id_str,
                "delivery_partner_name": row.get("delivery_partner_name") or "Delivery Boy",
                "delivery_partner_phone": row.get("delivery_partner_phone") or "",
                "total_orders": 0,
                "delivered": 0,
                "failed": 0,
                "rider_cancelled": 0,
                "active": 0,
                "cancelled": 0,
                "cod_to_collect": 0.0,
                "delivery_fee": 0.0,
                "tip": 0.0,
                "delivery_earning": 0.0,
                "platform_fee": 0.0,
                "store_earning": 0.0,
                "last_record_at": "",
            }

        rider_row = rider_summary_map[effective_partner_id_str]

        rider_row["total_orders"] += 1

        if row.get("history_type") == "delivered":
            rider_row["delivered"] += 1
        elif row.get("history_type") == "failed":
            rider_row["failed"] += 1
        elif row.get("history_type") == "rider_cancelled":
            rider_row["rider_cancelled"] += 1
        elif row.get("history_type") == "active":
            rider_row["active"] += 1
        elif row.get("history_type") == "cancelled":
            rider_row["cancelled"] += 1

        rider_row["cod_to_collect"] += _store_history_float(row.get("amount_to_collect"))
        rider_row["delivery_fee"] += _store_history_float(row.get("delivery_fee"))
        rider_row["tip"] += _store_history_float(row.get("tip_amount"))
        rider_row["delivery_earning"] += _store_history_float(row.get("delivery_fee_plus_tip"))
        rider_row["platform_fee"] += _store_history_float(row.get("platform_fee"))
        rider_row["store_earning"] += _store_history_float(row.get("store_earning"))

        if record_at and str(record_at) > str(rider_row.get("last_record_at") or ""):
            rider_row["last_record_at"] = record_at

        history_orders.append(row)

    history_orders.sort(
        key=lambda x: str(x.get("record_at") or ""),
        reverse=True
    )

    rider_summary_rows = list(rider_summary_map.values())

    for rider_row in rider_summary_rows:
        rider_row["cod_to_collect"] = round(_store_history_float(rider_row.get("cod_to_collect")), 2)
        rider_row["delivery_fee"] = round(_store_history_float(rider_row.get("delivery_fee")), 2)
        rider_row["tip"] = round(_store_history_float(rider_row.get("tip")), 2)
        rider_row["delivery_earning"] = round(_store_history_float(rider_row.get("delivery_earning")), 2)
        rider_row["platform_fee"] = round(_store_history_float(rider_row.get("platform_fee")), 2)
        rider_row["store_earning"] = round(_store_history_float(rider_row.get("store_earning")), 2)

    rider_summary_rows.sort(
        key=lambda x: (
            str(x.get("last_record_at") or ""),
            int(x.get("total_orders") or 0)
        ),
        reverse=True
    )

    history_metrics = {
        "total": len(history_orders),
        "total_delivery_boys": len(rider_summary_rows),
        "delivered": sum(1 for r in history_orders if r.get("history_type") == "delivered"),
        "failed": sum(1 for r in history_orders if r.get("history_type") == "failed"),
        "rider_cancelled": sum(1 for r in history_orders if r.get("history_type") == "rider_cancelled"),
        "active": sum(1 for r in history_orders if r.get("history_type") == "active"),
        "cancelled": sum(1 for r in history_orders if r.get("history_type") == "cancelled"),
        "cod_to_collect": round(sum(_store_history_float(r.get("amount_to_collect")) for r in history_orders), 2),
        "delivery_fee": round(sum(_store_history_float(r.get("delivery_fee")) for r in history_orders), 2),
        "tip": round(sum(_store_history_float(r.get("tip_amount")) for r in history_orders), 2),
        "delivery_earning": round(sum(_store_history_float(r.get("delivery_fee_plus_tip")) for r in history_orders), 2),
        "platform_fee": round(sum(_store_history_float(r.get("platform_fee")) for r in history_orders), 2),
        "store_earning": round(sum(_store_history_float(r.get("store_earning")) for r in history_orders), 2),
    }

    store_view = dict(store)
    store_view["id"] = str(store["_id"])

    return render_template(
        "store_delivery_history.html",
        user=u,
        store=store_view,
        orders=history_orders,
        rider_summary_rows=rider_summary_rows,
        delivery_people=list(delivery_people_map.values()),
        history_metrics=history_metrics,
        q=q,
        status_filter=status_filter,
        delivery_user_filter=delivery_user_filter,
        payment_type_filter=payment_type_filter,
        date_from=date_from,
        date_to=date_to,
        active_page="delivery_history"
    )
