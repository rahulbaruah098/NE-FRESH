"""Orders payments route section.

Route decorators, endpoint names, URL paths, HTTP methods and function bodies are
preserved from the Step 6 source. Shared legacy helpers are imported from
``routes.orders.shared`` during this transitional decomposition.
"""

from routes.orders.shared import *

@app.route("/orders/<oid>/payment", methods=["GET"], endpoint="order_payment")
@login_required()
def order_payment(oid):
    u = current_user()

    try:
        oid_obj = ObjectId(oid)
    except Exception:
        flash("Invalid payment attempt.", "danger")
        return redirect(url_for("checkout"))

    # If this ID has already become a real paid order, go to tracking.
    existing_order = mongo.orders.find_one({
        "_id": oid_obj,
        "user_id": u["id"]
    })

    if existing_order:
        payment_status = (existing_order.get("payment_status") or "").strip().upper()

        if payment_status in ["PAID", "ONLINE_PAID", "SUCCESS"]:
            return redirect(url_for("order_track", oid=str(oid_obj)))

    attempt = mongo.payment_attempts.find_one({
        "_id": oid_obj,
        "user_id": u["id"]
    })

    if not attempt:
        flash("Payment attempt not found. Please checkout again.", "warning")
        return redirect(url_for("checkout"))

    attempt_status = (attempt.get("status") or "").strip().upper()

    if attempt_status in ["PAID", "CONVERTED_TO_ORDER"] and attempt.get("order_id"):
        return redirect(url_for("order_track", oid=str(attempt.get("order_id"))))

    order_for_template = dict(attempt.get("order_doc") or {})
    order_for_template["_id"] = oid_obj
    order_for_template["id"] = str(oid_obj)
    order_for_template["payment_method"] = "ONLINE"
    order_for_template["payment_status"] = attempt.get("payment_status") or "PENDING_PAYMENT"
    order_for_template["total_payable"] = float(attempt.get("total_payable") or attempt.get("amount") or 0)

    settings = get_checkout_payment_gateway_settings()

    if not settings.get("enabled") or not settings.get("razorpay_key_id"):
        flash("Online payment is currently unavailable. Please try again later or choose COD.", "warning")
        return redirect(url_for("checkout"))

    return render_template(
        "order_payment.html",
        user=u,
        order=order_for_template,
        payment_gateway_settings=settings,
        razorpay_key_id=settings.get("razorpay_key_id")
    )


@app.route("/api/payment/create-razorpay-order/<oid>", methods=["POST"], endpoint="api_create_razorpay_order")
@login_required()
def api_create_razorpay_order(oid):
    u = current_user()

    try:
        oid_obj = ObjectId(oid)
    except Exception:
        return jsonify({
            "ok": False,
            "message": "Invalid payment attempt."
        }), 400

    # If payment was already converted to order, do not recreate Razorpay order.
    existing_order = mongo.orders.find_one({
        "_id": oid_obj,
        "user_id": u["id"]
    })

    if existing_order:
        payment_status = (existing_order.get("payment_status") or "").strip().upper()

        if payment_status in ["PAID", "ONLINE_PAID", "SUCCESS"]:
            return jsonify({
                "ok": True,
                "already_paid": True,
                "message": "Payment is already completed.",
                "redirect_url": url_for("order_track", oid=str(oid_obj))
            })

    attempt = mongo.payment_attempts.find_one({
        "_id": oid_obj,
        "user_id": u["id"]
    })

    if not attempt:
        return jsonify({
            "ok": False,
            "message": "Payment attempt not found. Please checkout again."
        }), 404

    attempt_status = (attempt.get("status") or "").strip().upper()

    if attempt_status in ["PAID", "CONVERTED_TO_ORDER"]:
        return jsonify({
            "ok": True,
            "already_paid": True,
            "message": "Payment is already completed.",
            "redirect_url": url_for("order_track", oid=str(attempt.get("order_id") or oid_obj))
        })

    settings = get_server_payment_gateway_settings()
    client, client_error = get_razorpay_client_from_settings(settings)

    if client_error:
        return jsonify({
            "ok": False,
            "message": client_error
        }), 400

    total_payable = float(attempt.get("total_payable") or attempt.get("amount") or 0)

    if total_payable <= 0:
        return jsonify({
            "ok": False,
            "message": "Invalid payment amount."
        }), 400

    amount_paise = int(round(total_payable * 100))
    receipt_id = f"attempt_{str(oid_obj)[-12:]}"

    try:
        razorpay_order = client.order.create({
            "amount": amount_paise,
            "currency": "INR",
            "receipt": receipt_id,
            "payment_capture": 1 if settings.get("auto_capture_enabled", True) else 0,
            "notes": {
                "payment_attempt_id": str(oid_obj),
                "customer_id": str(u.get("_id") or u.get("id") or ""),
                "source": "NE_FRESH_CHECKOUT"
            }
        })
    except Exception as exc:
        now = datetime.utcnow().isoformat()

        mongo.payment_attempts.update_one(
            {"_id": oid_obj},
            {
                "$set": {
                    "razorpay_order_create_error": str(exc),
                    "razorpay_order_create_failed_at": now,
                    "updated_at": now
                }
            }
        )

        return jsonify({
            "ok": False,
            "message": f"Unable to create Razorpay order: {str(exc)}"
        }), 500

    now = datetime.utcnow().isoformat()

    mongo.payment_attempts.update_one(
        {"_id": oid_obj},
        {
            "$set": {
                "razorpay_order_id": razorpay_order.get("id"),
                "razorpay_order_amount": amount_paise,
                "razorpay_order_currency": "INR",
                "razorpay_order_receipt": receipt_id,
                "razorpay_order_status": razorpay_order.get("status"),
                "razorpay_order_created_at": now,
                "payment_gateway": "RAZORPAY",
                "payment_gateway_mode": settings.get("mode"),
                "payment_status": "PENDING_PAYMENT",
                "payment_collection_status": "ONLINE_PENDING",
                "updated_at": now
            },
            "$push": {
                "payment_audit_logs": {
                    "action": "RAZORPAY_ORDER_CREATED_FOR_ATTEMPT",
                    "payment_attempt_id": str(oid_obj),
                    "razorpay_order_id": razorpay_order.get("id"),
                    "amount": total_payable,
                    "amount_paise": amount_paise,
                    "created_by": str(u.get("_id") or u.get("id") or ""),
                    "created_by_name": u.get("name") or "Customer",
                    "created_at": now
                }
            }
        }
    )

    return jsonify({
        "ok": True,
        "message": "Razorpay order created.",
        "order_id": str(oid_obj),
        "payment_attempt_id": str(oid_obj),
        "razorpay_order_id": razorpay_order.get("id"),
        "razorpay_key_id": settings.get("razorpay_key_id"),
        "amount": amount_paise,
        "currency": "INR",
        "display_amount": total_payable,
        "customer": {
            "name": u.get("name") or "Customer",
            "email": u.get("email") or "",
            "phone": u.get("phone") or ""
        }
    })


@app.route("/api/payment/verify-razorpay-payment/<oid>", methods=["POST"], endpoint="api_verify_razorpay_payment")
@login_required()
def api_verify_razorpay_payment(oid):
    u = current_user()

    try:
        oid_obj = ObjectId(oid)
    except Exception:
        return jsonify({
            "ok": False,
            "message": "Invalid payment attempt."
        }), 400

    existing_order = mongo.orders.find_one({
        "_id": oid_obj,
        "user_id": u["id"]
    })

    if existing_order:
        payment_status = (existing_order.get("payment_status") or "").strip().upper()

        if payment_status in ["PAID", "ONLINE_PAID", "SUCCESS"]:
            return jsonify({
                "ok": True,
                "message": "Payment is already verified.",
                "redirect_url": url_for("order_track", oid=str(oid_obj))
            })

    attempt = mongo.payment_attempts.find_one({
        "_id": oid_obj,
        "user_id": u["id"]
    })

    if not attempt:
        return jsonify({
            "ok": False,
            "message": "Payment attempt not found. Please checkout again."
        }), 404

    attempt_status = (attempt.get("status") or "").strip().upper()

    if attempt_status in ["PAID", "CONVERTED_TO_ORDER"] and attempt.get("order_id"):
        return jsonify({
            "ok": True,
            "message": "Payment is already verified.",
            "redirect_url": url_for("order_track", oid=str(attempt.get("order_id")))
        })

    data = request.get_json(silent=True) or {}

    razorpay_order_id = (data.get("razorpay_order_id") or "").strip()
    razorpay_payment_id = (data.get("razorpay_payment_id") or "").strip()
    razorpay_signature = (data.get("razorpay_signature") or "").strip()

    if not razorpay_order_id or not razorpay_payment_id or not razorpay_signature:
        return jsonify({
            "ok": False,
            "message": "Missing Razorpay payment verification details."
        }), 400

    saved_razorpay_order_id = (attempt.get("razorpay_order_id") or "").strip()

    if saved_razorpay_order_id and saved_razorpay_order_id != razorpay_order_id:
        return jsonify({
            "ok": False,
            "message": "Razorpay order ID mismatch."
        }), 400

    settings = get_server_payment_gateway_settings()
    client, client_error = get_razorpay_client_from_settings(settings)

    if client_error:
        return jsonify({
            "ok": False,
            "message": client_error
        }), 400

    try:
        verify_razorpay_payment_signature(
            client,
            razorpay_order_id,
            razorpay_payment_id,
            razorpay_signature,
        )
    except Exception as exc:
        now = datetime.utcnow().isoformat()

        failed_event = {
            "action": "RAZORPAY_PAYMENT_VERIFICATION_FAILED",
            "payment_attempt_id": str(oid_obj),
            "razorpay_order_id": razorpay_order_id,
            "razorpay_payment_id": razorpay_payment_id,
            "error": str(exc),
            "created_by": str(u.get("_id") or u.get("id") or ""),
            "created_by_name": u.get("name") or "Customer",
            "created_at": now
        }

        mongo.payment_attempts.update_one(
            {"_id": oid_obj},
            {
                "$set": {
                    "payment_status": "PAYMENT_VERIFICATION_FAILED",
                    "payment_collection_status": "ONLINE_VERIFICATION_FAILED",
                    "razorpay_payment_verification_error": str(exc),
                    "razorpay_payment_verification_failed_at": now,
                    "updated_at": now
                },
                "$push": {
                    "payment_audit_logs": failed_event
                }
            }
        )

        return jsonify({
            "ok": False,
            "message": "Payment verification failed. Please contact NE FRESH support."
        }), 400

    now = datetime.utcnow().isoformat()

    order_doc = dict(attempt.get("order_doc") or {})
    order_items = list(attempt.get("order_items") or [])
    transactions = list(attempt.get("transactions") or [])
    order_address = dict(attempt.get("order_address") or {})

    if not order_doc or not order_items:
        return jsonify({
            "ok": False,
            "message": "Payment attempt data is incomplete. Please contact NE FRESH support."
        }), 400

    # Atomically reserve stock at final payment success before creating the real order.
    stock_reserved, stock_message = _reserve_order_stock_items(order_items)

    if not stock_reserved:
        return jsonify({
            "ok": False,
            "message": stock_message or "One product is out of stock or quantity is no longer available."
        }), 409

    paid_event = {
        "action": "RAZORPAY_PAYMENT_VERIFIED",
        "order_id": str(oid_obj),
        "payment_attempt_id": str(oid_obj),
        "razorpay_order_id": razorpay_order_id,
        "razorpay_payment_id": razorpay_payment_id,
        "amount": float(attempt.get("total_payable") or attempt.get("amount") or order_doc.get("total_payable") or 0),
        "created_by": str(u.get("_id") or u.get("id") or ""),
        "created_by_name": u.get("name") or "Customer",
        "created_at": now
    }

    order_doc["_id"] = oid_obj
    order_doc["order_number"] = (order_doc.get("order_number") or "").strip() or _next_public_order_number()
    order_doc["status"] = "PLACED"
    order_doc["payment_status"] = "PAID"
    order_doc["payment_collection_status"] = "PAID"
    order_doc["payment_received_by"] = "ADMIN_PLATFORM"
    order_doc["payment_collected_at"] = now
    order_doc["payment_collection_channel"] = "RAZORPAY"
    order_doc["payment_reconciliation_status"] = "VERIFIED"
    order_doc["upi_delivery_reconciliation_status"] = "NOT_APPLICABLE"

    order_doc["razorpay_order_id"] = razorpay_order_id
    order_doc["razorpay_payment_id"] = razorpay_payment_id
    order_doc["razorpay_signature"] = razorpay_signature
    order_doc["razorpay_payment_verified_at"] = now

    order_doc["payment_gateway"] = "RAZORPAY"
    order_doc["payment_gateway_mode"] = settings.get("mode")

    order_doc["platform_fee_status"] = "RECEIVED"
    order_doc["platform_fee_received_at"] = now
    order_doc["rider_cash_settlement_status"] = "NOT_REQUIRED"
    order_doc["cod_collection_status"] = "NOT_REQUIRED"

    order_doc["order_settlement_status"] = "PENDING_STORE_PAYOUT"
    order_doc["settlement_status"] = "PENDING"
    order_doc["updated_at"] = now

    order_doc["payment_audit_logs"] = list(order_doc.get("payment_audit_logs") or []) + [paid_event]
    order_doc["settlement_audit_logs"] = list(order_doc.get("settlement_audit_logs") or []) + [paid_event]

    try:
        mongo.orders.insert_one(order_doc)
    except DuplicateKeyError:
        _release_order_stock_items(order_items)
        return jsonify({
            "ok": True,
            "message": "Payment is already verified.",
            "redirect_url": url_for("order_track", oid=str(oid_obj))
        })

    for item in order_items:
        item_doc = dict(item)
        item_doc["order_id"] = oid_obj

        try:
            mongo.order_items.insert_one(item_doc)
        except DuplicateKeyError:
            pass

    for tx in transactions:
        tx_doc = dict(tx)
        tx_doc["order_id"] = oid_obj
        tx_doc["status"] = "PAID"
        tx_doc["payment_status"] = "PAID"
        tx_doc["payment_collection_status"] = "PAID"
        tx_doc["payment_received_by"] = "ADMIN_PLATFORM"
        tx_doc["payment_collected_at"] = now
        tx_doc["payment_collection_channel"] = "RAZORPAY"
        tx_doc["payment_reconciliation_status"] = "VERIFIED"
        tx_doc["upi_delivery_reconciliation_status"] = "NOT_APPLICABLE"
        tx_doc["razorpay_order_id"] = razorpay_order_id
        tx_doc["razorpay_payment_id"] = razorpay_payment_id
        tx_doc["payment_gateway"] = "RAZORPAY"
        tx_doc["payment_gateway_mode"] = settings.get("mode")
        tx_doc["platform_fee_status"] = "RECEIVED"
        tx_doc["rider_cash_settlement_status"] = "NOT_REQUIRED"
        tx_doc["order_settlement_status"] = "PENDING_STORE_PAYOUT"
        tx_doc["settlement_status"] = "PENDING"
        tx_doc["updated_at"] = now

        try:
            mongo.transactions.insert_one(tx_doc)
        except DuplicateKeyError:
            pass

    if order_address:
        address_doc = dict(order_address)
        address_doc["order_id"] = oid_obj

        try:
            mongo.order_addresses.insert_one(address_doc)
        except DuplicateKeyError:
            pass

    mongo.order_events.insert_one({
        "order_id": oid_obj,
        "status": "PLACED",
        "note": "Online payment verified successfully. Order placed and waiting for store confirmation.",
        "created_at": now
    })

    mongo.cart_items.delete_many({
        "cart_id": attempt.get("cart_id")
    })

    mongo.payment_attempts.update_one(
        {"_id": oid_obj},
        {
            "$set": {
                "status": "CONVERTED_TO_ORDER",
                "payment_status": "PAID",
                "payment_collection_status": "PAID",
                "order_id": str(oid_obj),
                "razorpay_order_id": razorpay_order_id,
                "razorpay_payment_id": razorpay_payment_id,
                "razorpay_signature": razorpay_signature,
                "converted_to_order_at": now,
                "updated_at": now
            },
            "$push": {
                "payment_audit_logs": paid_event
            }
        }
    )

    return jsonify({
        "ok": True,
        "message": "Payment verified successfully. Order placed.",
        "redirect_url": url_for("order_track", oid=str(oid_obj))
    })
