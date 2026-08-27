"""Store returns route section.

Route decorators, endpoint names, URL paths, HTTP methods and function bodies are
preserved from the Step 6 source. Shared legacy helpers are imported from
``routes.store.shared`` during this transitional decomposition.
"""

from routes.store.shared import *

@app.route('/store/returns', methods=['GET'], endpoint='store_returns')
@login_required(role='store')
def store_returns_page():
    """
    Store-side return/refund request page.

    Store can:
    - View own return requests
    - Recommend APPROVE / REJECT / NEED_ADMIN_REVIEW
    - Add store remark

    Store cannot process refund.
    Admin has final authority.
    """
    u, store = _get_current_store_or_redirect()

    if not store:
        flash("Store not found.", "danger")
        return redirect(url_for("store_dashboard"))

    store_id = store["_id"]
    store_id_str = str(store_id)

    q = (request.args.get("q") or "").strip()
    return_filter = (request.args.get("return_status") or "").strip().upper()
    review_filter = (request.args.get("review_status") or "").strip().upper()
    if review_filter == "APPROVE":
        review_filter = "APPROVED"

    if review_filter == "REJECT":
        review_filter = "REJECTED"
    date_from = (request.args.get("from") or "").strip()
    date_to = (request.args.get("to") or "").strip()

    raw_orders = list(
        mongo.orders.find({
            "$and": [
                {
                    "$or": [
                        {"store_id": store_id},
                        {"store_id": store_id_str}
                    ]
                },
                {
                    "$or": [
                        {"return_status": {"$exists": True, "$ne": ""}},
                        {"return_requested_at": {"$exists": True}},
                        {"refund_status": {"$exists": True, "$ne": ""}},
                        {"return_audit_logs": {"$exists": True, "$ne": []}}
                    ]
                }
            ]
        }).sort("return_requested_at", -1)
    )

    rows = []

    for order in raw_orders:
        row = dict(order)
        row["id"] = str(row.get("_id") or "")

        row = _decorate_store_delivery_order(row)

        row["return_status"] = (row.get("return_status") or "RETURN_REQUESTED").strip().upper()
        row["refund_status"] = (row.get("refund_status") or "PENDING").strip().upper()
        row["return_reason"] = row.get("return_reason") or ""
        row["return_note"] = row.get("return_note") or ""
        row["return_requested_at"] = row.get("return_requested_at") or row.get("updated_at") or row.get("created_at") or ""

        row["refund_amount"] = _store_delivery_money_float(row.get("refund_amount"), 0)
        row["refund_items_amount"] = _store_delivery_money_float(row.get("refund_items_amount"), 0)
        row["refund_delivery_fee"] = _store_delivery_money_float(row.get("refund_delivery_fee"), 0)
        row["refund_platform_fee"] = _store_delivery_money_float(row.get("refund_platform_fee"), 0)
        row["refund_tip_amount"] = _store_delivery_money_float(row.get("refund_tip_amount"), 0)

        row["refund_method"] = row.get("refund_method") or ""
        row["refund_reference"] = row.get("refund_reference") or ""
        row["refund_processed_at"] = row.get("refund_processed_at") or ""
        row["refund_processed_by_name"] = row.get("refund_processed_by_name") or ""

        row["store_refund_deduction"] = _store_delivery_money_float(
            row.get("store_refund_deduction")
            if row.get("store_refund_deduction") is not None
            else row.get("refund_deduction"),
            row["refund_items_amount"]
        )

        row["store_adjustment_due"] = _store_delivery_money_float(
            row.get("store_adjustment_due"),
            0
        )

        row["original_store_payout_amount"] = _store_delivery_money_float(
            row.get("original_store_payout_amount")
            if row.get("original_store_payout_amount") is not None
            else row.get("store_earning"),
            row.get("items_subtotal") or 0
        )

        row["adjusted_store_payout"] = _store_delivery_money_float(
            row.get("adjusted_store_payout"),
            max(
                float(row.get("original_store_payout_amount") or 0)
                - float(row.get("store_refund_deduction") or 0),
                0
            )
        )

        row["settlement_impact"] = (
            row.get("settlement_impact")
            or (
                "ADJUST_FROM_NEXT_PAYOUT"
                if row["store_adjustment_due"] > 0
                else (
                    "DEDUCT_FROM_PENDING_PAYOUT"
                    if row["store_refund_deduction"] > 0
                    else "NO_DEDUCTION"
                )
            )
        )

        row["store_return_review_status"] = (
            row.get("store_return_review_status")
            or row.get("store_review_status")
            or "PENDING"
        ).strip().upper()

        # Backward support for old values used before Store final decision flow.
        if row["store_return_review_status"] == "APPROVE":
            row["store_return_review_status"] = "APPROVED"

        if row["store_return_review_status"] == "REJECT":
            row["store_return_review_status"] = "REJECTED"

        row["store_return_review_remark"] = (
            row.get("store_return_review_remark")
            or row.get("store_review_note")
            or row.get("store_return_review_note")
            or ""
        )

        row["store_reviewed_at"] = row.get("store_reviewed_at") or ""
        row["admin_return_review_status"] = (
            row.get("admin_return_review_status")
            or row.get("admin_decision")
            or (
                "NOT_REQUIRED"
                if row["store_return_review_status"] in ["APPROVED", "REJECTED"]
                else "PENDING"
            )
        ).strip().upper()

        report_date = str(row.get("return_requested_at") or "")

        if date_from and report_date and report_date[:10] < date_from:
            continue

        if date_to and report_date and report_date[:10] > date_to:
            continue

        if return_filter and row.get("return_status") != return_filter:
            continue

        if review_filter and row.get("store_return_review_status") != review_filter:
            continue

        if q:
            haystack = " ".join([
                str(row.get("id") or ""),
                str(row.get("customer_name") or ""),
                str(row.get("customer_phone") or ""),
                str(row.get("return_reason") or ""),
                str(row.get("return_note") or ""),
                str(row.get("return_status") or ""),
                str(row.get("refund_status") or ""),
                str(row.get("refund_method") or ""),
                str(row.get("refund_reference") or ""),
                str(row.get("store_return_review_status") or ""),
                str(row.get("admin_return_review_status") or ""),
                str(row.get("settlement_impact") or "")
            ]).lower()

            if q.lower() not in haystack:
                continue

        rows.append(row)

        metrics = {
        "total": len(rows),

        "pending_review": sum(
            1 for r in rows
            if (r.get("store_return_review_status") or "") == "PENDING"
        ),

        "approved": sum(
            1 for r in rows
            if (r.get("store_return_review_status") or "") in ["APPROVED", "APPROVE"]
        ),

        "rejected": sum(
            1 for r in rows
            if (r.get("store_return_review_status") or "") in ["REJECTED", "REJECT"]
        ),

        "need_admin_review": sum(
            1 for r in rows
            if (r.get("store_return_review_status") or "") == "NEED_ADMIN_REVIEW"
        ),

        "ready_for_refund": sum(
            1 for r in rows
            if (r.get("refund_status") or "") == "READY_FOR_REFUND"
        ),

        "refund_processed": sum(
            1 for r in rows
            if (r.get("refund_status") or "") in ["PROCESSED", "ADJUSTED"]
        ),

        "refund_amount": round(
            sum(float(r.get("refund_amount") or 0) for r in rows),
            2
        ),

        "items_refund_amount": round(
            sum(float(r.get("refund_items_amount") or 0) for r in rows),
            2
        ),

        "store_refund_deduction": round(
            sum(float(r.get("store_refund_deduction") or 0) for r in rows),
            2
        ),

        "store_adjustment_due": round(
            sum(float(r.get("store_adjustment_due") or 0) for r in rows),
            2
        ),
    }

    store_view = dict(store)
    store_view["id"] = str(store["_id"])

    return render_template(
        "store_returns.html",
        user=u,
        store=store_view,
        returns=rows,
        metrics=metrics,
        q=q,
        return_filter=return_filter,
        review_filter=review_filter,
        date_from=date_from,
        date_to=date_to,
        active_page="returns"
    )


@app.route('/store/returns/<oid>/review', methods=['POST'], endpoint='store_return_review')
@login_required(role='store')
def store_return_review(oid):
    """
    Store final return decision.

    Store can:
    - Approve return
    - Reject return
    - Send to Admin review

    Store cannot process customer refund money.
    Admin/NE FRESH processes refund and platform settlement after store approval.
    """
    u, store = _get_current_store_or_redirect()

    if not store:
        flash("Store not found.", "danger")
        return redirect(url_for("store_dashboard"))

    oid_obj, order = _get_store_owned_order(store, oid)

    if not oid_obj or not order:
        flash("Return order not found for your store.", "danger")
        return redirect(url_for("store_returns"))

    return_status = (order.get("return_status") or "").strip().upper()
    refund_status = (order.get("refund_status") or "").strip().upper()

    if refund_status in ["PROCESSED", "ADJUSTED"]:
        flash("Refund is already processed for this order.", "warning")
        return redirect(url_for("store_returns"))

    if return_status not in [
        "RETURN_REQUESTED",
        "REQUESTED",
        "STORE_REVIEWED",
        "NEED_ADMIN_REVIEW"
    ]:
        flash("This order does not have an active return request for store decision.", "warning")
        return redirect(url_for("store_returns"))

    decision_raw = (request.form.get("store_review_decision") or "").strip().upper()
    remark = (request.form.get("store_review_remark") or "").strip()

    # Backward support for old template values.
    if decision_raw == "APPROVE":
        decision = "APPROVED"
    elif decision_raw == "REJECT":
        decision = "REJECTED"
    else:
        decision = decision_raw

    if decision not in ["APPROVED", "REJECTED", "NEED_ADMIN_REVIEW"]:
        flash("Please select a valid store return decision.", "warning")
        return redirect(url_for("store_returns"))

    if len(remark) > 700:
        remark = remark[:700]

    now = datetime.utcnow().isoformat()

    old_store_review_status = (
        order.get("store_return_review_status")
        or order.get("store_review_status")
        or "PENDING"
    )

    refund_amount = _store_delivery_money_float(order.get("refund_amount"), 0)
    refund_items_amount = _store_delivery_money_float(order.get("refund_items_amount"), refund_amount)
    refund_delivery_fee = _store_delivery_money_float(order.get("refund_delivery_fee"), 0)
    refund_platform_fee = _store_delivery_money_float(order.get("refund_platform_fee"), 0)
    refund_tip_amount = _store_delivery_money_float(order.get("refund_tip_amount"), 0)

    if decision == "APPROVED":
        next_return_status = "STORE_APPROVED"
        next_refund_status = "READY_FOR_REFUND"
        next_admin_review_status = "NOT_REQUIRED"
        return_pickup_required = True
        return_pickup_status = "PENDING_ASSIGNMENT"
        store_refund_deduction = refund_items_amount
        settlement_impact = "DEDUCT_FROM_PENDING_PAYOUT"
        order_settlement_status = "REFUND_PENDING"
        event_note = "Store approved the return. Refund is ready for Admin/NE FRESH processing."

    elif decision == "REJECTED":
        next_return_status = "STORE_REJECTED"
        next_refund_status = "REJECTED"
        next_admin_review_status = "NOT_REQUIRED"
        return_pickup_required = False
        return_pickup_status = "NOT_REQUIRED"
        store_refund_deduction = 0.0
        settlement_impact = "NO_DEDUCTION"
        order_settlement_status = "RETURN_REJECTED"
        event_note = "Store rejected the return request."

    else:
        next_return_status = "NEED_ADMIN_REVIEW"
        next_refund_status = "PENDING"
        next_admin_review_status = "PENDING"
        return_pickup_required = False
        return_pickup_status = "PENDING_ADMIN_REVIEW"
        store_refund_deduction = 0.0
        settlement_impact = "PENDING_ADMIN_REVIEW"
        order_settlement_status = "ADMIN_RETURN_REVIEW_PENDING"
        event_note = "Store requested Admin review for this return."

    return_event = {
        "action": "RETURN_DECIDED_BY_STORE",
        "order_id": str(oid_obj),
        "store_id": str(store.get("_id") or ""),
        "store_name": store.get("store_name") or "",
        "old_status": old_store_review_status,
        "new_status": decision,
        "return_status": next_return_status,
        "refund_status": next_refund_status,
        "refund_amount": refund_amount,
        "refund_items_amount": refund_items_amount,
        "refund_delivery_fee": refund_delivery_fee,
        "refund_platform_fee": refund_platform_fee,
        "refund_tip_amount": refund_tip_amount,
        "store_refund_deduction": store_refund_deduction,
        "settlement_impact": settlement_impact,
        "order_settlement_status": order_settlement_status,
        "note": remark,
        "created_by": str(u.get("_id") or u.get("id") or ""),
        "created_by_name": u.get("name") or store.get("store_name") or "Store",
        "created_by_role": "store",
        "created_at": now
    }

    update_data = {
        "return_status": next_return_status,

        "store_return_review_status": decision,
        "store_review_status": decision,
        "store_return_review_remark": remark,
        "store_review_note": remark,
        "store_reviewed_by": str(u.get("_id") or u.get("id") or ""),
        "store_reviewed_by_name": u.get("name") or store.get("store_name") or "Store",
        "store_reviewed_at": now,

        "admin_return_review_status": next_admin_review_status,
        "refund_status": next_refund_status,

        "return_pickup_required": return_pickup_required,
        "return_pickup_status": return_pickup_status,

        # Store only decides product return validity.
        # Admin/NE FRESH still handles final money/refund settlement.
        "refund_amount": refund_amount,
        "refund_items_amount": refund_items_amount,
        "refund_delivery_fee": refund_delivery_fee,
        "refund_platform_fee": refund_platform_fee,
        "refund_tip_amount": refund_tip_amount,

        "store_refund_deduction": store_refund_deduction,
        "refund_deduction": store_refund_deduction,
        "settlement_impact": settlement_impact,
        "order_settlement_status": order_settlement_status,
        "settlement_status": order_settlement_status,

        "last_return_event": return_event,
        "updated_at": now
    }

    mongo.orders.update_one(
        {"_id": oid_obj},
        {
            "$set": update_data,
            "$push": {
                "return_audit_logs": return_event
            }
        }
    )

    mongo.transactions.update_many(
        {"order_id": oid_obj},
        {
            "$set": {
                "return_status": next_return_status,
                "store_return_review_status": decision,
                "store_review_status": decision,
                "admin_return_review_status": next_admin_review_status,
                "refund_status": next_refund_status,
                "refund_amount": refund_amount,
                "refund_items_amount": refund_items_amount,
                "store_refund_deduction": store_refund_deduction,
                "refund_deduction": store_refund_deduction,
                "settlement_impact": settlement_impact,
                "order_settlement_status": order_settlement_status,
                "settlement_status": order_settlement_status,
                "updated_at": now
            }
        }
    )

    mongo.order_events.insert_one({
        "order_id": oid_obj,
        "status": next_return_status,
        "note": f"{event_note} {remark}".strip(),
        "created_at": now
    })

    if decision == "APPROVED":
        flash("Return approved by Store. Refund is now ready for Admin/NE FRESH processing.", "success")
    elif decision == "REJECTED":
        flash("Return rejected by Store.", "success")
    else:
        flash("Return sent to Admin review.", "success")

    return redirect(url_for("store_returns"))
