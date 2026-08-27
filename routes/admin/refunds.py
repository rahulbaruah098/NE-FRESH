"""Admin refunds route section.

Route decorators, endpoint names, URL paths, HTTP methods and function bodies are
preserved from the Step 6 source. Shared legacy helpers are imported from
``routes.admin.shared`` during this transitional decomposition.
"""

from routes.admin.shared import *

@app.route("/admin/refund-processing", methods=["GET"], endpoint="admin_refund_processing")
@login_required(role="admin")
def admin_refund_processing():
    """
    Admin refund processing queue.

    This is the action page.
    It is separate from the read-only Returns & Refund Settlements report.
    """
    q = (request.args.get("q") or "").strip()
    queue_filter = (request.args.get("queue") or "").strip().upper()
    refund_filter = (request.args.get("refund_status") or "").strip().upper()
    payment_filter = (request.args.get("payment") or "").strip().upper()

    raw_orders = list(
        mongo.orders.find({
            "$or": [
                # Online paid cancelled orders waiting for Admin refund processing.
                {
                    "status": "CANCELLED",
                    "refund_status": "READY_FOR_REFUND"
                },

                # Store approved return. Admin only processes money/refund.
                {
                    "return_status": "STORE_APPROVED",
                    "refund_status": "READY_FOR_REFUND"
                },

                # Store explicitly sent return to Admin review.
                {
                    "return_status": "NEED_ADMIN_REVIEW",
                    "admin_return_review_status": "PENDING"
                }
            ],
            "refund_status": {
                "$nin": [
                    "PROCESSED",
                    "ADJUSTED",
                    "REJECTED",
                    "NOT_REQUIRED",
                    "VOID"
                ]
            }
        }).sort("updated_at", -1)
    )

    rows = []

    for order in raw_orders:
        row = _admin_hydrate_refund_processing_order(order)

        refund_status = (row.get("refund_status") or "").upper()
        return_status = (row.get("return_status") or "").upper()
        queue_type = (row.get("queue_type") or "").upper()
        payment_method = (row.get("payment_method") or "").upper()

        if queue_filter and queue_filter != queue_type:
            continue

        if refund_filter and refund_filter != refund_status:
            continue

        if payment_filter:
            if payment_filter == "ONLINE":
                if payment_method in ["COD", "CASH_ON_DELIVERY", "COD_RIDER_COLLECTION"]:
                    continue
            elif payment_filter == "COD":
                if payment_method not in ["COD", "CASH_ON_DELIVERY", "COD_RIDER_COLLECTION"]:
                    continue

        if q:
            haystack = " ".join([
                str(row.get("id") or ""),
                str(row.get("store_name") or ""),
                str(row.get("customer_name") or ""),
                str(row.get("customer_phone") or ""),
                str(row.get("payment_method") or ""),
                str(row.get("payment_status") or ""),
                str(row.get("return_status") or ""),
                str(row.get("refund_status") or ""),
                str(row.get("queue_label") or ""),
                str(row.get("return_reason") or ""),
                str(row.get("refund_reason") or "")
            ]).lower()

            if q.lower() not in haystack:
                continue

        rows.append(row)

    metrics = {
        "total": len(rows),
        "admin_review": sum(1 for r in rows if r.get("queue_type") == "ADMIN_REVIEW"),
        "ready_for_refund": sum(1 for r in rows if r.get("refund_status") == "READY_FOR_REFUND"),
        "cancel_refunds": sum(1 for r in rows if r.get("queue_type") == "CANCEL_REFUND"),
        "return_refunds": sum(1 for r in rows if r.get("queue_type") == "RETURN_REFUND"),
        "refund_amount": round(sum(float(r.get("refund_amount") or 0) for r in rows), 2),
    }

    try:
        metrics.update(build_delivery_mode_order_metrics())
    except Exception:
        pass

    return render_template(
        "admin_refund_processing.html",
        user=current_user(),
        refunds=rows,
        metrics=metrics,
        q=q,
        queue_filter=queue_filter,
        refund_filter=refund_filter,
        payment_filter=payment_filter,
        active_group="settlements",
        active_page="refund_processing"
    )


@app.route("/admin/refund-processing/<oid>/admin-review", methods=["POST"], endpoint="admin_refund_admin_review")
@login_required(role="admin")
def admin_refund_admin_review(oid):
    """
    Admin decision only for NEED_ADMIN_REVIEW return cases.

    Approve:
    - moves refund to READY_FOR_REFUND

    Reject:
    - closes refund as REJECTED
    """
    admin_user = current_user() or {}
    oid_obj = _admin_order_id_or_none(oid)

    if not oid_obj:
        flash("Invalid order.", "danger")
        return redirect(url_for("admin_refund_processing"))

    order = mongo.orders.find_one({"_id": oid_obj})

    if not order:
        flash("Order not found.", "danger")
        return redirect(url_for("admin_refund_processing"))

    return_status = (order.get("return_status") or "").strip().upper()
    admin_review_status = (order.get("admin_return_review_status") or "").strip().upper()

    if return_status != "NEED_ADMIN_REVIEW" and admin_review_status != "PENDING":
        flash("This order does not require Admin return review.", "warning")
        return redirect(url_for("admin_refund_processing"))

    decision = (request.form.get("admin_review_decision") or "").strip().upper()
    remark = (request.form.get("admin_review_remark") or "").strip()

    if decision not in ["APPROVE", "REJECT"]:
        flash("Please select a valid Admin review decision.", "warning")
        return redirect(url_for("admin_refund_processing"))

    if len(remark) > 700:
        remark = remark[:700]

    now = datetime.utcnow().isoformat()

    if decision == "APPROVE":
        next_return_status = "STORE_APPROVED"
        next_refund_status = "READY_FOR_REFUND"
        next_admin_review_status = "APPROVED"
        event_note = "Admin approved return review. Refund is ready for processing."
        flash_message = "Admin review approved. Refund is now ready for processing."
    else:
        next_return_status = "ADMIN_REJECTED"
        next_refund_status = "REJECTED"
        next_admin_review_status = "REJECTED"
        event_note = "Admin rejected return review."
        flash_message = "Admin review rejected."

    review_event = {
        "action": "ADMIN_RETURN_REVIEW_DECISION",
        "order_id": str(oid_obj),
        "old_return_status": return_status,
        "new_return_status": next_return_status,
        "old_refund_status": order.get("refund_status"),
        "new_refund_status": next_refund_status,
        "decision": decision,
        "note": remark,
        "created_by": str(admin_user.get("id") or admin_user.get("_id") or ""),
        "created_by_name": admin_user.get("name") or admin_user.get("email") or "Admin",
        "created_by_role": "admin",
        "created_at": now
    }

    mongo.orders.update_one(
        {"_id": oid_obj},
        {
            "$set": {
                "return_status": next_return_status,
                "refund_status": next_refund_status,
                "admin_return_review_status": next_admin_review_status,
                "admin_return_review_remark": remark,
                "admin_return_reviewed_at": now,
                "admin_return_reviewed_by": str(admin_user.get("id") or admin_user.get("_id") or ""),
                "last_refund_event": review_event,
                "updated_at": now
            },
            "$push": {
                "return_audit_logs": review_event,
                "refund_audit_logs": review_event
            }
        }
    )


    mongo.transactions.update_many(
        {"order_id": oid_obj},
        {
            "$set": {
                "return_status": next_return_status,
                "refund_status": next_refund_status,
                "admin_return_review_status": next_admin_review_status,
                "updated_at": now
            }
        }
    )

    mongo.order_events.insert_one({
        "order_id": oid_obj,
        "status": next_return_status,
        "note": event_note,
        "created_at": now
    })

    flash(flash_message, "success")
    return redirect(url_for("admin_refund_processing"))


@app.route("/admin/refund-processing/<oid>/process", methods=["POST"], endpoint="admin_refund_process")
@login_required(role="admin")
def admin_refund_process(oid):
    """
    Admin marks refund processed after actual refund is completed.

    First version:
    - manual Razorpay/dashboard/manual UPI/cash reference
    - no direct gateway API call yet
    """
    admin_user = current_user() or {}
    oid_obj = _admin_order_id_or_none(oid)

    if not oid_obj:
        flash("Invalid order.", "danger")
        return redirect(url_for("admin_refund_processing"))

    order = mongo.orders.find_one({"_id": oid_obj})

    if not order:
        flash("Order not found.", "danger")
        return redirect(url_for("admin_refund_processing"))

    row = _admin_hydrate_refund_processing_order(order)

    refund_status = (row.get("refund_status") or "").upper()
    return_status = (row.get("return_status") or "").upper()

    if refund_status in ["PROCESSED", "ADJUSTED", "REJECTED", "NOT_REQUIRED", "VOID"]:
        flash("This refund is already closed.", "warning")
        return redirect(url_for("admin_refund_processing"))

    if return_status == "NEED_ADMIN_REVIEW":
        flash("Please approve or reject Admin review before processing refund.", "warning")
        return redirect(url_for("admin_refund_processing"))

    refund_items_amount = _admin_settlement_money(
        request.form.get("refund_items_amount"),
        row.get("refund_items_amount") or 0
    )
    refund_delivery_fee = _admin_settlement_money(
        request.form.get("refund_delivery_fee"),
        row.get("refund_delivery_fee") or 0
    )
    refund_platform_fee = _admin_settlement_money(
        request.form.get("refund_platform_fee"),
        row.get("refund_platform_fee") or 0
    )
    refund_tip_amount = _admin_settlement_money(
        request.form.get("refund_tip_amount"),
        row.get("refund_tip_amount") or 0
    )

    refund_finance = calculate_refund_finance_state(
        row,
        refund_items_amount,
        refund_delivery_fee,
        refund_platform_fee,
        refund_tip_amount,
    )
    refund_items_amount = refund_finance["refund_items_amount"]
    refund_delivery_fee = refund_finance["refund_delivery_fee"]
    refund_platform_fee = refund_finance["refund_platform_fee"]
    refund_tip_amount = refund_finance["refund_tip_amount"]
    refund_amount = refund_finance["refund_amount"]
    total_payable = refund_finance["total_payable"]
    gross_platform_fee = refund_finance["gross_platform_fee"]
    net_platform_fee_after_refund = refund_finance["net_platform_fee_after_refund"]
    next_platform_fee_status = refund_finance["next_platform_fee_status"]

    if refund_amount <= 0:
        flash("Refund amount must be greater than zero.", "warning")
        return redirect(url_for("admin_refund_processing"))

    if total_payable > 0 and refund_amount > total_payable:
        flash("Refund amount cannot be greater than original payable amount.", "warning")
        return redirect(url_for("admin_refund_processing"))

    refund_method = (request.form.get("refund_method") or "MANUAL").strip().upper()
    refund_reference = (request.form.get("refund_reference") or "").strip()
    refund_note = (request.form.get("refund_note") or "").strip()

    if refund_method not in [
        "MANUAL",
        "RAZORPAY_MANUAL",
        "UPI",
        "BANK_TRANSFER",
        "CASH",
        "WALLET",
        "OTHER"
    ]:
        refund_method = "MANUAL"

    if len(refund_reference) > 120:
        refund_reference = refund_reference[:120]

    if len(refund_note) > 700:
        refund_note = refund_note[:700]

    status = refund_finance["status"]
    store_payout_status = refund_finance["store_payout_status"]
    store_payout_amount = refund_finance["store_payout_amount"]
    is_cancel_refund = refund_finance["is_cancel_refund"]
    store_refund_deduction = refund_finance["store_refund_deduction"]
    store_already_received_order_money = refund_finance["store_already_received_order_money"]
    adjusted_store_payout = refund_finance["adjusted_store_payout"]
    store_adjustment_due = refund_finance["store_adjustment_due"]
    settlement_impact = refund_finance["settlement_impact"]
    next_store_payout_status = refund_finance["next_store_payout_status"]
    payment_status_after_refund = refund_finance["payment_status_after_refund"]

    now = datetime.utcnow().isoformat()

    refund_event = {
        "action": "REFUND_PROCESSED_BY_ADMIN",
        "order_id": str(oid_obj),
        "refund_amount": refund_amount,
        "refund_items_amount": refund_items_amount,
        "refund_delivery_fee": refund_delivery_fee,
        "refund_platform_fee": refund_platform_fee,
        "refund_tip_amount": refund_tip_amount,
        "refund_method": refund_method,
        "refund_reference": refund_reference,
        "store_refund_deduction": store_refund_deduction,
        "adjusted_store_payout": adjusted_store_payout,
        "store_adjustment_due": store_adjustment_due,
        "settlement_impact": settlement_impact,
        "created_by": str(admin_user.get("id") or admin_user.get("_id") or ""),
        "created_by_name": admin_user.get("name") or admin_user.get("email") or "Admin",
        "created_by_role": "admin",
        "note": refund_note,
        "created_at": now
    }

    update_data = {
        "refund_status": "PROCESSED",
        "refund_processed_at": now,
        "refund_processed_by": str(admin_user.get("id") or admin_user.get("_id") or ""),
        "refund_processed_by_name": admin_user.get("name") or admin_user.get("email") or "Admin",
        "refund_method": refund_method,
        "refund_reference": refund_reference,
        "refund_note": refund_note,

        "refund_amount": refund_amount,
        "refund_items_amount": refund_items_amount,
        "refund_delivery_fee": refund_delivery_fee,
        "refund_platform_fee": refund_platform_fee,
        "refund_tip_amount": refund_tip_amount,

        "payment_status": payment_status_after_refund,

        "store_refund_deduction": store_refund_deduction,
        "refund_deduction": store_refund_deduction,
        "store_payout_amount": adjusted_store_payout if not is_cancel_refund and store_payout_status != "PAID" else store_payout_amount,
        "adjusted_store_payout": adjusted_store_payout,
        "store_adjustment_due": store_adjustment_due,
        "store_payout_status": next_store_payout_status,
        "settlement_impact": settlement_impact,

        "platform_fee_adjustment": refund_platform_fee,
        "platform_fee_status": next_platform_fee_status,
        "net_platform_fee_after_refund": net_platform_fee_after_refund,

        "order_settlement_status": "REFUND_PROCESSED",
        "settlement_status": "REFUND_PROCESSED",

        "return_status": "RETURN_COMPLETED" if not is_cancel_refund else "CANCELLED",
        "last_refund_event": refund_event,
        "updated_at": now
    }

    refund_claim = mongo.orders.update_one(
        {
            "_id": oid_obj,
            "refund_status": {"$nin": ["PROCESSED", "ADJUSTED", "REJECTED", "NOT_REQUIRED", "VOID"]},
        },
        {
            "$set": update_data,
            "$push": {
                "refund_audit_logs": refund_event,
                "settlement_audit_logs": refund_event
            }
        }
    )
    if refund_claim.modified_count != 1:
        flash("This refund was already processed or the order changed. Please refresh.", "warning")
        return redirect(url_for("admin_refund_processing"))

    mongo.transactions.update_many(
        {"order_id": oid_obj},
        {
            "$set": {
                "status": payment_status_after_refund,
                "payment_status": payment_status_after_refund,
                "refund_status": "PROCESSED",
                "refund_processed_at": now,
                "refund_method": refund_method,
                "refund_reference": refund_reference,
                "refund_amount": refund_amount,
                "refund_items_amount": refund_items_amount,
                "refund_delivery_fee": refund_delivery_fee,
                "refund_platform_fee": refund_platform_fee,
                "refund_tip_amount": refund_tip_amount,
                "store_refund_deduction": store_refund_deduction,
                "refund_deduction": store_refund_deduction,
                "store_payout_amount": adjusted_store_payout if not is_cancel_refund and store_payout_status != "PAID" else store_payout_amount,
                "adjusted_store_payout": adjusted_store_payout,
                "store_adjustment_due": store_adjustment_due,
                "store_payout_status": next_store_payout_status,
                "settlement_impact": settlement_impact,
                "platform_fee_adjustment": refund_platform_fee,
                "platform_fee_status": next_platform_fee_status,
                "net_platform_fee_after_refund": net_platform_fee_after_refund,
                "order_settlement_status": "REFUND_PROCESSED",
                "settlement_status": "REFUND_PROCESSED",
                "updated_at": now
            }
        }
    )

    adjustment_doc = None
    if store_already_received_order_money and store_adjustment_due > 0:
        adjustment_doc = finance_create_store_adjustment(
            row,
            store_adjustment_due,
            reason="REFUND_AFTER_STORE_RECEIPT",
            actor=admin_user,
        )
        if adjustment_doc:
            adjustment_id = str(adjustment_doc.get("_id") or "")
            adjustment_status = (adjustment_doc.get("status") or FINANCE_STORE_ADJUSTMENT_OPEN).strip().upper()
            adjustment_remaining = _admin_settlement_money(
                adjustment_doc.get("remaining_amount"),
                store_adjustment_due,
            )
            adjustment_event = {
                "action": "STORE_REFUND_ADJUSTMENT_CREATED",
                "order_id": str(oid_obj),
                "store_id": str(row.get("store_id") or ""),
                "store_name": row.get("store_name") or "",
                "amount_received": 0.0,
                "amount_paid": 0.0,
                "store_adjustment_due": adjustment_remaining,
                "reference_no": adjustment_id,
                "settlement_impact": "ADJUST_FROM_NEXT_PAYOUT",
                "created_by": str(admin_user.get("id") or admin_user.get("_id") or ""),
                "created_by_name": admin_user.get("name") or admin_user.get("email") or "Admin",
                "created_by_role": "admin",
                "note": "Store refund recovery moved to the carry-forward adjustment ledger.",
                "created_at": now,
            }
            adjustment_update = {
                "store_finance_adjustment_id": adjustment_id,
                "store_finance_adjustment_status": adjustment_status,
                "store_adjustment_due": adjustment_remaining,
                "updated_at": now,
            }
            mongo.orders.update_one(
                {"_id": oid_obj},
                {
                    "$set": adjustment_update,
                    "$push": {"settlement_audit_logs": adjustment_event},
                },
            )
            mongo.transactions.update_many(
                {"order_id": oid_obj},
                {"$set": adjustment_update},
            )

    mongo.order_events.insert_one({
        "order_id": oid_obj,
        "status": "REFUND_PROCESSED",
        "note": (
            f"Refund ₹{refund_amount:.2f} processed by Admin. Reference: {refund_reference or '-'}"
            + (
                f" Store carry-forward adjustment ₹{store_adjustment_due:.2f} created."
                if adjustment_doc and store_adjustment_due > 0
                else ""
            )
        ),
        "created_at": now
    })

    flash(
        "Refund processed successfully."
        + (
            f" Store refund adjustment ₹{store_adjustment_due:.2f} will be recovered from a future Store payout."
            if adjustment_doc and store_adjustment_due > 0
            else ""
        ),
        "success"
    )
    return redirect(url_for("admin_refund_processing"))


@app.route("/admin/returns-settlements", methods=["GET"], endpoint="admin_returns_settlements")
@login_required(role="admin")
def admin_returns_settlements():
    """
    Admin read-only returns/refund settlement report.

    Shows:
    - cancelled/refunded/returned orders
    - refund amount breakup
    - store payout adjustment
    - platform fee adjustment
    """
    q = (request.args.get("q") or "").strip()
    refund_filter = (request.args.get("refund_status") or "").strip().upper()
    return_filter = (request.args.get("return_status") or "").strip().upper()
    payment_filter = (request.args.get("payment") or "").strip().upper()
    date_from = (request.args.get("from") or "").strip()
    date_to = (request.args.get("to") or "").strip()

    raw_orders = list(
        mongo.orders.find({
            "$or": [
                {"status": {"$in": ["CANCELLED", "RETURNED", "RETURN_REQUESTED", "RETURN_PICKED_UP", "RETURN_COMPLETED"]}},
                {"payment_status": {"$in": ["REFUNDED", "VOID"]}},
                {"refund_status": {"$exists": True}},
                {"return_status": {"$exists": True}},
                {"refund_amount": {"$gt": 0}},
                {"store_adjustment_due": {"$gt": 0}},
                {"refund_deduction": {"$gt": 0}},
                {"platform_fee_adjustment": {"$gt": 0}}
            ]
        }).sort("updated_at", -1)
    )

    rows = []

    for order in raw_orders:
        row = _admin_hydrate_return_settlement_order(order)

        report_date = str(
            row.get("refund_processed_at")
            or row.get("cancelled_at")
            or row.get("updated_at")
            or row.get("created_at")
            or ""
        )

        row["report_date"] = report_date
        row["report_date_label"] = (
            "Refund Processed"
            if row.get("refund_processed_at")
            else "Cancelled/Updated"
        )

        if date_from and report_date and report_date[:10] < date_from:
            continue

        if date_to and report_date and report_date[:10] > date_to:
            continue

        if refund_filter and refund_filter != row.get("refund_status"):
            continue

        if return_filter and return_filter != row.get("return_status"):
            continue

        if payment_filter:
            if payment_filter == "ONLINE":
                if row.get("payment_method") == "COD":
                    continue
            elif payment_filter == "COD":
                if row.get("payment_method") != "COD":
                    continue
            elif payment_filter != row.get("payment_method"):
                continue

        if q:
            haystack = " ".join([
                str(row.get("id") or ""),
                str(row.get("store_name") or ""),
                str(row.get("customer_name") or ""),
                str(row.get("customer_phone") or ""),
                str(row.get("payment_method") or ""),
                str(row.get("payment_status") or ""),
                str(row.get("return_status") or ""),
                str(row.get("refund_status") or ""),
                str(row.get("refund_reference") or ""),
                str(row.get("refund_method") or ""),
                str(row.get("settlement_impact") or "")
            ]).lower()

            if q.lower() not in haystack:
                continue

        rows.append(row)

    processed_rows = [
        r for r in rows
        if (r.get("refund_status") or "").upper() == "PROCESSED"
    ]

    pending_rows = [
        r for r in rows
        if (r.get("refund_status") or "").upper() in ["PENDING", "READY_FOR_REFUND", "NOT_STARTED"]
    ]

    cancel_refund_rows = [
        r for r in rows
        if (r.get("refund_type") or "").upper() == "CANCEL_REFUND"
    ]

    return_refund_rows = [
        r for r in rows
        if (r.get("refund_type") or "").upper() == "RETURN_REFUND"
    ]

    metrics = {
        "total_records": len(rows),
        "processed_records": len(processed_rows),
        "pending_records": len(pending_rows),
        "cancel_refund_records": len(cancel_refund_rows),
        "return_refund_records": len(return_refund_rows),

        "total_refund_amount": round(sum(float(r.get("refund_amount") or 0) for r in rows), 2),
        "processed_refund_amount": round(sum(float(r.get("refund_amount") or 0) for r in processed_rows), 2),
        "pending_refund_amount": round(sum(float(r.get("refund_amount") or 0) for r in pending_rows), 2),

        "items_refund_amount": round(sum(float(r.get("refund_items_amount") or 0) for r in rows), 2),
        "delivery_refund_amount": round(sum(float(r.get("refund_delivery_fee") or 0) for r in rows), 2),
        "platform_refund_amount": round(sum(float(r.get("refund_platform_fee") or 0) for r in rows), 2),
        "tip_refund_amount": round(sum(float(r.get("refund_tip_amount") or 0) for r in rows), 2),

        "store_deduction_amount": round(sum(float(r.get("store_refund_deduction") or 0) for r in rows), 2),
        "store_adjustment_due": round(sum(float(r.get("store_adjustment_due") or 0) for r in rows), 2),
        "net_platform_fee_after_refund": round(sum(float(r.get("net_platform_fee") or 0) for r in rows), 2),
    }

    return render_template(
        "admin_returns_settlements.html",
        user=current_user(),
        returns=rows,
        metrics=metrics,
        q=q,
        refund_filter=refund_filter,
        return_filter=return_filter,
        payment_filter=payment_filter,
        date_from=date_from,
        date_to=date_to,
        active_group="settlements",
        active_page="returns_settlements"
    )


@app.route("/admin/returns-settlements/export.csv", methods=["GET"], endpoint="admin_returns_settlements_export_csv")
@login_required(role="admin")
def admin_returns_settlements_export_csv():
    q = (request.args.get("q") or "").strip()
    refund_filter = (request.args.get("refund_status") or "").strip().upper()
    return_filter = (request.args.get("return_status") or "").strip().upper()
    payment_filter = (request.args.get("payment") or "").strip().upper()
    date_from = (request.args.get("from") or "").strip()
    date_to = (request.args.get("to") or "").strip()

    raw_orders = list(
        mongo.orders.find({
            "$or": [
                {"status": {"$in": ["CANCELLED", "RETURNED", "RETURN_REQUESTED", "RETURN_PICKED_UP", "RETURN_COMPLETED"]}},
                {"payment_status": {"$in": ["REFUNDED", "VOID", "PARTIALLY_REFUNDED"]}},
                {"refund_status": {"$exists": True}},
                {"return_status": {"$exists": True}},
                {"refund_amount": {"$gt": 0}},
                {"store_adjustment_due": {"$gt": 0}},
                {"refund_deduction": {"$gt": 0}},
                {"store_refund_deduction": {"$gt": 0}},
                {"platform_fee_adjustment": {"$gt": 0}}
            ]
        }).sort("updated_at", -1)
    )

    rows = [[
        "Order ID",
        "Store Name",
        "Customer Name",
        "Customer Phone",

        "Order Status",
        "Payment Method",
        "Payment Status",
        "Return Status",
        "Refund Status",
        "Refund Type",
        "Refund Type Label",

        "Items Subtotal",
        "Delivery Fee",
        "Platform Fee",
        "Tip Amount",
        "Total Payable",

        "Refund Amount",
        "Refund Items Amount",
        "Refund Delivery Fee",
        "Refund Platform Fee",
        "Refund Tip Amount",
        "Refund Method",
        "Refund Reference",
        "Refund Note",
        "Refund Processed At",
        "Refund Processed By",

        "Store Payout Amount",
        "Original Store Payout",
        "Store Refund Deduction",
        "Adjusted Store Payout",
        "Store Adjustment Due",
        "Settlement Impact",

        "Gross Platform Fee",
        "Platform Fee Adjustment",
        "Net Platform Fee",

        "Store Payout Status",
        "Order Settlement Status",
        "Settlement Status",

        "Cancelled At",
        "Created At",
        "Updated At",
        "Report Date"
    ]]

    for order in raw_orders:
        row = _admin_hydrate_return_settlement_order(order)

        report_date = str(
            row.get("refund_processed_at")
            or row.get("cancelled_at")
            or row.get("updated_at")
            or row.get("created_at")
            or ""
        )

        if date_from and report_date and report_date[:10] < date_from:
            continue

        if date_to and report_date and report_date[:10] > date_to:
            continue

        if refund_filter and refund_filter != row.get("refund_status"):
            continue

        if return_filter and return_filter != row.get("return_status"):
            continue

        if payment_filter:
            if payment_filter == "ONLINE":
                if row.get("payment_method") in ["COD", "COD_RIDER_COLLECTION", "CASH_ON_DELIVERY"]:
                    continue
            elif payment_filter == "COD":
                if row.get("payment_method") not in ["COD", "COD_RIDER_COLLECTION", "CASH_ON_DELIVERY"]:
                    continue
            elif payment_filter != row.get("payment_method"):
                continue

        if q:
            haystack = " ".join([
                str(row.get("id") or ""),
                str(row.get("store_name") or ""),
                str(row.get("customer_name") or ""),
                str(row.get("customer_phone") or ""),
                str(row.get("payment_method") or ""),
                str(row.get("payment_status") or ""),
                str(row.get("return_status") or ""),
                str(row.get("refund_status") or ""),
                str(row.get("refund_method") or ""),
                str(row.get("refund_reference") or ""),
                str(row.get("settlement_impact") or ""),
                str(row.get("order_settlement_status") or "")
            ]).lower()

            if q.lower() not in haystack:
                continue

        rows.append([
            row.get("id"),
            row.get("store_name"),
            row.get("customer_name"),
            row.get("customer_phone"),

            row.get("status"),
            row.get("payment_method"),
            row.get("payment_status"),
            row.get("return_status"),
            row.get("refund_status"),
            row.get("refund_type"),
            row.get("refund_type_label"),

            row.get("items_subtotal"),
            row.get("delivery_fee"),
            row.get("platform_fee"),
            row.get("tip_amount"),
            row.get("total_payable"),

            row.get("refund_amount"),
            row.get("refund_items_amount"),
            row.get("refund_delivery_fee"),
            row.get("refund_platform_fee"),
            row.get("refund_tip_amount"),
            row.get("refund_method"),
            row.get("refund_reference"),
            row.get("refund_note"),
            row.get("refund_processed_at"),
            row.get("refund_processed_by_name"),

            row.get("store_payout_amount"),
            row.get("original_store_payout_amount"),
            row.get("store_refund_deduction"),
            row.get("adjusted_store_payout"),
            row.get("store_adjustment_due"),
            row.get("settlement_impact"),

            row.get("gross_platform_fee"),
            row.get("platform_fee_adjustment"),
            row.get("net_platform_fee"),

            row.get("store_payout_status"),
            row.get("order_settlement_status"),
            row.get("settlement_status"),

            row.get("cancelled_at"),
            row.get("created_at"),
            row.get("updated_at"),
            report_date
        ])

    return _admin_csv_response(rows, "nefresh_returns_refund_settlements.csv")
