"""Admin settings route section.

Route decorators, endpoint names, URL paths, HTTP methods and function bodies are
preserved from the Step 6 source. Shared legacy helpers are imported from
``routes.admin.shared`` during this transitional decomposition.
"""

from routes.admin.shared import *

@app.route("/admin/in-house-delivery/disable", methods=["POST"], endpoint="admin_disable_in_house_delivery_quick")
@login_required(role="admin")
def admin_disable_in_house_delivery_quick():
    _admin_save_in_house_delivery_operation(False)
    flash("In-house delivery has been disabled. Related Admin and Store pages are now hidden.", "success")
    return _admin_redirect_back("admin_dashboard")


@app.route("/admin/in-house-delivery/enable", methods=["POST"], endpoint="admin_enable_in_house_delivery_quick")
@login_required(role="admin")
def admin_enable_in_house_delivery_quick():
    _admin_save_in_house_delivery_operation(True)
    flash("In-house delivery has been enabled. Related Admin and Store pages are now visible again.", "success")
    return _admin_redirect_back("admin_dashboard")


@app.route("/admin/exports", methods=["GET"], endpoint="admin_exports")
@login_required(role="admin")
def admin_exports():
    """
    Central Admin Export Center.

    This page does not generate CSV itself.
    It gives Admin one place to download all important reports.
    """
    export_cards = [
        {
            "title": "Store Payouts & In-house Collection",
            "description": "Rider COD cash, store payout pending, refund deduction, adjusted payout and settlement impact.",
            "icon": "💳",
            "page_endpoint": "admin_settlements",
            "export_endpoint": "admin_settlements_export_csv",
            "button": "Download Settlements CSV",
            "tag": "Settlement"
        },
        {
            "title": "Platform Earnings",
            "description": "Platform fee earnings, refund platform fee adjustment, net platform fee and payment status.",
            "icon": "📈",
            "page_endpoint": "admin_platform_earnings",
            "export_endpoint": "admin_platform_earnings_export_csv",
            "button": "Download Earnings CSV",
            "tag": "Earnings"
        },
        {
            "title": "Return / Refund Settlement Impact",
            "description": "Cancelled, returned and refunded orders with refund amount, store deduction and payout impact.",
            "icon": "↩️",
            "page_endpoint": "admin_returns_settlements",
            "export_endpoint": "admin_returns_settlements_export_csv",
            "button": "Download Returns CSV",
            "tag": "Refunds"
        },
        {
            "title": "Settlement Audit Logs",
            "description": "Audit history for rider cash received, store payout paid and refund processed by Admin.",
            "icon": "📜",
            "page_endpoint": "admin_settlement_audit_logs",
            "export_endpoint": "admin_settlement_audit_logs_export_csv",
            "button": "Download Audit CSV",
            "tag": "Audit"
        },
        {
            "title": "Transactions",
            "description": "Transaction-level report from the existing transactions export.",
            "icon": "🧾",
            "page_endpoint": "",
            "export_endpoint": "admin_transactions_csv",
            "button": "Download Transactions CSV",
            "tag": "Transactions"
        },
    ]

    return render_template(
        "admin_exports.html",
        user=current_user(),
        export_cards=export_cards,
        active_page="exports",
        active_group="exports"
    )


@app.route("/admin/delivery-mode-settings", methods=["GET", "POST"], endpoint="admin_delivery_mode_settings")
@login_required(role="admin")
def admin_delivery_mode_settings():
    admin_user = current_user() or {}

    if request.method == "POST":
        existing_settings = get_delivery_mode_settings()

        operation_mode = (
            request.form.get("delivery_operation_mode")
            or existing_settings.get("delivery_operation_mode")
            or DELIVERY_OPERATION_IN_HOUSE_ONLY
        ).strip().upper()

        if operation_mode not in VALID_DELIVERY_OPERATION_MODES:
            operation_mode = DELIVERY_OPERATION_IN_HOUSE_ONLY

        if operation_mode == DELIVERY_OPERATION_IN_HOUSE_ONLY:
            in_house_enabled = True
            external_local_enabled = False
            third_party_enabled = False
            routing_mode = DELIVERY_ROUTING_MODE_MANUAL
            active_delivery_mode = DELIVERY_MODE_IN_HOUSE
        else:
            in_house_enabled = False
            external_local_enabled = _admin_bool_from_form(
                "external_local_delivery_enabled",
                existing_settings.get("external_local_delivery_enabled", True),
            )
            third_party_enabled = _admin_bool_from_form(
                "third_party_shipping_enabled",
                existing_settings.get("third_party_shipping_enabled", True),
            )

            if not external_local_enabled and not third_party_enabled:
                external_local_enabled = True
                flash("At least one external delivery channel is required in Connected External Delivery mode. External Local Delivery has been enabled automatically.", "warning")

            routing_mode = DELIVERY_ROUTING_MODE_AUTO
            active_delivery_mode = (
                DELIVERY_MODE_EXTERNAL_LOCAL
                if external_local_enabled
                else DELIVERY_MODE_THIRD_PARTY
            )

        allow_online_payment = _admin_bool_from_form("allow_online_payment", False)
        allow_pay_online_on_delivery = _admin_bool_from_form("allow_cod_payment", False)

        pay_on_delivery_upi_enabled = _admin_bool_from_form(
            "pay_on_delivery_upi_enabled",
            bool(existing_settings.get("pay_on_delivery_upi_enabled", False))
        )
        pay_on_delivery_upi_id = (
            request.form.get("pay_on_delivery_upi_id")
            or existing_settings.get("pay_on_delivery_upi_id")
            or ""
        ).strip()
        pay_on_delivery_upi_name = (
            request.form.get("pay_on_delivery_upi_name")
            or existing_settings.get("pay_on_delivery_upi_name")
            or "NE LOCALS"
        ).strip()

        if len(pay_on_delivery_upi_name) > 80:
            pay_on_delivery_upi_name = pay_on_delivery_upi_name[:80]

        upi_id_pattern = re.compile(r"^[A-Za-z0-9._-]{2,256}@[A-Za-z0-9.-]{2,64}$")

        if pay_on_delivery_upi_enabled and not allow_pay_online_on_delivery:
            pay_on_delivery_upi_enabled = False

        if pay_on_delivery_upi_enabled and not upi_id_pattern.match(pay_on_delivery_upi_id):
            flash("Enter a valid official UPI ID before enabling UPI at delivery.", "warning")
            return redirect(url_for("admin_delivery_mode_settings"))

        if not allow_online_payment and not allow_pay_online_on_delivery:
            allow_online_payment = True
            flash("At least one customer payment method is required. Online Payment has been enabled automatically.", "warning")

        if allow_online_payment and allow_pay_online_on_delivery:
            delivery_payment_methods = DELIVERY_PAYMENT_ONLINE_AND_COD
        elif allow_pay_online_on_delivery:
            delivery_payment_methods = DELIVERY_PAYMENT_COD_ONLY
        else:
            delivery_payment_methods = DELIVERY_PAYMENT_ONLINE_ONLY

        # Backend keeps COD fields for compatibility. Customer-facing wording is
        # Pay on Delivery; in-house riders can record Cash or official UPI at handover.
        cod_collection_method = (
            COD_COLLECTION_DELIVERY_BOY
            if in_house_enabled and allow_pay_online_on_delivery
            else (COD_COLLECTION_STORE if allow_pay_online_on_delivery else "")
        )
        external_payment_rule = external_payment_rule_from_methods(
            DELIVERY_MODE_EXTERNAL_LOCAL,
            allow_pay_online_on_delivery,
            COD_COLLECTION_STORE if allow_pay_online_on_delivery else "",
        )

        return_refund_enabled = _admin_bool_from_form(
            "return_refund_enabled",
            bool(existing_settings.get("return_refund_enabled", in_house_enabled))
        )

        now = datetime.utcnow().isoformat()
        update_data = {
            "key": DELIVERY_MODE_SETTINGS_KEY,
            "delivery_operation_mode": operation_mode,
            "delivery_routing_mode": routing_mode,
            "active_delivery_mode": active_delivery_mode,

            "in_house_delivery_enabled": bool(in_house_enabled),
            "external_local_delivery_enabled": bool(external_local_enabled),
            "third_party_shipping_enabled": bool(third_party_enabled),
            "external_delivery_enabled": bool(external_local_enabled or third_party_enabled),
            "shiprocket_shipping_enabled": bool(third_party_enabled),

            "delivery_boy_panel_enabled": bool(in_house_enabled),
            "delivery_assignment_enabled": bool(in_house_enabled),
            "delivery_tracking_enabled": bool(in_house_enabled),
            "cod_rider_collection_enabled": bool(in_house_enabled),

            "return_refund_enabled": bool(return_refund_enabled),

            "delivery_payment_methods": delivery_payment_methods,
            "allow_online_payment": bool(allow_online_payment),
            "allow_cod_payment": bool(allow_pay_online_on_delivery),
            "cod_collection_method": cod_collection_method,
            "pay_on_delivery_upi_enabled": bool(pay_on_delivery_upi_enabled),
            "pay_on_delivery_upi_id": pay_on_delivery_upi_id,
            "pay_on_delivery_upi_name": pay_on_delivery_upi_name or "NE LOCALS",
            "external_payment_rule": external_payment_rule,

            "external_local_provider": "LOCAL_DELIVERY_PARTNER",
            "third_party_provider": "SHIPROCKET",

            "updated_at": now,
            "updated_by": str(admin_user.get("_id") or admin_user.get("id") or ""),
            "updated_by_name": admin_user.get("name") or admin_user.get("email") or "Admin",
        }

        mongo.platform_settings.update_one(
            {"key": DELIVERY_MODE_SETTINGS_KEY},
            {
                "$set": update_data,
                "$setOnInsert": {"created_at": now}
            },
            upsert=True
        )

        if operation_mode == DELIVERY_OPERATION_IN_HOUSE_ONLY:
            flash("In-house Delivery mode saved. External delivery channels are disabled while in-house is active.", "success")
        else:
            flash("Connected External Delivery mode saved. Checkout will route orders to External Local or Shiprocket only.", "success")
        return redirect(url_for("admin_delivery_mode_settings"))

    settings = get_delivery_mode_settings()
    external_settings = get_external_delivery_settings()

    return render_template(
        "admin_delivery_routing_settings.html",
        user=admin_user,
        settings=settings,
        external_settings=external_settings,
        delivery_channels=[
            {
                "field": "external_local_delivery_enabled",
                "title": "External Local Delivery",
                "subtitle": "For Rapido/Ola/Uber-style local delivery. NE Locals stores only the order reference and charges the hard-coded local fare; rider payment/tracking stays outside NE Locals.",
                "badge": f"Up to {external_settings.get('external_local_max_distance_km', 25)} km",
                "icon": "⚡",
            },
            {
                "field": "third_party_shipping_enabled",
                "title": "Shiprocket / Courier Shipping",
                "subtitle": "Used for outside-local/inter-city orders. Shiprocket booking uses real API credentials when configured and requires Online Payment before shipment creation.",
                "badge": "Outside local zone",
                "icon": "📦",
            },
        ],
        active_group="delivery",
        active_page="delivery_mode_settings",
    )


@app.route("/admin/delivery-fee-settings", methods=["GET", "POST"], endpoint="admin_delivery_fee_settings")
@login_required(role="admin")
def admin_delivery_fee_settings():
    admin_user = current_user() or {}

    existing = mongo.platform_settings.find_one({
        "key": ADMIN_DELIVERY_FEE_SETTINGS_KEY
    }) or {}

    if request.method == "POST":
        delivery_base_fee = _admin_money_or_default(
            request.form.get("delivery_base_fee"),
            existing.get("delivery_base_fee", BASE_DELIVERY_FEE_INR)
        )

        free_delivery_above = _admin_money_or_default(
            request.form.get("free_delivery_above"),
            existing.get("free_delivery_above", 0)
        )

        delivery_min_order_amount = _admin_money_or_default(
            request.form.get("delivery_min_order_amount"),
            existing.get("delivery_min_order_amount", 0)
        )

        max_delivery_distance_km = _admin_float_or_none(
            request.form.get("max_delivery_distance_km"),
            0,
            999999
        )

        delivery_fee_slabs_enabled = _admin_bool_from_form(
            "delivery_fee_slabs_enabled",
            False
        )

        delivery_fee_slabs = _admin_clean_delivery_fee_slabs_from_form()

        if delivery_fee_slabs_enabled and not delivery_fee_slabs:
            flash("Please add at least one valid delivery fee slab or disable distance-wise slabs.", "warning")
            return redirect(url_for("admin_delivery_fee_settings"))

        delivery_boy_earning_rule = (
            request.form.get("delivery_boy_earning_rule")
            or "DELIVERY_FEE_PLUS_TIP"
        ).strip().upper()

        if delivery_boy_earning_rule not in ["DELIVERY_FEE_PLUS_TIP"]:
            delivery_boy_earning_rule = "DELIVERY_FEE_PLUS_TIP"

        notes = (request.form.get("notes") or "").strip()

        if len(notes) > 1000:
            notes = notes[:1000]

        now = datetime.utcnow().isoformat()

        update_data = {
            "key": ADMIN_DELIVERY_FEE_SETTINGS_KEY,
            "delivery_base_fee": round(float(delivery_base_fee), 2),
            "free_delivery_above": round(float(free_delivery_above), 2),
            "delivery_min_order_amount": round(float(delivery_min_order_amount), 2),
            "max_delivery_distance_km": max_delivery_distance_km,
            "delivery_fee_slabs_enabled": bool(delivery_fee_slabs_enabled),
            "delivery_fee_slabs": delivery_fee_slabs,
            "delivery_boy_earning_rule": delivery_boy_earning_rule,
            "notes": notes,
            "updated_at": now,
            "updated_by": str(admin_user.get("_id") or admin_user.get("id") or ""),
            "updated_by_name": admin_user.get("name") or admin_user.get("email") or "Admin"
        }

        mongo.platform_settings.update_one(
            {"key": ADMIN_DELIVERY_FEE_SETTINGS_KEY},
            {
                "$set": update_data,
                "$setOnInsert": {
                    "created_at": now
                }
            },
            upsert=True
        )

        flash("Delivery fee settings updated successfully.", "success")
        return redirect(url_for("admin_delivery_fee_settings"))

    settings = get_platform_delivery_fee_settings()

    return render_template(
        "admin_delivery_fee_settings.html",
        user=admin_user,
        settings=settings,
        active_group="delivery",
        active_page="delivery_fee_settings"
    )


@app.route("/admin/payment-settings", methods=["GET", "POST"], endpoint="admin_payment_settings")
@login_required(role="admin")
def admin_payment_settings():
    """
    Admin controls online payment settings.

    Razorpay Key ID / Secret are read from .env only.
    This page only controls gateway enable/disable, mode, capture/refund flags and notes.
    """
    admin_user = current_user() or {}

    if request.method == "POST":
        enabled = _admin_bool_from_form("enabled", False)
        auto_refund_enabled = _admin_bool_from_form("auto_refund_enabled", False)
        auto_capture_enabled = _admin_bool_from_form("auto_capture_enabled", True)

        gateway = (request.form.get("gateway") or "RAZORPAY").strip().upper()
        mode = (request.form.get("mode") or "TEST").strip().upper()

        if gateway not in ["RAZORPAY"]:
            gateway = "RAZORPAY"

        if mode not in ["TEST", "LIVE"]:
            mode = "TEST"

        notes = (request.form.get("notes") or "").strip()

        if len(notes) > 1000:
            notes = notes[:1000]

        env_status = _admin_get_razorpay_env_status(mode)

        if enabled:
            if not env_status.get("key_id_configured") or not env_status.get("key_secret_configured"):
                flash(
                    "Razorpay credentials are missing in .env. Please set the Key ID and Secret for the selected mode before enabling online payment.",
                    "warning"
                )
                return redirect(url_for("admin_payment_settings"))

        now = datetime.utcnow().isoformat()

        old_settings = mongo.platform_settings.find_one({
            "key": PAYMENT_GATEWAY_SETTINGS_KEY
        }) or {}

        update_data = {
            "key": PAYMENT_GATEWAY_SETTINGS_KEY,
            "enabled": bool(enabled),
            "gateway": gateway,
            "mode": mode,

            # Security: do not store Razorpay keys/secrets in MongoDB.
            # Actual keys are loaded from .env in routes/orders/routes.py.
            "auto_refund_enabled": bool(auto_refund_enabled),
            "auto_capture_enabled": bool(auto_capture_enabled),
            "notes": notes,

            "updated_at": now,
            "updated_by": str(admin_user.get("id") or admin_user.get("_id") or ""),
            "updated_by_name": admin_user.get("name") or admin_user.get("email") or "Admin"
        }

        mongo.platform_settings.update_one(
            {"key": PAYMENT_GATEWAY_SETTINGS_KEY},
            {
                "$set": update_data,
                "$unset": {
                    "razorpay_key_id": "",
                    "razorpay_key_secret": "",
                    "webhook_secret": ""
                },
                "$setOnInsert": {
                    "created_at": now
                }
            },
            upsert=True
        )

        mongo.admin_audit_logs.insert_one({
            "action": "PAYMENT_GATEWAY_SETTINGS_UPDATED",
            "module": "payment_gateway",
            "old_value": {
                "enabled": old_settings.get("enabled"),
                "gateway": old_settings.get("gateway"),
                "mode": old_settings.get("mode"),
                "auto_refund_enabled": old_settings.get("auto_refund_enabled"),
                "auto_capture_enabled": old_settings.get("auto_capture_enabled"),
            },
            "new_value": {
                "enabled": update_data.get("enabled"),
                "gateway": update_data.get("gateway"),
                "mode": update_data.get("mode"),
                "auto_refund_enabled": update_data.get("auto_refund_enabled"),
                "auto_capture_enabled": update_data.get("auto_capture_enabled"),
            },
            "created_at": now,
            "created_by": str(admin_user.get("id") or admin_user.get("_id") or ""),
            "created_by_name": admin_user.get("name") or admin_user.get("email") or "Admin"
        })

        flash("Online payment settings updated successfully.", "success")
        return redirect(url_for("admin_payment_settings"))

    settings = _admin_get_payment_gateway_settings()

    secret_masked = (
        "Razorpay secret is configured in .env for the selected mode."
        if settings.get("razorpay_key_secret_configured")
        else "Razorpay secret is not configured in .env for the selected mode."
    )

    webhook_masked = ""

    return render_template(
        "admin_payment_settings.html",
        user=current_user(),
        settings=settings,
        secret_masked=secret_masked,
        webhook_masked=webhook_masked,
        active_group="system",
        active_page="payment_settings"
    )


@app.route("/admin/return-refund-policy", methods=["GET", "POST"], endpoint="admin_return_refund_policy")
@login_required(role="admin")
def admin_return_refund_policy():
    """
    Admin controls whether customer return/refund is enabled.

    If disabled:
    - Customer return button is hidden
    - Backend return request route is blocked

    If enabled:
    - Return allowed only within configured hours after delivery
    """
    admin_user = current_user() or {}

    if request.method == "POST":
        enabled = _admin_bool_from_form("enabled", False)

        try:
            return_window_hours = int(float(request.form.get("return_window_hours") or 24))
        except Exception:
            return_window_hours = 24

        if return_window_hours < 1:
            return_window_hours = 1

        if return_window_hours > 720:
            return_window_hours = 720

        default_refund_items = _admin_bool_from_form("default_refund_items", True)
        default_refund_delivery_fee = _admin_bool_from_form("default_refund_delivery_fee", False)
        default_refund_platform_fee = _admin_bool_from_form("default_refund_platform_fee", False)
        default_refund_tip = _admin_bool_from_form("default_refund_tip", False)

        policy_note = (request.form.get("policy_note") or "").strip()

        if len(policy_note) > 1000:
            policy_note = policy_note[:1000]

        now = datetime.utcnow().isoformat()

        old_settings = mongo.platform_settings.find_one({
            "key": RETURN_REFUND_POLICY_SETTINGS_KEY
        }) or {}

        update_data = {
            "key": RETURN_REFUND_POLICY_SETTINGS_KEY,
            "enabled": bool(enabled),
            "return_window_hours": return_window_hours,

            # Default refund breakup when customer creates request.
            # Admin can still edit final refund on refund-processing page.
            "default_refund_items": bool(default_refund_items),
            "default_refund_delivery_fee": bool(default_refund_delivery_fee),
            "default_refund_platform_fee": bool(default_refund_platform_fee),
            "default_refund_tip": bool(default_refund_tip),

            "policy_note": policy_note,
            "updated_at": now,
            "updated_by": str(admin_user.get("id") or admin_user.get("_id") or ""),
            "updated_by_name": admin_user.get("name") or admin_user.get("email") or "Admin"
        }

        mongo.platform_settings.update_one(
            {"key": RETURN_REFUND_POLICY_SETTINGS_KEY},
            {
                "$set": update_data,
                "$setOnInsert": {
                    "created_at": now
                }
            },
            upsert=True
        )

        mongo.admin_audit_logs.insert_one({
            "action": "RETURN_REFUND_POLICY_UPDATED",
            "module": "return_refund_policy",
            "old_value": {
                "enabled": old_settings.get("enabled"),
                "return_window_hours": old_settings.get("return_window_hours"),
                "default_refund_items": old_settings.get("default_refund_items"),
                "default_refund_delivery_fee": old_settings.get("default_refund_delivery_fee"),
                "default_refund_platform_fee": old_settings.get("default_refund_platform_fee"),
                "default_refund_tip": old_settings.get("default_refund_tip"),
            },
            "new_value": {
                "enabled": update_data.get("enabled"),
                "return_window_hours": update_data.get("return_window_hours"),
                "default_refund_items": update_data.get("default_refund_items"),
                "default_refund_delivery_fee": update_data.get("default_refund_delivery_fee"),
                "default_refund_platform_fee": update_data.get("default_refund_platform_fee"),
                "default_refund_tip": update_data.get("default_refund_tip"),
            },
            "created_at": now,
            "created_by": str(admin_user.get("id") or admin_user.get("_id") or ""),
            "created_by_name": admin_user.get("name") or admin_user.get("email") or "Admin"
        })

        flash("Return/refund policy updated successfully.", "success")
        return redirect(url_for("admin_return_refund_policy"))

    settings = _admin_get_return_refund_policy_settings()

    return render_template(
        "admin_return_refund_policy.html",
        user=current_user(),
        settings=settings,
        active_group="settlements",
        active_page="return_refund_policy"
    )


@app.route("/admin/platform-fee-settings", methods=["GET", "POST"], endpoint="admin_platform_fee_settings")
@login_required(role="admin")
def admin_platform_fee_settings():
    """
    Admin controls platform fee charged on every order.

    Platform fee belongs to website/admin owner.

    Supported fee types:
    - fixed
    - percent
    - fixed_plus_percent
    """

    if request.method == "POST":
        enabled = _admin_bool_from_form("enabled", False)

        fee_type = (request.form.get("fee_type") or "fixed").strip().lower()

        if fee_type not in ["fixed", "percent", "fixed_plus_percent"]:
            fee_type = "fixed"

        fixed_amount = _admin_money_or_default(
            request.form.get("fixed_amount"),
            0
        )

        percent = _admin_float_or_none(
            request.form.get("percent"),
            0,
            100
        )

        if percent is None:
            percent = 0.0

        min_fee = _admin_money_or_default(
            request.form.get("min_fee"),
            0
        )

        max_fee = _admin_money_or_default(
            request.form.get("max_fee"),
            0
        )

        display_name = (request.form.get("display_name") or "Platform Fee").strip()

        if not display_name:
            display_name = "Platform Fee"

        description = (
            request.form.get("description")
            or "Platform fee supports secure ordering, customer support, and platform operations."
        ).strip()

        if max_fee > 0 and min_fee > max_fee:
            flash("Maximum platform fee must be greater than minimum platform fee.", "warning")
            return redirect(url_for("admin_platform_fee_settings"))

        if enabled:
            if fee_type == "fixed" and fixed_amount <= 0:
                flash("Please enter a fixed platform fee greater than 0, or disable platform fee.", "warning")
                return redirect(url_for("admin_platform_fee_settings"))

            if fee_type == "percent" and percent <= 0:
                flash("Please enter a platform fee percentage greater than 0, or disable platform fee.", "warning")
                return redirect(url_for("admin_platform_fee_settings"))

            if fee_type == "fixed_plus_percent" and fixed_amount <= 0 and percent <= 0:
                flash("Please enter fixed amount or percentage for platform fee.", "warning")
                return redirect(url_for("admin_platform_fee_settings"))

        now = datetime.utcnow().isoformat()
        admin_user = current_user() or {}

        settings_doc = {
            "key": PLATFORM_FEE_SETTINGS_KEY,
            "enabled": bool(enabled),
            "fee_type": fee_type,
            "fixed_amount": round(float(fixed_amount or 0), 2),
            "percent": round(float(percent or 0), 2),
            "min_fee": round(float(min_fee or 0), 2),
            "max_fee": round(float(max_fee or 0), 2),
            "display_name": display_name,
            "description": description,
            "updated_at": now,
            "updated_by": str(admin_user.get("_id") or admin_user.get("id") or "")
        }

        old_settings = mongo.platform_settings.find_one({
            "key": PLATFORM_FEE_SETTINGS_KEY
        }) or {}

        mongo.platform_settings.update_one(
            {
                "key": PLATFORM_FEE_SETTINGS_KEY
            },
            {
                "$set": settings_doc,
                "$setOnInsert": {
                    "created_at": now
                }
            },
            upsert=True
        )

        # Audit log for future reference.
        mongo.admin_audit_logs.insert_one({
            "action": "PLATFORM_FEE_SETTINGS_UPDATED",
            "module": "platform_fee",
            "old_value": {
                "enabled": old_settings.get("enabled"),
                "fee_type": old_settings.get("fee_type"),
                "fixed_amount": old_settings.get("fixed_amount"),
                "percent": old_settings.get("percent"),
                "min_fee": old_settings.get("min_fee"),
                "max_fee": old_settings.get("max_fee"),
            },
            "new_value": {
                "enabled": settings_doc.get("enabled"),
                "fee_type": settings_doc.get("fee_type"),
                "fixed_amount": settings_doc.get("fixed_amount"),
                "percent": settings_doc.get("percent"),
                "min_fee": settings_doc.get("min_fee"),
                "max_fee": settings_doc.get("max_fee"),
            },
            "created_at": now,
            "created_by": str(admin_user.get("_id") or admin_user.get("id") or ""),
            "created_by_name": admin_user.get("name") or admin_user.get("email") or "Admin"
        })

        flash("Platform fee settings updated successfully.", "success")
        return redirect(url_for("admin_platform_fee_settings"))

    settings = get_platform_fee_settings()

    preview_rows = []

    for amount in [100, 500, 1000]:
        result = calculate_platform_fee(amount)

        preview_rows.append({
            "items_total": amount,
            "platform_fee": result.get("platform_fee", 0),
            "total_with_platform_fee": amount + float(result.get("platform_fee", 0) or 0)
        })

    return render_template(
        "admin_platform_fee_settings.html",
        user=current_user(),
        settings=settings,
        preview_rows=preview_rows,
        active_group="system",
        active_page="platform_fee_settings"
    )
