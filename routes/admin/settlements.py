"""Admin settlements route section.

Route decorators, endpoint names, URL paths, HTTP methods and function bodies are
preserved from the Step 6 source. Shared legacy helpers are imported from
``routes.admin.shared`` during this transitional decomposition.
"""

from routes.admin.shared import *

@app.route("/admin/settlements", methods=["GET"], endpoint="admin_settlements")
@login_required(role="admin")
def admin_settlements():
    rider_cash_orders_raw = list(
        mongo.orders.find({
            "status": "DELIVERED",
            "payment_method": "COD",
            "payment_status": {"$in": ["COLLECTED_BY_RIDER", "COD_COLLECTED_BY_RIDER"]},
            "payment_collection_channel": {"$ne": "UPI"},
            "rider_cash_settlement_status": {"$in": ["PENDING", "RIDER_CASH_PENDING"]}
        }).sort("delivered_at", -1)
    )

    upi_delivery_orders_raw = list(
        mongo.orders.find({
            "status": "DELIVERED",
            "payment_method": "COD",
            "payment_collection_channel": "UPI",
            "upi_delivery_reconciliation_status": {"$in": ["PENDING", "PENDING_ADMIN_VERIFICATION"]}
        }).sort("delivered_at", -1)
    )

    store_platform_fee_orders_raw = list(
        mongo.orders.find({
            "status": "DELIVERED",
            "payment_received_by": "STORE",
            "platform_fee": {"$gt": 0},
            "platform_fee_status": {"$nin": ["RECEIVED", "NOT_REQUIRED", "ADJUSTED"]}
        }).sort("delivered_at", -1)
    )

    external_partner_orders_raw = list(
        mongo.orders.find({
            "status": "DELIVERED",
            "$or": [
                {"payment_flow": "COD_PARTNER_COLLECTION"},
                {"cod_collection_method": COD_COLLECTION_EXTERNAL_PARTNER}
            ],
            "external_cod_remittance_status": {"$nin": ["RECEIVED", "VERIFIED", "SETTLED", "PAID"]}
        }).sort("delivered_at", -1)
    )

    pending_store_payout_raw = list(
        mongo.orders.find({
            "status": "DELIVERED",
            "store_payout_status": {"$in": [
                "PENDING_AFTER_DELIVERY", "PENDING", "PAYOUT_PENDING",
                "PENDING_PAYMENT_RECONCILIATION", "PROCESSING"
            ]}
        }).sort("delivered_at", -1)
    )

    online_paid_orders_raw = list(
        mongo.orders.find({
            "payment_method": {"$in": ["ONLINE", "ONLINE_PAYMENT", "RAZORPAY"]},
            "payment_status": {"$in": ["PAID", "ONLINE_PAID", "SUCCESS"]}
        }).sort("payment_collected_at", -1)
    )

    cod_collected_orders_raw = list(
        mongo.orders.find({
            "payment_method": "COD",
            "payment_status": {"$in": ["COLLECTED_BY_RIDER", "COD_COLLECTED_BY_RIDER"]}
        }).sort("delivered_at", -1)
    )

    platform_fee_received_orders_raw = list(
        mongo.orders.find({"platform_fee_status": "RECEIVED"}).sort("platform_fee_received_at", -1)
    )

    rider_cash_orders = [_admin_hydrate_settlement_order(o) for o in rider_cash_orders_raw]
    upi_delivery_orders = [_admin_hydrate_settlement_order(o) for o in upi_delivery_orders_raw]
    store_platform_fee_orders = [_admin_hydrate_settlement_order(o) for o in store_platform_fee_orders_raw]
    external_partner_orders = [_admin_hydrate_settlement_order(o) for o in external_partner_orders_raw]

    # Only orders where Admin actually owes the Store are included. Direct Store
    # collections are business-reconciled separately and never appear as Admin payout.
    store_payout_orders = []
    for raw in pending_store_payout_raw:
        row = _admin_hydrate_settlement_order(raw)
        if not row.get("store_payout_required"):
            continue
        if not row.get("customer_payment_reconciled"):
            continue
        store_payout_orders.append(row)

    online_paid_orders = [_admin_hydrate_settlement_order(o) for o in online_paid_orders_raw]
    cod_collected_orders = [_admin_hydrate_settlement_order(o) for o in cod_collected_orders_raw]
    platform_fee_received_orders = [_admin_hydrate_settlement_order(o) for o in platform_fee_received_orders_raw]

    delivery_monthly_rows, delivery_monthly_metrics = _admin_delivery_monthly_rows()

    outstanding_store_adjustments = list(mongo.store_finance_adjustments.find({
        "status": {"$in": [FINANCE_STORE_ADJUSTMENT_OPEN, FINANCE_STORE_ADJUSTMENT_PARTIAL]},
        "remaining_amount": {"$gt": 0}
    }))

    metrics = {
        "online_payment_received_count": len(online_paid_orders),
        "online_payment_received_amount": round(sum(
            float(o.get("total_payable") or o.get("total_amount") or 0) for o in online_paid_orders
        ), 2),
        "cod_collected_by_rider_count": len(cod_collected_orders),
        "cod_collected_by_rider_amount": round(sum(
            float(o.get("cod_collected_amount") or o.get("total_payable") or 0) for o in cod_collected_orders
        ), 2),
        "platform_fee_received_total_amount": round(sum(
            float(o.get("net_platform_fee") or o.get("platform_fee") or 0) for o in platform_fee_received_orders
        ), 2),
        "rider_cash_pending_count": len(rider_cash_orders),
        "rider_cash_pending_amount": round(sum(float(o.get("rider_cash_to_submit") or 0) for o in rider_cash_orders), 2),
        "upi_delivery_pending_count": len(upi_delivery_orders),
        "upi_delivery_pending_amount": round(sum(
            float(o.get("cod_collected_amount") or o.get("total_payable") or 0) for o in upi_delivery_orders
        ), 2),
        "store_platform_fee_pending_count": len(store_platform_fee_orders),
        "store_platform_fee_pending_amount": round(sum(float(o.get("net_platform_fee") or 0) for o in store_platform_fee_orders), 2),
        "external_partner_remittance_pending_count": len(external_partner_orders),
        "external_partner_remittance_pending_amount": round(sum(
            float(o.get("external_cod_amount") or o.get("cod_collected_amount") or o.get("total_payable") or 0)
            for o in external_partner_orders
        ), 2),
        "store_payout_pending_count": len(store_payout_orders),
        "store_payout_blocked_count": sum(1 for o in store_payout_orders if not o.get("store_payout_eligible")),
        "store_payout_original_amount": round(sum(float(o.get("original_store_payout_amount") or 0) for o in store_payout_orders), 2),
        "store_payout_pending_amount": round(sum(float(o.get("final_store_payout_preview") or 0) for o in store_payout_orders), 2),
        "store_refund_deduction_amount": round(sum(float(o.get("store_refund_deduction") or 0) for o in store_payout_orders), 2),
        "store_carry_forward_adjustment_amount": round(sum(
            float(a.get("remaining_amount") or 0) for a in outstanding_store_adjustments
        ), 2),
        "store_adjustment_due_amount": round(sum(float(o.get("store_adjustment_due") or 0) for o in store_payout_orders), 2),
        "platform_fee_pending_amount": round(
            sum(float(o.get("net_platform_fee") or o.get("platform_fee") or 0) for o in rider_cash_orders)
            + sum(float(o.get("net_platform_fee") or o.get("platform_fee") or 0) for o in upi_delivery_orders)
            + sum(float(o.get("net_platform_fee") or 0) for o in store_platform_fee_orders)
            + sum(float(o.get("net_platform_fee") or 0) for o in external_partner_orders),
            2
        ),
    }

    store_adjustments = []
    for adjustment in outstanding_store_adjustments:
        row = dict(adjustment)
        row["id"] = str(row.get("_id") or "")
        row["source_order_id"] = str(row.get("source_order_id") or "")
        row["original_amount"] = _admin_settlement_money(row.get("original_amount"), 0)
        row["applied_amount"] = _admin_settlement_money(row.get("applied_amount"), 0)
        row["remaining_amount"] = _admin_settlement_money(row.get("remaining_amount"), 0)
        row["status"] = (row.get("status") or FINANCE_STORE_ADJUSTMENT_OPEN).strip().upper()
        store_adjustments.append(row)

    return render_template(
        "admin_settlements.html",
        user=current_user(),
        rider_cash_orders=rider_cash_orders,
        upi_delivery_orders=upi_delivery_orders,
        store_platform_fee_orders=store_platform_fee_orders,
        external_partner_orders=external_partner_orders,
        store_payout_orders=store_payout_orders,
        store_adjustments=store_adjustments,
        delivery_monthly_rows=delivery_monthly_rows,
        delivery_monthly_metrics=delivery_monthly_metrics,
        metrics=metrics,
        active_group="settlements",
        active_page="settlements"
    )


@app.route("/admin/settlements/export.csv", methods=["GET"], endpoint="admin_settlements_export_csv")
@login_required(role="admin")
def admin_settlements_export_csv():
    sections = []

    sections.append(("Rider COD Cash Pending", list(mongo.orders.find({
        "status": "DELIVERED",
        "payment_method": "COD",
        "payment_status": {"$in": ["COLLECTED_BY_RIDER", "COD_COLLECTED_BY_RIDER"]},
        "payment_collection_channel": {"$ne": "UPI"},
        "rider_cash_settlement_status": {"$in": ["PENDING", "RIDER_CASH_PENDING"]}
    }).sort("delivered_at", -1))))

    sections.append(("UPI At Delivery Verification Pending", list(mongo.orders.find({
        "status": "DELIVERED",
        "payment_method": "COD",
        "payment_collection_channel": "UPI",
        "upi_delivery_reconciliation_status": {"$in": ["PENDING", "PENDING_ADMIN_VERIFICATION"]}
    }).sort("delivered_at", -1))))

    sections.append(("Store Platform Fee Remittance Pending", list(mongo.orders.find({
        "status": "DELIVERED",
        "payment_received_by": "STORE",
        "platform_fee": {"$gt": 0},
        "platform_fee_status": {"$nin": ["RECEIVED", "NOT_REQUIRED", "ADJUSTED"]}
    }).sort("delivered_at", -1))))

    sections.append(("External Partner Remittance Pending", list(mongo.orders.find({
        "status": "DELIVERED",
        "$or": [
            {"payment_flow": "COD_PARTNER_COLLECTION"},
            {"cod_collection_method": COD_COLLECTION_EXTERNAL_PARTNER}
        ],
        "external_cod_remittance_status": {"$nin": ["RECEIVED", "VERIFIED", "SETTLED", "PAID"]}
    }).sort("delivered_at", -1))))

    payout_candidates = list(mongo.orders.find({
        "status": "DELIVERED",
        "store_payout_status": {"$in": [
            "PENDING_AFTER_DELIVERY", "PENDING", "PAYOUT_PENDING",
            "PENDING_PAYMENT_RECONCILIATION", "PROCESSING"
        ]}
    }).sort("delivered_at", -1))
    sections.append(("Store Payout Pending", [
        o for o in payout_candidates
        if finance_reconciliation_snapshot(o).get("store_payout_required")
        and finance_reconciliation_snapshot(o).get("customer_payment_reconciled")
    ]))

    rows = [[
        "Section", "Order ID", "Store Name", "Customer Name", "Customer Phone", "Delivery Partner",
        "Payment Method", "Payment Flow", "Payment Status", "Collection Channel", "Payment Receiver",
        "Payment Reconciliation", "Platform Fee Reconciliation", "External Partner Remittance",
        "Items Subtotal", "Customer Amount", "Delivery Partner Earning", "Rider Cash To Submit", "Platform Fee", "Net Platform Fee",
        "Original Store Payout", "Refund Deduction", "Carry-forward Adjustment Preview", "Final Store Payout Preview",
        "Store Adjustment Due", "Settlement Impact", "Refund Status", "Return Status",
        "Store Payout Required", "Store Payout Eligible", "Store Payout Block Reason",
        "Rider Cash Status", "Platform Fee Status", "Store Payout Status", "Order Settlement Status",
        "Delivered At", "Updated At"
    ]]

    for section, docs in sections:
        for order in docs:
            o = _admin_hydrate_settlement_order(dict(order))
            state = o.get("finance_reconciliation") or {}
            rows.append([
                section, o.get("id"), o.get("store_name"), o.get("customer_name"), o.get("customer_phone"), o.get("delivery_partner_name"),
                o.get("payment_method"), o.get("payment_flow") or o.get("official_payment_mode") or "", o.get("payment_status"),
                o.get("payment_collection_channel") or "", state.get("payment_receiver_label") or "",
                o.get("payment_reconciliation_status") or "", o.get("platform_fee_reconciliation_status") or "",
                o.get("external_cod_remittance_status") or "",
                o.get("items_subtotal"), o.get("cod_collected_amount") or o.get("total_payable"), o.get("delivery_boy_earning"),
                o.get("rider_cash_to_submit"), o.get("platform_fee"), o.get("net_platform_fee"),
                o.get("original_store_payout_amount"), o.get("store_refund_deduction"), o.get("store_carry_forward_adjustment_preview"),
                o.get("final_store_payout_preview"), o.get("store_adjustment_due"), o.get("settlement_impact"),
                o.get("refund_status"), o.get("return_status"), "YES" if o.get("store_payout_required") else "NO",
                "YES" if o.get("store_payout_eligible") else "NO", o.get("store_payout_block_reason") or "",
                o.get("rider_cash_settlement_status"), o.get("platform_fee_status"), o.get("store_payout_status"),
                o.get("order_settlement_status"), o.get("delivered_at"), o.get("updated_at")
            ])

    # Open/partially-applied Store refund recovery is a business liability, not an
    # order payout. Export it as its own read-only finance section so the ledger
    # can be reconciled independently from individual payout rows.
    for adjustment in mongo.store_finance_adjustments.find({
        "status": {"$in": [FINANCE_STORE_ADJUSTMENT_OPEN, FINANCE_STORE_ADJUSTMENT_PARTIAL]},
        "remaining_amount": {"$gt": 0}
    }).sort("created_at", 1):
        rows.append([
            "Store Refund Carry-forward Adjustment",
            adjustment.get("source_order_id") or "",
            adjustment.get("store_name") or "",
            "", "", "",
            "", "", "", "", "STORE",
            "BUSINESS_RECONCILED", "", "",
            "", "", "", "", "", "",
            adjustment.get("original_amount") or 0, "", adjustment.get("applied_amount") or 0, adjustment.get("remaining_amount") or 0,
            adjustment.get("remaining_amount") or 0, adjustment.get("reason") or "REFUND_RECOVERY", "", "",
            "NO", "NO", "Carry-forward recovery is automatically deducted from a future Admin-to-Store payout.",
            "", "", "NOT_APPLICABLE", adjustment.get("status") or FINANCE_STORE_ADJUSTMENT_OPEN,
            adjustment.get("created_at") or "", adjustment.get("updated_at") or ""
        ])

    return _admin_csv_response(rows, "nefresh_payment_settlements.csv")


@app.route("/admin/settlements/<oid>/store-platform-fee-received", methods=["POST"], endpoint="admin_settlement_store_platform_fee_received")
@login_required(role="admin")
def admin_settlement_store_platform_fee_received(oid):
    admin_user = current_user() or {}
    oid_obj = _admin_order_id_or_none(oid)
    if not oid_obj:
        flash("Invalid order.", "danger")
        return redirect(url_for("admin_settlements"))

    order = mongo.orders.find_one({"_id": oid_obj})
    if not order:
        flash("Order not found.", "danger")
        return redirect(url_for("admin_settlements"))

    state = finance_reconciliation_snapshot(order)
    if not state.get("is_store_collection") or not state.get("customer_payment_reconciled"):
        flash("This order does not have a reconciled Store-collected customer payment.", "warning")
        return redirect(url_for("admin_settlements"))

    net_platform_fee = _admin_settlement_money(state.get("net_platform_fee"), 0)
    if net_platform_fee <= 0:
        flash("No Platform Fee remittance is due for this order.", "info")
        return redirect(url_for("admin_settlements"))
    if (order.get("platform_fee_status") or "").strip().upper() == "RECEIVED":
        flash("Platform Fee is already received for this order.", "info")
        return redirect(url_for("admin_settlements"))

    payment_mode = (request.form.get("payment_mode") or "CASH").strip().upper()
    if payment_mode not in {"CASH", "UPI", "BANK_TRANSFER"}:
        payment_mode = "CASH"

    reference = (request.form.get("reference_no") or "").strip()[:120]
    note = (request.form.get("note") or "").strip()[:250]

    if payment_mode in {"UPI", "BANK_TRANSFER"} and not reference:
        flash("Payment reference is required for UPI or Bank Transfer Platform Fee remittance.", "warning")
        return redirect(url_for("admin_settlements"))

    now = datetime.utcnow().isoformat()
    transition = build_store_platform_fee_received_state(
        oid_obj,
        net_platform_fee,
        admin_user,
        payment_mode,
        reference,
        note,
        now,
    )
    event = transition["event"]
    update_data = transition["order_update"]

    result = mongo.orders.update_one(
        {
            "_id": oid_obj,
            "platform_fee_status": {"$nin": ["RECEIVED", "NOT_REQUIRED", "ADJUSTED"]},
        },
        {"$set": update_data, "$push": {"settlement_audit_logs": event}}
    )
    if result.modified_count != 1:
        flash("Platform Fee remittance was already reconciled or the order changed. Please refresh.", "warning")
        return redirect(url_for("admin_settlements"))

    mongo.transactions.update_many(
        {"order_id": oid_obj},
        {"$set": transition["transaction_update"]}
    )
    mongo.order_events.insert_one({
        "order_id": oid_obj,
        "status": "STORE_PLATFORM_FEE_RECEIVED",
        "note": transition["event_note"],
        "created_at": now,
    })
    flash("Store Platform Fee remittance received. Business reconciliation is complete for this order.", "success")
    return redirect(url_for("admin_settlements"))


@app.route(
    "/admin/settlements/delivery-partner/<rider_id>/<period>/paid",
    methods=["POST"],
    endpoint="admin_delivery_monthly_settlement_paid"
)
@login_required(role="admin")
def admin_delivery_monthly_settlement_paid(rider_id, period):
    admin_user = current_user() or {}
    rider_id = str(rider_id or "").strip()
    period = str(period or "").strip()

    if not rider_id or not re.match(r"^\d{4}-\d{2}$", period):
        flash("Invalid delivery partner or settlement month.", "danger")
        return redirect(url_for("admin_settlements"))

    if not delivery_monthly_period_is_closed(period):
        flash("The current delivery-partner month cannot be paid before the month is closed.", "warning")
        return redirect(url_for("admin_settlements"))

    id_values = delivery_partner_id_values(rider_id)
    if not id_values:
        flash("Delivery partner could not be identified.", "danger")
        return redirect(url_for("admin_settlements"))

    raw_orders = list(mongo.orders.find({
        "status": "DELIVERED",
        "delivery_payout_model": DELIVERY_PAYOUT_MODEL_MONTHLY_V1,
        "delivery_monthly_period": period,
        "delivery_partner_id": {"$in": id_values},
    }).sort("delivered_at", 1))

    if not raw_orders:
        flash("No monthly delivery earnings were found for this period.", "warning")
        return redirect(url_for("admin_settlements"))

    existing_batch = mongo.delivery_partner_monthly_settlements.find_one({
        "delivery_partner_id_str": rider_id,
        "period": period,
    })
    if existing_batch and (existing_batch.get("status") or "").upper() == DELIVERY_MONTHLY_BATCH_STATUS_PAID:
        flash("This delivery-partner month is already paid.", "info")
        return redirect(url_for("admin_settlements"))

    hydrated = [_admin_hydrate_settlement_order(row) for row in raw_orders]
    unreconciled = [row for row in hydrated if not delivery_monthly_payment_is_reconciled(row)]

    if unreconciled:
        flash(
            f"Cannot pay this monthly settlement yet. {len(unreconciled)} customer payment/remittance record(s) still need reconciliation.",
            "warning"
        )
        return redirect(url_for("admin_settlements"))

    payout_mode = (request.form.get("payout_mode") or "UPI").strip().upper()
    if payout_mode not in {"UPI", "BANK_TRANSFER", "CASH"}:
        payout_mode = "UPI"

    reference_no = (request.form.get("reference_no") or "").strip()[:120]
    note = (request.form.get("note") or "").strip()[:250]

    if payout_mode != "CASH" and not reference_no:
        flash("Payment reference is required for UPI or Bank Transfer monthly settlement.", "warning")
        return redirect(url_for("admin_settlements"))

    now = datetime.utcnow().isoformat()
    gross_amount, batch_doc = build_delivery_monthly_batch_doc(
        hydrated,
        raw_orders,
        rider_id,
        period,
        payout_mode,
        reference_no,
        note,
        admin_user,
        now,
    )

    # Atomic/idempotent close: the unique rider+period index prevents two Admin
    # requests from creating/paying the same monthly batch twice. If another
    # request wins the race, keep the already-paid batch and do not re-pay.
    try:
        result = mongo.delivery_partner_monthly_settlements.update_one(
            {
                "delivery_partner_id_str": rider_id,
                "period": period,
                "status": {"$ne": DELIVERY_MONTHLY_BATCH_STATUS_PAID},
            },
            {"$set": batch_doc, "$setOnInsert": {"created_at": now}},
            upsert=True,
        )
    except DuplicateKeyError:
        flash("This delivery-partner month was already paid by another request.", "info")
        return redirect(url_for("admin_settlements"))

    batch = mongo.delivery_partner_monthly_settlements.find_one({
        "delivery_partner_id_str": rider_id,
        "period": period,
    }) or {}

    if (batch.get("status") or "").upper() != DELIVERY_MONTHLY_BATCH_STATUS_PAID:
        flash("Monthly settlement could not be finalized. Please try again.", "danger")
        return redirect(url_for("admin_settlements"))
    batch_id = str(batch.get("_id") or result.upserted_id or "")

    settlement_event = {
        "action": "DELIVERY_PARTNER_MONTHLY_SETTLEMENT_PAID",
        "period": period,
        "amount_paid": gross_amount,
        "payment_mode": payout_mode,
        "reference_no": reference_no,
        "batch_id": batch_id,
        "settlement_impact": "DELIVERY_PARTNER_MONTHLY_PAID",
        "created_by": str(admin_user.get("id") or admin_user.get("_id") or ""),
        "created_by_name": admin_user.get("name") or admin_user.get("email") or "Admin",
        "created_by_role": "admin",
        "note": note,
        "created_at": now,
    }

    order_filter = {
        "_id": {"$in": [row["_id"] for row in raw_orders]},
        "delivery_payout_model": DELIVERY_PAYOUT_MODEL_MONTHLY_V1,
    }
    order_update = {
        "$set": {
            "delivery_boy_payout_status": DELIVERY_MONTHLY_STATUS_PAID,
            "delivery_settlement_status": DELIVERY_MONTHLY_STATUS_PAID,
            "delivery_monthly_settlement_status": DELIVERY_MONTHLY_STATUS_PAID,
            "delivery_monthly_settlement_id": batch_id,
            "delivery_monthly_paid_at": now,
            "delivery_boy_payout_paid_at": now,
            "delivery_boy_payout_marked_by": str(admin_user.get("id") or admin_user.get("_id") or ""),
            "delivery_boy_payout_note": note,
            "updated_at": now,
        }
    }
    mongo.orders.update_many(order_filter, order_update)

    # Store one audit event per monthly batch (not once per order), otherwise
    # the Admin audit totals would multiply the monthly amount by order count.
    mongo.orders.update_one(
        {"_id": raw_orders[0]["_id"]},
        {
            "$push": {"settlement_audit_logs": settlement_event},
            "$set": {"last_settlement_event": settlement_event}
        }
    )

    mongo.transactions.update_many(
        {"order_id": {"$in": [row["_id"] for row in raw_orders]}},
        {"$set": {
            "delivery_boy_payout_status": DELIVERY_MONTHLY_STATUS_PAID,
            "delivery_settlement_status": DELIVERY_MONTHLY_STATUS_PAID,
            "delivery_monthly_settlement_status": DELIVERY_MONTHLY_STATUS_PAID,
            "delivery_monthly_settlement_id": batch_id,
            "delivery_monthly_paid_at": now,
            "delivery_boy_payout_paid_at": now,
            "delivery_boy_payout_marked_by": str(admin_user.get("id") or admin_user.get("_id") or ""),
            "delivery_boy_payout_note": note,
            "updated_at": now,
        }}
    )

    flash(
        f"Monthly delivery-partner settlement for {delivery_monthly_period_label(period)} marked paid: ₹{gross_amount:.2f}.",
        "success"
    )
    return redirect(url_for("admin_settlements"))


@app.route("/admin/settlements/<oid>/upi-delivery-verified", methods=["POST"], endpoint="admin_settlement_upi_delivery_verified")
@login_required(role="admin")
def admin_settlement_upi_delivery_verified(oid):
    admin_user = current_user() or {}
    oid_obj = _admin_order_id_or_none(oid)

    if not oid_obj:
        flash("Invalid order.", "danger")
        return redirect(url_for("admin_settlements"))

    order = mongo.orders.find_one({"_id": oid_obj})

    if not order:
        flash("Order not found.", "danger")
        return redirect(url_for("admin_settlements"))

    order = _admin_hydrate_settlement_order(order)

    if (order.get("status") or "").upper() != "DELIVERED":
        flash("UPI payment can be verified only after delivery.", "warning")
        return redirect(url_for("admin_settlements"))

    if (order.get("payment_method") or "").upper() != "COD":
        flash("This order is not Pay on Delivery / COD.", "warning")
        return redirect(url_for("admin_settlements"))

    if (order.get("payment_collection_channel") or "").upper() != "UPI":
        flash("This order was not recorded as UPI at delivery.", "warning")
        return redirect(url_for("admin_settlements"))

    if (order.get("upi_delivery_reconciliation_status") or "").upper() == "VERIFIED":
        flash("UPI payment is already verified for this order.", "info")
        return redirect(url_for("admin_settlements"))

    reference = (order.get("upi_delivery_reference") or "").strip()
    if not reference:
        flash("UPI transaction/reference is missing. Verify the order manually before proceeding.", "warning")
        return redirect(url_for("admin_settlements"))

    now = datetime.utcnow().isoformat()
    note = (request.form.get("note") or "").strip()[:250]
    transition = build_upi_delivery_verified_state(order, oid_obj, admin_user, note, now)
    amount_received = transition["amount_received"]
    settlement_event = transition["event"]
    update_data = transition["order_update"]

    reconciliation_claim = mongo.orders.update_one(
        {
            "_id": oid_obj,
            "upi_delivery_reconciliation_status": {"$ne": "VERIFIED"},
        },
        {"$set": update_data, "$push": {"settlement_audit_logs": settlement_event}}
    )
    if reconciliation_claim.modified_count != 1:
        flash("UPI payment was already verified or the order changed. Please refresh.", "warning")
        return redirect(url_for("admin_settlements"))

    mongo.transactions.update_many(
        {"order_id": oid_obj},
        {"$set": transition["transaction_update"]}
    )

    mongo.order_events.insert_one({
        "order_id": oid_obj,
        "status": "UPI_AT_DELIVERY_VERIFIED",
        "note": transition["event_note"],
        "created_at": now
    })

    flash("UPI payment verified. Store payout is now pending.", "success")
    return redirect(url_for("admin_settlements"))


@app.route("/admin/settlements/<oid>/rider-cash-received", methods=["POST"], endpoint="admin_settlement_rider_cash_received")
@login_required(role="admin")
def admin_settlement_rider_cash_received(oid):
    admin_user = current_user() or {}
    oid_obj = _admin_order_id_or_none(oid)

    if not oid_obj:
        flash("Invalid order.", "danger")
        return redirect(url_for("admin_settlements"))

    order = mongo.orders.find_one({"_id": oid_obj})

    if not order:
        flash("Order not found.", "danger")
        return redirect(url_for("admin_settlements"))

    order = _admin_hydrate_settlement_order(order)

    if order.get("status") != "DELIVERED":
        flash("Rider cash can be marked received only after delivery.", "warning")
        return redirect(url_for("admin_settlements"))

    if order.get("payment_method") != "COD":
        flash("This is not a COD order.", "warning")
        return redirect(url_for("admin_settlements"))

    if (order.get("payment_collection_channel") or "").upper() == "UPI":
        flash("This payment was received through official UPI. There is no rider cash to receive.", "warning")
        return redirect(url_for("admin_settlements"))

    if order.get("payment_status") not in ["COLLECTED_BY_RIDER", "COD_COLLECTED_BY_RIDER"]:
        flash("COD cash has not been marked collected by rider for this order.", "warning")
        return redirect(url_for("admin_settlements"))

    if order.get("rider_cash_settlement_status") == "RECEIVED":
        flash("Rider cash is already received for this order.", "info")
        return redirect(url_for("admin_settlements"))

    now = datetime.utcnow().isoformat()
    note = (request.form.get("note") or "").strip()
    transition = build_rider_cash_received_state(order, oid_obj, admin_user, note, now)
    rider_cash_to_submit = transition["rider_cash_to_submit"]
    platform_fee = transition["platform_fee"]
    store_payout_amount = transition["store_payout_amount"]
    settlement_event = transition["event"]
    update_data = transition["order_update"]

    cash_claim = mongo.orders.update_one(
        {
            "_id": oid_obj,
            "rider_cash_settlement_status": {"$ne": "RECEIVED"},
        },
        {
            "$set": update_data,
            "$push": {
                "settlement_audit_logs": settlement_event
            }
        }
    )
    if cash_claim.modified_count != 1:
        flash("Rider cash was already received or the order changed. Please refresh.", "warning")
        return redirect(url_for("admin_settlements"))

    mongo.transactions.update_many(
        {"order_id": oid_obj},
        {"$set": transition["transaction_update"]}
    )

    mongo.order_events.insert_one({
        "order_id": oid_obj,
        "status": "RIDER_CASH_RECEIVED_BY_ADMIN",
        "note": transition["event_note"],
        "created_at": now
    })

    flash("Rider cash received by Admin. Store payout is now pending.", "success")
    return redirect(url_for("admin_settlements"))


@app.route("/admin/settlements/<oid>/store-payout-paid", methods=["POST"], endpoint="admin_settlement_store_payout_paid")
@login_required(role="admin")
def admin_settlement_store_payout_paid(oid):
    admin_user = current_user() or {}
    oid_obj = _admin_order_id_or_none(oid)

    if not oid_obj:
        flash("Invalid order.", "danger")
        return redirect(url_for("admin_settlements"))

    raw_order = mongo.orders.find_one({"_id": oid_obj})
    if not raw_order:
        flash("Order not found.", "danger")
        return redirect(url_for("admin_settlements"))

    order = _admin_hydrate_settlement_order(raw_order)
    if (order.get("status") or "").upper() != "DELIVERED":
        flash("Store payout can be marked only after delivery.", "warning")
        return redirect(url_for("admin_settlements"))

    state = order.get("finance_reconciliation") or finance_reconciliation_snapshot(order)
    if not state.get("store_payout_required"):
        flash("Admin Store payout is not required because the Store received the customer payment directly.", "info")
        return redirect(url_for("admin_settlements"))
    if not state.get("customer_payment_reconciled"):
        flash("Cannot pay Store before the customer/business payment is reconciled.", "warning")
        return redirect(url_for("admin_settlements"))
    if state.get("refund_unresolved"):
        flash("Resolve the active return/refund before paying the Store.", "warning")
        return redirect(url_for("admin_settlements"))

    current_payout_status = (order.get("store_payout_status") or "").upper()
    if current_payout_status == "PAID":
        flash("Store payout is already marked paid.", "info")
        return redirect(url_for("admin_settlements"))
    if current_payout_status == "PROCESSING":
        flash("Store payout is already being processed. Please refresh before retrying.", "warning")
        return redirect(url_for("admin_settlements"))

    note = (request.form.get("note") or "").strip()[:250]
    reference_no = (request.form.get("reference_no") or "").strip()[:120]
    payout_mode = (request.form.get("payout_mode") or "CASH").strip().upper()
    if payout_mode not in {"CASH", "UPI", "BANK_TRANSFER", "ADJUSTMENT"}:
        payout_mode = "CASH"

    payout_base = calculate_store_payout_base(order)
    original_store_payout_amount = payout_base["original_store_payout_amount"]
    store_refund_deduction = payout_base["store_refund_deduction"]
    adjusted_store_payout = payout_base["adjusted_store_payout"]
    store_adjustment_due = payout_base["store_adjustment_due"]
    settlement_impact = payout_base["settlement_impact"]

    # Validate the transfer method before reserving the payout or consuming any
    # carry-forward Store refund adjustments.
    outstanding_adjustment = finance_store_outstanding_adjustment_total(order.get("store_id"))
    carry_preview = round(min(outstanding_adjustment, adjusted_store_payout), 2)
    final_preview = round(max(adjusted_store_payout - carry_preview, 0), 2)
    if final_preview > 0 and payout_mode in {"UPI", "BANK_TRANSFER"} and not reference_no:
        flash("Payment reference is required for UPI or Bank Transfer Store payout.", "warning")
        return redirect(url_for("admin_settlements"))

    previous_status = order.get("store_payout_status") or "PENDING_AFTER_DELIVERY"
    processing_at = datetime.utcnow().isoformat()

    # Claim the order first. This compare-and-set prevents two Admin requests from
    # paying the same Store order concurrently.
    claim = mongo.orders.update_one(
        {
            "_id": oid_obj,
            "store_payout_status": {"$nin": ["PAID", "PROCESSING", "NOT_REQUIRED"]},
        },
        {"$set": {
            "store_payout_status": "PROCESSING",
            "store_payout_processing_at": processing_at,
            "store_payout_processing_by": str(admin_user.get("id") or admin_user.get("_id") or ""),
            "updated_at": processing_at,
        }}
    )
    if claim.modified_count != 1:
        latest = mongo.orders.find_one({"_id": oid_obj}) or {}
        if (latest.get("store_payout_status") or "").upper() == "PAID":
            flash("Store payout is already paid.", "info")
        else:
            flash("Store payout could not be reserved because another request is processing it.", "warning")
        return redirect(url_for("admin_settlements"))

    carry_applied = 0.0
    carry_applications = []
    finalized = False

    try:
        carry_applied, carry_applications = finance_apply_store_adjustments(
            order.get("store_id"),
            oid_obj,
            adjusted_store_payout,
            actor=admin_user,
        )
        final_store_payout = round(max(adjusted_store_payout - carry_applied, 0), 2)
        effective_mode = "ADJUSTMENT" if final_store_payout <= 0 else payout_mode
        paid_at = datetime.utcnow().isoformat()
        platform_fee = _admin_settlement_money(order.get("platform_fee"))

        final_impact = settlement_impact
        if carry_applied > 0:
            final_impact = "CARRY_FORWARD_ADJUSTMENT_APPLIED"

        settlement_event = {
            "action": "STORE_PAYOUT_PAID_BY_ADMIN",
            "order_id": str(oid_obj),
            "store_id": str(order.get("store_id") or ""),
            "store_name": order.get("store_name") or "",
            "amount_paid": final_store_payout,
            "original_store_payout_amount": original_store_payout_amount,
            "store_refund_deduction": store_refund_deduction,
            "adjusted_store_payout": adjusted_store_payout,
            "carry_forward_adjustment_applied": carry_applied,
            "carry_forward_applications": carry_applications,
            "store_adjustment_due": store_adjustment_due,
            "settlement_impact": final_impact,
            "platform_fee": platform_fee,
            "payment_mode": effective_mode,
            "reference_no": reference_no,
            "created_by": str(admin_user.get("id") or admin_user.get("_id") or ""),
            "created_by_name": admin_user.get("name") or admin_user.get("email") or "Admin",
            "created_by_role": "admin",
            "note": note,
            "created_at": paid_at
        }

        update_data = {
            "store_payout_status": "PAID",
            "store_payout_paid_at": paid_at,
            "store_payout_marked_by": str(admin_user.get("id") or admin_user.get("_id") or ""),
            "store_payout_marked_by_name": admin_user.get("name") or admin_user.get("email") or "Admin",
            "store_payout_note": note,
            "store_payout_reference_no": reference_no,
            "store_payout_mode": effective_mode,
            "original_store_payout_amount": original_store_payout_amount,
            "store_refund_deduction": store_refund_deduction,
            "refund_deduction": store_refund_deduction,
            "adjusted_store_payout": adjusted_store_payout,
            "store_payout_before_carry_forward": adjusted_store_payout,
            "store_carry_forward_adjustment_applied": carry_applied,
            "store_carry_forward_adjustment_applications": carry_applications,
            "store_payout_amount": final_store_payout,
            "store_payout_paid_amount": final_store_payout,
            "store_adjustment_due": store_adjustment_due,
            "settlement_impact": final_impact,
            "store_settlement_status": "PAID",
            "order_settlement_status": "BUSINESS_RECONCILED",
            "settlement_status": "BUSINESS_RECONCILED",
            "last_settlement_event": settlement_event,
            "updated_at": paid_at
        }

        final_update = mongo.orders.update_one(
            {"_id": oid_obj, "store_payout_status": "PROCESSING"},
            {"$set": update_data, "$push": {"settlement_audit_logs": settlement_event}}
        )
        if final_update.modified_count != 1:
            raise RuntimeError("Store payout finalization lost its processing lock.")

        finalized = True

    except Exception as exc:
        # If the payout itself was not finalized, restore any adjustment ledger
        # amounts consumed during this attempt and release the order claim.
        if carry_applications and not finalized:
            try:
                finance_rollback_store_adjustments(carry_applications, oid_obj)
            except Exception as rollback_exc:
                log_warning("[STORE PAYOUT ADJUSTMENT ROLLBACK ERROR]", str(rollback_exc))

        mongo.orders.update_one(
            {"_id": oid_obj, "store_payout_status": "PROCESSING"},
            {"$set": {
                "store_payout_status": previous_status,
                "updated_at": datetime.utcnow().isoformat()
            }}
        )
        log_warning("[STORE PAYOUT ERROR]", str(exc))
        flash("Store payout could not be completed safely. No duplicate payout was recorded.", "danger")
        return redirect(url_for("admin_settlements"))

    # The order document is the authoritative settlement record. The transaction
    # mirror and operational event are updated after finalization; a failure here
    # must not reverse an already-completed Store payout.
    try:
        mongo.transactions.update_many(
            {"order_id": oid_obj},
            {"$set": update_data}
        )
    except Exception as exc:
        log_warning("[STORE PAYOUT TRANSACTION MIRROR ERROR]", str(exc))

    try:
        mongo.order_events.insert_one({
            "order_id": oid_obj,
            "status": "STORE_PAYOUT_PAID",
            "note": (
                f"Store payout ₹{update_data['store_payout_paid_amount']:.2f} settled by Admin. "
                f"Refund deduction ₹{store_refund_deduction:.2f}; "
                f"carry-forward adjustment ₹{carry_applied:.2f}."
            ),
            "created_at": update_data["store_payout_paid_at"]
        })
    except Exception as exc:
        log_warning("[STORE PAYOUT EVENT LOG ERROR]", str(exc))

    flash(
        f"Store payout settled: ₹{update_data['store_payout_paid_amount']:.2f}."
        + (
            f" Carry-forward adjustment applied: ₹{carry_applied:.2f}."
            if carry_applied > 0
            else ""
        ),
        "success"
    )
    return redirect(url_for("admin_settlements"))


@app.route("/admin/platform-earnings", methods=["GET"], endpoint="admin_platform_earnings")
@login_required(role="admin")
def admin_platform_earnings():
    """Read-only platform-fee reconciliation report across every payment flow."""
    q = (request.args.get("q") or "").strip()
    status_filter = (request.args.get("status") or "").strip().upper()
    payment_filter = (request.args.get("payment") or "").strip().upper()
    date_from = (request.args.get("from") or "").strip()
    date_to = (request.args.get("to") or "").strip()

    raw_orders = list(mongo.orders.find({"platform_fee": {"$exists": True}}).sort("created_at", -1))
    rows = []
    for order in raw_orders:
        row = _admin_platform_earning_row(order)
        if row.get("gross_platform_fee", 0) <= 0 and row.get("net_platform_fee", 0) <= 0:
            continue

        report_date = row.get("report_date") or ""
        if date_from and report_date and report_date[:10] < date_from:
            continue
        if date_to and report_date and report_date[:10] > date_to:
            continue

        payment_method = (row.get("payment_method") or "").upper()
        if payment_filter == "ONLINE" and payment_method in {"COD", "CASH_ON_DELIVERY", "COD_RIDER_COLLECTION"}:
            continue
        if payment_filter == "COD" and payment_method not in {"COD", "CASH_ON_DELIVERY", "COD_RIDER_COLLECTION"}:
            continue
        if payment_filter not in {"", "ONLINE", "COD"} and payment_filter != payment_method:
            continue

        earning_status = (row.get("platform_earning_status") or "").upper()
        if status_filter and status_filter != earning_status and status_filter != (row.get("platform_fee_status") or "").upper():
            continue

        if q:
            haystack = " ".join([
                str(row.get("id") or ""), str(row.get("store_name") or ""), str(row.get("customer_name") or ""),
                str(row.get("customer_phone") or ""), str(row.get("payment_method") or ""), str(row.get("payment_flow") or ""),
                str(row.get("payment_collection_label") or ""), str(row.get("payment_receiver_label") or ""),
                str(row.get("platform_earning_status") or ""), str(row.get("order_settlement_status") or "")
            ]).lower()
            if q.lower() not in haystack:
                continue
        rows.append(row)

    received_rows = [r for r in rows if (r.get("platform_earning_status") or "").upper() == "RECEIVED"]
    pending_rows = [r for r in rows if (r.get("platform_earning_status") or "").upper() not in {"RECEIVED", "NOT_REQUIRED", "ADJUSTED"}]
    cod_rows = [r for r in rows if (r.get("payment_method") or "").upper() in {"COD", "CASH_ON_DELIVERY", "COD_RIDER_COLLECTION"}]
    online_rows = [r for r in rows if r not in cod_rows]
    store_due_rows = [r for r in rows if (r.get("platform_earning_status") or "").upper() == "DUE_FROM_STORE"]
    partner_due_rows = [r for r in rows if (r.get("platform_earning_status") or "").upper() == "PENDING_PARTNER_REMITTANCE"]

    metrics = {
        "total_records": len(rows),
        "gross_platform_fee": round(sum(float(r.get("gross_platform_fee") or 0) for r in rows), 2),
        "refund_platform_fee": round(sum(float(r.get("refund_platform_fee") or 0) for r in rows), 2),
        "total_platform_fee": round(sum(float(r.get("net_platform_fee") or 0) for r in rows), 2),
        "received_count": len(received_rows),
        "received_amount": round(sum(float(r.get("net_platform_fee") or 0) for r in received_rows), 2),
        "pending_count": len(pending_rows),
        "pending_amount": round(sum(float(r.get("net_platform_fee") or 0) for r in pending_rows), 2),
        "cod_count": len(cod_rows),
        "cod_amount": round(sum(float(r.get("net_platform_fee") or 0) for r in cod_rows), 2),
        "online_count": len(online_rows),
        "online_amount": round(sum(float(r.get("net_platform_fee") or 0) for r in online_rows), 2),
        "store_due_count": len(store_due_rows),
        "store_due_amount": round(sum(float(r.get("net_platform_fee") or 0) for r in store_due_rows), 2),
        "partner_due_count": len(partner_due_rows),
        "partner_due_amount": round(sum(float(r.get("net_platform_fee") or 0) for r in partner_due_rows), 2),
    }

    return render_template(
        "admin_platform_earnings.html",
        user=current_user(), earnings=rows, metrics=metrics, q=q,
        status_filter=status_filter, payment_filter=payment_filter,
        date_from=date_from, date_to=date_to,
        active_group="settlements", active_page="platform_earnings"
    )


@app.route("/admin/platform-earnings/export.csv", methods=["GET"], endpoint="admin_platform_earnings_export_csv")
@login_required(role="admin")
def admin_platform_earnings_export_csv():
    q = (request.args.get("q") or "").strip()
    status_filter = (request.args.get("status") or "").strip().upper()
    payment_filter = (request.args.get("payment") or "").strip().upper()
    date_from = (request.args.get("from") or "").strip()
    date_to = (request.args.get("to") or "").strip()

    output = [[
        "Order ID", "Store", "Customer", "Payment Method", "Payment Flow", "Collection", "Business Receiver",
        "Customer Payment Reconciliation", "Gross Platform Fee", "Refund Platform Fee", "Net Platform Fee",
        "Platform Fee Status", "Platform Reconciliation", "Rider Cash Status", "UPI Reconciliation",
        "External Partner Remittance", "Store Payout Status", "Order Settlement", "Date"
    ]]

    for raw in mongo.orders.find({"platform_fee": {"$exists": True}}).sort("created_at", -1):
        row = _admin_platform_earning_row(raw)
        if row.get("gross_platform_fee", 0) <= 0 and row.get("net_platform_fee", 0) <= 0:
            continue
        report_date = row.get("report_date") or ""
        if date_from and report_date and report_date[:10] < date_from:
            continue
        if date_to and report_date and report_date[:10] > date_to:
            continue
        pm = (row.get("payment_method") or "").upper()
        if payment_filter == "ONLINE" and pm in {"COD", "CASH_ON_DELIVERY", "COD_RIDER_COLLECTION"}:
            continue
        if payment_filter == "COD" and pm not in {"COD", "CASH_ON_DELIVERY", "COD_RIDER_COLLECTION"}:
            continue
        if payment_filter not in {"", "ONLINE", "COD"} and payment_filter != pm:
            continue
        if status_filter and status_filter not in {(row.get("platform_earning_status") or "").upper(), (row.get("platform_fee_status") or "").upper()}:
            continue
        if q:
            haystack = " ".join([str(row.get(k) or "") for k in [
                "id", "store_name", "customer_name", "customer_phone", "payment_method", "payment_flow",
                "payment_collection_label", "payment_receiver_label", "platform_earning_status", "order_settlement_status"
            ]]).lower()
            if q.lower() not in haystack:
                continue
        output.append([
            row.get("id"), row.get("store_name"), row.get("customer_name"), row.get("payment_method"),
            row.get("payment_flow") or row.get("official_payment_mode") or "", row.get("payment_collection_label") or "",
            row.get("payment_receiver_label") or "", row.get("payment_reconciliation_status") or "",
            row.get("gross_platform_fee"), row.get("refund_platform_fee"), row.get("net_platform_fee"),
            row.get("platform_fee_status"), row.get("platform_earning_status"), row.get("rider_cash_settlement_status"),
            row.get("upi_delivery_reconciliation_status"), row.get("external_cod_remittance_status"),
            row.get("store_payout_status"), row.get("order_settlement_status"), report_date
        ])

    return _admin_csv_response(output, "nefresh_platform_earnings.csv")


@app.route("/admin/settlement-audit-logs", methods=["GET"], endpoint="admin_settlement_audit_logs")
@login_required(role="admin")
def admin_settlement_audit_logs():
    """
    Admin read-only settlement audit log page.

    Shows settlement_audit_logs pushed inside orders during:
    - UPI_AT_DELIVERY_VERIFIED_BY_ADMIN
    - RIDER_CASH_RECEIVED_BY_ADMIN
    - DELIVERY_PARTNER_MONTHLY_SETTLEMENT_PAID
    - STORE_PAYOUT_PAID_BY_ADMIN
    - STORE_PLATFORM_FEE_RECEIVED_BY_ADMIN
    - EXTERNAL_PARTNER_REMITTANCE_RECEIVED
    - STORE_CUSTOMER_PAYMENT_RECORDED
    - STORE_REFUND_ADJUSTMENT_CREATED
    - REFUND_PROCESSED_BY_ADMIN

    No update action is available here.
    """
    q = (request.args.get("q") or "").strip()
    action_filter = (request.args.get("action") or "").strip().upper()
    date_from = (request.args.get("from") or "").strip()
    date_to = (request.args.get("to") or "").strip()

    raw_orders = list(
        mongo.orders.find({
            "$or": [
                {"settlement_audit_logs": {"$exists": True, "$ne": []}},
                {"last_settlement_event": {"$exists": True}}
            ]
        }).sort("updated_at", -1)
    )

    logs = []

    for order in raw_orders:
        hydrated_order = _admin_hydrate_settlement_order(dict(order))

        order_id = hydrated_order.get("id") or str(order.get("_id") or "")
        short_order_id = order_id[-6:] if order_id else ""

        audit_entries = order.get("settlement_audit_logs") or []

        if not isinstance(audit_entries, list):
            audit_entries = []

        # Fallback for orders that only have last_settlement_event.
        last_event = order.get("last_settlement_event")
        if isinstance(last_event, dict):
            last_action = last_event.get("action")
            already_exists = any(
                isinstance(entry, dict)
                and entry.get("action") == last_action
                and entry.get("created_at") == last_event.get("created_at")
                for entry in audit_entries
            )

            if not already_exists:
                audit_entries.append(last_event)

        for entry in audit_entries:
            if not isinstance(entry, dict):
                continue

            action = (entry.get("action") or "").strip().upper()
            created_at = str(entry.get("created_at") or "")

            if action_filter and action != action_filter:
                continue

            if date_from and created_at and created_at[:10] < date_from:
                continue

            if date_to and created_at and created_at[:10] > date_to:
                continue

            amount_received = _admin_settlement_money(
                entry.get("amount_received") if entry.get("amount_received") is not None else entry.get("amount"),
                0
            )
            amount_paid = _admin_settlement_money(entry.get("amount_paid"), 0)

            refund_amount = _admin_settlement_money(
                entry.get("refund_amount"),
                hydrated_order.get("refund_amount") or 0
            )

            refund_items_amount = _admin_settlement_money(
                entry.get("refund_items_amount"),
                hydrated_order.get("refund_items_amount") or 0
            )

            refund_delivery_fee = _admin_settlement_money(
                entry.get("refund_delivery_fee"),
                hydrated_order.get("refund_delivery_fee") or 0
            )

            refund_platform_fee = _admin_settlement_money(
                entry.get("refund_platform_fee"),
                hydrated_order.get("refund_platform_fee") or 0
            )

            refund_tip_amount = _admin_settlement_money(
                entry.get("refund_tip_amount"),
                hydrated_order.get("refund_tip_amount") or 0
            )

            original_store_payout_amount = _admin_settlement_money(
                entry.get("original_store_payout_amount"),
                hydrated_order.get("original_store_payout_amount") or hydrated_order.get("items_subtotal") or 0
            )

            store_refund_deduction = _admin_settlement_money(
                entry.get("store_refund_deduction"),
                hydrated_order.get("store_refund_deduction") or 0
            )

            adjusted_store_payout = _admin_settlement_money(
                entry.get("adjusted_store_payout"),
                hydrated_order.get("adjusted_store_payout") or hydrated_order.get("store_payout_amount") or 0
            )

            store_adjustment_due = _admin_settlement_money(
                entry.get("store_adjustment_due"),
                hydrated_order.get("store_adjustment_due") or 0
            )

            store_payout_amount = _admin_settlement_money(
                entry.get("store_payout_amount"),
                hydrated_order.get("store_payout_amount") or adjusted_store_payout or 0
            )

            platform_fee = _admin_settlement_money(
                entry.get("platform_fee"),
                hydrated_order.get("platform_fee") or 0
            )

            amount_display = (
                refund_amount
                if refund_amount > 0
                else amount_received
                if amount_received > 0
                else amount_paid
            )

            log_row = {
                "order_id": order_id,
                "short_order_id": short_order_id,
                "action": action,
                "action_label": _admin_settlement_action_label(action),

                "amount_received": amount_received,
                "amount_paid": amount_paid,
                "refund_amount": refund_amount,
                "refund_items_amount": refund_items_amount,
                "refund_delivery_fee": refund_delivery_fee,
                "refund_platform_fee": refund_platform_fee,
                "refund_tip_amount": refund_tip_amount,
                "amount_display": amount_display,

                "platform_fee": platform_fee,
                "store_payout_amount": store_payout_amount,
                "original_store_payout_amount": original_store_payout_amount,
                "store_refund_deduction": store_refund_deduction,
                "adjusted_store_payout": adjusted_store_payout,
                "store_adjustment_due": store_adjustment_due,
                "settlement_impact": entry.get("settlement_impact") or hydrated_order.get("settlement_impact") or "",

                "payment_mode": entry.get("payment_mode") or entry.get("refund_method") or entry.get("channel") or "",
                "reference_no": entry.get("reference_no") or entry.get("refund_reference") or entry.get("reference") or "",
                "refund_method": entry.get("refund_method") or hydrated_order.get("refund_method") or "",
                "refund_reference": entry.get("refund_reference") or hydrated_order.get("refund_reference") or "",
                "note": entry.get("note") or "",

                "created_by": entry.get("created_by") or "",
                "created_by_name": entry.get("created_by_name") or "Admin",
                "created_by_role": entry.get("created_by_role") or "admin",
                "created_at": created_at,

                "store_id": entry.get("store_id") or str(hydrated_order.get("store_id") or ""),
                "store_name": entry.get("store_name") or hydrated_order.get("store_name") or "",
                "customer_name": hydrated_order.get("customer_name") or "",
                "customer_phone": hydrated_order.get("customer_phone") or "",
                "delivery_partner_name": hydrated_order.get("delivery_partner_name") or "",
                "delivery_partner_phone": hydrated_order.get("delivery_partner_phone") or "",

                "payment_method": hydrated_order.get("payment_method") or "",
                "payment_status": hydrated_order.get("payment_status") or "",
                "payment_collection_label": hydrated_order.get("payment_collection_label") or "",
                "payment_receiver_label": hydrated_order.get("payment_receiver_label") or "",
                "payment_reconciliation_status": hydrated_order.get("payment_reconciliation_status") or "",
                "platform_fee_reconciliation_status": hydrated_order.get("platform_fee_reconciliation_status") or hydrated_order.get("platform_fee_status") or "",
                "business_reconciliation_complete": bool(hydrated_order.get("business_reconciliation_complete")),
                "delivery_payout_model": hydrated_order.get("delivery_payout_model") or "",
                "rider_cash_settlement_status": hydrated_order.get("rider_cash_settlement_status") or "",
                "platform_fee_status": hydrated_order.get("platform_fee_status") or "",
                "store_payout_status": hydrated_order.get("store_payout_status") or "",
                "order_settlement_status": hydrated_order.get("order_settlement_status") or "",
            }

            if q:
                haystack = " ".join([
                    str(log_row.get("order_id") or ""),
                    str(log_row.get("short_order_id") or ""),
                    str(log_row.get("action") or ""),
                    str(log_row.get("store_name") or ""),
                    str(log_row.get("customer_name") or ""),
                    str(log_row.get("customer_phone") or ""),
                    str(log_row.get("delivery_partner_name") or ""),
                    str(log_row.get("created_by_name") or ""),
                    str(log_row.get("reference_no") or ""),
                    str(log_row.get("note") or ""),
                    str(log_row.get("payment_method") or ""),
                    str(log_row.get("payment_collection_label") or ""),
                    str(log_row.get("payment_receiver_label") or ""),
                    str(log_row.get("payment_reconciliation_status") or ""),
                    str(log_row.get("platform_fee_reconciliation_status") or ""),
                    str(log_row.get("refund_method") or ""),
                    str(log_row.get("refund_reference") or ""),
                    str(log_row.get("settlement_impact") or ""),
                    str(log_row.get("order_settlement_status") or "")
                ]).lower()

                if q.lower() not in haystack:
                    continue

            logs.append(log_row)

    logs.sort(
        key=lambda row: str(row.get("created_at") or ""),
        reverse=True
    )

    upi_verification_logs = [
        row for row in logs
        if row.get("action") == "UPI_AT_DELIVERY_VERIFIED_BY_ADMIN"
    ]

    rider_cash_logs = [
        row for row in logs
        if row.get("action") == "RIDER_CASH_RECEIVED_BY_ADMIN"
    ]

    delivery_monthly_logs = [
        row for row in logs
        if row.get("action") == "DELIVERY_PARTNER_MONTHLY_SETTLEMENT_PAID"
    ]

    store_payout_logs = [
        row for row in logs
        if row.get("action") == "STORE_PAYOUT_PAID_BY_ADMIN"
    ]

    refund_logs = [
        row for row in logs
        if row.get("action") == "REFUND_PROCESSED_BY_ADMIN"
    ]

    store_customer_payment_logs = [
        row for row in logs
        if row.get("action") == "STORE_CUSTOMER_PAYMENT_RECORDED"
    ]

    store_platform_fee_logs = [
        row for row in logs
        if row.get("action") == "STORE_PLATFORM_FEE_RECEIVED_BY_ADMIN"
    ]

    external_partner_remittance_logs = [
        row for row in logs
        if row.get("action") == "EXTERNAL_PARTNER_REMITTANCE_RECEIVED"
    ]

    store_refund_adjustment_logs = [
        row for row in logs
        if row.get("action") == "STORE_REFUND_ADJUSTMENT_CREATED"
    ]

    metrics = {
        "total_logs": len(logs),
        "upi_verification_logs": len(upi_verification_logs),
        "upi_verified_amount": round(
            sum(float(row.get("amount_received") or 0) for row in upi_verification_logs),
            2
        ),
        "rider_cash_logs": len(rider_cash_logs),
        "delivery_monthly_logs": len(delivery_monthly_logs),
        "delivery_monthly_paid_amount": round(
            sum(float(row.get("amount_paid") or 0) for row in delivery_monthly_logs),
            2
        ),
        "store_payout_logs": len(store_payout_logs),
        "refund_logs": len(refund_logs),

        "rider_cash_received_amount": round(
            sum(float(row.get("amount_received") or 0) for row in rider_cash_logs),
            2
        ),

        "store_payout_paid_amount": round(
            sum(float(row.get("amount_paid") or 0) for row in store_payout_logs),
            2
        ),

        "refund_processed_amount": round(
            sum(float(row.get("refund_amount") or 0) for row in refund_logs),
            2
        ),

        "store_refund_deduction_amount": round(
            sum(float(row.get("store_refund_deduction") or 0) for row in refund_logs),
            2
        ),

        "store_adjustment_due_amount": round(
            sum(float(row.get("store_adjustment_due") or 0) for row in refund_logs),
            2
        ),

        "store_customer_payment_logs": len(store_customer_payment_logs),
        "store_customer_payment_amount": round(
            sum(float(row.get("amount_received") or 0) for row in store_customer_payment_logs), 2
        ),
        "store_platform_fee_logs": len(store_platform_fee_logs),
        "store_platform_fee_received_amount": round(
            sum(float(row.get("amount_received") or row.get("platform_fee") or 0) for row in store_platform_fee_logs), 2
        ),
        "external_partner_remittance_logs": len(external_partner_remittance_logs),
        "external_partner_remittance_amount": round(
            sum(float(row.get("amount_received") or 0) for row in external_partner_remittance_logs), 2
        ),
        "store_refund_adjustment_logs": len(store_refund_adjustment_logs),
        "store_refund_adjustment_created_amount": round(
            sum(float(row.get("store_adjustment_due") or row.get("amount_display") or 0) for row in store_refund_adjustment_logs), 2
        ),

        "platform_fee_tracked": round(
            sum(float(row.get("platform_fee") or 0) for row in logs),
            2
        ),
    }

    return render_template(
        "admin_settlement_audit_logs.html",
        user=current_user(),
        logs=logs,
        metrics=metrics,
        q=q,
        action_filter=action_filter,
        date_from=date_from,
        date_to=date_to,
        active_group="settlements",
        active_page="settlement_audit_logs"
    )


@app.route("/admin/settlement-audit-logs/export.csv", methods=["GET"], endpoint="admin_settlement_audit_logs_export_csv")
@login_required(role="admin")
def admin_settlement_audit_logs_export_csv():
    q = (request.args.get("q") or "").strip()
    action_filter = (request.args.get("action") or "").strip().upper()
    date_from = (request.args.get("from") or "").strip()
    date_to = (request.args.get("to") or "").strip()

    raw_orders = list(
        mongo.orders.find({
            "$or": [
                {"settlement_audit_logs": {"$exists": True, "$ne": []}},
                {"last_settlement_event": {"$exists": True}}
            ]
        }).sort("updated_at", -1)
    )

    log_rows = []

    for order in raw_orders:
        hydrated_order = _admin_hydrate_settlement_order(dict(order))

        order_id = hydrated_order.get("id") or str(order.get("_id") or "")
        short_order_id = order_id[-6:] if order_id else ""

        audit_entries = order.get("settlement_audit_logs") or []

        if not isinstance(audit_entries, list):
            audit_entries = []

        last_event = order.get("last_settlement_event")
        if isinstance(last_event, dict):
            last_action = last_event.get("action")

            already_exists = any(
                isinstance(entry, dict)
                and entry.get("action") == last_action
                and entry.get("created_at") == last_event.get("created_at")
                for entry in audit_entries
            )

            if not already_exists:
                audit_entries.append(last_event)

        for entry in audit_entries:
            if not isinstance(entry, dict):
                continue

            action = (entry.get("action") or "").strip().upper()
            created_at = str(entry.get("created_at") or "")

            if action_filter and action != action_filter:
                continue

            if date_from and created_at and created_at[:10] < date_from:
                continue

            if date_to and created_at and created_at[:10] > date_to:
                continue

            amount_received = _admin_settlement_money(
                entry.get("amount_received") if entry.get("amount_received") is not None else entry.get("amount"),
                0
            )
            amount_paid = _admin_settlement_money(entry.get("amount_paid"), 0)

            refund_amount = _admin_settlement_money(
                entry.get("refund_amount"),
                hydrated_order.get("refund_amount") or 0
            )

            refund_items_amount = _admin_settlement_money(
                entry.get("refund_items_amount"),
                hydrated_order.get("refund_items_amount") or 0
            )

            refund_delivery_fee = _admin_settlement_money(
                entry.get("refund_delivery_fee"),
                hydrated_order.get("refund_delivery_fee") or 0
            )

            refund_platform_fee = _admin_settlement_money(
                entry.get("refund_platform_fee"),
                hydrated_order.get("refund_platform_fee") or 0
            )

            refund_tip_amount = _admin_settlement_money(
                entry.get("refund_tip_amount"),
                hydrated_order.get("refund_tip_amount") or 0
            )

            original_store_payout_amount = _admin_settlement_money(
                entry.get("original_store_payout_amount"),
                hydrated_order.get("original_store_payout_amount") or hydrated_order.get("items_subtotal") or 0
            )

            store_refund_deduction = _admin_settlement_money(
                entry.get("store_refund_deduction"),
                hydrated_order.get("store_refund_deduction") or 0
            )

            adjusted_store_payout = _admin_settlement_money(
                entry.get("adjusted_store_payout"),
                hydrated_order.get("adjusted_store_payout") or hydrated_order.get("store_payout_amount") or 0
            )

            store_adjustment_due = _admin_settlement_money(
                entry.get("store_adjustment_due"),
                hydrated_order.get("store_adjustment_due") or 0
            )

            store_payout_amount = _admin_settlement_money(
                entry.get("store_payout_amount"),
                hydrated_order.get("store_payout_amount") or adjusted_store_payout or 0
            )

            platform_fee = _admin_settlement_money(
                entry.get("platform_fee"),
                hydrated_order.get("platform_fee") or 0
            )

            amount_display = (
                refund_amount
                if refund_amount > 0
                else amount_received
                if amount_received > 0
                else amount_paid
            )

            log_row = {
                "order_id": order_id,
                "short_order_id": short_order_id,
                "action": action,
                "action_label": _admin_settlement_action_label(action),
                "amount_received": amount_received,
                "amount_paid": amount_paid,
                "refund_amount": refund_amount,
                "refund_items_amount": refund_items_amount,
                "refund_delivery_fee": refund_delivery_fee,
                "refund_platform_fee": refund_platform_fee,
                "refund_tip_amount": refund_tip_amount,
                "amount_display": amount_display,

                "platform_fee": platform_fee,
                "store_payout_amount": store_payout_amount,
                "original_store_payout_amount": original_store_payout_amount,
                "store_refund_deduction": store_refund_deduction,
                "adjusted_store_payout": adjusted_store_payout,
                "store_adjustment_due": store_adjustment_due,
                "settlement_impact": entry.get("settlement_impact") or hydrated_order.get("settlement_impact") or "",

                "payment_mode": entry.get("payment_mode") or entry.get("refund_method") or entry.get("channel") or "",
                "reference_no": entry.get("reference_no") or entry.get("refund_reference") or entry.get("reference") or "",
                "refund_method": entry.get("refund_method") or hydrated_order.get("refund_method") or "",
                "refund_reference": entry.get("refund_reference") or hydrated_order.get("refund_reference") or "",
                "note": entry.get("note") or "",
                "created_by": entry.get("created_by") or "",
                "created_by_name": entry.get("created_by_name") or "Admin",
                "created_by_role": entry.get("created_by_role") or "admin",
                "created_at": created_at,
                "store_id": entry.get("store_id") or str(hydrated_order.get("store_id") or ""),
                "store_name": entry.get("store_name") or hydrated_order.get("store_name") or "",
                "customer_name": hydrated_order.get("customer_name") or "",
                "customer_phone": hydrated_order.get("customer_phone") or "",
                "delivery_partner_name": hydrated_order.get("delivery_partner_name") or "",
                "delivery_partner_phone": hydrated_order.get("delivery_partner_phone") or "",
                "payment_method": hydrated_order.get("payment_method") or "",
                "payment_status": hydrated_order.get("payment_status") or "",
                "payment_collection_label": hydrated_order.get("payment_collection_label") or "",
                "payment_receiver_label": hydrated_order.get("payment_receiver_label") or "",
                "payment_reconciliation_status": hydrated_order.get("payment_reconciliation_status") or "",
                "platform_fee_reconciliation_status": hydrated_order.get("platform_fee_reconciliation_status") or hydrated_order.get("platform_fee_status") or "",
                "business_reconciliation_complete": bool(hydrated_order.get("business_reconciliation_complete")),
                "delivery_payout_model": hydrated_order.get("delivery_payout_model") or "",
                "rider_cash_settlement_status": hydrated_order.get("rider_cash_settlement_status") or "",
                "platform_fee_status": hydrated_order.get("platform_fee_status") or "",
                "store_payout_status": hydrated_order.get("store_payout_status") or "",
                "order_settlement_status": hydrated_order.get("order_settlement_status") or "",
            }

            if q:
                haystack = " ".join([
                    str(log_row.get("order_id") or ""),
                    str(log_row.get("short_order_id") or ""),
                    str(log_row.get("action") or ""),
                    str(log_row.get("store_name") or ""),
                    str(log_row.get("customer_name") or ""),
                    str(log_row.get("customer_phone") or ""),
                    str(log_row.get("delivery_partner_name") or ""),
                    str(log_row.get("created_by_name") or ""),
                    str(log_row.get("reference_no") or ""),
                    str(log_row.get("note") or ""),
                    str(log_row.get("payment_method") or ""),
                    str(log_row.get("payment_collection_label") or ""),
                    str(log_row.get("payment_receiver_label") or ""),
                    str(log_row.get("payment_reconciliation_status") or ""),
                    str(log_row.get("platform_fee_reconciliation_status") or ""),
                    str(log_row.get("refund_method") or ""),
                    str(log_row.get("refund_reference") or ""),
                    str(log_row.get("settlement_impact") or ""),
                    str(log_row.get("order_settlement_status") or "")
                ]).lower()

                if q.lower() not in haystack:
                    continue

            log_rows.append(log_row)

    log_rows.sort(
        key=lambda row: str(row.get("created_at") or ""),
        reverse=True
    )

    rows = [[
        "Order ID",
        "Short Order ID",
        "Action",
        "Action Label",
        "Amount Received",
        "Amount Paid",
        "Refund Amount",
        "Refund Items Amount",
        "Refund Delivery Fee",
        "Refund Platform Fee",
        "Refund Tip Amount",
        "Amount Display",
        "Platform Fee",
        "Original Store Payout",
        "Store Refund Deduction",
        "Adjusted Store Payout",
        "Store Adjustment Due",
        "Settlement Impact",
        "Payment Mode",
        "Reference No",
        "Refund Method",
        "Refund Reference",
        "Note",
        "Created By Name",
        "Created By Role",
        "Created At",
        "Store Name",
        "Customer Name",
        "Customer Phone",
        "Delivery Boy",
        "Delivery Boy Phone",
        "Payment Method",
        "Payment Status",
        "Collection",
        "Payment Receiver",
        "Payment Reconciliation",
        "Platform Fee Reconciliation",
        "Business Reconciliation Complete",
        "Rider Cash Status",
        "Platform Fee Status",
        "Store Payout Status",
        "Order Settlement Status"
    ]]

    for log in log_rows:
        rows.append([
            log.get("order_id"),
            log.get("short_order_id"),
            log.get("action"),
            log.get("action_label"),
            log.get("amount_received"),
            log.get("amount_paid"),
            log.get("refund_amount"),
            log.get("refund_items_amount"),
            log.get("refund_delivery_fee"),
            log.get("refund_platform_fee"),
            log.get("refund_tip_amount"),
            log.get("amount_display"),
            log.get("platform_fee"),
            log.get("original_store_payout_amount"),
            log.get("store_refund_deduction"),
            log.get("adjusted_store_payout"),
            log.get("store_adjustment_due"),
            log.get("settlement_impact"),
            log.get("payment_mode"),
            log.get("reference_no"),
            log.get("refund_method"),
            log.get("refund_reference"),
            log.get("note"),
            log.get("created_by_name"),
            log.get("created_by_role"),
            log.get("created_at"),
            log.get("store_name"),
            log.get("customer_name"),
            log.get("customer_phone"),
            log.get("delivery_partner_name"),
            log.get("delivery_partner_phone"),
            log.get("payment_method"),
            log.get("payment_status"),
            log.get("payment_collection_label"),
            log.get("payment_receiver_label"),
            log.get("payment_reconciliation_status"),
            log.get("platform_fee_reconciliation_status"),
            "YES" if log.get("business_reconciliation_complete") else "NO",
            log.get("rider_cash_settlement_status"),
            log.get("platform_fee_status"),
            log.get("store_payout_status"),
            log.get("order_settlement_status")
        ])

    return _admin_csv_response(rows, "nefresh_settlement_audit_logs.csv")
