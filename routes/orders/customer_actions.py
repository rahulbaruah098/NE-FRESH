"""Orders customer actions route section.

Route decorators, endpoint names, URL paths, HTTP methods and function bodies are
preserved from the Step 6 source. Shared legacy helpers are imported from
``routes.orders.shared`` during this transitional decomposition.
"""

from routes.orders.shared import *

@app.route('/orders/<oid>/cancel', methods=['POST'])
@login_required()
def order_cancel(oid):
    u = current_user()

    try:
        oid_obj = ObjectId(oid)
    except Exception:
        flash("Invalid order.", "danger")
        return redirect(url_for("orders"))

    order_doc = mongo.orders.find_one({
        "_id": oid_obj,
        "user_id": u["id"]
    })

    if not order_doc:
        flash("Order not found.", "danger")
        return redirect(url_for("orders"))

    current_status = (order_doc.get("status") or "").strip().upper()

    if current_status not in CANCELLABLE_STATUSES:
        flash("This order can no longer be cancelled.", "warning")
        return redirect(url_for("order_track", oid=oid))

    order_doc = normalize_order_money_fields(order_doc)

    order_items = list(mongo.order_items.find({"order_id": oid_obj}))

    _release_order_stock_items(order_items)

    now = datetime.utcnow().isoformat()

    payment_method = (order_doc.get("payment_method") or "COD").strip().upper()
    payment_status = (order_doc.get("payment_status") or "PENDING").strip().upper()

    items_subtotal = _money_float(order_doc.get("items_subtotal"), 0.0)
    delivery_fee = _money_float(order_doc.get("delivery_fee"), 0.0)
    platform_fee = _money_float(order_doc.get("platform_fee"), 0.0)
    tip_amount = _money_float(order_doc.get("tip_amount"), order_doc.get("delivery_tip_amount") or 0.0)
    total_payable = _money_float(
        order_doc.get("total_payable"),
        items_subtotal + delivery_fee + platform_fee + tip_amount
    )

    is_online_paid = (
        payment_method not in ["COD", "CASH_ON_DELIVERY", "COD_RIDER_COLLECTION"]
        and payment_status in ["PAID", "ONLINE_PAID", "SUCCESS"]
    )

    is_cod_order = payment_method in ["COD", "CASH_ON_DELIVERY", "COD_RIDER_COLLECTION"]

    if is_online_paid:
        # Online paid order cancelled before delivery.
        # Do not mark REFUNDED immediately.
        # Admin will process gateway/manual refund from refund processing queue.
        refund_items_amount = items_subtotal
        refund_delivery_fee = delivery_fee
        refund_platform_fee = platform_fee
        refund_tip_amount = tip_amount
        refund_amount = round(total_payable, 2)

        new_payment_status = payment_status
        refund_status = "READY_FOR_REFUND"
        refund_reason = "CUSTOMER_CANCELLED_BEFORE_DELIVERY"
        order_settlement_status = "REFUND_PENDING"
        payment_collection_status = "PAID_REFUND_PENDING"
        transaction_status = "REFUND_PENDING"

        flash_message = "Order cancelled successfully. Refund is pending NE FRESH/Admin processing."

    elif is_cod_order:
        # COD order cancelled before delivery.
        # Customer has not paid yet, so refund is not required.
        refund_items_amount = 0.0
        refund_delivery_fee = 0.0
        refund_platform_fee = 0.0
        refund_tip_amount = 0.0
        refund_amount = 0.0

        new_payment_status = "VOID"
        refund_status = "NOT_REQUIRED"
        refund_reason = "COD_CANCELLED_BEFORE_PAYMENT"
        order_settlement_status = "CANCELLED_VOID"
        payment_collection_status = "VOID"
        transaction_status = "VOID"

        flash_message = "Order cancelled successfully."

    else:
        # Any unpaid/non-COD pending payment order.
        refund_items_amount = 0.0
        refund_delivery_fee = 0.0
        refund_platform_fee = 0.0
        refund_tip_amount = 0.0
        refund_amount = 0.0

        new_payment_status = "VOID"
        refund_status = "NOT_REQUIRED"
        refund_reason = "UNPAID_ORDER_CANCELLED"
        order_settlement_status = "CANCELLED_VOID"
        payment_collection_status = "VOID"
        transaction_status = "VOID"

        flash_message = "Order cancelled successfully."

    cancel_event = {
        "action": "ORDER_CANCELLED_BY_CUSTOMER",
        "order_id": str(oid_obj),
        "old_status": current_status,
        "new_status": "CANCELLED",
        "payment_method": payment_method,
        "old_payment_status": payment_status,
        "new_payment_status": new_payment_status,
        "refund_status": refund_status,
        "refund_reason": refund_reason,
        "refund_amount": refund_amount,
        "refund_items_amount": refund_items_amount,
        "refund_delivery_fee": refund_delivery_fee,
        "refund_platform_fee": refund_platform_fee,
        "refund_tip_amount": refund_tip_amount,
        "created_by": str(u.get("_id") or u.get("id") or ""),
        "created_by_name": u.get("name") or "Customer",
        "created_by_role": "customer",
        "created_at": now
    }

    update_data = {
        "status": "CANCELLED",
        "cancelled_at": now,
        "cancelled_by": "customer",
        "cancelled_by_id": str(u.get("_id") or u.get("id") or ""),
        "cancelled_by_name": u.get("name") or "Customer",

        "payment_status": new_payment_status,
        "payment_collection_status": payment_collection_status,

        "refund_status": refund_status,
        "refund_reason": refund_reason,
        "refund_amount": refund_amount,
        "refund_items_amount": refund_items_amount,
        "refund_delivery_fee": refund_delivery_fee,
        "refund_platform_fee": refund_platform_fee,
        "refund_tip_amount": refund_tip_amount,

        "refund_method": "",
        "refund_reference": "",
        "refund_processed_at": None,

        "delivery_partner_id": None,
        "delivery_partner_name": "",
        "delivery_partner_phone": "",

        "store_payout_status": "NOT_REQUIRED",
        "rider_cash_settlement_status": "NOT_REQUIRED",
        "platform_fee_status": "REFUND_PENDING" if is_online_paid and platform_fee > 0 else "NOT_REQUIRED",
        "order_settlement_status": order_settlement_status,
        "settlement_status": order_settlement_status,

        "return_status": "CANCELLED",
        "last_refund_event": cancel_event,
        "updated_at": now
    }

    mongo.orders.update_one(
        {"_id": oid_obj},
        {
            "$set": update_data,
            "$push": {
                "refund_audit_logs": cancel_event,
                "settlement_audit_logs": cancel_event
            }
        }
    )

    mongo.transactions.update_many(
        {"order_id": oid_obj},
        {
            "$set": {
                "status": transaction_status,
                "payment_status": new_payment_status,
                "refund_status": refund_status,
                "refund_reason": refund_reason,
                "refund_amount": refund_amount,
                "refund_items_amount": refund_items_amount,
                "refund_delivery_fee": refund_delivery_fee,
                "refund_platform_fee": refund_platform_fee,
                "refund_tip_amount": refund_tip_amount,
                "order_settlement_status": order_settlement_status,
                "settlement_status": order_settlement_status,
                "updated_at": now
            }
        }
    )

    mongo.order_events.insert_one({
        "order_id": oid_obj,
        "status": "CANCELLED",
        "note": (
            "Cancelled by customer. "
            + (
                f"Refund amount ₹{refund_amount:.2f} is ready for Admin processing."
                if is_online_paid
                else "Refund not required."
            )
        ),
        "created_at": now
    })

    flash(flash_message, "success")
    return redirect(url_for("orders"))


@app.route('/orders/<oid>/return-request', methods=['POST'], endpoint='order_return_request')
@login_required()
def order_return_request(oid):
    """
    Customer return request.

    Rules:
    - Only delivered orders can be returned.
    - Customer can request only their own order.
    - No duplicate open return request.
    - This does not process refund directly.
    - Admin/store review will happen in next steps.
    """
    u = current_user()

    if not is_delivery_feature_enabled("return_refund_enabled", True):
        flash("Return/refund is currently disabled by NE FRESH.", "warning")
        return redirect(url_for("order_track", oid=oid))

    try:
        oid_obj = ObjectId(oid)
    except Exception:
        flash("Invalid order.", "danger")
        return redirect(url_for("orders"))

    order_doc = mongo.orders.find_one({
        "_id": oid_obj,
        "user_id": u["id"]
    })

    if not order_doc:
        flash("Order not found.", "danger")
        return redirect(url_for("orders"))

    order_status = (order_doc.get("status") or "").strip().upper()
    payment_status = (order_doc.get("payment_status") or "").strip().upper()
    return_status = (order_doc.get("return_status") or "").strip().upper()
    refund_status = (order_doc.get("refund_status") or "").strip().upper()

    return_policy_settings = get_return_refund_policy_settings()
    return_eligibility = get_order_return_eligibility(order_doc, return_policy_settings)

    if not return_eligibility.get("policy_enabled"):
        flash("Return/refund is currently disabled by NE FRESH.", "warning")
        return redirect(url_for("order_track", oid=oid))

    if not return_eligibility.get("allowed"):
        flash(return_eligibility.get("reason") or "Return request is not allowed for this order.", "warning")
        return redirect(url_for("order_track", oid=oid))

    if order_status != "DELIVERED":
        flash("Return request is allowed only after delivery.", "warning")
        return redirect(url_for("order_track", oid=oid))

    if payment_status == "REFUNDED" or refund_status in ["PROCESSED", "ADJUSTED"]:
        flash("This order is already refunded.", "info")
        return redirect(url_for("order_track", oid=oid))

    if return_status in [
        "RETURN_REQUESTED",
        "REQUESTED",
        "STORE_REVIEWED",
        "APPROVED",
        "PENDING_ASSIGNMENT",
        "ASSIGNED",
        "PICKUP_STARTED",
        "PICKED_UP_FROM_CUSTOMER",
        "RETURNED_TO_STORE",
        "VERIFIED_BY_STORE",
        "COMPLETED"
    ]:
        flash("Return request is already submitted for this order.", "info")
        return redirect(url_for("order_track", oid=oid))

    reason = (request.form.get("return_reason") or "").strip()
    note = (request.form.get("return_note") or "").strip()

    allowed_reasons = {
        "Damaged product",
        "Wrong item",
        "Missing item",
        "Quality issue",
        "Expired product",
        "Other"
    }

    if reason not in allowed_reasons:
        flash("Please select a valid return reason.", "warning")
        return redirect(url_for("order_track", oid=oid))

    if len(note) > 700:
        note = note[:700]

    now = datetime.utcnow().isoformat()

    normalized_order = normalize_order_money_fields(dict(order_doc))

    items_subtotal = round(float(normalized_order.get("items_subtotal") or 0), 2)
    delivery_fee = round(float(normalized_order.get("delivery_fee") or 0), 2)
    platform_fee = round(float(normalized_order.get("platform_fee") or 0), 2)
    tip_amount = round(float(normalized_order.get("tip_amount") or normalized_order.get("delivery_tip_amount") or 0), 2)
    total_payable = round(float(normalized_order.get("total_payable") or normalized_order.get("total_amount") or 0), 2)

        # Customer request stage:
    # Admin controls default refund breakup from Return/Refund Policy settings.
    # Final refund can still be changed later by Admin during refund processing.
    refund_items_amount = items_subtotal if return_policy_settings.get("default_refund_items") else 0.0
    refund_delivery_fee = delivery_fee if return_policy_settings.get("default_refund_delivery_fee") else 0.0
    refund_platform_fee = platform_fee if return_policy_settings.get("default_refund_platform_fee") else 0.0
    refund_tip_amount = tip_amount if return_policy_settings.get("default_refund_tip") else 0.0
    refund_amount = round(
        refund_items_amount
        + refund_delivery_fee
        + refund_platform_fee
        + refund_tip_amount,
        2
    )

    return_event = {
        "action": "RETURN_REQUESTED_BY_CUSTOMER",
        "order_id": str(oid_obj),
        "reason": reason,
        "note": note,
        "refund_amount": refund_amount,
        "refund_items_amount": refund_items_amount,
        "refund_delivery_fee": refund_delivery_fee,
        "refund_platform_fee": refund_platform_fee,
        "refund_tip_amount": refund_tip_amount,
        "created_by": str(u.get("_id") or u.get("id") or ""),
        "created_by_name": u.get("name") or "Customer",
        "created_by_role": "customer",
        "created_at": now
    }

    update_data = {
        "return_status": "RETURN_REQUESTED",
        "return_requested_at": now,
        "return_requested_by": str(u.get("_id") or u.get("id") or ""),
        "return_requested_by_name": u.get("name") or "Customer",
        "return_reason": reason,
        "return_note": note,

        "refund_status": "PENDING",
        "refund_amount": refund_amount,
        "refund_items_amount": refund_items_amount,
        "refund_delivery_fee": refund_delivery_fee,
        "refund_platform_fee": refund_platform_fee,
        "refund_tip_amount": refund_tip_amount,

        "refund_method": "",
        "refund_reference": "",
        "refund_processed_at": None,

        "return_pickup_required": True,
        "return_pickup_status": "PENDING_ASSIGNMENT",

        "store_return_review_status": "PENDING",
        "store_return_review_remark": "",
        "admin_return_review_status": "PENDING",
        "admin_return_review_remark": "",

        "store_refund_deduction": refund_items_amount,
        "refund_deduction": refund_items_amount,

        "updated_at": now,
        "last_return_event": return_event
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
                "return_status": "RETURN_REQUESTED",
                "refund_status": "PENDING",
                "refund_amount": refund_amount,
                "refund_items_amount": refund_items_amount,
                "updated_at": now
            }
        }
    )

    mongo.order_events.insert_one({
        "order_id": oid_obj,
        "status": "RETURN_REQUESTED",
        "note": f"Return requested by customer. Reason: {reason}",
        "created_at": now
    })

    flash("Return request submitted successfully. NE FRESH/Admin will review it.", "success")
    return redirect(url_for("order_track", oid=oid))
