"""Orders history tracking route section.

Route decorators, endpoint names, URL paths, HTTP methods and function bodies are
preserved from the Step 6 source. Shared legacy helpers are imported from
``routes.orders.shared`` during this transitional decomposition.
"""

from routes.orders.shared import *

@app.route("/orders", endpoint="orders")
@login_required()
def my_orders():
    u = current_user()

    orders = list(
        mongo.orders.find({"user_id": u["id"]}).sort("created_at", -1)
    )

    for o in orders:
        o["id"] = str(o["_id"])
        o["order_number"] = (o.get("order_number") or "").strip()
        o["display_order_number"] = o["order_number"] or f"#{o['id']}"
        o["store_name"] = o.get("store_name", "")

        o = normalize_order_money_fields(o)
        o = decorate_customer_payment_display(o)
        o = decorate_order_delivery_mode_display(o)

        o["delivery_fee"] = float(o.get("delivery_fee") or 0)
        o["delivery_fee_amount"] = float(o.get("delivery_fee_amount") or o.get("delivery_fee") or 0)
        o["delivery_fee_source"] = o.get("delivery_fee_source") or ""
        o["delivery_fee_slab"] = o.get("delivery_fee_slab") or {}
        o["delivery_fee_details"] = o.get("delivery_fee_details") or {}

        o["free_delivery_above_applied"] = bool(o.get("free_delivery_above_applied"))
        o["free_delivery_above"] = float(o.get("free_delivery_above") or 0)
        o["original_delivery_fee"] = float(o.get("original_delivery_fee") or o.get("delivery_fee") or 0)
        o["free_delivery_savings"] = float(o.get("free_delivery_savings") or 0)
        o["delivery_fee_source"] = o.get("delivery_fee_source") or ""
        o["delivery_fee_slab"] = o.get("delivery_fee_slab") or {}
        o["delivery_fee_details"] = o.get("delivery_fee_details") or {}

        o["free_delivery_above_applied"] = bool(o.get("free_delivery_above_applied"))
        o["free_delivery_above"] = float(o.get("free_delivery_above") or 0)
        o["original_delivery_fee"] = float(o.get("original_delivery_fee") or o.get("delivery_fee") or 0)
        o["free_delivery_savings"] = float(o.get("free_delivery_savings") or 0)

        o["platform_fee"] = float(o.get("platform_fee") or 0)
        o["admin_platform_earning"] = float(o.get("admin_platform_earning") or o.get("platform_fee") or 0)
        o["platform_fee_source"] = o.get("platform_fee_source") or "disabled"

        o["tip_amount"] = float(o.get("tip_amount") or 0)
        o["delivery_tip_amount"] = float(o.get("delivery_tip_amount") or o.get("tip_amount") or 0)

        # Already normalized above:
        # items_subtotal = product subtotal
        # store_earning = store payout amount
        # total_amount / total_payable = final customer payable

        o["admin_platform_fee_status"] = o.get("admin_platform_fee_status") or ""
        o["settlement_status"] = o.get("settlement_status") or ""

        # Customer-facing return/refund display fields
        o["return_status"] = (o.get("return_status") or "").strip().upper()
        o["refund_status"] = (o.get("refund_status") or "").strip().upper()
        o["refund_reason"] = o.get("refund_reason") or ""
        o["refund_amount"] = float(o.get("refund_amount") or 0)
        o["refund_items_amount"] = float(o.get("refund_items_amount") or 0)
        o["refund_delivery_fee"] = float(o.get("refund_delivery_fee") or 0)
        o["refund_platform_fee"] = float(o.get("refund_platform_fee") or 0)
        o["refund_tip_amount"] = float(o.get("refund_tip_amount") or 0)
        o["refund_method"] = o.get("refund_method") or ""
        o["refund_reference"] = o.get("refund_reference") or ""
        o["refund_processed_at"] = o.get("refund_processed_at") or ""
        o["refund_processed_by_name"] = o.get("refund_processed_by_name") or ""
        o["return_requested_at"] = o.get("return_requested_at") or ""
        o["return_reason"] = o.get("return_reason") or ""

        # Customer-friendly delivery workflow state for My Orders page
        o["needs_reassignment"] = bool(o.get("needs_reassignment"))
        o["delivery_cancelled_by_partner"] = bool(o.get("delivery_cancelled_by_partner"))
        o["delivery_reassigned_at"] = o.get("delivery_reassigned_at")

        o["delivery_failed_reason"] = o.get("delivery_failed_reason") or ""
        o["delivery_failed_note"] = o.get("delivery_failed_note") or ""
        o["delivery_failed_at"] = o.get("delivery_failed_at") or ""
        o["delivery_failed_requires_store_action"] = bool(o.get("delivery_failed_requires_store_action", False))
        o["delivery_failed_store_decision"] = o.get("delivery_failed_store_decision") or ""
        o["delivery_rescheduled"] = bool(o.get("delivery_rescheduled", False))
        o["delivery_rescheduled_for"] = o.get("delivery_rescheduled_for") or ""
        o["delivery_rescheduled_note"] = o.get("delivery_rescheduled_note") or ""

        # Customer-facing My Orders date/time display only.
        # Stored timestamps and all order workflow logic remain unchanged.
        o["created_at_display"] = _format_customer_datetime(o.get("created_at"))
        o["delivery_rescheduled_for_display"] = _format_customer_datetime(
            o.get("delivery_rescheduled_for")
        )

    return_refund_enabled_now = is_delivery_feature_enabled("return_refund_enabled", True)
    return_policy_settings = get_return_refund_policy_settings()

    if not return_refund_enabled_now:
        return_policy_settings["enabled"] = False

    for o in orders:
        if return_refund_enabled_now:
            return_eligibility = get_order_return_eligibility(o, return_policy_settings)
        else:
            return_eligibility = {
                "allowed": False,
                "reason": "Return/refund is currently disabled by NE FRESH.",
                "policy_enabled": False,
                "return_window_hours": None,
                "deadline": ""
            }

        o["return_allowed"] = bool(return_eligibility.get("allowed"))
        o["return_not_allowed_reason"] = return_eligibility.get("reason") or ""
        o["return_policy_enabled"] = bool(return_eligibility.get("policy_enabled"))
        o["return_window_hours"] = return_eligibility.get("return_window_hours")
        o["return_deadline"] = return_eligibility.get("deadline") or ""

    return render_template(
        "orders.html",
        orders=orders,
        user=u,
        return_policy_settings=return_policy_settings,
        return_policy_enabled=bool(return_refund_enabled_now and return_policy_settings.get("enabled")),
        return_window_hours=return_policy_settings.get("return_window_hours")
    )


@app.route("/orders/<oid>")
@login_required()
def order_track(oid):
    u = current_user()

    # Store users must stay inside the Store operations shell.  Keep this
    # redirect as a defensive fallback for old bookmarks or stale links.
    if (u.get("role") or "").strip().lower() == "store":
        return redirect(url_for("store_order_track", oid=oid))

    data = get_order_full(
        oid,
        for_user_id=u["id"] if u["role"] == "customer" else None
    )

    if not data:
        flash("Order not found.", "danger")
        return redirect(url_for("orders"))

    return_refund_enabled_now = is_delivery_feature_enabled("return_refund_enabled", True)
    return_policy_settings = get_return_refund_policy_settings()

    if not return_refund_enabled_now:
        return_policy_settings["enabled"] = False

    order_doc = data.get("order") or {}

    order_doc = normalize_order_money_fields(order_doc)
    order_doc = decorate_customer_payment_display(order_doc)
    order_doc = decorate_order_delivery_mode_display(order_doc)

    # Keep MongoDB _id/id for routes and relations.
    # Use the same public customer-facing number shown on My Orders.
    order_doc["id"] = str(order_doc.get("_id") or order_doc.get("id") or oid)
    order_doc["order_number"] = (order_doc.get("order_number") or "").strip()
    order_doc["display_order_number"] = (
        order_doc["order_number"] or f"#{order_doc['id']}"
    )

    # Customer-facing display values only; stored timestamps stay unchanged.
    for _field in [
        "assigned_at",
        "delivery_rescheduled_for",
        "refund_processed_at",
        "return_requested_at",
    ]:
        order_doc[f"{_field}_display"] = _format_customer_datetime(
            order_doc.get(_field)
        )

    for _event in data.get("events", []):
        _event["created_at_display"] = _format_customer_datetime(
            _event.get("created_at")
        )

    # Display-only store contact for the customer Track Order page.
    # No order/store data is modified.
    order_doc["store_phone_display"] = ""
    _store_id = order_doc.get("store_id")

    if _store_id:
        try:
            _store_obj_id = (
                _store_id
                if isinstance(_store_id, ObjectId)
                else ObjectId(str(_store_id))
            )

            _store_doc = mongo.stores.find_one(
                {"_id": _store_obj_id},
                {
                    "phone": 1,
                    "mobile": 1,
                    "phone_number": 1,
                    "mobile_number": 1,
                    "contact_number": 1,
                    "contact_phone": 1,
                },
            ) or {}

            order_doc["store_phone_display"] = str(
                _store_doc.get("phone")
                or _store_doc.get("mobile")
                or _store_doc.get("phone_number")
                or _store_doc.get("mobile_number")
                or _store_doc.get("contact_number")
                or _store_doc.get("contact_phone")
                or ""
            ).strip()
        except Exception:
            order_doc["store_phone_display"] = ""

    data["order"] = order_doc

    if return_refund_enabled_now:
        return_eligibility = get_order_return_eligibility(order_doc, return_policy_settings)
    else:
        return_eligibility = {
            "allowed": False,
            "reason": "Return/refund is currently disabled by NE FRESH.",
            "policy_enabled": False,
            "return_window_hours": None,
            "deadline": ""
        }

    return render_template(
        "order_track.html",
        user=u,
        return_policy_settings=return_policy_settings,
        return_policy_enabled=bool(return_refund_enabled_now and return_policy_settings.get("enabled")),
        return_window_hours=return_policy_settings.get("return_window_hours"),
        return_allowed=bool(return_eligibility.get("allowed")),
        return_not_allowed_reason=return_eligibility.get("reason") or "",
        return_deadline=return_eligibility.get("deadline") or "",
        **data
    )


@app.route("/orders/<oid>/feedback", methods=["POST"])
@login_required()
def order_feedback(oid):
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

    if order_doc.get("status") != "DELIVERED":
        flash("You can submit feedback only after delivery.", "warning")
        return redirect(url_for("order_track", oid=oid))

    if request.form.get("received_confirm") != "1":
        flash("Please confirm that you received your items.", "warning")
        return redirect(url_for("order_track", oid=oid))

    now = datetime.utcnow().isoformat()

    store_rating = _clamp_rating(request.form.get("store_rating"))
    store_comment = (request.form.get("store_comment") or "").strip() or None

    if store_rating:
        mongo.store_ratings.insert_one({
            "user_id": u["id"],
            "order_id": oid_obj,
            "store_id": order_doc.get("store_id"),
            "rating": store_rating,
            "comment": store_comment,
            "created_at": now
        })

    delivery_rating = _clamp_rating(request.form.get("delivery_rating"))
    delivery_comment = (request.form.get("delivery_comment") or "").strip() or None

    if order_doc.get("delivery_partner_id") and delivery_rating:
        mongo.delivery_ratings.insert_one({
            "user_id": u["id"],
            "order_id": oid_obj,
            "delivery_partner_id": order_doc.get("delivery_partner_id"),
            "rating": delivery_rating,
            "comment": delivery_comment,
            "created_at": now
        })

    order_items = list(mongo.order_items.find({"order_id": oid_obj}))

    for it in order_items:
        pid = it.get("product_id")
        if not pid:
            continue

        pid_str = str(pid)
        rating_value = _clamp_rating(request.form.get(f"product_rating_{pid_str}"))
        comment_value = (request.form.get(f"product_comment_{pid_str}") or "").strip() or None

        if rating_value:
            mongo.product_ratings.insert_one({
                "user_id": u["id"],
                "order_id": oid_obj,
                "product_id": pid,
                "product_name": it.get("product_name", ""),
                "rating": rating_value,
                "comment": comment_value,
                "created_at": now
            })

    title = (request.form.get("complaint_title") or "").strip()
    desc = (request.form.get("complaint_description") or "").strip()

    image = request.files.get("complaint_image")
    image_path = None

    if image and image.filename and allowed_file(image.filename):
        fn = secure_filename(image.filename)
        save_as = datetime.utcnow().strftime("%Y%m%d%H%M%S_") + fn
        os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
        image.save(os.path.join(app.config["UPLOAD_FOLDER"], save_as))
        image_path = f"uploads/{save_as}"

    if title or desc or image_path:
        message = f"{title}\n{desc}".strip()

        mongo.complaints.insert_one({
            "user_id": u["id"],
            "order_id": oid_obj,
            "target_type": "store",
            "target_id": order_doc.get("store_id"),
            "title": title or None,
            "message": message,
            "image_path": image_path,
            "status": "NEW",
            "created_at": now
        })

        if order_doc.get("delivery_partner_id"):
            mongo.complaints.insert_one({
                "user_id": u["id"],
                "order_id": oid_obj,
                "target_type": "delivery",
                "target_id": order_doc.get("delivery_partner_id"),
                "title": title or None,
                "message": message,
                "image_path": image_path,
                "status": "NEW",
                "created_at": now
            })

    flash("Thanks for your feedback!", "success")
    return redirect(url_for("order_track", oid=oid))
