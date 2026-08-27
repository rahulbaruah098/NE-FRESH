"""Store orders route section.

Route decorators, endpoint names, URL paths, HTTP methods and function bodies are
preserved from the Step 6 source. Shared legacy helpers are imported from
``routes.store.shared`` during this transitional decomposition.
"""

from routes.store.shared import *

@app.route('/store/orders', methods=['GET'], endpoint='store_orders')
@login_required(role='store')
def store_orders_page():
    u, store = _get_current_store_or_redirect()

    if not store:
        return redirect(url_for("store_dashboard"))

    page_context = _build_store_split_page_context(store) or {}

    available_delivery_people = _hydrate_store_delivery_people_for_template(store)

    decorated_orders = []

    for order in page_context.get("orders") or []:
        if not store_order_visible_to_store(order):
            continue

        row = dict(order)

        row["_id"] = row.get("_id") or row.get("id")
        row["id"] = str(row.get("_id") or row.get("id") or "")

        # Ensure old/new order money fields are safely available in Store Orders page.
        row = _decorate_store_delivery_order(row)

        decorated_orders.append(row)

    page_context["orders"] = decorated_orders
    page_context["available_delivery_people"] = available_delivery_people
    page_context["delivery_accept_radius_km"] = DELIVERY_ACCEPT_RADIUS_KM

    return render_template(
        "store_orders.html",
        user=u,
        store=store,
        **page_context
    )


@app.route('/store/active-orders', methods=['GET'], endpoint='store_active_orders')
@login_required(role='store')
def store_active_orders_page():
    """Show only orders that are still in the active fulfilment/delivery workflow."""
    u, store = _get_current_store_or_redirect()

    if not store:
        return redirect(url_for("store_dashboard"))

    page_context = _build_store_split_page_context(store) or {}
    available_delivery_people = _hydrate_store_delivery_people_for_template(store)

    active_orders = []
    for order in page_context.get("orders") or []:
        if not store_order_visible_to_store(order):
            continue

        row = dict(order)
        row["_id"] = row.get("_id") or row.get("id")
        row["id"] = str(row.get("_id") or row.get("id") or "")
        row = _decorate_store_delivery_order(row)

        # Missing legacy status is treated like the normal Store default (PLACED)
        # rather than disappearing from the active-work queue.
        if is_store_order_active(row):
            active_orders.append(row)

    page_context["orders"] = active_orders
    page_context["active_order_count"] = len(active_orders)
    page_context["available_delivery_people"] = available_delivery_people
    page_context["delivery_accept_radius_km"] = DELIVERY_ACCEPT_RADIUS_KM

    return render_template(
        "store_active_orders.html",
        user=u,
        store=store,
        **page_context
    )


@app.route('/store/orders/<oid>/track', methods=['GET'], endpoint='store_order_track')
@login_required(role='store')
def store_order_track_page(oid):
    """Store-contained order tracking page.

    This deliberately does not reuse the customer order_track template/shell.
    The order is first verified against the signed-in Store, then the shared
    read-only order data is hydrated for Store operations.
    """
    u, store = _get_current_store_or_redirect()

    if not store:
        flash("Store not found.", "danger")
        return redirect(url_for("store_dashboard"))

    oid_obj, owned_order = _get_store_owned_order(store, oid)
    if not oid_obj or not owned_order:
        flash("Order not found for your store.", "danger")
        return redirect(url_for("store_orders"))

    data = get_order_full(str(oid_obj))
    if not data or not data.get("order"):
        flash("Order tracking information is unavailable.", "warning")
        return redirect(url_for("store_orders"))

    order = dict(data.get("order") or {})
    order["_id"] = order.get("_id") or oid_obj
    order["id"] = str(order.get("_id") or oid_obj)
    order = _decorate_store_delivery_order(order)
    public_order_number = str(order.get("order_number") or order.get("display_order_number") or "").strip()
    order["order_number"] = str(order.get("order_number") or "").strip()
    order["display_order_number"] = public_order_number or f"#{order['id']}"

    # Customer name/phone can be absent on old orders. Fill display-only values
    # from the owning customer record without mutating the order.
    if not order.get("customer_name") or not order.get("customer_phone"):
        customer = None
        customer_id = order.get("user_id")
        if customer_id:
            try:
                customer = mongo.users.find_one({"_id": ObjectId(str(customer_id))})
            except Exception:
                try:
                    customer = mongo.users.find_one({"_id": str(customer_id)})
                except Exception:
                    customer = None
        customer = customer or {}
        order["customer_name"] = order.get("customer_name") or customer.get("name") or customer.get("username") or "Customer"
        order["customer_phone"] = order.get("customer_phone") or customer.get("phone") or customer.get("contact") or ""

    data["order"] = order
    available_delivery_people = _hydrate_store_delivery_people_for_template(store)

    return render_template(
        "store_order_track.html",
        user=u,
        store=store,
        available_delivery_people=available_delivery_people,
        **data
    )


@app.route("/store/cancelled-orders")
@app.route("/store/cancelled-orders/<cancel_type>")
@login_required(role="store")
def store_cancelled_orders(cancel_type="customer"):
    u = current_user()

    store = mongo.stores.find_one({
        "user_id": u["id"]
    })

    if not store:
        flash("Store not found.", "danger")
        return redirect(url_for("store_dashboard"))

    cancel_type = (cancel_type or "customer").strip().lower()

    if cancel_type not in ["customer", "store"]:
        cancel_type = "customer"

    store_id_values = [
        store["_id"],
        str(store["_id"])
    ]

    base_query = {
        "$and": [
            {
                "store_id": {
                    "$in": store_id_values
                }
            },
            {
                "status": {
                    "$in": ["CANCELLED", "CANCELED"]
                }
            },
            _store_cancelled_source_query(cancel_type)
        ]
    }

    cancelled_orders = list(
        mongo.orders.find(base_query).sort("cancelled_at", -1)
    )

    prepared_orders = [
        _store_prepare_cancelled_order_row(order, store)
        for order in cancelled_orders
    ]

    customer_cancelled_count = mongo.orders.count_documents({
        "$and": [
            {
                "store_id": {
                    "$in": store_id_values
                }
            },
            {
                "status": {
                    "$in": ["CANCELLED", "CANCELED"]
                }
            },
            _store_cancelled_source_query("customer")
        ]
    })

    store_cancelled_count = mongo.orders.count_documents({
        "$and": [
            {
                "store_id": {
                    "$in": store_id_values
                }
            },
            {
                "status": {
                    "$in": ["CANCELLED", "CANCELED"]
                }
            },
            _store_cancelled_source_query("store")
        ]
    })

    total_cancelled_count = customer_cancelled_count + store_cancelled_count

    total_cancelled_value = round(
        sum(float(order.get("total_payable") or 0) for order in prepared_orders),
        2
    )

    online_refund_pending_count = sum(
        1
        for order in prepared_orders
        if order.get("refund_status") in [
            "READY_FOR_REFUND",
            "REFUND_PENDING",
            "NOT_STARTED",
            "PENDING"
        ]
    )

    cod_void_count = sum(
        1
        for order in prepared_orders
        if order.get("payment_method") in [
            "COD",
            "CASH_ON_DELIVERY",
            "COD_RIDER_COLLECTION"
        ]
        and order.get("payment_status") in [
            "VOID",
            "CANCELLED",
            "PENDING"
        ]
    )

    return render_template(
        "store_cancelled_orders.html",
        user=u,
        store=store,
        orders=prepared_orders,
        cancel_type=cancel_type,
        customer_cancelled_count=customer_cancelled_count,
        store_cancelled_count=store_cancelled_count,
        total_cancelled_count=total_cancelled_count,
        current_cancelled_count=len(prepared_orders),
        total_cancelled_value=total_cancelled_value,
        online_refund_pending_count=online_refund_pending_count,
        cod_void_count=cod_void_count
    )


@app.route('/store/order/<oid>/status', methods=['POST'])
@app.route('/store/orders/<oid>/status', methods=['POST'])
@login_required(role='store')
def store_order_status(oid):
    return_endpoint = "store_active_orders" if (request.form.get("return_to") or "").strip().lower() == "active_orders" else "store_orders"
    u = current_user()

    store = mongo.stores.find_one({"user_id": u["id"]})

    if not store:
        flash("Store not found.", "danger")
        return redirect(url_for("store_dashboard"))

    try:
        oid_obj = ObjectId(oid)
    except Exception:
        flash("Invalid order.", "danger")
        return redirect(url_for(return_endpoint))

    order = mongo.orders.find_one({
        "_id": oid_obj,
        "$or": [
            {"store_id": store["_id"]},
            {"store_id": str(store["_id"])}
        ]
    })

    if not order:
        flash("Order not found.", "danger")
        return redirect(url_for(return_endpoint))

    current_status = (order.get("status") or "").strip().upper()
    new_status = (request.form.get("status") or "").strip().upper()
    now = datetime.utcnow().isoformat()

    if current_status in {"CANCELLED", "CANCELED", "DELIVERED"}:
        flash("This order can no longer be updated.", "warning")
        return redirect(url_for(return_endpoint))

    # Delivery workflow statuses must not be controlled by the normal order dropdown.
    delivery_locked_statuses = {
        "READY_FOR_PICKUP",
        "SHIPMENT_READY",
        "ASSIGNED_TO_DELIVERY",
        "ACCEPTED_BY_DELIVERY_MAN",
        "REACHED_STORE",
        "PICKED_UP",
        "OUT_FOR_DELIVERY",
        "DELIVERY_FAILED",
        "DELIVERED"
    }

    if current_status in delivery_locked_statuses:
        flash("This order is controlled by the delivery workflow. Use Delivery Control actions.", "warning")
        return redirect(url_for(return_endpoint))

    allowed_statuses = {
        "PLACED",
        "CONFIRMED",
        "PREPARING",
        "PACKAGING",
        "CANCELLED",
    }

    if new_status not in allowed_statuses:
        flash("Invalid order status selected.", "warning")
        return redirect(url_for(return_endpoint))

    update_data = {
        "status": new_status,
        "updated_at": now
    }

    unset_data = {}

    if new_status == "PREPARING":
        update_data["preparing_at"] = now

    if new_status == "PACKAGING":
        update_data["packaging_at"] = now

    if new_status == "CANCELLED":
        # Restore stock only once because already-cancelled orders are blocked above.
        # Use the shared reservation/restoration service so bundle child stock is
        # restored exactly the same way as customer cancellation.
        order_items = list(mongo.order_items.find({
            "$or": [
                {"order_id": oid_obj},
                {"order_id": str(oid_obj)}
            ]
        }))
        _release_order_stock_items(order_items)

        payment_method = (order.get("payment_method") or "COD").strip().upper()
        payment_status = (order.get("payment_status") or "PENDING").strip().upper()

        is_cod_order = payment_method in {
            "COD",
            "CASH_ON_DELIVERY",
            "COD_RIDER_COLLECTION"
        }

        is_online_paid = (
            payment_method not in {
                "COD",
                "CASH_ON_DELIVERY",
                "COD_RIDER_COLLECTION"
            }
            and payment_status in {
                "PAID",
                "ONLINE_PAID",
                "SUCCESS"
            }
        )

        update_data.update({
            "status": "CANCELLED",
            "cancelled_at": now,
            "cancelled_by": "store",
            "cancelled_by_role": "store",
            "cancelled_by_id": str(u.get("_id") or u.get("id")),
            "cancelled_by_name": u.get("name") or "Store User",
            "cancel_reason": request.form.get("cancel_reason") or "Cancelled by store",
            "cancellation_reason": request.form.get("cancel_reason") or "Cancelled by store",

            # Make sure order does not remain in active delivery/store queue.
            "delivery_status": "CANCELLED",
            "delivery_fulfillment_status": "CANCELLED",
            "ready_for_pickup": False,
            "shipment_ready": False,
            "needs_reassignment": False,
            "delivery_cancelled_by_partner": False,

            # Internal delivery settlement should not remain active.
            "delivery_boy_earning": 0,
            "delivery_boy_payout_amount": 0,
            "delivery_boy_payout_status": "NOT_REQUIRED",
            "rider_cash_to_submit": 0,
            "expected_rider_cash_to_submit": 0,
            "rider_cash_settlement_status": "NOT_REQUIRED",
            "cod_collection_status": "NOT_REQUIRED",

            "updated_at": now
        })

        if is_online_paid:
            update_data.update({
                "refund_status": "READY_FOR_REFUND",
                "refund_reason": "STORE_CANCELLED_BEFORE_DELIVERY",
                "order_settlement_status": "REFUND_PENDING",
                "payment_collection_status": "PAID_REFUND_PENDING",
                "transaction_status": "REFUND_PENDING"
            })
        elif is_cod_order:
            update_data.update({
                "payment_status": "VOID",
                "refund_status": "NOT_REQUIRED",
                "refund_reason": "COD_CANCELLED_BEFORE_PAYMENT",
                "order_settlement_status": "CANCELLED_VOID",
                "payment_collection_status": "VOID",
                "transaction_status": "VOID",
                "platform_fee_status": "NOT_REQUIRED",
                "store_payout_status": "NOT_REQUIRED"
            })
        else:
            update_data.update({
                "refund_status": "NOT_REQUIRED",
                "order_settlement_status": "CANCELLED",
                "payment_collection_status": "CANCELLED",
                "transaction_status": "CANCELLED"
            })

        unset_data.update({
            "delivery_partner_id": "",
            "delivery_partner_name": "",
            "delivery_partner_phone": "",
            "delivery_assignment_source": "",
            "assigned_at": "",
            "delivery_assigned_at": "",
            "reached_store_at": "",
            "picked_up_at": "",
            "out_for_delivery_at": "",
            "shipment_ready_at": "",
            "ready_for_pickup_at": ""
        })

    update_payload = {
        "$set": update_data
    }

    if unset_data:
        update_payload["$unset"] = unset_data

    mongo.orders.update_one(
        {
            "_id": oid_obj,
            "$or": [
                {"store_id": store["_id"]},
                {"store_id": str(store["_id"])}
            ]
        },
        update_payload
    )

    if new_status == "CANCELLED":
        # Stop any active delivery notifications for this order.
        mongo.delivery_notifications.update_many(
            {
                "$or": [
                    {"order_id": oid_obj},
                    {"order_id": str(oid_obj)}
                ]
            },
            {
                "$set": {
                    "is_active": False,
                    "updated_at": now,
                    "closed_reason": "ORDER_CANCELLED_BY_STORE"
                }
            }
        )

        # Keep transactions aligned for reports.
        mongo.transactions.update_many(
            {
                "$or": [
                    {"order_id": oid_obj},
                    {"order_id": str(oid_obj)}
                ]
            },
            {
                "$set": {
                    "status": update_data.get("transaction_status", "CANCELLED"),
                    "payment_status": update_data.get("payment_status", payment_status),
                    "payment_collection_status": update_data.get("payment_collection_status", "CANCELLED"),
                    "order_settlement_status": update_data.get("order_settlement_status", "CANCELLED"),
                    "refund_status": update_data.get("refund_status", "NOT_REQUIRED"),
                    "updated_at": now
                }
            }
        )

    add_order_event(
        oid_obj,
        new_status,
        "Cancelled by store" if new_status == "CANCELLED" else "Updated by store",
        u
    )

    _create_store_notification(
        store,
        title="Order cancelled" if new_status == "CANCELLED" else "Order status updated",
        message=(
            f"Order #{str(order['_id'])[-6:]} was cancelled by store."
            if new_status == "CANCELLED"
            else f"Order #{str(order['_id'])[-6:]} status changed to {new_status}."
        ),
        notif_type="order",
        order=order,
        event_key=f"store-status-{str(order['_id'])}-{new_status}-{now}"
    )

    if new_status == "CANCELLED":
        flash("Order cancelled successfully and removed from active queue.", "success")
    else:
        flash("Order status updated successfully.", "success")

    return redirect(url_for(return_endpoint))
