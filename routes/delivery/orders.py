"""Delivery orders route section.

Route decorators, endpoint names, URL paths, HTTP methods and function bodies are
preserved from the Step 6 source. Shared legacy helpers are imported from
``routes.delivery.shared`` during this transitional decomposition.
"""

from routes.delivery.shared import *

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
        o = _decorate_delivery_financials(_hydrate_delivery_order(o))

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
        o = _decorate_delivery_financials(_hydrate_delivery_order(o))

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
    total_platform_fee = 0
    total_expected_earning = 0

    for o in orders:
        total_payable += float(o.get("total_payable") or 0)
        total_delivery_fee += float(o.get("delivery_fee") or 0)
        total_tip += float(o.get("tip_amount") or 0)
        total_platform_fee += float(o.get("platform_fee") or 0)
        total_expected_earning += float(o.get("delivery_boy_expected_earning") or 0)
        total_cod_collected += float(o.get("cod_collected_amount") or 0)

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
        total_payable=total_payable,
        total_platform_fee=total_platform_fee,
        total_expected_earning=total_expected_earning
    )


@app.route('/delivery/cod-settlements', methods=['GET'], endpoint='delivery_cod_settlements')
@login_required(role='delivery')
def delivery_cod_settlements():
    """
    Delivery-boy read-only COD settlement view.

    Important:
    - Delivery boy can view COD collected, monthly earning, and the full business cash to submit.
    - Delivery boy cannot mark cash submitted/received.
    - Admin marks rider cash received from Admin Payment & Settlements.
    """
    u = current_user()

    availability = _get_delivery_availability(u["id"])
    delivery_active = bool(availability.get("active"))

    q = (request.args.get("q") or "").strip()
    status_filter = (request.args.get("status") or "").strip().upper()
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
                },
                {
                    "payment_method": "COD"
                },
                {
                    "payment_status": {"$in": ["COLLECTED_BY_RIDER", "COD_COLLECTED_BY_RIDER"]}
                },
                {
                    "payment_collection_channel": {"$ne": "UPI"}
                }
            ]
        }).sort("delivered_at", -1)
    )

    rows = []

    for o in raw_orders:
        o = _decorate_delivery_financials(_hydrate_delivery_order(o))

        delivered_at = str(o.get("delivered_at") or o.get("updated_at") or "")

        if date_from and delivered_at and delivered_at[:10] < date_from:
            continue

        if date_to and delivered_at and delivered_at[:10] > date_to:
            continue

        rider_status = (o.get("rider_cash_settlement_status") or "").strip().upper()

        if status_filter:
            if status_filter == "PENDING":
                if rider_status in ["RECEIVED", "PAID", "SETTLED"]:
                    continue
            elif status_filter == "RECEIVED":
                if rider_status != "RECEIVED":
                    continue
            elif status_filter != rider_status:
                continue

        if q:
            haystack = " ".join([
                str(o.get("id") or ""),
                str(o.get("store_name") or ""),
                str(o.get("customer_name") or ""),
                str(o.get("customer_phone") or ""),
                str(o.get("payment_status") or ""),
                str(o.get("rider_cash_settlement_status") or ""),
                str(o.get("order_settlement_status") or "")
            ]).lower()

            if q.lower() not in haystack:
                continue

        rows.append(o)

    pending_rows = [
        r for r in rows
        if (r.get("rider_cash_settlement_status") or "").upper() not in ["RECEIVED", "PAID", "SETTLED"]
    ]

    received_rows = [
        r for r in rows
        if (r.get("rider_cash_settlement_status") or "").upper() in ["RECEIVED", "PAID", "SETTLED"]
    ]

    monthly_rows = [
        r for r in rows
        if bool(r.get("is_monthly_delivery_payout"))
    ]
    legacy_rows = [
        r for r in rows
        if not bool(r.get("is_monthly_delivery_payout"))
    ]

    metrics = {
        "total_orders": len(rows),
        "cod_collected": round(sum(float(r.get("cod_collected_amount") or 0) for r in rows), 2),
        "delivery_earning": round(sum(float(r.get("delivery_boy_earning") or 0) for r in monthly_rows), 2),
        "legacy_delivery_earning": round(sum(float(r.get("delivery_boy_earning") or 0) for r in legacy_rows), 2),
        "monthly_orders": len(monthly_rows),
        "legacy_orders": len(legacy_rows),
        "cash_to_submit": round(sum(float(r.get("rider_cash_to_submit") or 0) for r in rows), 2),
        "pending_cash": round(sum(float(r.get("rider_cash_to_submit") or 0) for r in pending_rows), 2),
        "received_cash": round(sum(float(r.get("rider_cash_to_submit") or 0) for r in received_rows), 2),
        "pending_orders": len(pending_rows),
        "received_orders": len(received_rows),
    }

    return render_template(
        "delivery_cod_settlements.html",
        user=u,
        orders=rows,
        metrics=metrics,
        delivery_active=delivery_active,
        delivery_availability=availability,
        q=q,
        status_filter=status_filter,
        date_from=date_from,
        date_to=date_to
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
        o = _decorate_delivery_financials(_hydrate_delivery_order(o))

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
        o = _decorate_delivery_financials(_hydrate_delivery_order(o)) 

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
        "cancelled_by_me": sum(1 for r in rows if r.get("record_type") == "cancelled_by_me"),
        "cod_to_collect": sum(float(r.get("amount_to_collect") or 0) for r in rows),
        "total_payable": sum(float(r.get("total_payable") or 0) for r in rows),
        "delivery_fee": sum(float(r.get("delivery_fee") or 0) for r in rows),
        "tip": sum(float(r.get("tip_amount") or 0) for r in rows),
        "delivery_earning": sum(float(r.get("delivery_boy_expected_earning") or 0) for r in rows),
        "platform_fee": sum(float(r.get("platform_fee") or 0) for r in rows)
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

    order = _decorate_delivery_financials(_hydrate_delivery_order(order))

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
        ao = _decorate_delivery_financials(_hydrate_delivery_order(ao))
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
        delivery_availability=availability,
        pay_on_delivery_upi=_delivery_pay_on_delivery_upi_settings()
    )


@app.route('/delivery/order/<oid>/upi-qr', methods=['GET'], endpoint='delivery_order_upi_qr')
@login_required(role='delivery')
def delivery_order_upi_qr(oid):
    """
    Render the official Pay-on-Delivery UPI QR for the assigned rider.

    The QR contains only the Admin-configured public UPI receiving address,
    payee name, exact order amount and an order reference. It does not expose
    gateway secrets or rider personal payment details.
    """
    u = current_user()

    try:
        oid_obj = ObjectId(oid)
    except Exception:
        abort(404)

    order = mongo.orders.find_one({
        "_id": oid_obj,
        "$or": [
            {"delivery_partner_id": u["id"]},
            {"delivery_partner_id": str(u["id"])}
        ]
    })

    if not order:
        abort(404)

    payment_method = (order.get("payment_method") or "COD").strip().upper()
    status = (order.get("status") or "").strip().upper()

    if payment_method not in {"COD", "CASH_ON_DELIVERY", "COD_RIDER_COLLECTION"}:
        abort(404)

    if status != "OUT_FOR_DELIVERY":
        abort(404)

    upi_settings = _delivery_pay_on_delivery_upi_settings()
    if not upi_settings.get("enabled") or not upi_settings.get("upi_id"):
        abort(404)

    amount = round(_delivery_money_float(
        order.get("total_payable"),
        order.get("total_amount") or 0
    ), 2)

    if amount <= 0:
        abort(404)

    order_number = (order.get("order_number") or "").strip()
    short_order_id = order_number or str(oid_obj)[-6:]
    transaction_ref = re.sub(r"[^A-Za-z0-9]", "", f"NELOCALS{short_order_id}")[:35]

    upi_payload = "upi://pay?" + urlencode({
        "pa": upi_settings.get("upi_id") or "",
        "pn": upi_settings.get("payee_name") or "NE LOCALS",
        "am": f"{amount:.2f}",
        "cu": "INR",
        "tr": transaction_ref,
        "tn": f"NE LOCALS Order {short_order_id}",
    })

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=7,
        border=3,
    )
    qr.add_data(upi_payload)
    qr.make(fit=True)

    image = qr.make_image(fill_color="black", back_color="white")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)

    response = send_file(buffer, mimetype="image/png", max_age=0)
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return response
