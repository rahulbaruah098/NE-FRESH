"""Delivery actions route section.

Route decorators, endpoint names, URL paths, HTTP methods and function bodies are
preserved from the Step 6 source. Shared legacy helpers are imported from
``routes.delivery.shared`` during this transitional decomposition.
"""

from routes.delivery.shared import *

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

    if new_status not in DELIVERY_STATUS_ALLOWED:
        flash("Invalid delivery status selected.", "warning")
        return redirect(request.referrer or url_for("delivery_active_orders"))

    current_status = (order.get("status") or "").strip().upper()

    if current_status == "DELIVERED":
        flash("This order is already delivered.", "info")
        return redirect(request.referrer or url_for("delivery_active_orders"))

    if not is_delivery_transition_allowed(current_status, new_status):
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
        payment_method = (order.get("payment_method") or "COD").strip().upper()
        payment_status = (order.get("payment_status") or "PENDING").strip().upper()
        cod_received = request.form.get('cod_received')
        payment_collection_channel = (request.form.get("payment_collection_channel") or "CASH").strip().upper()
        upi_delivery_reference = re.sub(
            r"\s+",
            "",
            (request.form.get("upi_delivery_reference") or "").strip()
        ).upper()

        items_subtotal = _delivery_money_float(
            order.get("items_subtotal")
            if order.get("items_subtotal") is not None
            else order.get("store_earning"),
            0.0
        )

        delivery_fee = _delivery_money_float(
            order.get("delivery_fee_amount")
            if order.get("delivery_fee_amount") is not None
            else order.get("delivery_fee"),
            0.0
        )

        platform_fee = _delivery_money_float(order.get("platform_fee"), 0.0)

        tip_amount = _delivery_money_float(
            order.get("tip_amount")
            if order.get("tip_amount") is not None
            else order.get("delivery_tip_amount"),
            0.0
        )

        total_payable = _delivery_money_float(
            order.get("total_payable"),
            items_subtotal + delivery_fee + platform_fee + tip_amount
        )

        delivery_boy_earning = round(delivery_fee + tip_amount, 2)
        store_payout_amount = round(items_subtotal, 2)
        admin_platform_earning = round(platform_fee, 2)

        collected_payment_statuses = [
            "PAID",
            "COLLECTED",
            "ONLINE_PAID",
            "COLLECTED_BY_RIDER",
            "COD_COLLECTED_BY_RIDER",
            "COD_UPI_RECORDED"
        ]

        if payment_method == "COD" and payment_status not in collected_payment_statuses:
            if cod_received != '1':
                flash('Please confirm that the Pay on Delivery amount has been received before marking Delivered.', 'warning')
                return redirect(request.referrer or url_for('delivery_active_orders'))

            if payment_collection_channel not in {"CASH", "UPI"}:
                flash('Select how the customer paid: Cash or UPI.', 'warning')
                return redirect(request.referrer or url_for('delivery_active_orders'))

            if payment_collection_channel == "UPI":
                upi_settings = _delivery_pay_on_delivery_upi_settings()

                if not upi_settings.get("enabled"):
                    flash('UPI at delivery is not configured by Admin. Collect payment by cash or contact Admin.', 'warning')
                    return redirect(request.referrer or url_for('delivery_active_orders'))

                if not re.match(r"^[A-Za-z0-9._/-]{6,40}$", upi_delivery_reference):
                    flash('Enter the customer UPI transaction/reference number before marking Delivered.', 'warning')
                    return redirect(request.referrer or url_for('delivery_active_orders'))

                duplicate_upi = mongo.orders.find_one({
                    "_id": {"$ne": oid_obj},
                    "payment_collection_channel": "UPI",
                    "upi_delivery_reference": {
                        "$regex": f"^{re.escape(upi_delivery_reference)}$",
                        "$options": "i"
                    }
                })

                if duplicate_upi:
                    flash('This UPI transaction/reference is already recorded on another order. Please verify the payment reference.', 'warning')
                    return redirect(request.referrer or url_for('delivery_active_orders'))

                rider_cash_to_submit = 0.0

                update_data.update({
                    "items_subtotal": store_payout_amount,
                    "total_amount": round(total_payable, 2),
                    "total_payable": round(total_payable, 2),
                    "delivery_fee": delivery_fee,
                    "delivery_fee_amount": delivery_fee,
                    "platform_fee": platform_fee,
                    "tip_amount": tip_amount,
                    "delivery_tip_amount": tip_amount,

                    "payment_status": "COD_UPI_RECORDED",
                    "payment_received_by": "ADMIN_PLATFORM",
                    "payment_collected_at": now,
                    "payment_collection_status": "COLLECTED",
                    "payment_collection_channel": "UPI",
                    "payment_reconciliation_status": "PENDING_UPI_VERIFICATION",
                    "cod_collection_status": "UPI_RECORDED",
                    "cod_collected_amount": round(total_payable, 2),
                    "upi_delivery_reference": upi_delivery_reference,
                    "upi_delivery_payee_id": upi_settings.get("upi_id") or "",
                    "upi_delivery_payee_name": upi_settings.get("payee_name") or "NE LOCALS",
                    "upi_delivery_reconciliation_status": "PENDING_ADMIN_VERIFICATION",
                    "upi_delivery_recorded_at": now,
                    "upi_delivery_recorded_by": str(u.get("id") or u.get("_id") or ""),

                    "expected_rider_cash_to_submit": 0.0,
                    "rider_cash_to_submit": 0.0,
                    "rider_cash_settlement_status": "NOT_REQUIRED",

                    "delivery_boy_earning": delivery_boy_earning,
                    "delivery_boy_payout_amount": delivery_boy_earning,
                    "delivery_boy_payout_status": DELIVERY_MONTHLY_STATUS_ACCRUED,

                    "store_earning": store_payout_amount,
                    "store_payout_amount": store_payout_amount,
                    "store_payout_status": "PENDING_PAYMENT_RECONCILIATION",

                    "admin_platform_earning": admin_platform_earning,
                    "platform_fee_status": "PENDING_UPI_RECONCILIATION",
                    "platform_fee_received_at": None,

                    "order_settlement_status": "UPI_RECONCILIATION_PENDING",
                    "settlement_status": "UPI_RECONCILIATION_PENDING",
                    "store_settlement_status": "PENDING_PAYMENT_RECONCILIATION",
                    "admin_platform_fee_status": "PENDING_UPI_RECONCILIATION",
                    "delivery_settlement_status": DELIVERY_MONTHLY_STATUS_ACCRUED,

                    "last_settlement_event": {
                        "action": "UPI_AT_DELIVERY_RECORDED",
                        "amount_collected": round(total_payable, 2),
                        "upi_reference": upi_delivery_reference,
                        "delivery_boy_earning": delivery_boy_earning,
                        "rider_cash_to_submit": 0.0,
                        "platform_fee": admin_platform_earning,
                        "store_payout_amount": store_payout_amount,
                        "created_by": str(u.get("id") or u.get("_id") or ""),
                        "created_by_name": u.get("name") or "Delivery Partner",
                        "created_at": now
                    }
                })

                event_note = (
                    f"UPI payment ₹{total_payable:.2f} recorded at delivery. "
                    f"Reference {upi_delivery_reference}. Pending Admin verification. Order delivered."
                )
            else:
                # Customer COD cash belongs entirely to the business. The rider's
                # delivery fee + tip are NOT deducted here; they accrue for monthly pay.
                rider_cash_to_submit = round(max(total_payable, 0), 2)

                update_data.update({
                "items_subtotal": store_payout_amount,
                "total_amount": round(total_payable, 2),
                "total_payable": round(total_payable, 2),
                "delivery_fee": delivery_fee,
                "delivery_fee_amount": delivery_fee,
                "platform_fee": platform_fee,
                "tip_amount": tip_amount,
                "delivery_tip_amount": tip_amount,

                    "payment_status": "COLLECTED_BY_RIDER",
                    "payment_received_by": "DELIVERY_BOY",
                    "payment_collected_at": now,
                    "payment_collection_status": "COLLECTED",
                    "payment_collection_channel": "CASH",
                    "payment_reconciliation_status": "PENDING_RIDER_CASH",
                    "upi_delivery_reference": "",
                    "upi_delivery_reconciliation_status": "NOT_APPLICABLE",
                    "cod_collection_status": "COLLECTED",

                "cod_collected_amount": round(total_payable, 2),
                "expected_rider_cash_to_submit": rider_cash_to_submit,
                "rider_cash_to_submit": rider_cash_to_submit,
                "rider_cash_settlement_status": "PENDING",

                "delivery_boy_earning": delivery_boy_earning,
                "delivery_boy_payout_amount": delivery_boy_earning,
                "delivery_boy_payout_status": DELIVERY_MONTHLY_STATUS_ACCRUED,

                "store_earning": store_payout_amount,
                "store_payout_amount": store_payout_amount,
                "store_payout_status": "PENDING_AFTER_DELIVERY",

                "admin_platform_earning": admin_platform_earning,
                "platform_fee_status": "PENDING_RIDER_CASH_SETTLEMENT",
                "platform_fee_received_at": None,

                "order_settlement_status": "RIDER_CASH_SETTLEMENT_PENDING",
                "settlement_status": "RIDER_CASH_PENDING",
                "store_settlement_status": "PAYOUT_PENDING",
                "admin_platform_fee_status": "PENDING_RIDER_CASH",
                "delivery_settlement_status": DELIVERY_MONTHLY_STATUS_ACCRUED,

                "last_settlement_event": {
                    "action": "COD_COLLECTED_BY_RIDER",
                    "amount_collected": round(total_payable, 2),
                    "delivery_boy_earning": delivery_boy_earning,
                    "rider_cash_to_submit": rider_cash_to_submit,
                    "platform_fee": admin_platform_earning,
                    "store_payout_amount": store_payout_amount,
                    "created_by": str(u.get("id") or u.get("_id") or ""),
                    "created_by_name": u.get("name") or "Delivery Partner",
                    "created_at": now
                }
            })

                event_note = (
                    f"COD cash ₹{total_payable:.2f} collected by delivery boy. "
                    f"Delivery earning ₹{delivery_boy_earning:.2f} accrued for monthly settlement. "
                    f"Full business cash to submit ₹{rider_cash_to_submit:.2f}. Order delivered."
                )

        else:
            update_data.update({
                "items_subtotal": store_payout_amount,
                "total_amount": round(total_payable, 2),
                "total_payable": round(total_payable, 2),
                "delivery_fee": delivery_fee,
                "delivery_fee_amount": delivery_fee,
                "platform_fee": platform_fee,
                "tip_amount": tip_amount,
                "delivery_tip_amount": tip_amount,

                "payment_status": payment_status if payment_status else "PAID",
                "payment_collection_status": "NOT_REQUIRED",
                "payment_collection_channel": order.get("payment_collection_channel") or "RAZORPAY",
                "payment_reconciliation_status": order.get("payment_reconciliation_status") or "VERIFIED",
                "upi_delivery_reconciliation_status": "NOT_APPLICABLE",
                "cod_collection_status": "NOT_REQUIRED",

                "delivery_boy_earning": delivery_boy_earning,
                "delivery_boy_payout_amount": delivery_boy_earning,
                "delivery_boy_payout_status": DELIVERY_MONTHLY_STATUS_ACCRUED,

                "store_earning": store_payout_amount,
                "store_payout_amount": store_payout_amount,
                "store_payout_status": "PENDING_AFTER_DELIVERY",

                "admin_platform_earning": admin_platform_earning,
                "platform_fee_status": order.get("platform_fee_status") or "RECEIVED",

                "order_settlement_status": "STORE_PAYOUT_PENDING",
                "settlement_status": "PAYOUT_PENDING",
                "store_settlement_status": "PAYOUT_PENDING",
                "delivery_settlement_status": DELIVERY_MONTHLY_STATUS_ACCRUED
            })

            event_note = "Order delivered. Delivery earning accrued for monthly settlement. No COD cash collection required."

        in_house_order = bool(order.get("in_house_delivery_enabled_at_order", True))
        if in_house_order and order.get("delivery_partner_id"):
            monthly_period = delivery_monthly_period_from_utc(now)
            update_data.update({
                "delivery_payout_model": DELIVERY_PAYOUT_MODEL_MONTHLY_V1,
                "delivery_monthly_period": monthly_period,
                "delivery_monthly_settlement_status": DELIVERY_MONTHLY_STATUS_ACCRUED,
                "delivery_monthly_earning_amount": delivery_boy_earning,
                "delivery_monthly_accrued_at": now,
                "delivery_monthly_settlement_id": "",
                "delivery_monthly_paid_at": None,
                "delivery_boy_payout_status": DELIVERY_MONTHLY_STATUS_ACCRUED,
                "delivery_settlement_status": DELIVERY_MONTHLY_STATUS_ACCRUED,
            })

        update_data["delivered_at"] = now

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
        payable_amount = _delivery_money_float(
            update_data.get("cod_collected_amount")
            if update_data.get("cod_collected_amount") is not None
            else order.get("total_payable"),
            _delivery_money_float(order.get("total_amount"), 0.0)
        )

        txn_collection_channel = (
            update_data.get("payment_collection_channel")
            or order.get("payment_collection_channel")
            or ""
        ).strip().upper()
        txn_upi_reconciliation = (
            update_data.get("upi_delivery_reconciliation_status")
            or order.get("upi_delivery_reconciliation_status")
            or ""
        ).strip().upper()

        txn_status = (
            "PAYMENT_RECORDED_PENDING_RECONCILIATION"
            if txn_collection_channel == "UPI" and txn_upi_reconciliation != "VERIFIED"
            else "PAID"
        )

        txn_update_data = {
            "status": txn_status,
            "amount": payable_amount,
            "payment_method": order.get("payment_method") or "COD",
            "payment_status": update_data.get("payment_status") or order.get("payment_status") or "PAID",
            "payment_received_by": update_data.get("payment_received_by") or order.get("payment_received_by"),
            "payment_collection_status": update_data.get("payment_collection_status") or order.get("payment_collection_status"),
            "payment_collection_channel": txn_collection_channel,
            "payment_reconciliation_status": update_data.get("payment_reconciliation_status") or order.get("payment_reconciliation_status"),
            "cod_collection_status": update_data.get("cod_collection_status") or order.get("cod_collection_status"),
            "cod_collected_amount": update_data.get("cod_collected_amount", order.get("cod_collected_amount", 0)),
            "upi_delivery_reference": update_data.get("upi_delivery_reference") or order.get("upi_delivery_reference") or "",
            "upi_delivery_reconciliation_status": txn_upi_reconciliation or "NOT_APPLICABLE",

            "items_subtotal": update_data.get("store_earning", order.get("items_subtotal", 0)),
            "delivery_fee": update_data.get("delivery_boy_earning", order.get("delivery_fee", 0)) - _delivery_money_float(order.get("tip_amount"), 0.0),
            "platform_fee": update_data.get("admin_platform_earning", order.get("platform_fee", 0)),
            "tip_amount": _delivery_money_float(order.get("tip_amount"), 0.0),

            "store_payout_amount": update_data.get("store_payout_amount"),
            "store_payout_status": update_data.get("store_payout_status"),

            "delivery_boy_earning": update_data.get("delivery_boy_earning"),
            "delivery_boy_payout_amount": update_data.get("delivery_boy_payout_amount"),
            "delivery_boy_payout_status": update_data.get("delivery_boy_payout_status"),
            "delivery_payout_model": update_data.get("delivery_payout_model") or order.get("delivery_payout_model") or "",
            "delivery_monthly_period": update_data.get("delivery_monthly_period") or order.get("delivery_monthly_period") or "",
            "delivery_monthly_settlement_status": update_data.get("delivery_monthly_settlement_status") or order.get("delivery_monthly_settlement_status") or "",
            "delivery_monthly_earning_amount": update_data.get("delivery_monthly_earning_amount", order.get("delivery_monthly_earning_amount", 0)),
            "delivery_monthly_settlement_id": update_data.get("delivery_monthly_settlement_id") or order.get("delivery_monthly_settlement_id") or "",
            "delivery_monthly_paid_at": update_data.get("delivery_monthly_paid_at", order.get("delivery_monthly_paid_at")),

            "expected_rider_cash_to_submit": update_data.get("expected_rider_cash_to_submit", order.get("expected_rider_cash_to_submit", 0)),
            "rider_cash_to_submit": update_data.get("rider_cash_to_submit"),
            "rider_cash_settlement_status": update_data.get("rider_cash_settlement_status"),

            "platform_fee_status": update_data.get("platform_fee_status"),
            "order_settlement_status": update_data.get("order_settlement_status"),
            "settlement_status": update_data.get("settlement_status"),

            "updated_at": now
        }

        existing_txn = mongo.transactions.find_one({
            "order_id": oid_obj
        })

        if existing_txn:
            mongo.transactions.update_many(
                {"order_id": oid_obj},
                {
                    "$set": txn_update_data
                }
            )
        else:
            txn_update_data.update({
                "order_id": oid_obj,
                "store_id": order.get("store_id"),
                "user_id": order.get("user_id"),
                "method": order.get("payment_method") or "COD",
                "created_at": now
            })

            mongo.transactions.insert_one(txn_update_data)

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
    - Mark order back as SHIPMENT_READY
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
                "status": "SHIPMENT_READY",

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
                    "order_status": "SHIPMENT_READY",
                    "payment_status": order.get("payment_status", ""),
                    "customer_name": order.get("customer_name", ""),
                    "customer_phone": order.get("customer_phone", ""),
                    "total_payable": float(
                        order.get("total_payable")
                        or (
                            float(order.get("items_subtotal") or order.get("total_amount") or 0)
                            + float(order.get("delivery_fee") or 0)
                            + float(order.get("platform_fee") or 0)
                            + float(order.get("tip_amount") or 0)
                        )
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
