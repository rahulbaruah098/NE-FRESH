"""Store dashboard settings route section.

Route decorators, endpoint names, URL paths, HTTP methods and function bodies are
preserved from the Step 6 source. Shared legacy helpers are imported from
``routes.store.shared`` during this transitional decomposition.
"""

from routes.store.shared import *

@app.route('/store/dashboard')
@login_required(role='store')
def store_dashboard():
    u, store = _get_current_store_or_redirect()

    if not store:
        flash("Store not found.", "danger")
        return redirect(url_for("login"))

    store["id"] = str(store["_id"])

    page_context = _build_store_split_page_context(store)

    notification_settings = mongo.store_notification_settings.find_one({
        "store_id": store["_id"]
    }) or {
        "enabled": False
    }

    return render_template(
        "store_dashboard.html",
        user=u,
        store=store,
        notification_settings=notification_settings,
        **page_context
    )


@app.route("/store/online-toggle", methods=["POST"], endpoint="store_online_toggle")
@login_required(role="store")
def store_online_toggle():
    u, store = _get_current_store_or_redirect()

    if not store:
        return jsonify({
            "ok": False,
            "message": "Store not found."
        }), 404

    current_status = int(store.get("is_online", store.get("is_open", 1)) or 0)
    next_status = 0 if current_status == 1 else 1

    now = datetime.utcnow().isoformat()

    mongo.stores.update_one(
        {"_id": store["_id"]},
        {
            "$set": {
                "is_online": next_status,
                "is_open": next_status,
                "updated_at": now,
                "online_status_updated_at": now
            }
        }
    )

    return jsonify({
        "ok": True,
        "is_online": next_status,
        "message": "Store is now online." if next_status else "Store is now offline."
    })


@app.route("/store/delivery-toggle", methods=["POST"], endpoint="store_delivery_toggle")
@login_required(role="store")
def store_delivery_toggle():
    u, store = _get_current_store_or_redirect()

    if not store:
        return jsonify({
            "ok": False,
            "message": "Store not found."
        }), 404

    current_status = int(store.get("delivery_enabled", 1 if store.get("delivery_available", True) else 0) or 0)
    next_status = 0 if current_status == 1 else 1

    now = datetime.utcnow().isoformat()

    mongo.stores.update_one(
        {"_id": store["_id"]},
        {
            "$set": {
                "delivery_enabled": next_status,
                "delivery_available": bool(next_status),
                "updated_at": now,
                "delivery_status_updated_at": now
            }
        }
    )

    return jsonify({
        "ok": True,
        "delivery_enabled": next_status,
        "message": "Delivery is now enabled." if next_status else "Delivery is now disabled."
    })


@app.route('/store/settings', methods=['GET', 'POST'], endpoint='store_settings')
@login_required(role='store')
def store_settings_page():
    u, store = _get_current_store_or_redirect()

    if not store:
        flash("Store not found.", "danger")
        return redirect(url_for("store_dashboard"))

    if request.method == "POST":
        now = datetime.utcnow().isoformat()

        def _settings_int_or_default(value, default=0, min_value=None, max_value=None):
            try:
                if value is None or str(value).strip() == "":
                    number = int(default)
                else:
                    number = int(float(value))

                if min_value is not None and number < min_value:
                    return int(default)

                if max_value is not None and number > max_value:
                    return int(default)

                return number
            except Exception:
                return int(default)

        def _settings_text(name, limit=500):
            value = (request.form.get(name) or "").strip()
            if len(value) > limit:
                value = value[:limit]
            return value

        existing_is_online = bool(int(store.get("is_online", store.get("is_open", 1)) or 0))
        existing_delivery_enabled = bool(
            int(store.get("delivery_enabled", 1 if store.get("delivery_available", True) else 0) or 0)
        )

        is_online = _store_bool_from_form("is_online", existing_is_online)
        accepting_orders = _store_bool_from_form(
            "accepting_orders",
            bool(int(store.get("accepting_orders", 1) or 0))
        )

        delivery_enabled = _store_bool_from_form("delivery_enabled", existing_delivery_enabled)

        allow_cod = _store_bool_from_form(
            "allow_cod",
            bool(int(store.get("allow_cod", 1) or 0))
        )

        allow_online_payment = _store_bool_from_form(
            "allow_online_payment",
            bool(int(store.get("allow_online_payment", 1) or 0))
        )


        hide_out_of_stock = _store_bool_from_form(
            "hide_out_of_stock",
            bool(int(store.get("hide_out_of_stock", 0) or 0))
        )

        allow_preorder = _store_bool_from_form(
            "allow_preorder",
            bool(int(store.get("allow_preorder", 0) or 0))
        )

        opening_time = _settings_text("opening_time", 20)
        closing_time = _settings_text("closing_time", 20)
        weekly_off_day = _settings_text("weekly_off_day", 40)
        temporary_close_message = _settings_text("temporary_close_message", 250)

        min_order_amount = _store_money_or_default(
            request.form.get("min_order_amount"),
            store.get("min_order_amount", 0)
        )


        lat_raw = (request.form.get("latitude") or "").strip()
        lng_raw = (request.form.get("longitude") or "").strip()

        latitude = _store_float_or_none(lat_raw, -90, 90)
        longitude = _store_float_or_none(lng_raw, -180, 180)

        estimated_delivery_time = _settings_int_or_default(
            request.form.get("estimated_delivery_time"),
            store.get("estimated_delivery_time", 45) or 45,
            0,
            300
        )

        low_stock_alert_quantity = _settings_int_or_default(
            request.form.get("low_stock_alert_quantity"),
            store.get("low_stock_alert_quantity", 5) or 5,
            0,
            100000
        )

        rider_instructions = _settings_text("rider_instructions", 500)

        notification_preferences = {
            "new_order_alert": _store_bool_from_form("new_order_alert", True),
            "order_cancel_alert": _store_bool_from_form("order_cancel_alert", True),
            "low_stock_alert": _store_bool_from_form("low_stock_alert", True),
            "new_review_alert": _store_bool_from_form("new_review_alert", True),
            "delivery_alert": _store_bool_from_form("delivery_alert", True),
            "email_alert": _store_bool_from_form("email_alert", False),
            "dashboard_alert": _store_bool_from_form("dashboard_alert", True),
        }

        update_data = {
            "is_online": 1 if is_online else 0,
            "is_open": 1 if is_online else 0,
            "accepting_orders": 1 if accepting_orders else 0,
            "temporary_close_message": temporary_close_message,

            "opening_time": opening_time,
            "closing_time": closing_time,
            "weekly_off_day": weekly_off_day,

            "min_order_amount": min_order_amount,
            "allow_cod": 1 if allow_cod else 0,
            "allow_online_payment": 1 if allow_online_payment else 0,

            "delivery_enabled": 1 if delivery_enabled else 0,
            "delivery_available": bool(delivery_enabled),

            # Delivery fee/rate/slab/minimum order are Admin-controlled only.
            "estimated_delivery_time": estimated_delivery_time,
            "rider_instructions": rider_instructions,

            "low_stock_alert_quantity": low_stock_alert_quantity,
            "hide_out_of_stock": 1 if hide_out_of_stock else 0,
            "allow_preorder": 1 if allow_preorder else 0,

            "notification_preferences": notification_preferences,
            "settings_updated_at": now,
            "updated_at": now,
        }

        mongo.stores.update_one(
            {"_id": store["_id"]},
            {"$set": update_data}
        )

        mongo.store_notification_settings.update_one(
            {"store_id": store["_id"]},
            {
                "$set": {
                    "store_id": store["_id"],
                    "enabled": bool(notification_preferences.get("dashboard_alert")),
                    "preferences": notification_preferences,
                    "updated_at": now
                },
                "$setOnInsert": {
                    "created_at": now
                }
            },
            upsert=True
        )

        flash("Store settings updated successfully.", "success")
        return redirect(url_for("store_settings"))

    store["id"] = str(store["_id"])

    notification_settings = mongo.store_notification_settings.find_one({
        "store_id": store["_id"]
    }) or {}

    page_context = _build_store_split_page_context(store)

    return render_template(
        "store_settings.html",
        user=u,
        store=store,
        notification_settings=notification_settings,
        **page_context
    )


@app.route('/store/delivered-orders')
@login_required(role='store')
def store_delivered_orders():
    """Show all delivered orders for this store."""
    u = current_user()

    store = mongo.stores.find_one({"user_id": u["id"]})

    if not store:
        flash("Store not found.", "danger")
        return redirect(url_for("store_dashboard"))

    delivered_docs = list(
        mongo.orders.find({
            "store_id": store["_id"],
            "status": "DELIVERED"
        }).sort("created_at", -1)
    )

    delivered = []

    for o in delivered_docs:
        customer = None

        if o.get("user_id"):
            try:
                customer = mongo.users.find_one({"_id": ObjectId(o.get("user_id"))})
            except Exception:
                customer = None

        addr = mongo.order_addresses.find_one({"order_id": o["_id"]})

        row = dict(o)
        row["id"] = str(o["_id"])
        row["customer_name"] = customer.get("name") if customer else o.get("customer_name", "")
        row["customer_phone"] = customer.get("phone") if customer else o.get("customer_phone", "")

        row["addr_line1"] = addr.get("line1") if addr else ""
        row["addr_line2"] = addr.get("line2") if addr else ""
        row["addr_city"] = addr.get("city") if addr else ""
        row["addr_state"] = addr.get("state") if addr else ""
        row["addr_pincode"] = addr.get("pincode") if addr else ""
        row["addr_lat"] = addr.get("latitude") if addr else None
        row["addr_lng"] = addr.get("longitude") if addr else None

        row = _decorate_store_delivery_order(row)

        delivered.append(row)

    store_view = dict(store)
    store_view["id"] = str(store["_id"])

    return render_template(
        "store_delivered_orders.html",
        user=u,
        store=store_view,
        orders=delivered
    )


@app.route('/store/payouts', methods=['GET'], endpoint='store_payouts')
@login_required(role='store')
def store_payouts_page():
    """Read-only Store finance view with direct-collection and Admin-payout separated."""
    u, store = _get_current_store_or_redirect()

    if not store:
        flash("Store not found.", "danger")
        return redirect(url_for("store_dashboard"))

    store_id = store.get("_id")
    store_id_str = str(store_id)
    q = (request.args.get("q") or "").strip()
    status_filter = (request.args.get("status") or "").strip().upper()

    payout_docs = list(mongo.orders.find({
        "$and": [
            {"$or": [{"store_id": store_id}, {"store_id": store_id_str}]},
            {"status": "DELIVERED"}
        ]
    }).sort("delivered_at", -1))

    payout_rows = []
    store_carry_forward_outstanding = finance_store_outstanding_adjustment_total(store_id)

    for order in payout_docs:
        row = _decorate_store_delivery_order(dict(order))
        row["id"] = str(row.get("_id") or row.get("id") or "")

        items_subtotal = _store_delivery_money_float(row.get("items_subtotal"), row.get("store_earning") or 0)
        original_store_payout_amount = _store_delivery_money_float(
            row.get("original_store_payout_amount") if row.get("original_store_payout_amount") is not None else row.get("store_earning"),
            items_subtotal
        )
        store_refund_deduction = _store_delivery_money_float(
            row.get("store_refund_deduction") if row.get("store_refund_deduction") is not None else row.get("refund_deduction"),
            0
        )
        adjusted_store_payout = _store_delivery_money_float(
            row.get("adjusted_store_payout"),
            max(original_store_payout_amount - store_refund_deduction, 0)
        )
        store_adjustment_due = _store_delivery_money_float(row.get("store_adjustment_due"), 0)
        finance_state = finance_reconciliation_snapshot(row)
        payout_required = bool(finance_state.get("store_payout_required"))
        customer_reconciled = bool(finance_state.get("customer_payment_reconciled"))
        payout_status = (row.get("store_payout_status") or finance_state.get("store_payout_status") or "").strip().upper()

        carry_outstanding = store_carry_forward_outstanding
        carry_preview = round(min(carry_outstanding, adjusted_store_payout), 2) if payout_required and payout_status != "PAID" else 0.0
        final_preview = round(max(adjusted_store_payout - carry_preview, 0), 2)

        if not payout_required and customer_reconciled:
            payout_status = "NOT_REQUIRED"
            net_store_earning = round(original_store_payout_amount - store_refund_deduction, 2)
        elif payout_status == "PAID":
            net_store_earning = _store_delivery_money_float(
                row.get("store_payout_paid_amount"),
                row.get("store_payout_amount") or adjusted_store_payout
            )
        else:
            net_store_earning = final_preview

        row["items_subtotal"] = round(items_subtotal, 2)
        row["original_store_payout_amount"] = round(original_store_payout_amount, 2)
        row["store_refund_deduction"] = round(store_refund_deduction, 2)
        row["refund_deduction"] = round(store_refund_deduction, 2)
        row["adjusted_store_payout"] = round(adjusted_store_payout, 2)
        row["store_adjustment_due"] = round(store_adjustment_due, 2)
        row["store_carry_forward_adjustment_outstanding"] = carry_outstanding
        row["store_carry_forward_adjustment_preview"] = carry_preview
        row["final_store_payout_preview"] = final_preview
        row["net_store_earning"] = round(net_store_earning, 2)
        row["store_payout_status"] = payout_status or "PENDING_AFTER_DELIVERY"
        row["store_payout_required"] = payout_required
        row["customer_payment_reconciled"] = customer_reconciled
        row["payment_receiver_label"] = finance_state.get("payment_receiver_label") or ""
        row["payment_collection_label"] = finance_state.get("collection_label") or ""
        row["store_payout_eligible"] = bool(finance_state.get("store_payout_eligible"))
        row["store_payout_block_reason"] = finance_state.get("store_payout_block_reason") or ""
        row["platform_fee_reconciliation_status"] = finance_state.get("platform_fee_reconciliation_status") or ""
        row["settlement_impact"] = row.get("settlement_impact") or (
            "ADJUST_FROM_NEXT_PAYOUT" if store_adjustment_due > 0 else (
                "DEDUCT_FROM_PENDING_PAYOUT" if store_refund_deduction > 0 else "NO_DEDUCTION"
            )
        )
        row["store_settlement_status"] = row.get("store_settlement_status") or ("DIRECT_COLLECTION_RECONCILED" if not payout_required and customer_reconciled else "PAYOUT_PENDING")
        row["order_settlement_status"] = row.get("order_settlement_status") or ("BUSINESS_RECONCILED" if not payout_required and customer_reconciled else "STORE_PAYOUT_PENDING")
        row["rider_cash_settlement_status"] = row.get("rider_cash_settlement_status") or "NOT_REQUIRED"
        row["platform_fee_status"] = row.get("platform_fee_status") or ""
        row["store_payout_paid_at"] = row.get("store_payout_paid_at") or ""
        row["store_payout_reference_no"] = row.get("store_payout_reference_no") or ""
        row["store_payout_mode"] = row.get("store_payout_mode") or ""
        row["store_payout_note"] = row.get("store_payout_note") or ""

        if q:
            haystack = " ".join([
                str(row.get("id") or ""), str(row.get("customer_name") or ""), str(row.get("customer_phone") or ""),
                str(row.get("payment_method") or ""), str(row.get("payment_collection_label") or ""),
                str(row.get("payment_receiver_label") or ""), str(row.get("store_payout_status") or ""),
                str(row.get("order_settlement_status") or ""), str(row.get("store_payout_reference_no") or "")
            ]).lower()
            if q.lower() not in haystack:
                continue

        if status_filter:
            if status_filter == "PENDING":
                if not payout_required or payout_status in ["PAID", "SETTLED"]:
                    continue
            elif status_filter == "PAID":
                if payout_status != "PAID":
                    continue
            elif status_filter == "DIRECT":
                if payout_required or not customer_reconciled:
                    continue
            elif status_filter == "BLOCKED":
                if not payout_required or row.get("store_payout_eligible") or payout_status == "PAID":
                    continue
            elif status_filter not in [payout_status, (row.get("order_settlement_status") or "").upper()]:
                continue

        payout_rows.append(row)

    pending_rows = [r for r in payout_rows if r.get("store_payout_required") and (r.get("store_payout_status") or "").upper() != "PAID"]
    paid_rows = [r for r in payout_rows if (r.get("store_payout_status") or "").upper() == "PAID"]
    direct_rows = [r for r in payout_rows if not r.get("store_payout_required") and r.get("customer_payment_reconciled")]

    metrics = {
        "total_orders": len(payout_rows),
        "pending_orders": len(pending_rows),
        "paid_orders": len(paid_rows),
        "direct_collection_orders": len(direct_rows),
        "blocked_orders": sum(1 for r in pending_rows if not r.get("store_payout_eligible")),
        "pending_amount": round(sum(float(r.get("final_store_payout_preview") or 0) for r in pending_rows), 2),
        "paid_amount": round(sum(float(r.get("net_store_earning") or 0) for r in paid_rows), 2),
        "direct_collection_amount": round(sum(float(r.get("original_store_payout_amount") or 0) for r in direct_rows), 2),
        "total_store_earning": round(sum(float(r.get("original_store_payout_amount") or 0) for r in payout_rows), 2),
        "total_refund_deduction": round(sum(float(r.get("store_refund_deduction") or 0) for r in payout_rows), 2),
        "total_adjusted_payout": round(sum(float(r.get("adjusted_store_payout") or 0) for r in payout_rows if r.get("store_payout_required")), 2),
        "total_adjustment_due": round(sum(float(r.get("store_adjustment_due") or 0) for r in payout_rows), 2),
        "carry_forward_outstanding": store_carry_forward_outstanding,
    }

    store_view = dict(store)
    store_view["id"] = str(store["_id"])

    return render_template(
        "store_payouts.html",
        user=u,
        store=store_view,
        payouts=payout_rows,
        metrics=metrics,
        q=q,
        status_filter=status_filter
    )
