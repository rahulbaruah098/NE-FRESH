"""Admin routes extracted from the updated app.py.

Logic, decorators, endpoint names and route paths are intentionally preserved.
Only the file location changed.
"""

from app_core import *

ADMIN_IN_HOUSE_DELIVERY_ENDPOINTS = {
    "admin_delivery_overview",
    "admin_delivery_history",
    "admin_create_delivery",
    "admin_delivery_list",
    "admin_delivery_reviews",
    "admin_delivery_export_csv",
    "admin_delivery_reviews_export_csv",
    "admin_store_delivery_toggle",
}

ADMIN_RETURN_REFUND_ENDPOINTS = {
    "admin_return_refund_policy",
    "admin_refund_processing",
    "admin_refund_admin_review",
    "admin_refund_process",
    "admin_returns_settlements",
    "admin_returns_settlements_export_csv",
}


@app.before_request
def _block_admin_delivery_and_return_pages_when_disabled():
    endpoint = request.endpoint or ""

    if endpoint in ADMIN_IN_HOUSE_DELIVERY_ENDPOINTS:
        if is_delivery_feature_enabled("in_house_delivery_enabled", True):
            return None

        flash("In-house delivery-boy system is currently disabled.", "warning")
        return redirect(url_for("admin_delivery_mode_settings"))

    if endpoint == "admin_settlement_rider_cash_received":
        if is_delivery_feature_enabled("cod_rider_collection_enabled", True):
            return None

        flash("COD rider cash collection is currently disabled.", "warning")
        return redirect(url_for("admin_settlements"))

    if endpoint in ADMIN_RETURN_REFUND_ENDPOINTS:
        if is_delivery_feature_enabled("return_refund_enabled", True):
            return None

        flash("Return/refund module is currently disabled.", "warning")
        return redirect(url_for("admin_delivery_mode_settings"))

    return None


def _admin_bool_from_form(name, default=False):
    value = request.form.get(name)

    if value is None:
        return bool(default)

    return str(value).strip().lower() in ["1", "true", "yes", "on"]


def _admin_float_or_none(value, min_value=None, max_value=None):
    try:
        if value is None or str(value).strip() == "":
            return None

        number = float(value)

        if min_value is not None and number < min_value:
            return None

        if max_value is not None and number > max_value:
            return None

        return number
    except Exception:
        return None


def _admin_money_or_default(value, default=0):
    try:
        if value is None or str(value).strip() == "":
            return float(default)

        number = float(value)

        if number < 0:
            return float(default)

        return round(number, 2)
    except Exception:
        return float(default)



def _admin_settlement_money(value, default=0.0):
    try:
        if value is None or str(value).strip() == "":
            return float(default)
        return round(float(value), 2)
    except Exception:
        return float(default)


def _admin_order_id_or_none(oid):
    try:
        return ObjectId(str(oid))
    except Exception:
        return None


def _admin_hydrate_settlement_order(order):
    order = order or {}

    oid = order.get("_id")
    order["id"] = str(oid) if oid else ""

    order["items_subtotal"] = _admin_settlement_money(
        order.get("items_subtotal"),
        order.get("store_earning") or 0
    )

    order["store_earning"] = _admin_settlement_money(
        order.get("store_earning"),
        order.get("items_subtotal") or 0
    )

    original_store_payout = _admin_settlement_money(
        order.get("original_store_payout_amount"),
        order.get("store_earning") or order.get("items_subtotal") or 0
    )

    saved_store_payout = _admin_settlement_money(
        order.get("store_payout_amount"),
        original_store_payout
    )

    order["delivery_fee"] = _admin_settlement_money(order.get("delivery_fee"))

    order["tip_amount"] = _admin_settlement_money(
        order.get("tip_amount"),
        order.get("delivery_tip_amount") or 0
    )

    order["delivery_boy_earning"] = _admin_settlement_money(
        order.get("delivery_boy_earning"),
        order["delivery_fee"] + order["tip_amount"]
    )

    order["platform_fee"] = _admin_settlement_money(order.get("platform_fee"))

    order["admin_platform_earning"] = _admin_settlement_money(
        order.get("admin_platform_earning"),
        order["platform_fee"]
    )

    order["total_payable"] = _admin_settlement_money(
        order.get("total_payable"),
        (
            order["items_subtotal"]
            + order["delivery_fee"]
            + order["platform_fee"]
            + order["tip_amount"]
        )
    )

    order["total_amount"] = _admin_settlement_money(
        order.get("total_amount"),
        order["total_payable"]
    )

    order["cod_collected_amount"] = _admin_settlement_money(
        order.get("cod_collected_amount"),
        order["total_payable"] if (order.get("payment_method") or "").upper() == "COD" else 0
    )

    order["rider_cash_to_submit"] = _admin_settlement_money(
        order.get("rider_cash_to_submit"),
        order.get("expected_rider_cash_to_submit") or 0
    )

    refund_items_amount = _admin_settlement_money(
        order.get("refund_items_amount")
        if order.get("refund_items_amount") is not None
        else order.get("refund_item_amount"),
        0
    )

    store_refund_deduction = _admin_settlement_money(
        order.get("store_refund_deduction")
        if order.get("store_refund_deduction") is not None
        else order.get("refund_deduction"),
        refund_items_amount
    )

    adjusted_store_payout = _admin_settlement_money(
        order.get("adjusted_store_payout"),
        saved_store_payout
    )

    if store_refund_deduction > 0 and order.get("adjusted_store_payout") is None:
        adjusted_store_payout = round(max(original_store_payout - store_refund_deduction, 0), 2)

    store_adjustment_due = _admin_settlement_money(
        order.get("store_adjustment_due"),
        0
    )

    settlement_impact = (
        order.get("settlement_impact")
        or (
            "DEDUCT_FROM_PENDING_PAYOUT"
            if store_refund_deduction > 0
            else "NO_DEDUCTION"
        )
    )

    order["original_store_payout_amount"] = original_store_payout
    order["store_refund_deduction"] = store_refund_deduction
    order["refund_deduction"] = store_refund_deduction
    order["adjusted_store_payout"] = adjusted_store_payout
    order["store_adjustment_due"] = store_adjustment_due
    order["settlement_impact"] = settlement_impact

    # For the settlement page, store_payout_amount must mean the payable amount Admin will actually pay now.
    order["store_payout_amount"] = adjusted_store_payout

    order["refund_status"] = (order.get("refund_status") or "").upper()
    order["return_status"] = (order.get("return_status") or "").upper()
    order["payment_method"] = (order.get("payment_method") or "").upper()
    order["payment_status"] = (order.get("payment_status") or "").upper()
    order["rider_cash_settlement_status"] = (order.get("rider_cash_settlement_status") or "").upper()
    order["platform_fee_status"] = (order.get("platform_fee_status") or "").upper()
    order["store_payout_status"] = (order.get("store_payout_status") or "").upper()
    order["order_settlement_status"] = (order.get("order_settlement_status") or "").upper()

    return order


def _admin_csv_value(value):
    value = "" if value is None else str(value)
    return '"' + value.replace('"', '""') + '"'


def _admin_csv_response(rows, filename):
    csv_data = "\n".join(
        ",".join(_admin_csv_value(col) for col in row)
        for row in rows
    )

    return Response(
        csv_data,
        mimetype="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename={filename}"
        }
    )


def _admin_is_store_complaint_doc(complaint):
    """
    Single source of truth:
    Store complaint = view-only in Admin panel until Admin takes over.

    Handles both new and old complaint records:
    - assigned_to / target_type
    - old target_kind
    - store_id / store_id_str / real store_name
    - excludes direct Admin complaints saved with store_name = NE FRESH Admin
    """
    complaint = complaint or {}

    admin_takeover_status = str(
        complaint.get("admin_takeover_status") or ""
    ).strip().upper()

    if admin_takeover_status == "TAKEN_OVER":
        return False

    assigned_to = str(complaint.get("assigned_to") or "").strip().lower()
    target_type = str(complaint.get("target_type") or "").strip().lower()
    target_kind = str(complaint.get("target_kind") or "").strip().lower()

    store_name = str(complaint.get("store_name") or "").strip()
    store_name_lower = store_name.lower()

    has_real_store_id = bool(
        complaint.get("store_id")
        or complaint.get("store_id_str")
    )

    has_real_store_name = bool(
        store_name
        and store_name_lower not in [
            "ne fresh admin",
            "ne fresh admin / website owner",
            "admin",
            "website owner"
        ]
    )

    return bool(
        assigned_to == "store"
        or target_type == "store"
        or target_kind == "store"
        or has_real_store_id
        or has_real_store_name
    )


def _admin_prepare_complaint_row(c):
    """
    Normalizes one complaint row for Admin complaints page.
    Keeps direct Admin complaints editable.
    Keeps Store complaints view-only unless taken over.
    """
    c = c or {}

    c["id"] = str(c.get("_id") or c.get("id") or "")
    c["complaint_image_path"] = c.get("complaint_image_path") or c.get("image_path") or ""

    admin_takeover_status = str(
        c.get("admin_takeover_status") or ""
    ).strip().upper()

    is_taken_over = admin_takeover_status == "TAKEN_OVER"
    is_store_complaint = _admin_is_store_complaint_doc(c)

    c["admin_takeover_status"] = admin_takeover_status
    c["is_admin_takeover"] = is_taken_over

    if is_store_complaint:
        c["assigned_to"] = "store"
        c["target_type"] = "store"
        c["assigned_label"] = c.get("store_name") or "Store"
        c["admin_can_update"] = False
    else:
        c["assigned_to"] = "admin"
        c["target_type"] = "admin"
        c["assigned_label"] = "NE FRESH Admin"
        c["admin_can_update"] = True

    status = str(c.get("status") or "open").strip().lower()
    progress_status = str(c.get("progress_status") or "received").strip().lower()

    c["status"] = status
    c["progress_status"] = progress_status
    c["status_label"] = status.replace("_", " ").title()
    c["progress_status_label"] = progress_status.replace("_", " ").title()

    created_at = c.get("created_at") or ""
    updated_at = c.get("updated_at") or ""

    c["created_at_display"] = created_at
    c["updated_at_display"] = updated_at

    try:
        if isinstance(created_at, str) and created_at:
            clean_dt = created_at.replace("Z", "")
            dt_obj = datetime.fromisoformat(clean_dt)
            c["created_at_display"] = dt_obj.strftime("%d %b %Y, %I:%M %p")
    except Exception:
        pass

    try:
        if isinstance(updated_at, str) and updated_at:
            clean_dt = updated_at.replace("Z", "")
            dt_obj = datetime.fromisoformat(clean_dt)
            c["updated_at_display"] = dt_obj.strftime("%d %b %Y, %I:%M %p")
    except Exception:
        pass

    return c

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


ADMIN_DELIVERY_FEE_SETTINGS_KEY = "delivery_fee_settings"


def _admin_clean_delivery_fee_slabs_from_form():
    min_values = request.form.getlist("slab_min_km[]")
    max_values = request.form.getlist("slab_max_km[]")
    fee_values = request.form.getlist("slab_fee[]")

    max_len = max(len(min_values), len(max_values), len(fee_values), 0)
    cleaned = []

    for index in range(max_len):
        min_raw = min_values[index] if index < len(min_values) else ""
        max_raw = max_values[index] if index < len(max_values) else ""
        fee_raw = fee_values[index] if index < len(fee_values) else ""

        if str(min_raw).strip() == "" and str(max_raw).strip() == "" and str(fee_raw).strip() == "":
            continue

        min_km = _admin_float_or_none(min_raw, 0, 999999)
        max_km = _admin_float_or_none(max_raw, 0, 999999)
        fee = _admin_money_or_default(fee_raw, -1)

        if min_km is None:
            min_km = 0.0

        if fee is None or fee < 0:
            continue

        if max_km is not None and max_km <= min_km:
            continue

        cleaned.append({
            "min_km": round(float(min_km), 3),
            "max_km": round(float(max_km), 3) if max_km is not None else None,
            "fee": round(float(fee), 2)
        })

    cleaned.sort(key=lambda row: float(row.get("min_km") or 0))

    return cleaned



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
        # Pay Online on Delivery.
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
                "subtitle": "For Rapido/Ola/Uber-style local delivery. NE FRESH stores only the order reference and charges the hard-coded local fare; rider payment/tracking stays outside NE FRESH.",
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


PAYMENT_GATEWAY_SETTINGS_KEY = "payment_gateway_settings"


def _admin_get_razorpay_env_status(mode="TEST"):
    mode = (mode or "TEST").strip().upper()

    if mode == "LIVE":
        key_id = (os.getenv("RAZORPAY_LIVE_KEY_ID") or "").strip()
        key_secret = (os.getenv("RAZORPAY_LIVE_KEY_SECRET") or "").strip()
    else:
        key_id = (os.getenv("RAZORPAY_TEST_KEY_ID") or "").strip()
        key_secret = (os.getenv("RAZORPAY_TEST_KEY_SECRET") or "").strip()

    return {
        "key_id_configured": bool(key_id),
        "key_secret_configured": bool(key_secret),
        "key_id_masked": (
            key_id[:10] + "..." + key_id[-4:]
            if len(key_id) > 16
            else ("Configured" if key_id else "Not configured")
        ),
    }


def _admin_get_payment_gateway_settings():
    settings = mongo.platform_settings.find_one({
        "key": PAYMENT_GATEWAY_SETTINGS_KEY
    }) or {}

    mode = (settings.get("mode") or "TEST").strip().upper()

    if mode not in ["TEST", "LIVE"]:
        mode = "TEST"

    gateway = (settings.get("gateway") or "RAZORPAY").strip().upper()

    if gateway not in ["RAZORPAY"]:
        gateway = "RAZORPAY"

    env_status = _admin_get_razorpay_env_status(mode)

    return {
        "enabled": bool(settings.get("enabled", False)),
        "gateway": gateway,
        "mode": mode,

        # Keys are now read from .env only.
        "razorpay_key_id": env_status.get("key_id_masked") or "",
        "razorpay_key_id_configured": bool(env_status.get("key_id_configured")),
        "razorpay_key_secret_configured": bool(env_status.get("key_secret_configured")),

        "auto_refund_enabled": bool(settings.get("auto_refund_enabled", False)),
        "auto_capture_enabled": bool(settings.get("auto_capture_enabled", True)),
        "notes": settings.get("notes") or "",
        "updated_at": settings.get("updated_at") or "",
        "updated_by_name": settings.get("updated_by_name") or "",
    }


# =========================================================
# ADMIN - ONLINE PAYMENT / RAZORPAY SETTINGS
# =========================================================

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

RETURN_REFUND_POLICY_SETTINGS_KEY = "return_refund_policy_settings"


def _admin_get_return_refund_policy_settings():
    settings = mongo.platform_settings.find_one({
        "key": RETURN_REFUND_POLICY_SETTINGS_KEY
    }) or {}

    try:
        return_window_hours = int(settings.get("return_window_hours") or 24)
    except Exception:
        return_window_hours = 24

    if return_window_hours < 1:
        return_window_hours = 1

    if return_window_hours > 720:
        return_window_hours = 720

    return {
        "enabled": bool(settings.get("enabled", False)),
        "return_window_hours": return_window_hours,
        "default_refund_items": bool(settings.get("default_refund_items", True)),
        "default_refund_delivery_fee": bool(settings.get("default_refund_delivery_fee", False)),
        "default_refund_platform_fee": bool(settings.get("default_refund_platform_fee", False)),
        "default_refund_tip": bool(settings.get("default_refund_tip", False)),
        "policy_note": settings.get("policy_note") or "",
        "updated_at": settings.get("updated_at") or "",
        "updated_by_name": settings.get("updated_by_name") or "",
    }

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


def _admin_hydrate_return_settlement_order(order):
    """
    Admin read-only helper for return/refund settlement report.

    This does not update database.
    It safely prepares old/new order fields for display.
    """
    row = _admin_hydrate_settlement_order(dict(order or {}))

    status = (row.get("status") or "").strip().upper()
    payment_status = (row.get("payment_status") or "").strip().upper()
    return_status = (row.get("return_status") or row.get("order_return_status") or "").strip().upper()
    refund_status = (row.get("refund_status") or "").strip().upper()

    items_subtotal = _admin_settlement_money(row.get("items_subtotal"), 0)
    delivery_fee = _admin_settlement_money(row.get("delivery_fee"), 0)
    platform_fee = _admin_settlement_money(row.get("platform_fee"), 0)
    tip_amount = _admin_settlement_money(row.get("tip_amount"), row.get("delivery_tip_amount") or 0)
    total_payable = _admin_settlement_money(row.get("total_payable"), items_subtotal + delivery_fee + platform_fee + tip_amount)

    refund_items_amount = _admin_settlement_money(
        row.get("refund_items_amount")
        if row.get("refund_items_amount") is not None
        else row.get("refund_item_amount"),
        0
    )

    refund_delivery_fee = _admin_settlement_money(row.get("refund_delivery_fee"), 0)
    refund_platform_fee = _admin_settlement_money(
        row.get("refund_platform_fee")
        if row.get("refund_platform_fee") is not None
        else row.get("platform_fee_adjustment"),
        0
    )
    refund_tip_amount = _admin_settlement_money(row.get("refund_tip_amount"), 0)

    refund_amount = _admin_settlement_money(row.get("refund_amount"), 0)

    # Backward support for old cancelled paid orders.
    if refund_amount <= 0 and payment_status == "REFUNDED":
        refund_amount = total_payable

        if refund_items_amount <= 0:
            refund_items_amount = items_subtotal

    if not return_status:
        if status in ["RETURNED", "RETURN_REQUESTED", "RETURN_PICKED_UP", "RETURN_COMPLETED"]:
            return_status = status
        elif status == "CANCELLED":
            return_status = "CANCELLED"
        else:
            return_status = "NOT_REQUESTED"

    if not refund_status:
        if payment_status == "REFUNDED":
            refund_status = "PROCESSED"
        elif status == "CANCELLED":
            refund_status = "VOID" if payment_status != "REFUNDED" else "PROCESSED"
        else:
            refund_status = "NOT_STARTED"

    store_payout_amount = _admin_settlement_money(row.get("store_payout_amount"), row.get("store_earning") or items_subtotal)
    store_payout_status = (row.get("store_payout_status") or "").strip().upper()

    # Refund impact on store payout:
    # - before payout paid: reduce pending store payout
    # - after payout paid: store owes adjustment in future payout
    store_refund_deduction = _admin_settlement_money(
        row.get("store_refund_deduction")
        if row.get("store_refund_deduction") is not None
        else row.get("refund_deduction"),
        refund_items_amount
    )

    if store_payout_status == "PAID":
        store_adjustment_due = _admin_settlement_money(
            row.get("store_adjustment_due"),
            store_refund_deduction
        )
        adjusted_store_payout = store_payout_amount
        settlement_impact = "ADJUST_FROM_NEXT_PAYOUT" if store_adjustment_due > 0 else "NO_ADJUSTMENT"
    else:
        store_adjustment_due = _admin_settlement_money(row.get("store_adjustment_due"), 0)
        adjusted_store_payout = round(max(store_payout_amount - store_refund_deduction, 0), 2)
        settlement_impact = "DEDUCT_FROM_PENDING_PAYOUT" if store_refund_deduction > 0 else "NO_DEDUCTION"

    gross_platform_fee = platform_fee
    platform_fee_adjustment = refund_platform_fee
    net_platform_fee = round(max(gross_platform_fee - platform_fee_adjustment, 0), 2)

    row["status"] = status
    row["payment_status"] = payment_status
    row["return_status"] = return_status
    row["refund_status"] = refund_status

    row["items_subtotal"] = items_subtotal
    row["delivery_fee"] = delivery_fee
    row["platform_fee"] = platform_fee
    row["tip_amount"] = tip_amount
    row["total_payable"] = total_payable

    row["refund_amount"] = refund_amount
    row["refund_items_amount"] = refund_items_amount
    row["refund_delivery_fee"] = refund_delivery_fee
    row["refund_platform_fee"] = refund_platform_fee
    row["refund_tip_amount"] = refund_tip_amount

    row["store_payout_amount"] = store_payout_amount
    row["store_refund_deduction"] = store_refund_deduction
    row["adjusted_store_payout"] = adjusted_store_payout
    row["store_adjustment_due"] = store_adjustment_due
    row["settlement_impact"] = settlement_impact

    row["gross_platform_fee"] = gross_platform_fee
    row["platform_fee_adjustment"] = platform_fee_adjustment
    row["net_platform_fee"] = net_platform_fee

    row["refund_method"] = row.get("refund_method") or ""
    row["refund_reference"] = row.get("refund_reference") or ""
    row["refund_note"] = row.get("refund_note") or ""

    row["refund_processed_at"] = row.get("refund_processed_at") or ""
    row["refund_processed_by_name"] = row.get("refund_processed_by_name") or ""

    row["cancelled_at"] = row.get("cancelled_at") or ""
    row["created_at"] = row.get("created_at") or ""
    row["updated_at"] = row.get("updated_at") or ""

    row["order_settlement_status"] = (
        row.get("order_settlement_status")
        or row.get("settlement_status")
        or ""
    ).strip().upper()

    row["settlement_status"] = (
        row.get("settlement_status")
        or row.get("order_settlement_status")
        or ""
    ).strip().upper()

    if status == "CANCELLED":
        row["refund_type"] = "CANCEL_REFUND"
        row["refund_type_label"] = "Cancelled Order Refund"
    elif return_status in ["RETURN_COMPLETED", "STORE_APPROVED", "NEED_ADMIN_REVIEW", "ADMIN_REJECTED"]:
        row["refund_type"] = "RETURN_REFUND"
        row["refund_type_label"] = "Return Refund"
    else:
        row["refund_type"] = "REFUND"
        row["refund_type_label"] = "Refund"

    return row



def _admin_hydrate_refund_processing_order(order):
    """
    Admin action helper for refund processing queue.

    Shows:
    - Online paid cancelled orders waiting for refund
    - Store-approved returns waiting for refund
    - Admin-review return cases
    """
    row = _admin_hydrate_return_settlement_order(dict(order or {}))

    status = (row.get("status") or "").strip().upper()
    return_status = (row.get("return_status") or "").strip().upper()
    refund_status = (row.get("refund_status") or "").strip().upper()
    payment_method = (row.get("payment_method") or "").strip().upper()
    payment_status = (row.get("payment_status") or "").strip().upper()
    admin_review_status = (row.get("admin_return_review_status") or "").strip().upper()
    store_review_status = (row.get("store_return_review_status") or row.get("store_review_status") or "").strip().upper()

    items_subtotal = _admin_settlement_money(row.get("items_subtotal"), 0)
    delivery_fee = _admin_settlement_money(row.get("delivery_fee"), 0)
    platform_fee = _admin_settlement_money(row.get("platform_fee"), 0)
    tip_amount = _admin_settlement_money(row.get("tip_amount"), 0)
    total_payable = _admin_settlement_money(row.get("total_payable"), items_subtotal + delivery_fee + platform_fee + tip_amount)

    refund_items_amount = _admin_settlement_money(row.get("refund_items_amount"), 0)
    refund_delivery_fee = _admin_settlement_money(row.get("refund_delivery_fee"), 0)
    refund_platform_fee = _admin_settlement_money(row.get("refund_platform_fee"), 0)
    refund_tip_amount = _admin_settlement_money(row.get("refund_tip_amount"), 0)

    refund_amount = _admin_settlement_money(
        row.get("refund_amount"),
        refund_items_amount + refund_delivery_fee + refund_platform_fee + refund_tip_amount
    )

    # If online cancellation was created before full breakup existed, use full payable as refund.
    if status == "CANCELLED" and refund_status == "READY_FOR_REFUND" and refund_amount <= 0:
        refund_items_amount = items_subtotal
        refund_delivery_fee = delivery_fee
        refund_platform_fee = platform_fee
        refund_tip_amount = tip_amount
        refund_amount = total_payable

    # Queue type for display/action.
    if status == "CANCELLED" and refund_status == "READY_FOR_REFUND":
        queue_type = "CANCEL_REFUND"
        queue_label = "Cancelled Order Refund"
    elif return_status == "NEED_ADMIN_REVIEW" and admin_review_status == "PENDING":
        queue_type = "ADMIN_REVIEW"
        queue_label = "Needs Admin Review"
    elif return_status == "STORE_APPROVED" or refund_status == "READY_FOR_REFUND":
        queue_type = "RETURN_REFUND"
        queue_label = "Store Approved Return Refund"
    else:
        queue_type = "REFUND"
        queue_label = "Refund"

    row["status"] = status
    row["return_status"] = return_status
    row["refund_status"] = refund_status
    row["payment_method"] = payment_method
    row["payment_status"] = payment_status
    row["admin_return_review_status"] = admin_review_status or "NOT_REQUIRED"
    row["store_return_review_status"] = store_review_status or ""

    row["items_subtotal"] = items_subtotal
    row["delivery_fee"] = delivery_fee
    row["platform_fee"] = platform_fee
    row["tip_amount"] = tip_amount
    row["total_payable"] = total_payable

    row["refund_items_amount"] = refund_items_amount
    row["refund_delivery_fee"] = refund_delivery_fee
    row["refund_platform_fee"] = refund_platform_fee
    row["refund_tip_amount"] = refund_tip_amount
    row["refund_amount"] = refund_amount

    row["queue_type"] = queue_type
    row["queue_label"] = queue_label

    row["return_reason"] = row.get("return_reason") or row.get("refund_reason") or ""
    row["return_note"] = row.get("return_note") or ""
    row["store_return_review_remark"] = row.get("store_return_review_remark") or row.get("store_review_note") or ""
    row["admin_return_review_remark"] = row.get("admin_return_review_remark") or ""

    return row


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

    refund_amount = round(
        refund_items_amount
        + refund_delivery_fee
        + refund_platform_fee
        + refund_tip_amount,
        2
    )

    total_payable = _admin_settlement_money(row.get("total_payable"), 0)

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

    status = (row.get("status") or "").upper()
    store_payout_status = (row.get("store_payout_status") or "").upper()
    store_payout_amount = _admin_settlement_money(row.get("store_payout_amount"), 0)

    is_cancel_refund = status == "CANCELLED"
    store_refund_deduction = 0.0 if is_cancel_refund else refund_items_amount

    if is_cancel_refund:
        adjusted_store_payout = 0.0
        store_adjustment_due = 0.0
        settlement_impact = "CANCEL_REFUND_NO_STORE_PAYOUT"
        next_store_payout_status = "NOT_REQUIRED"
    elif store_payout_status == "PAID":
        adjusted_store_payout = store_payout_amount
        store_adjustment_due = store_refund_deduction
        settlement_impact = "ADJUST_FROM_NEXT_PAYOUT" if store_adjustment_due > 0 else "NO_ADJUSTMENT"
        next_store_payout_status = "PAID"
    else:
        adjusted_store_payout = round(max(store_payout_amount - store_refund_deduction, 0), 2)
        store_adjustment_due = 0.0
        settlement_impact = "DEDUCT_FROM_PENDING_PAYOUT" if store_refund_deduction > 0 else "NO_DEDUCTION"
        next_store_payout_status = "PENDING_AFTER_DELIVERY" if adjusted_store_payout > 0 else "ADJUSTED"

    payment_status_after_refund = "REFUNDED" if refund_amount >= total_payable else "PARTIALLY_REFUNDED"

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
        "platform_fee_status": "REFUNDED" if refund_platform_fee > 0 else row.get("platform_fee_status"),

        "order_settlement_status": "REFUND_PROCESSED",
        "settlement_status": "REFUND_PROCESSED",

        "return_status": "RETURN_COMPLETED" if not is_cancel_refund else "CANCELLED",
        "last_refund_event": refund_event,
        "updated_at": now
    }

    mongo.orders.update_one(
        {"_id": oid_obj},
        {
            "$set": update_data,
            "$push": {
                "refund_audit_logs": refund_event,
                "settlement_audit_logs": refund_event
            }
        }
    )

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
                "platform_fee_adjustment": refund_platform_fee,
                "order_settlement_status": "REFUND_PROCESSED",
                "settlement_status": "REFUND_PROCESSED",
                "updated_at": now
            }
        }
    )

    mongo.order_events.insert_one({
        "order_id": oid_obj,
        "status": "REFUND_PROCESSED",
        "note": f"Refund ₹{refund_amount:.2f} processed by Admin. Reference: {refund_reference or '-'}",
        "created_at": now
    })

    flash("Refund processed successfully.", "success")
    return redirect(url_for("admin_refund_processing"))


def _admin_parse_delivery_zone_polygon(raw):
    try:
        if not raw or not str(raw).strip():
            return []

        data = json.loads(raw)

        if not isinstance(data, list):
            return []

        cleaned = []

        for point in data:
            if not isinstance(point, (list, tuple)) or len(point) != 2:
                continue

            lat = _admin_float_or_none(point[0], -90, 90)
            lng = _admin_float_or_none(point[1], -180, 180)

            if lat is not None and lng is not None:
                cleaned.append([lat, lng])

        if len(cleaned) < 3:
            return []

        return cleaned
    except Exception:
        return []


# =========================================================
# ADMIN - PLATFORM FEE SETTINGS
# =========================================================

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


@app.route("/admin/settlements", methods=["GET"], endpoint="admin_settlements")
@login_required(role="admin")
def admin_settlements():
    rider_cash_orders_raw = list(
        mongo.orders.find({
            "status": "DELIVERED",
            "payment_method": "COD",
            "payment_status": {"$in": ["COLLECTED_BY_RIDER", "COD_COLLECTED_BY_RIDER"]},
            "rider_cash_settlement_status": {"$in": ["PENDING", "RIDER_CASH_PENDING"]}
        }).sort("delivered_at", -1)
    )

    store_payout_orders_raw = list(
        mongo.orders.find({
            "status": "DELIVERED",
            "store_payout_status": {"$in": ["PENDING_AFTER_DELIVERY", "PENDING", "PAYOUT_PENDING"]},
            "$or": [
                {"payment_method": {"$ne": "COD"}},
                {"rider_cash_settlement_status": "RECEIVED"}
            ]
        }).sort("delivered_at", -1)
    )

    online_paid_orders_raw = list(
        mongo.orders.find({
            "payment_method": {
                "$in": ["ONLINE", "ONLINE_PAYMENT", "RAZORPAY"]
            },
            "payment_status": {
                "$in": ["PAID", "ONLINE_PAID", "SUCCESS"]
            }
        }).sort("payment_collected_at", -1)
    )

    cod_collected_orders_raw = list(
        mongo.orders.find({
            "payment_method": "COD",
            "payment_status": {
                "$in": ["COLLECTED_BY_RIDER", "COD_COLLECTED_BY_RIDER"]
            }
        }).sort("delivered_at", -1)
    )

    platform_fee_received_orders_raw = list(
        mongo.orders.find({
            "platform_fee_status": "RECEIVED"
        }).sort("platform_fee_received_at", -1)
    )

    rider_cash_orders = [
        _admin_hydrate_settlement_order(o)
        for o in rider_cash_orders_raw
    ]

    store_payout_orders = [
        _admin_hydrate_settlement_order(o)
        for o in store_payout_orders_raw
    ]

    online_paid_orders = [
        _admin_hydrate_settlement_order(o)
        for o in online_paid_orders_raw
    ]

    cod_collected_orders = [
        _admin_hydrate_settlement_order(o)
        for o in cod_collected_orders_raw
    ]

    platform_fee_received_orders = [
        _admin_hydrate_settlement_order(o)
        for o in platform_fee_received_orders_raw
    ]

    metrics = {

        "online_payment_received_count": len(online_paid_orders),
        "online_payment_received_amount": round(
            sum(float(o.get("total_payable") or o.get("total_amount") or 0) for o in online_paid_orders),
            2
        ),

        "cod_collected_by_rider_count": len(cod_collected_orders),
        "cod_collected_by_rider_amount": round(
            sum(float(o.get("cod_collected_amount") or o.get("total_payable") or 0) for o in cod_collected_orders),
            2
        ),

        "platform_fee_received_total_amount": round(
            sum(float(o.get("platform_fee") or 0) for o in platform_fee_received_orders),
            2
        ),
        
        "rider_cash_pending_count": len(rider_cash_orders),
        "rider_cash_pending_amount": round(
            sum(float(o.get("rider_cash_to_submit") or 0) for o in rider_cash_orders),
            2
        ),

        "store_payout_pending_count": len(store_payout_orders),

        # Original earning before refund deduction.
        "store_payout_original_amount": round(
            sum(float(o.get("original_store_payout_amount") or 0) for o in store_payout_orders),
            2
        ),

        # Actual payable amount Admin should pay now.
        "store_payout_pending_amount": round(
            sum(float(o.get("adjusted_store_payout") or o.get("store_payout_amount") or 0) for o in store_payout_orders),
            2
        ),

        "store_refund_deduction_amount": round(
            sum(float(o.get("store_refund_deduction") or 0) for o in store_payout_orders),
            2
        ),

        "store_adjustment_due_amount": round(
            sum(float(o.get("store_adjustment_due") or 0) for o in store_payout_orders),
            2
        ),

        "platform_fee_pending_amount": round(
            sum(float(o.get("platform_fee") or 0) for o in rider_cash_orders),
            2
        ),

        "platform_fee_received_amount": round(
            sum(float(o.get("platform_fee") or 0) for o in store_payout_orders),
            2
        ),
    }

    return render_template(
        "admin_settlements.html",
        user=current_user(),
        rider_cash_orders=rider_cash_orders,
        store_payout_orders=store_payout_orders,
        metrics=metrics,
        active_group="settlements",
        active_page="settlements"
    )



@app.route("/admin/settlements/export.csv", methods=["GET"], endpoint="admin_settlements_export_csv")
@login_required(role="admin")
def admin_settlements_export_csv():
    rider_cash_orders_raw = list(
        mongo.orders.find({
            "status": "DELIVERED",
            "payment_method": "COD",
            "payment_status": {"$in": ["COLLECTED_BY_RIDER", "COD_COLLECTED_BY_RIDER"]},
            "rider_cash_settlement_status": {"$in": ["PENDING", "RIDER_CASH_PENDING"]}
        }).sort("delivered_at", -1)
    )

    store_payout_orders_raw = list(
        mongo.orders.find({
            "status": "DELIVERED",
            "store_payout_status": {"$in": ["PENDING_AFTER_DELIVERY", "PENDING", "PAYOUT_PENDING"]},
            "$or": [
                {"payment_method": {"$ne": "COD"}},
                {"rider_cash_settlement_status": "RECEIVED"}
            ]
        }).sort("delivered_at", -1)
    )

    rows = [[
        "Section",
        "Order ID",
        "Store Name",
        "Customer Name",
        "Customer Phone",
        "Delivery Boy",
        "Payment Method",
        "Payment Status",

        "Items Subtotal",
        "COD Collected",
        "Delivery Boy Earning",
        "Rider Cash To Submit",
        "Platform Fee",

        "Original Store Payout",
        "Store Refund Deduction",
        "Adjusted Store Payout",
        "Store Adjustment Due",
        "Settlement Impact",

        "Refund Status",
        "Refund Amount",
        "Refund Items Amount",
        "Refund Delivery Fee",
        "Refund Platform Fee",
        "Refund Tip Amount",
        "Refund Method",
        "Refund Reference",
        "Refund Processed At",

        "Return Status",
        "Store Return Review Status",
        "Admin Return Review Status",

        "Rider Cash Status",
        "Platform Fee Status",
        "Store Payout Status",
        "Order Settlement Status",
        "Delivered At",
        "Updated At"
    ]]

    def _append_settlement_csv_row(section, order):
        o = _admin_hydrate_settlement_order(dict(order))

        rows.append([
            section,
            o.get("id"),
            o.get("store_name"),
            o.get("customer_name"),
            o.get("customer_phone"),
            o.get("delivery_partner_name"),
            o.get("payment_method"),
            o.get("payment_status"),

            o.get("items_subtotal"),
            o.get("cod_collected_amount"),
            o.get("delivery_boy_earning"),
            o.get("rider_cash_to_submit"),
            o.get("platform_fee"),

            o.get("original_store_payout_amount"),
            o.get("store_refund_deduction"),
            o.get("adjusted_store_payout"),
            o.get("store_adjustment_due"),
            o.get("settlement_impact"),

            o.get("refund_status"),
            _admin_settlement_money(o.get("refund_amount"), 0),
            _admin_settlement_money(o.get("refund_items_amount"), 0),
            _admin_settlement_money(o.get("refund_delivery_fee"), 0),
            _admin_settlement_money(o.get("refund_platform_fee"), 0),
            _admin_settlement_money(o.get("refund_tip_amount"), 0),
            o.get("refund_method") or "",
            o.get("refund_reference") or "",
            o.get("refund_processed_at") or "",

            o.get("return_status"),
            (o.get("store_return_review_status") or o.get("store_review_status") or ""),
            (o.get("admin_return_review_status") or o.get("admin_decision") or ""),

            o.get("rider_cash_settlement_status"),
            o.get("platform_fee_status"),
            o.get("store_payout_status"),
            o.get("order_settlement_status"),
            o.get("delivered_at"),
            o.get("updated_at")
        ])

    for order in rider_cash_orders_raw:
        _append_settlement_csv_row("Rider COD Cash Pending", order)

    for order in store_payout_orders_raw:
        _append_settlement_csv_row("Store Payout Pending", order)

    return _admin_csv_response(rows, "nefresh_payment_settlements.csv")


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

    if order.get("payment_status") not in ["COLLECTED_BY_RIDER", "COD_COLLECTED_BY_RIDER"]:
        flash("COD has not been marked collected by rider for this order.", "warning")
        return redirect(url_for("admin_settlements"))

    if order.get("rider_cash_settlement_status") == "RECEIVED":
        flash("Rider cash is already received for this order.", "info")
        return redirect(url_for("admin_settlements"))

    now = datetime.utcnow().isoformat()
    note = (request.form.get("note") or "").strip()

    rider_cash_to_submit = _admin_settlement_money(order.get("rider_cash_to_submit"))
    platform_fee = _admin_settlement_money(order.get("platform_fee"))
    store_payout_amount = _admin_settlement_money(order.get("store_payout_amount"))

    settlement_event = {
        "action": "RIDER_CASH_RECEIVED_BY_ADMIN",
        "order_id": str(oid_obj),
        "amount_received": rider_cash_to_submit,
        "platform_fee": platform_fee,
        "store_payout_amount": store_payout_amount,
        "created_by": str(admin_user.get("id") or admin_user.get("_id") or ""),
        "created_by_name": admin_user.get("name") or admin_user.get("email") or "Admin",
        "created_by_role": "admin",
        "note": note,
        "created_at": now
    }

    update_data = {
        "rider_cash_settlement_status": "RECEIVED",
        "rider_cash_received_at": now,
        "rider_cash_received_by": str(admin_user.get("id") or admin_user.get("_id") or ""),
        "rider_cash_received_by_name": admin_user.get("name") or admin_user.get("email") or "Admin",
        "rider_cash_settlement_note": note,

        "platform_fee_status": "RECEIVED",
        "platform_fee_received_at": now,
        "admin_platform_fee_status": "RECEIVED",

        "store_payout_status": "PENDING_AFTER_DELIVERY",
        "store_settlement_status": "PAYOUT_PENDING",

        "order_settlement_status": "STORE_PAYOUT_PENDING",
        "settlement_status": "STORE_PAYOUT_PENDING",

        "last_settlement_event": settlement_event,
        "updated_at": now
    }

    mongo.orders.update_one(
        {"_id": oid_obj},
        {
            "$set": update_data,
            "$push": {
                "settlement_audit_logs": settlement_event
            }
        }
    )

    mongo.transactions.update_many(
        {"order_id": oid_obj},
        {
            "$set": {
                "rider_cash_settlement_status": "RECEIVED",
                "rider_cash_received_at": now,
                "rider_cash_received_by": str(admin_user.get("id") or admin_user.get("_id") or ""),
                "rider_cash_settlement_note": note,
                "platform_fee_status": "RECEIVED",
                "platform_fee_received_at": now,
                "admin_platform_fee_status": "RECEIVED",
                "store_payout_status": "PENDING_AFTER_DELIVERY",
                "store_settlement_status": "PAYOUT_PENDING",
                "order_settlement_status": "STORE_PAYOUT_PENDING",
                "settlement_status": "STORE_PAYOUT_PENDING",
                "updated_at": now
            }
        }
    )

    mongo.order_events.insert_one({
        "order_id": oid_obj,
        "status": "RIDER_CASH_RECEIVED_BY_ADMIN",
        "note": (
            f"Admin received rider cash ₹{rider_cash_to_submit:.2f}. "
            f"Platform fee ₹{platform_fee:.2f} marked received. "
            f"Store payout ₹{store_payout_amount:.2f} is pending."
        ),
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

    order = mongo.orders.find_one({"_id": oid_obj})

    if not order:
        flash("Order not found.", "danger")
        return redirect(url_for("admin_settlements"))

    order = _admin_hydrate_settlement_order(order)

    if order.get("status") != "DELIVERED":
        flash("Store payout can be marked only after delivery.", "warning")
        return redirect(url_for("admin_settlements"))

    if order.get("payment_method") == "COD" and order.get("rider_cash_settlement_status") != "RECEIVED":
        flash("Cannot pay store before Admin receives rider COD cash.", "warning")
        return redirect(url_for("admin_settlements"))

    if order.get("store_payout_status") == "PAID":
        flash("Store payout is already marked paid.", "info")
        return redirect(url_for("admin_settlements"))

    now = datetime.utcnow().isoformat()
    note = (request.form.get("note") or "").strip()
    reference_no = (request.form.get("reference_no") or "").strip()
    payout_mode = (request.form.get("payout_mode") or "CASH").strip().upper()

    original_store_payout_amount = _admin_settlement_money(
        order.get("original_store_payout_amount"),
        order.get("store_earning") or order.get("items_subtotal") or 0
    )

    store_refund_deduction = _admin_settlement_money(
        order.get("store_refund_deduction")
        if order.get("store_refund_deduction") is not None
        else order.get("refund_deduction"),
        0
    )

    adjusted_store_payout = _admin_settlement_money(
        order.get("adjusted_store_payout"),
        order.get("store_payout_amount") or original_store_payout_amount
    )

    store_adjustment_due = _admin_settlement_money(
        order.get("store_adjustment_due"),
        0
    )

    settlement_impact = order.get("settlement_impact") or (
        "DEDUCT_FROM_PENDING_PAYOUT" if store_refund_deduction > 0 else "NO_DEDUCTION"
    )

    # Final amount Admin is paying now.
    store_payout_amount = adjusted_store_payout

    platform_fee = _admin_settlement_money(order.get("platform_fee"))

    settlement_event = {
        "action": "STORE_PAYOUT_PAID_BY_ADMIN",
        "order_id": str(oid_obj),
        "store_id": str(order.get("store_id") or ""),
        "store_name": order.get("store_name") or "",
        "amount_paid": store_payout_amount,
        "original_store_payout_amount": original_store_payout_amount,
        "store_refund_deduction": store_refund_deduction,
        "adjusted_store_payout": adjusted_store_payout,
        "store_adjustment_due": store_adjustment_due,
        "settlement_impact": settlement_impact,
        "platform_fee": platform_fee,
        "payment_mode": payout_mode,
        "reference_no": reference_no,
        "created_by": str(admin_user.get("id") or admin_user.get("_id") or ""),
        "created_by_name": admin_user.get("name") or admin_user.get("email") or "Admin",
        "created_by_role": "admin",
        "note": note,
        "created_at": now
    }

    update_data = {
        "store_payout_status": "PAID",
        "store_payout_paid_at": now,
        "store_payout_marked_by": str(admin_user.get("id") or admin_user.get("_id") or ""),
        "store_payout_marked_by_name": admin_user.get("name") or admin_user.get("email") or "Admin",
        "store_payout_note": note,
        "store_payout_reference_no": reference_no,
        "store_payout_mode": payout_mode,

        "original_store_payout_amount": original_store_payout_amount,
        "store_refund_deduction": store_refund_deduction,
        "refund_deduction": store_refund_deduction,
        "adjusted_store_payout": adjusted_store_payout,
        "store_payout_amount": adjusted_store_payout,
        "store_payout_paid_amount": store_payout_amount,
        "store_adjustment_due": store_adjustment_due,
        "settlement_impact": settlement_impact,

        "store_settlement_status": "PAID",
        "settlement_status": "SETTLED",
        "order_settlement_status": "SETTLED",

        "last_settlement_event": settlement_event,
        "updated_at": now
    }

    mongo.orders.update_one(
        {"_id": oid_obj},
        {
            "$set": update_data,
            "$push": {
                "settlement_audit_logs": settlement_event
            }
        }
    )

    mongo.transactions.update_many(
        {"order_id": oid_obj},
        {
            "$set": {
                "store_payout_status": "PAID",
                "store_payout_paid_at": now,
                "store_payout_marked_by": str(admin_user.get("id") or admin_user.get("_id") or ""),
                "store_payout_note": note,
                "store_payout_reference_no": reference_no,
                "store_payout_mode": payout_mode,

                "original_store_payout_amount": original_store_payout_amount,
                "store_refund_deduction": store_refund_deduction,
                "refund_deduction": store_refund_deduction,
                "adjusted_store_payout": adjusted_store_payout,
                "store_payout_amount": adjusted_store_payout,
                "store_payout_paid_amount": store_payout_amount,
                "store_adjustment_due": store_adjustment_due,
                "settlement_impact": settlement_impact,

                "store_settlement_status": "PAID",
                "settlement_status": "SETTLED",
                "order_settlement_status": "SETTLED",
                "updated_at": now
            }
        }
    )

    mongo.order_events.insert_one({
        "order_id": oid_obj,
        "status": "STORE_PAYOUT_PAID",
        "note": (
            f"Store payout ₹{store_payout_amount:.2f} marked paid by Admin. "
            f"Original payout ₹{original_store_payout_amount:.2f}, "
            f"refund deduction ₹{store_refund_deduction:.2f}. "
            f"Settlement completed."
        ),
        "created_at": now
    })

    flash("Store payout marked paid. Order settlement completed.", "success")
    return redirect(url_for("admin_settlements"))



@app.route("/admin/platform-earnings", methods=["GET"], endpoint="admin_platform_earnings")
@login_required(role="admin")
def admin_platform_earnings():
    """
    Admin read-only platform earnings report.

    Rule:
    - ONLINE_PLATFORM / online-paid orders: platform fee is received after successful payment.
    - COD_RIDER_COLLECTION orders: platform fee is received only after rider cash settlement is RECEIVED.
    """
    q = (request.args.get("q") or "").strip()
    status_filter = (request.args.get("status") or "").strip().upper()
    payment_filter = (request.args.get("payment") or "").strip().upper()
    date_from = (request.args.get("from") or "").strip()
    date_to = (request.args.get("to") or "").strip()

    raw_orders = list(
        mongo.orders.find({
            "platform_fee": {
                "$exists": True
            }
        }).sort("created_at", -1)
    )

    rows = []

    for order in raw_orders:
        row = _admin_hydrate_settlement_order(dict(order))

        platform_fee = _admin_settlement_money(row.get("platform_fee"), 0)

        if platform_fee <= 0:
            continue

        payment_method = (row.get("payment_method") or "").strip().upper()
        payment_status = (row.get("payment_status") or "").strip().upper()
        platform_fee_status = (row.get("platform_fee_status") or "").strip().upper()
        rider_cash_status = (row.get("rider_cash_settlement_status") or "").strip().upper()

        created_at = str(row.get("created_at") or "")
        delivered_at = str(row.get("delivered_at") or row.get("updated_at") or created_at)

        report_date = delivered_at or created_at

        if date_from and report_date and report_date[:10] < date_from:
            continue

        if date_to and report_date and report_date[:10] > date_to:
            continue

        # Final platform earning status for report.
        if platform_fee_status == "RECEIVED":
            earning_status = "RECEIVED"
        elif payment_method == "COD":
            if rider_cash_status == "RECEIVED":
                earning_status = "RECEIVED"
            else:
                earning_status = "PENDING_RIDER_CASH"
        elif payment_status in ["PAID", "ONLINE_PAID", "SUCCESS"]:
            earning_status = "RECEIVED"
        else:
            earning_status = "PENDING_PAYMENT"

        row["platform_earning_status"] = earning_status
        row["platform_fee"] = platform_fee
        row["report_date"] = report_date
        row["payment_method"] = payment_method or "UNKNOWN"
        row["payment_status"] = payment_status or "UNKNOWN"
        row["platform_fee_status"] = platform_fee_status or earning_status
        row["rider_cash_settlement_status"] = rider_cash_status or "NOT_REQUIRED"

        if payment_filter:
            if payment_filter == "ONLINE":
                if payment_method in ["COD", "COD_RIDER_COLLECTION"]:
                    continue
            elif payment_filter == "COD":
                if payment_method != "COD":
                    continue
            elif payment_filter != payment_method:
                continue

        if status_filter:
            if status_filter != earning_status and status_filter != platform_fee_status:
                continue

        if q:
            haystack = " ".join([
                str(row.get("id") or ""),
                str(row.get("store_name") or ""),
                str(row.get("customer_name") or ""),
                str(row.get("customer_phone") or ""),
                str(row.get("payment_method") or ""),
                str(row.get("payment_status") or ""),
                str(row.get("platform_fee_status") or ""),
                str(row.get("platform_earning_status") or ""),
                str(row.get("order_settlement_status") or "")
            ]).lower()

            if q.lower() not in haystack:
                continue

        rows.append(row)

    received_rows = [
        r for r in rows
        if (r.get("platform_earning_status") or "").upper() == "RECEIVED"
    ]

    pending_rows = [
        r for r in rows
        if (r.get("platform_earning_status") or "").upper() != "RECEIVED"
    ]

    cod_rows = [
        r for r in rows
        if (r.get("payment_method") or "").upper() == "COD"
    ]

    online_rows = [
        r for r in rows
        if (r.get("payment_method") or "").upper() != "COD"
    ]

    metrics = {
        "total_records": len(rows),
        "total_platform_fee": round(sum(float(r.get("platform_fee") or 0) for r in rows), 2),

        "received_count": len(received_rows),
        "received_amount": round(sum(float(r.get("platform_fee") or 0) for r in received_rows), 2),

        "pending_count": len(pending_rows),
        "pending_amount": round(sum(float(r.get("platform_fee") or 0) for r in pending_rows), 2),

        "cod_count": len(cod_rows),
        "cod_amount": round(sum(float(r.get("platform_fee") or 0) for r in cod_rows), 2),

        "online_count": len(online_rows),
        "online_amount": round(sum(float(r.get("platform_fee") or 0) for r in online_rows), 2),
    }

    return render_template(
        "admin_platform_earnings.html",
        user=current_user(),
        earnings=rows,
        metrics=metrics,
        q=q,
        status_filter=status_filter,
        payment_filter=payment_filter,
        date_from=date_from,
        date_to=date_to,
        active_group="settlements",
        active_page="platform_earnings"
    )



@app.route("/admin/platform-earnings/export.csv", methods=["GET"], endpoint="admin_platform_earnings_export_csv")
@login_required(role="admin")
def admin_platform_earnings_export_csv():
    q = (request.args.get("q") or "").strip()
    status_filter = (request.args.get("status") or "").strip().upper()
    payment_filter = (request.args.get("payment") or "").strip().upper()
    date_from = (request.args.get("from") or "").strip()
    date_to = (request.args.get("to") or "").strip()

    raw_orders = list(
        mongo.orders.find({
            "platform_fee": {
                "$exists": True
            }
        }).sort("created_at", -1)
    )

    rows = [[
        "Order ID",
        "Store Name",
        "Customer Name",
        "Customer Phone",
        "Payment Method",
        "Payment Status",

        "Items Subtotal",
        "Delivery Fee",
        "Tip",
        "Total Payable",
        "Store Earning",

        "Platform Fee",
        "Refund Platform Fee",
        "Net Platform Fee",
        "Platform Earning Status",
        "Platform Fee Status",

        "Refund Status",
        "Refund Amount",
        "Refund Method",
        "Refund Reference",
        "Refund Processed At",

        "Return Status",
        "Store Return Review Status",
        "Admin Return Review Status",

        "Rider Cash Status",
        "Rider Cash To Submit",
        "Store Payout Status",
        "Store Refund Deduction",
        "Adjusted Store Payout",
        "Store Adjustment Due",
        "Settlement Impact",
        "Order Settlement Status",

        "Report Date",
        "Created At"
    ]]

    for order in raw_orders:
        row = _admin_hydrate_settlement_order(dict(order))

        platform_fee = _admin_settlement_money(row.get("platform_fee"), 0)

        if platform_fee <= 0:
            continue

        payment_method = (row.get("payment_method") or "").strip().upper()
        payment_status = (row.get("payment_status") or "").strip().upper()
        platform_fee_status = (row.get("platform_fee_status") or "").strip().upper()
        rider_cash_status = (row.get("rider_cash_settlement_status") or "").strip().upper()

        created_at = str(row.get("created_at") or "")
        delivered_at = str(row.get("delivered_at") or row.get("updated_at") or created_at)
        report_date = delivered_at or created_at

        if date_from and report_date and report_date[:10] < date_from:
            continue

        if date_to and report_date and report_date[:10] > date_to:
            continue

        refund_platform_fee = _admin_settlement_money(
            row.get("refund_platform_fee")
            if row.get("refund_platform_fee") is not None
            else row.get("platform_fee_adjustment"),
            0
        )

        net_platform_fee = round(max(platform_fee - refund_platform_fee, 0), 2)

        if platform_fee_status == "RECEIVED":
            earning_status = "RECEIVED"
        elif platform_fee_status == "REFUNDED":
            earning_status = "REFUNDED"
        elif payment_method == "COD":
            if rider_cash_status == "RECEIVED":
                earning_status = "RECEIVED"
            else:
                earning_status = "PENDING_RIDER_CASH"
        elif payment_status in ["PAID", "ONLINE_PAID", "SUCCESS"]:
            earning_status = "RECEIVED"
        elif payment_status in ["REFUNDED", "PARTIALLY_REFUNDED"]:
            earning_status = "REFUNDED"
        else:
            earning_status = "PENDING_PAYMENT"

        if payment_filter:
            if payment_filter == "ONLINE":
                if payment_method in ["COD", "COD_RIDER_COLLECTION", "CASH_ON_DELIVERY"]:
                    continue
            elif payment_filter == "COD":
                if payment_method not in ["COD", "COD_RIDER_COLLECTION", "CASH_ON_DELIVERY"]:
                    continue
            elif payment_filter != payment_method:
                continue

        if status_filter:
            if status_filter != earning_status and status_filter != platform_fee_status:
                continue

        if q:
            haystack = " ".join([
                str(row.get("id") or ""),
                str(row.get("store_name") or ""),
                str(row.get("customer_name") or ""),
                str(row.get("customer_phone") or ""),
                str(payment_method or ""),
                str(payment_status or ""),
                str(platform_fee_status or ""),
                str(earning_status or ""),
                str(row.get("refund_status") or ""),
                str(row.get("refund_reference") or ""),
                str(row.get("return_status") or ""),
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
            payment_method or "UNKNOWN",
            payment_status or "UNKNOWN",

            row.get("items_subtotal"),
            row.get("delivery_fee"),
            row.get("tip_amount"),
            row.get("total_payable"),
            row.get("store_earning"),

            platform_fee,
            refund_platform_fee,
            net_platform_fee,
            earning_status,
            platform_fee_status or earning_status,

            row.get("refund_status") or "",
            _admin_settlement_money(row.get("refund_amount"), 0),
            row.get("refund_method") or "",
            row.get("refund_reference") or "",
            row.get("refund_processed_at") or "",

            row.get("return_status") or "",
            row.get("store_return_review_status") or row.get("store_review_status") or "",
            row.get("admin_return_review_status") or row.get("admin_decision") or "",

            rider_cash_status or "NOT_REQUIRED",
            row.get("rider_cash_to_submit"),
            row.get("store_payout_status"),
            row.get("store_refund_deduction"),
            row.get("adjusted_store_payout"),
            row.get("store_adjustment_due"),
            row.get("settlement_impact"),
            row.get("order_settlement_status"),

            report_date,
            row.get("created_at")
        ])

    return _admin_csv_response(rows, "nefresh_platform_earnings.csv")


@app.route("/admin/settlement-audit-logs", methods=["GET"], endpoint="admin_settlement_audit_logs")
@login_required(role="admin")
def admin_settlement_audit_logs():
    """
    Admin read-only settlement audit log page.

    Shows settlement_audit_logs pushed inside orders during:
    - RIDER_CASH_RECEIVED_BY_ADMIN
    - STORE_PAYOUT_PAID_BY_ADMIN
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

            amount_received = _admin_settlement_money(entry.get("amount_received"), 0)
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
                "action_label": action.replace("_", " ").title() if action else "Settlement Event",

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

                "payment_mode": entry.get("payment_mode") or entry.get("refund_method") or "",
                "reference_no": entry.get("reference_no") or entry.get("refund_reference") or "",
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

    rider_cash_logs = [
        row for row in logs
        if row.get("action") == "RIDER_CASH_RECEIVED_BY_ADMIN"
    ]

    store_payout_logs = [
        row for row in logs
        if row.get("action") == "STORE_PAYOUT_PAID_BY_ADMIN"
    ]

    refund_logs = [
        row for row in logs
        if row.get("action") == "REFUND_PROCESSED_BY_ADMIN"
    ]

    metrics = {
        "total_logs": len(logs),
        "rider_cash_logs": len(rider_cash_logs),
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

            amount_received = _admin_settlement_money(entry.get("amount_received"), 0)
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
                "action_label": action.replace("_", " ").title() if action else "Settlement Event",
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

                "payment_mode": entry.get("payment_mode") or entry.get("refund_method") or "",
                "reference_no": entry.get("reference_no") or entry.get("refund_reference") or "",
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
            log.get("rider_cash_settlement_status"),
            log.get("platform_fee_status"),
            log.get("store_payout_status"),
            log.get("order_settlement_status")
        ])

    return _admin_csv_response(rows, "nefresh_settlement_audit_logs.csv")


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

@app.route("/admin/dashboard")
@login_required(role="admin")
def admin_dashboard():
    # -------------------------
    # Load source collections once
    # -------------------------
    orders = list(mongo.orders.find({}))
    transactions = list(mongo.transactions.find({}))

    # -------------------------
    # Role-based user counts
    # -------------------------
    users_total = mongo.users.count_documents({})
    customers_total = mongo.users.count_documents({
        "role": {"$regex": "^customer$", "$options": "i"}
    })
    delivery_people_total = mongo.users.count_documents({
        "role": {"$regex": "^delivery$", "$options": "i"}
    })
    active_delivery_people_total = mongo.users.count_documents({
        "role": {"$regex": "^delivery$", "$options": "i"},
        "is_active": 1
    })

    stores_total = mongo.stores.count_documents({})
    products_total = mongo.products.count_documents({})
    orders_total = mongo.orders.count_documents({})

    # -------------------------
    # Normalize order status buckets
    # -------------------------
    status_counts = defaultdict(int)
    for order in orders:
        status_counts[_norm_status(order.get("status"))] += 1

    # Support mixed spellings already present in legacy data
    cancelled_orders_total = status_counts["CANCELLED"] + status_counts["CANCELED"]
    delivered_orders_total = status_counts["DELIVERED"]
    out_for_delivery_total = status_counts["OUT_FOR_DELIVERY"]
    assigned_orders_total = status_counts["ASSIGNED_TO_DELIVERY"] + status_counts["ACCEPTED_BY_DELIVERY_MAN"]
    preparing_orders_total = status_counts["PREPARING"] + status_counts["PACKAGING"]
    placed_orders_total = status_counts["PLACED"] + status_counts["CONFIRMED"]
    unassigned_orders_total = placed_orders_total

    # -------------------------
    # Payment / transaction buckets
    # -------------------------
    txn_status_counts = defaultdict(int)
    for txn in transactions:
        txn_status_counts[_norm_status(txn.get("status"))] += 1

    refunded_orders_total = txn_status_counts["REFUNDED"]
    failed_payments_total = txn_status_counts["FAILED"] + txn_status_counts["PAYMENT_FAILED"]
    pending_payments_total = txn_status_counts["PENDING"]
    paid_txn_total = txn_status_counts["PAID"]

    # -------------------------
    # GMV / earnings
    # -------------------------
    gmv = 0.0
    delivered_order_docs = []
    for order in orders:
        if _norm_status(order.get("status")) == "DELIVERED":
            delivered_order_docs.append(order)
            gmv += _order_total(order)

    total_earnings_from_paid_txn = sum(float(t.get("amount") or 0) for t in transactions if _norm_status(t.get("status")) == "PAID")
    total_earnings = total_earnings_from_paid_txn if total_earnings_from_paid_txn > 0 else gmv


        # -------------------------
    # Refund / return dashboard KPIs
    # -------------------------
    refund_pending_docs = []
    admin_review_needed_docs = []
    refund_processed_docs = []

    ready_for_refund_amount = 0.0
    refund_processed_amount = 0.0
    store_refund_deduction_amount = 0.0
    store_adjustment_due_amount = 0.0

    for order in orders:
        refund_status = _norm_status(order.get("refund_status"))
        return_status = _norm_status(order.get("return_status"))
        admin_review_status = _norm_status(order.get("admin_return_review_status"))

        refund_amount = float(order.get("refund_amount") or 0)
        store_refund_deduction = float(
            order.get("store_refund_deduction")
            if order.get("store_refund_deduction") is not None
            else order.get("refund_deduction")
            or 0
        )
        store_adjustment_due = float(order.get("store_adjustment_due") or 0)

        if refund_status in ["READY_FOR_REFUND", "PENDING"]:
            refund_pending_docs.append(order)
            ready_for_refund_amount += refund_amount

        if return_status == "NEED_ADMIN_REVIEW" and admin_review_status == "PENDING":
            admin_review_needed_docs.append(order)

        if refund_status in ["PROCESSED", "ADJUSTED"]:
            refund_processed_docs.append(order)
            refund_processed_amount += refund_amount

        store_refund_deduction_amount += store_refund_deduction
        store_adjustment_due_amount += store_adjustment_due

    refund_pending_count = len(refund_pending_docs)
    admin_review_needed_count = len(admin_review_needed_docs)
    refund_processed_count = len(refund_processed_docs)

    # -------------------------
    # Stores performance (revenue-first)
    # -------------------------
    by_store = []
    for store in mongo.stores.find({}).sort("store_name", 1):
        sid = store["_id"]
        store_orders = [o for o in orders if str(o.get("store_id")) == str(sid)]

        order_count = len(store_orders)
        revenue = 0.0
        delivered_count = 0

        for o in store_orders:
            if _norm_status(o.get("status")) == "DELIVERED":
                delivered_count += 1
                revenue += _order_total(o)

        by_store.append({
            "store_id": str(sid),
            "store_name": store.get("store_name", "") or "",
            "orders": order_count,
            "delivered_orders": delivered_count,
            "revenue": round(revenue, 2),
            "image_url": store.get("image_url") or store.get("logo") or "",
        })

    by_store.sort(key=lambda x: (x["revenue"], x["orders"]), reverse=True)

    # -------------------------
    # Rankings & summaries
    # -------------------------
    top_store_complaints = _top_store_complaints(limit=5)
    top_delivery_complaints = _top_delivery_complaints(limit=5)

    top_rated_stores = _rating_summary(
        collection_name="store_ratings",
        target_field="store_id",
        lookup_collection="stores",
        lookup_name_field="store_name",
        image_fields=["image_url", "logo"],
        limit=6,
    )

    top_rated_products = _rating_summary(
        collection_name="product_ratings",
        target_field="product_id",
        lookup_collection="products",
        lookup_name_field="name",
        image_fields=["image_path", "image_url"],
        limit=6,
    )

    top_rated_deliverymen = _rating_summary(
        collection_name="delivery_ratings",
        target_field="delivery_partner_id",
        lookup_collection="users",
        lookup_name_field="name",
        image_fields=[],
        limit=6,
    )

    top_selling_items = _top_selling_items(limit=6)
    most_popular_stores = _store_rankings_by_orders(limit=6)
    top_selling_store_tiles = _store_rankings_by_revenue(limit=6)
    top_customers = _top_customers(limit=6)
    top_deliverymen = _top_deliverymen(limit=6)

    # -------------------------
    # Chart data
    # -------------------------
    sales_labels, sales_values = _dashboard_monthly_sales()


    # -------------------------
    # Payment / settlement dashboard metrics
    # -------------------------
    online_paid_orders = list(
        mongo.orders.find({
            "payment_method": {
                "$in": ["ONLINE", "ONLINE_PAYMENT", "RAZORPAY"]
            },
            "payment_status": {
                "$in": ["PAID", "ONLINE_PAID", "SUCCESS"]
            }
        })
    )

    cod_rider_pending_orders = list(
        mongo.orders.find({
            "payment_method": {
                "$in": ["COD", "CASH_ON_DELIVERY", "COD_RIDER_COLLECTION"]
            },
            "status": "DELIVERED",
            "rider_cash_settlement_status": {
                "$nin": ["RECEIVED_BY_ADMIN", "RECEIVED", "SETTLED", "NOT_REQUIRED"]
            }
        })
    )

    platform_fee_received_orders = list(
        mongo.orders.find({
            "platform_fee_status": "RECEIVED"
        })
    )

    store_payout_pending_orders = list(
        mongo.orders.find({
            "status": "DELIVERED",
            "store_payout_status": {
                "$nin": ["PAID", "SETTLED", "NOT_REQUIRED"]
            }
        })
    )

    online_payment_received_amount = round(
        sum(float(o.get("total_payable") or o.get("total_amount") or 0) for o in online_paid_orders),
        2
    )

    cod_rider_cash_pending_amount = round(
        sum(float(o.get("rider_cash_to_submit") or o.get("expected_rider_cash_to_submit") or 0) for o in cod_rider_pending_orders),
        2
    )

    platform_fee_received_amount = round(
        sum(float(o.get("platform_fee") or 0) for o in platform_fee_received_orders),
        2
    )

    store_payout_pending_amount = round(
        sum(float(o.get("adjusted_store_payout") or o.get("store_payout_amount") or o.get("store_earning") or o.get("items_subtotal") or 0) for o in store_payout_pending_orders),
        2
    )


    # -------------------------
    # Quick links
    # -------------------------
    delivery_mode_settings = get_delivery_mode_settings()
    delivery_mode_ui = get_delivery_mode_ui_context(delivery_mode_settings)

    quick_links = [
        {"label": "Manage Users", "endpoint": "admin_users"},
        {"label": "Customer Refund Processing", "endpoint": "admin_refund_processing"},
        {"label": "Return / Refund Settlement Impact", "endpoint": "admin_returns_settlements"},
        {"label": "Store Payouts & In-house Collection", "endpoint": "admin_settlements"},
        {"label": "Customer Complaints", "endpoint": "admin_complaints"},
        {"label": "Create Store", "endpoint": "admin_create_store"},
    ]

    if delivery_mode_ui.get("is_external"):
        quick_links.extend([
            {"label": "Shiprocket / Courier Orders", "endpoint": "admin_external_delivery_orders"},
            {"label": "External Local Fare & Shiprocket Setup", "endpoint": "admin_external_delivery_settings"},
            {"label": "Delivery Routing & Channel Availability", "endpoint": "admin_delivery_mode_settings"},
        ])
    else:
        quick_links.extend([
            {"label": "Create In-house Delivery Boy", "endpoint": "admin_create_delivery"},
            {"label": "In-house Delivery Overview", "endpoint": "admin_delivery_overview"},
            {"label": "Delivery Routing & Channel Availability", "endpoint": "admin_delivery_mode_settings"},
        ])

    quick_links.append({"label": "Export Transactions CSV", "endpoint": "admin_transactions_csv"})

    metrics = {
        "users": users_total,
        "customers": customers_total,
        "stores": stores_total,
        "products": products_total,
        "orders": orders_total,
        "gmv": round(gmv, 2),
        "total_earnings": round(total_earnings, 2),
        "delivery_people": delivery_people_total,
        "active_delivery_people": active_delivery_people_total,
        "unassigned_orders": unassigned_orders_total,
        "accepted_by_delivery": status_counts["ACCEPTED_BY_DELIVERY_MAN"],
        "packaging_orders": status_counts["PACKAGING"] + status_counts["PREPARING"],
        "out_for_delivery": out_for_delivery_total,
        "delivered_orders": delivered_orders_total,
        "cancelled_orders": cancelled_orders_total,
        "refunded_orders": refunded_orders_total,
        "failed_payments": failed_payments_total,
        "pending_payments": pending_payments_total,
        "paid_transactions": paid_txn_total,
                # Payment / settlement dashboard KPIs
        "online_payment_received_amount": online_payment_received_amount,
        "online_payment_received_count": len(online_paid_orders),

        "cod_rider_cash_pending_amount": cod_rider_cash_pending_amount,
        "cod_rider_cash_pending_count": len(cod_rider_pending_orders),

        "platform_fee_received_amount": platform_fee_received_amount,

        "store_payout_pending_amount": store_payout_pending_amount,
        "store_payout_pending_count": len(store_payout_pending_orders),

        # Refund / return dashboard KPIs
        "refund_pending_count": refund_pending_count,
        "admin_review_needed_count": admin_review_needed_count,
        "refund_processed_count": refund_processed_count,
        "ready_for_refund_amount": round(ready_for_refund_amount, 2),
        "refund_processed_amount": round(refund_processed_amount, 2),
        "store_refund_deduction_amount": round(store_refund_deduction_amount, 2),
        "store_adjustment_due_amount": round(store_adjustment_due_amount, 2),
    }

    return render_template(
        "admin_dashboard.html",
        user=current_user(),
        metrics=metrics,
        by_store=by_store,
        top_store_complaints=top_store_complaints,
        top_delivery_complaints=top_delivery_complaints,
        top_rated_stores=top_rated_stores,
        top_rated_products=top_rated_products,
        top_rated_deliverymen=top_rated_deliverymen,
        top_selling_items=top_selling_items,
        most_popular_stores=most_popular_stores,
        top_selling_store_tiles=top_selling_store_tiles,
        top_customers=top_customers,
        top_deliverymen=top_deliverymen,
        sales_labels=sales_labels,
        sales_values=sales_values,
        quick_links=quick_links,
        complaints_window_label="(all time)",
    )

@app.route('/admin/approvals')
@login_required(role='admin')
def admin_approvals():
    flash('Approval feature under development.', 'info')
    return redirect(url_for('admin_dashboard'))


def _admin_notification_priority(value):
    priority = (value or "medium").strip().lower()

    if priority not in ["high", "medium", "low"]:
        priority = "medium"

    return priority


def _admin_notification_priority_rank(priority):
    priority = _admin_notification_priority(priority)

    if priority == "high":
        return 1

    if priority == "medium":
        return 2

    return 3


def _admin_notification_text(name, limit=500):
    value = (request.form.get(name) or "").strip()

    if len(value) > limit:
        value = value[:limit]

    return value


@app.route('/admin/notifications', methods=['GET', 'POST'], endpoint='admin_notifications')
@login_required(role='admin')
def admin_notifications():
    if request.method == 'POST':
        title = _admin_notification_text("title", 120)
        message = _admin_notification_text("message", 500)
        priority = _admin_notification_priority(request.form.get("priority"))
        link_url = _admin_notification_text("link_url", 300)
        display_location = (request.form.get("display_location") or "homepage").strip().lower()

        if display_location not in ["homepage", "all"]:
            display_location = "homepage"

        is_active = _admin_bool_from_form("is_active", True)
        show_ticker = _admin_bool_from_form("show_ticker", True)
        show_popup = _admin_bool_from_form("show_popup", False)

        if not title:
            flash("Notification title is required.", "warning")
            return redirect(url_for("admin_notifications"))

        if not message:
            flash("Notification message is required.", "warning")
            return redirect(url_for("admin_notifications"))

        now = datetime.utcnow().isoformat()

        mongo.homepage_notifications.insert_one({
            "title": title,
            "message": message,
            "priority": priority,
            "priority_rank": _admin_notification_priority_rank(priority),
            "link_url": link_url,
            "display_location": display_location,
            "is_active": 1 if is_active else 0,
            "show_ticker": 1 if show_ticker else 0,
            "show_popup": 1 if show_popup else 0,
            "created_at": now,
            "updated_at": now,
            "created_by": str((current_user() or {}).get("_id") or (current_user() or {}).get("id") or "")
        })

        flash("Homepage notification created successfully.", "success")
        return redirect(url_for("admin_notifications"))

    notifications = list(
        mongo.homepage_notifications.find({})
        .sort([
            ("priority_rank", 1),
            ("created_at", -1)
        ])
    )

    for n in notifications:
        n["id"] = str(n["_id"])
        n["priority"] = _admin_notification_priority(n.get("priority"))
        n["priority_rank"] = _admin_notification_priority_rank(n.get("priority"))

    stats = {
        "total": mongo.homepage_notifications.count_documents({}),
        "active": mongo.homepage_notifications.count_documents({"is_active": 1}),
        "high": mongo.homepage_notifications.count_documents({"priority": "high"}),
        "medium": mongo.homepage_notifications.count_documents({"priority": "medium"}),
        "low": mongo.homepage_notifications.count_documents({"priority": "low"}),
    }

    return render_template(
        "admin_notifications.html",
        user=current_user(),
        notifications=notifications,
        stats=stats,
        active_page="notifications"
    )


@app.route('/admin/notifications/<nid>/toggle', methods=['POST'], endpoint='admin_notification_toggle')
@login_required(role='admin')
def admin_notification_toggle(nid):
    try:
        notification_id = ObjectId(nid)
    except Exception:
        flash("Invalid notification.", "danger")
        return redirect(url_for("admin_notifications"))

    notification = mongo.homepage_notifications.find_one({"_id": notification_id})

    if not notification:
        flash("Notification not found.", "danger")
        return redirect(url_for("admin_notifications"))

    next_status = 0 if int(notification.get("is_active", 1) or 0) == 1 else 1

    mongo.homepage_notifications.update_one(
        {"_id": notification_id},
        {
            "$set": {
                "is_active": next_status,
                "updated_at": datetime.utcnow().isoformat()
            }
        }
    )

    flash("Notification status updated.", "success")
    return redirect(url_for("admin_notifications"))


@app.route('/admin/notifications/<nid>/delete', methods=['POST'], endpoint='admin_notification_delete')
@login_required(role='admin')
def admin_notification_delete(nid):
    try:
        notification_id = ObjectId(nid)
    except Exception:
        flash("Invalid notification.", "danger")
        return redirect(url_for("admin_notifications"))

    mongo.homepage_notifications.delete_one({"_id": notification_id})

    flash("Notification deleted successfully.", "success")
    return redirect(url_for("admin_notifications"))


@app.route('/admin/notifications/<nid>/priority', methods=['POST'], endpoint='admin_notification_update_priority')
@login_required(role='admin')
def admin_notification_update_priority(nid):
    try:
        notification_id = ObjectId(nid)
    except Exception:
        flash("Invalid notification.", "danger")
        return redirect(url_for("admin_notifications"))

    priority = _admin_notification_priority(request.form.get("priority"))

    mongo.homepage_notifications.update_one(
        {"_id": notification_id},
        {
            "$set": {
                "priority": priority,
                "priority_rank": _admin_notification_priority_rank(priority),
                "updated_at": datetime.utcnow().isoformat()
            }
        }
    )

    flash("Notification priority updated.", "success")
    return redirect(url_for("admin_notifications"))

@app.route('/admin/create-store', methods=['GET', 'POST'])
@login_required(role='admin')
def admin_create_store():

    # =========================
    # CREATE STORE
    # =========================
    if request.method == 'POST':

        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').lower().strip()
        phone_raw = request.form.get('phone', '').strip()
        password = request.form.get('password', '')
        store_name = request.form.get('store_name', '').strip()
        address = request.form.get('address', '').strip()
        city = (request.form.get("city") or "").strip()
        state = (request.form.get("state") or "Assam").strip()
        pincode = _clean_pin(request.form.get("pincode") or "")

        lat_raw = request.form.get('latitude')
        lng_raw = request.form.get('longitude')

        latitude = None
        longitude = None

        is_online = _admin_bool_from_form("is_online", True)
        delivery_enabled = _admin_bool_from_form("delivery_enabled", False)

        delivery_mode = (request.form.get("delivery_mode") or "polygon").strip().lower()
        if delivery_mode not in ["polygon"]:
            delivery_mode = "polygon"

        delivery_zone_polygon = _admin_parse_delivery_zone_polygon(
            request.form.get("delivery_zone_polygon") or ""
        )

        delivery_base_fee = _admin_money_or_default(
            request.form.get("delivery_base_fee"),
            40
        )

               # =========================
        # PARSE STORE LOCATION
        # =========================
        latitude = _admin_float_or_none(lat_raw, -90, 90)
        longitude = _admin_float_or_none(lng_raw, -180, 180)

        # =========================
        # NORMALIZE PHONE
        # =========================
        phone = normalize_phone(phone_raw)

        # =========================
        # VALIDATION
        # =========================
        if not name or not email or not phone or not password or not store_name:
            flash("Please fill all required fields.", "warning")
            return redirect(url_for('admin_create_store'))
        
        if pincode and not is_serviceable_pincode(pincode):
            flash("Please enter a valid 6-digit store pincode.", "warning")
            return redirect(url_for('admin_create_store'))

        if state and not is_assam_state(state):
            flash("Store state must be Assam for delivery operations.", "warning")
            return redirect(url_for('admin_create_store'))

        if delivery_enabled and delivery_mode == "polygon" and not delivery_zone_polygon:
            flash("Delivery zone polygon is required when delivery is enabled.", "warning")
            return redirect(url_for('admin_create_store'))

        if len(password) < 6:
            flash("Password must be at least 6 characters.", "warning")
            return redirect(url_for('admin_create_store'))

        # =========================
        # CHECK EXISTING USER
        # =========================
        existing = mongo.users.find_one({
            "$or": [
                {"email": email},
                {"phone": phone}
            ]
        })

        if existing:
            flash("Email or phone already exists. Use different details.", "warning")
            return redirect(url_for('admin_create_store'))

        # =========================
        # INSERT STORE USER
        # =========================
        try:

            result = mongo.users.insert_one({
                "name": name,
                "email": email,
                "phone": phone,
                "password_hash": generate_password_hash(password),
                "role": "store",
                "phone_verified": 1,
                "is_active": 1,
                "created_at": datetime.utcnow().isoformat()
            })

            user_id = str(result.inserted_id)

            # =========================
            # INSERT STORE
            # =========================
            now = datetime.utcnow().isoformat()

            mongo.stores.insert_one({
                "user_id": user_id,
                "store_name": store_name,

                "address": address,
                "city": city,
                "state": state,
                "pincode": pincode,

                "latitude": latitude,
                "longitude": longitude,

                # Admin/account status.
                "is_active": 1,

                # Store operational status.
                "is_online": 1 if is_online else 0,
                "is_open": 1 if is_online else 0,

                # Delivery/serviceability fields.
                "delivery_available": bool(delivery_enabled),
                "delivery_enabled": 1 if delivery_enabled else 0,
                "delivery_mode": delivery_mode,
                "delivery_zone_polygon": delivery_zone_polygon,
                "delivery_zone_configured": 1 if delivery_zone_polygon else 0,
                "delivery_base_fee": delivery_base_fee,

                "created_at": now,
                "updated_at": now
            })

        except DuplicateKeyError:

            flash(
                "Email or phone already exists. Please use different details.",
                "danger"
            )

            return redirect(url_for('admin_create_store'))

        except Exception as e:

            flash(f"Store creation failed: {e}", "danger")

            return redirect(url_for('admin_create_store'))

        flash("Store created successfully.", "success")

        return redirect(url_for('admin_create_store'))

    # =========================
    # DASHBOARD METRICS
    # =========================
    metrics = {
        "stores": mongo.stores.count_documents({}),
        "orders": mongo.orders.count_documents({}),
        "users": mongo.users.count_documents({"role": "customer"}),
        "products": mongo.products.count_documents({})
    }

    # =========================
    # RENDER PAGE
    # =========================
    return render_template(
        'admin_create_store.html',
        user=current_user(),
        metrics=metrics
    )

@app.route("/admin/stores")
@login_required(role="admin")
def admin_store_overview():
    stores = _admin_store_rows()

    total_stores = len(stores)
    active_stores = len([s for s in stores if s["is_active"] == 1])
    inactive_stores = len([s for s in stores if s["is_active"] != 1])

    total_transactions = mongo.transactions.count_documents({})
    commission_earned = 0.0
    total_store_withdrawals = 0.0

    for txn in mongo.transactions.find({}):
        amount = float(txn.get("amount") or 0)

        if _norm_status(txn.get("status")) == "PAID":
            commission_earned += float(txn.get("commission_amount") or 0)

        txn_type = _norm_status(txn.get("type") or txn.get("transaction_type"))
        if txn_type in ["STORE_WITHDRAWAL", "WITHDRAWAL"]:
            total_store_withdrawals += amount

        if commission_earned <= 0:
        # fallback commission estimate if commission_amount is not stored
            commission_earned = sum(float(s.get("revenue") or 0) for s in stores)

    top_selling_stores = sorted(
        stores,
        key=lambda x: (x["revenue"], x["orders"]),
        reverse=True
    )[:6]

    most_popular_stores = sorted(
        stores,
        key=lambda x: (x["orders"], x["rating"]),
        reverse=True
    )[:6]

    top_product_stores = sorted(
        stores,
        key=lambda x: (x["products"], x["orders"]),
        reverse=True
    )[:6]

    metrics = {
        "total_stores": total_stores,
        "active_stores": active_stores,
        "inactive_stores": inactive_stores,
        "new_stores": mongo.stores.count_documents({}),
        "total_transactions": total_transactions,
        "commission_earned": round(commission_earned, 2),
        "store_withdrawals": round(total_store_withdrawals, 2),
    }

    return render_template(
        "admin_store_overview.html",
        user=current_user(),
        metrics=metrics,
        stores=stores,
        top_selling_stores=top_selling_stores,
        most_popular_stores=most_popular_stores,
        top_product_stores=top_product_stores,
        active_group="store",
        active_page="store_overview",
    )

@app.route("/admin/stores/list")
@login_required(role="admin")
def admin_store_list():
    stores = _admin_store_rows()

    return render_template(
        "admin_store_list.html",
        user=current_user(),
        stores=stores,
        active_group="store",
        active_page="store_list",
    )

@app.route("/admin/stores/reviews")
@login_required(role="admin")
def admin_store_reviews():
    stores = _admin_store_rows()

    recommended_stores = sorted(
        stores,
        key=lambda x: (x["rating"], x["orders"], x["products"]),
        reverse=True
    )

    return render_template(
        "admin_store_reviews.html",
        user=current_user(),
        stores=stores,
        recommended_stores=recommended_stores,
        active_group="store",
        active_page="store_reviews",
    )

@app.route("/admin/stores/export.csv")
@login_required(role="admin")
def admin_stores_export_csv():
    stores = _admin_store_rows()

    rows = [
        ["SL", "Store Name", "Store ID", "Owner Name", "Owner Email", "Owner Phone", "Status", "Created At"]
    ]

    for idx, store in enumerate(stores, start=1):
        rows.append([
            idx,
            store.get("store_name", ""),
            store.get("id", ""),
            store.get("owner_name", ""),
            store.get("owner_email", ""),
            store.get("owner_phone", ""),
            "Active" if store.get("is_active") == 1 else "Inactive",
            store.get("created_at", ""),
        ])

    def csv_escape(value):
        value = "" if value is None else str(value)
        return '"' + value.replace('"', '""') + '"'

    csv_data = "\n".join(",".join(csv_escape(col) for col in row) for row in rows)

    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=nefresh_stores.csv"}
    )

@app.route("/admin/stores/<store_id>/toggle", methods=["POST"])
@login_required(role="admin")
def admin_store_toggle(store_id):
    try:
        sid = ObjectId(store_id)
    except Exception:
        flash("Invalid store.", "danger")
        return redirect(url_for("admin_store_list"))

    store = mongo.stores.find_one({"_id": sid})

    if not store:
        flash("Store not found.", "warning")
        return redirect(url_for("admin_store_list"))

    current_status = int(store.get("is_active", 1) or 0)
    next_status = 0 if current_status == 1 else 1

    mongo.stores.update_one(
        {"_id": sid},
        {"$set": {"is_active": next_status}}
    )

    user_id = store.get("user_id")
    if user_id:
        try:
            mongo.users.update_one(
                {"_id": ObjectId(str(user_id))},
                {"$set": {"is_active": next_status}}
            )
        except Exception:
            pass

    flash("Store status updated successfully.", "success")
    return redirect(url_for("admin_store_list"))


@app.route("/admin/stores/<store_id>/online-toggle", methods=["POST"])
@login_required(role="admin")
def admin_store_online_toggle(store_id):
    try:
        sid = ObjectId(store_id)
    except Exception:
        flash("Invalid store.", "danger")
        return redirect(url_for("admin_store_list"))

    store = mongo.stores.find_one({"_id": sid})

    if not store:
        flash("Store not found.", "warning")
        return redirect(url_for("admin_store_list"))

    current_status = int(store.get("is_online", store.get("is_open", 1)) or 0)
    next_status = 0 if current_status == 1 else 1

    now = datetime.utcnow().isoformat()

    mongo.stores.update_one(
        {"_id": sid},
        {
            "$set": {
                "is_online": next_status,
                "is_open": next_status,
                "online_status_updated_at": now,
                "updated_at": now
            }
        }
    )

    flash("Store is now online." if next_status else "Store is now offline.", "success")
    return redirect(url_for("admin_store_list"))


@app.route("/admin/stores/<store_id>/delivery-toggle", methods=["POST"])
@login_required(role="admin")
def admin_store_delivery_toggle(store_id):
    try:
        sid = ObjectId(store_id)
    except Exception:
        flash("Invalid store.", "danger")
        return redirect(url_for("admin_store_list"))

    store = mongo.stores.find_one({"_id": sid})

    if not store:
        flash("Store not found.", "warning")
        return redirect(url_for("admin_store_list"))

    current_status = int(store.get("delivery_enabled", 1 if store.get("delivery_available", True) else 0) or 0)
    next_status = 0 if current_status == 1 else 1

    now = datetime.utcnow().isoformat()

    mongo.stores.update_one(
        {"_id": sid},
        {
            "$set": {
                "delivery_enabled": next_status,
                "delivery_available": bool(next_status),
                "delivery_status_updated_at": now,
                "updated_at": now
            }
        }
    )

    flash("Store delivery is now enabled." if next_status else "Store delivery is now disabled.", "success")
    return redirect(url_for("admin_store_list"))


@app.route("/admin/stores/<store_id>/update", methods=["POST"])
@login_required(role="admin")
def admin_store_update(store_id):
    try:
        sid = ObjectId(store_id)
    except Exception:
        flash("Invalid store.", "danger")
        return redirect(url_for("admin_store_list"))

    store = mongo.stores.find_one({"_id": sid})

    if not store:
        flash("Store not found.", "warning")
        return redirect(url_for("admin_store_list"))

    store_name = request.form.get("store_name", "").strip()
    address = request.form.get("address", "").strip()
    city = (request.form.get("city") or store.get("city") or "").strip()
    state = (request.form.get("state") or store.get("state") or "Assam").strip()
    pincode = _clean_pin(request.form.get("pincode") or store.get("pincode") or "")

    latitude = _admin_float_or_none(
        request.form.get("latitude"),
        -90,
        90
    )
    longitude = _admin_float_or_none(
        request.form.get("longitude"),
        -180,
        180
    )

    is_online = _admin_bool_from_form(
        "is_online",
        bool(int(store.get("is_online", store.get("is_open", 1)) or 0))
    )

    delivery_enabled = _admin_bool_from_form(
        "delivery_enabled",
        bool(int(store.get("delivery_enabled", 1 if store.get("delivery_available", True) else 0) or 0))
    )

    delivery_mode = (request.form.get("delivery_mode") or store.get("delivery_mode") or "polygon").strip().lower()
    if delivery_mode not in ["polygon"]:
        delivery_mode = "polygon"

    delivery_zone_polygon = _admin_parse_delivery_zone_polygon(
        request.form.get("delivery_zone_polygon") or json.dumps(store.get("delivery_zone_polygon") or [])
    )

    delivery_base_fee = _admin_money_or_default(
        request.form.get("delivery_base_fee"),
        store.get("delivery_base_fee", 40)
    )

    owner_name = request.form.get("owner_name", "").strip()
    owner_email = request.form.get("owner_email", "").lower().strip()
    owner_phone = normalize_phone(request.form.get("owner_phone", "").strip())

    if not store_name:
        flash("Store name is required.", "warning")
        return redirect(url_for("admin_store_list"))
    
    if pincode and not is_serviceable_pincode(pincode):
        flash("Please enter a valid 6-digit store pincode.", "warning")
        return redirect(url_for("admin_store_list"))

    if state and not is_assam_state(state):
        flash("Store state must be Assam for delivery operations.", "warning")
        return redirect(url_for("admin_store_list"))

    if delivery_enabled and delivery_mode == "polygon" and not delivery_zone_polygon:
        flash("Delivery zone polygon is required when delivery is enabled.", "warning")
        return redirect(url_for("admin_store_list"))

    mongo.stores.update_one(
        {"_id": sid},
        {
            "$set": {
                "store_name": store_name,

                "address": address,
                "city": city,
                "state": state,
                "pincode": pincode,

                "latitude": latitude,
                "longitude": longitude,

                "is_online": 1 if is_online else 0,
                "is_open": 1 if is_online else 0,

                "delivery_available": bool(delivery_enabled),
                "delivery_enabled": 1 if delivery_enabled else 0,
                "delivery_mode": delivery_mode,
                "delivery_zone_polygon": delivery_zone_polygon,
                "delivery_zone_configured": 1 if delivery_zone_polygon else 0,
                "delivery_base_fee": delivery_base_fee,

                "updated_at": datetime.utcnow().isoformat()
            }
        }
    )

    user_id = store.get("user_id")

    if user_id:
        update_user = {}

        if owner_name:
            update_user["name"] = owner_name

        if owner_email:
            update_user["email"] = owner_email

        if owner_phone:
            update_user["phone"] = owner_phone

        if update_user:
            try:
                mongo.users.update_one(
                    {"_id": ObjectId(str(user_id))},
                    {"$set": update_user}
                )
            except Exception:
                pass

    flash("Store updated successfully.", "success")
    return redirect(url_for("admin_store_list"))

@app.route("/admin/stores/<store_id>/delete", methods=["POST"])
@login_required(role="admin")
def admin_store_delete(store_id):
    try:
        sid = ObjectId(store_id)
    except Exception:
        flash("Invalid store.", "danger")
        return redirect(url_for("admin_store_list"))

    store = mongo.stores.find_one({"_id": sid})

    if not store:
        flash("Store not found.", "warning")
        return redirect(url_for("admin_store_list"))

    order_cnt = mongo.orders.count_documents({
        "$or": [
            {"store_id": sid},
            {"store_id": str(sid)}
        ]
    })

    user_id = store.get("user_id")

    if order_cnt > 0:
        mongo.stores.update_one(
            {"_id": sid},
            {"$set": {"is_active": 0}}
        )

        if user_id:
            try:
                mongo.users.update_one(
                    {"_id": ObjectId(str(user_id))},
                    {"$set": {"is_active": 0}}
                )
            except Exception:
                pass

        flash("Store has orders, so it was disabled instead of deleted.", "warning")
        return redirect(url_for("admin_store_list"))

    mongo.products.delete_many({
        "$or": [
            {"store_id": sid},
            {"store_id": str(sid)}
        ]
    })

    mongo.stores.delete_one({"_id": sid})

    if user_id:
        try:
            mongo.users.delete_one({"_id": ObjectId(str(user_id))})
        except Exception:
            pass

    flash("Store deleted successfully.", "success")
    return redirect(url_for("admin_store_list"))

@app.route('/admin/delivery')
@login_required(role='admin')
def admin_delivery_overview():
    delivery_rows = _ad_delivery_rows()
    metrics = _ad_delivery_metrics(delivery_rows)
    top_deliverymen = _ad_top_deliverymen(limit=6)

    active_deliverymen = [
        row for row in delivery_rows
        if row.get("is_active") and row.get("is_online")
    ]

    recent_deliverymen = delivery_rows[:8]

    return render_template(
        "admin_delivery_overview.html",
        user=current_user(),
        active_group="delivery",
        active_page="delivery_overview",
        metrics=metrics,
        delivery_rows=delivery_rows,
        active_deliverymen=active_deliverymen,
        top_deliverymen=top_deliverymen,
        recent_deliverymen=recent_deliverymen,
    )

@app.route('/admin/delivery-history', methods=['GET'], endpoint='admin_delivery_history')
@login_required(role='admin')
def admin_delivery_history():
    """
    Admin Delivery Boy History.

    Global rider-wise delivery history across all stores.
    This is read-only and does not affect delivery assignment/status flow.
    """
    q = (request.args.get("q") or "").strip()
    status_filter = (request.args.get("status") or "").strip().upper()
    delivery_user_filter = (request.args.get("delivery_user_id") or "").strip()
    store_filter = (request.args.get("store_id") or "").strip()
    payment_type_filter = (request.args.get("payment_type") or "").strip().upper()
    date_from = (request.args.get("from") or "").strip()
    date_to = (request.args.get("to") or "").strip()

    allowed_history_statuses = {
        "DELIVERED",
        "DELIVERY_FAILED",
        "CANCELLED",
        "READY_FOR_PICKUP",
        "ASSIGNED_TO_DELIVERY",
        "ACCEPTED_BY_DELIVERY_MAN",
        "REACHED_STORE",
        "PICKED_UP",
        "OUT_FOR_DELIVERY"
    }

    def _adh_float(value, default=0.0):
        try:
            if value is None or str(value).strip() == "":
                return float(default)

            return float(value)
        except Exception:
            return float(default)

    def _adh_safe_str(value):
        if value is None:
            return ""

        try:
            if isinstance(value, ObjectId):
                return str(value)
        except Exception:
            pass

        return str(value)

    def _adh_history_entries(order):
        entries = order.get("delivery_history") or []
        return [entry for entry in entries if isinstance(entry, dict)]

    def _adh_latest_action(order, action_name):
        matched = []

        for entry in _adh_history_entries(order):
            if entry.get("action") == action_name:
                matched.append(entry)

        return matched[-1] if matched else {}

    def _adh_latest_value(order, *keys):
        for entry in reversed(_adh_history_entries(order)):
            for key in keys:
                value = entry.get(key)
                if value not in [None, ""]:
                    return value

        return ""

    def _adh_effective_partner_id(order):
        return (
            order.get("delivery_partner_id")
            or order.get("previous_delivery_partner_id")
            or _adh_latest_value(
                order,
                "delivery_partner_id",
                "previous_delivery_partner_id",
                "old_delivery_partner_id",
                "delivery_user_id",
                "previous_delivery_user_id"
            )
            or ""
        )

    def _adh_effective_partner_name(order):
        return (
            order.get("delivery_partner_name")
            or order.get("previous_delivery_partner_name")
            or _adh_latest_value(
                order,
                "delivery_partner_name",
                "previous_delivery_partner_name",
                "old_delivery_partner_name",
                "delivery_user_name",
                "previous_delivery_user_name"
            )
            or ""
        )

    def _adh_effective_partner_phone(order):
        return (
            order.get("delivery_partner_phone")
            or order.get("previous_delivery_partner_phone")
            or _adh_latest_value(
                order,
                "delivery_partner_phone",
                "previous_delivery_partner_phone",
                "old_delivery_partner_phone",
                "delivery_user_phone",
                "previous_delivery_user_phone"
            )
            or ""
        )

    def _adh_partner_lookup(partner_id, name="", phone=""):
        partner_id_str = _adh_safe_str(partner_id)

        if not partner_id_str:
            return {
                "id": "",
                "name": name or "Unknown Delivery Boy",
                "phone": phone or ""
            }

        delivery_user = None

        try:
            if ObjectId.is_valid(partner_id_str):
                delivery_user = mongo.users.find_one({"_id": ObjectId(partner_id_str)})
        except Exception:
            delivery_user = None

        if not delivery_user:
            try:
                delivery_user = mongo.users.find_one({"_id": partner_id_str})
            except Exception:
                delivery_user = None

        if delivery_user:
            name = name or delivery_user.get("name") or delivery_user.get("username") or ""
            phone = phone or delivery_user.get("phone") or delivery_user.get("contact") or ""

        return {
            "id": partner_id_str,
            "name": name or "Unknown Delivery Boy",
            "phone": phone or ""
        }

    def _adh_has_rider_cancel(order):
        if order.get("delivery_cancelled_by_partner"):
            return True

        if order.get("delivery_cancelled_at") or order.get("delivery_cancel_reason"):
            return True

        if _adh_latest_action(order, "cancelled_by_delivery_partner"):
            return True

        return False

    def _adh_record_at(order):
        rider_cancel_entry = _adh_latest_action(order, "cancelled_by_delivery_partner")

        return (
            order.get("delivered_at")
            or order.get("delivery_failed_at")
            or rider_cancel_entry.get("at")
            or order.get("delivery_cancelled_at")
            or order.get("out_for_delivery_at")
            or order.get("picked_up_at")
            or order.get("reached_store_at")
            or order.get("delivery_assigned_at")
            or order.get("assigned_at")
            or order.get("updated_at")
            or order.get("created_at")
            or ""
        )

    def _adh_assignment_source_label(source):
        source = (source or "").strip().lower()

        if source == "rider_self":
            return "Accepted by rider"

        if source == "store_manual":
            return "Assigned by store"

        if source == "store_reassign":
            return "Reassigned by store"

        if source == "admin_manual":
            return "Assigned by admin"

        if source == "admin_reassign":
            return "Reassigned by admin"

        if source:
            return source.replace("_", " ").title()

        return "Not assigned"

    def _adh_apply_status_label(row, has_rider_cancel_history):
        status = (row.get("status") or "").strip().upper()

        if has_rider_cancel_history and status in {
            "READY_FOR_PICKUP",
            "CANCELLED"
        }:
            row["history_type"] = "rider_cancelled"
            row["history_label"] = "Rider Cancelled Assignment"

        elif status == "DELIVERED":
            row["history_type"] = "delivered"
            row["history_label"] = "Delivered"

        elif status == "DELIVERY_FAILED":
            row["history_type"] = "failed"
            row["history_label"] = "Delivery Failed"

        elif status in {
            "ASSIGNED_TO_DELIVERY",
            "ACCEPTED_BY_DELIVERY_MAN",
            "REACHED_STORE",
            "PICKED_UP",
            "OUT_FOR_DELIVERY"
        }:
            row["history_type"] = "active"
            row["history_label"] = "Active Delivery"

        elif status == "READY_FOR_PICKUP":
            row["history_type"] = "ready"
            row["history_label"] = "Ready For Pickup"

        elif status == "CANCELLED":
            row["history_type"] = "cancelled"
            row["history_label"] = "Cancelled"

        else:
            row["history_type"] = "record"
            row["history_label"] = status.replace("_", " ").title() if status else "Record"

        return row

    def _adh_decorate_order(row):
        items_subtotal = _adh_float(
            row.get("items_subtotal")
            if row.get("items_subtotal") is not None
            else row.get("total_amount")
        )

        delivery_fee = _adh_float(row.get("delivery_fee"))
        platform_fee = _adh_float(row.get("platform_fee"))

        tip_amount = _adh_float(
            row.get("tip_amount")
            if row.get("tip_amount") is not None
            else row.get("delivery_tip_amount")
        )

        total_payable = _adh_float(
            row.get("total_payable"),
            items_subtotal + delivery_fee + platform_fee + tip_amount
        )

        payment_method = (row.get("payment_method") or "COD").strip().upper()
        payment_status = (row.get("payment_status") or "PENDING").strip().upper()

        if payment_method == "COD" and payment_status not in ["PAID", "COLLECTED", "ONLINE_PAID"]:
            amount_to_collect = total_payable
        else:
            amount_to_collect = 0.0

        delivery_fee_plus_tip = delivery_fee + tip_amount

        row["items_subtotal"] = round(items_subtotal, 2)
        row["delivery_fee"] = round(delivery_fee, 2)
        row["platform_fee"] = round(platform_fee, 2)
        row["tip_amount"] = round(tip_amount, 2)
        row["total_payable"] = round(total_payable, 2)
        row["payment_method"] = payment_method
        row["payment_status"] = payment_status
        row["is_cod_order"] = payment_method == "COD"
        row["amount_to_collect"] = round(amount_to_collect, 2)
        row["delivery_fee_plus_tip"] = round(delivery_fee_plus_tip, 2)

        # Store earning is product/items subtotal only.
        # Platform fee belongs to admin/platform.
        row["store_earning"] = round(items_subtotal, 2)
        row["admin_platform_earning"] = round(platform_fee, 2)

        row["delivery_assignment_source_label"] = _adh_assignment_source_label(
            row.get("delivery_assignment_source")
        )

        row["assigned_at"] = row.get("delivery_assigned_at") or row.get("assigned_at") or ""
        row["reached_store_at"] = row.get("reached_store_at") or ""
        row["picked_up_at"] = row.get("picked_up_at") or ""
        row["out_for_delivery_at"] = row.get("out_for_delivery_at") or ""
        row["delivered_at"] = row.get("delivered_at") or ""
        row["delivery_failed_at"] = row.get("delivery_failed_at") or ""
        row["delivery_failed_reason"] = row.get("delivery_failed_reason") or ""
        row["delivery_failed_note"] = row.get("delivery_failed_note") or ""

        rider_cancel_entry = _adh_latest_action(row, "cancelled_by_delivery_partner")

        row["rider_cancel_reason"] = (
            rider_cancel_entry.get("reason")
            or row.get("delivery_cancel_reason")
            or ""
        )

        row["rider_cancelled_at"] = (
            rider_cancel_entry.get("at")
            or row.get("delivery_cancelled_at")
            or ""
        )

        row["rider_cancelled_status_from"] = (
            rider_cancel_entry.get("status_before_cancel")
            or row.get("delivery_cancelled_status_from")
            or ""
        )

        return row

    stores = list(mongo.stores.find({}).sort("store_name", 1))

    store_lookup = {}
    store_filter_doc = None

    for store in stores:
        sid = _adh_safe_str(store.get("_id"))

        store_lookup[sid] = {
            "id": sid,
            "store_name": store.get("store_name") or store.get("name") or "Store",
            "phone": store.get("phone") or store.get("owner_phone") or "",
        }

        if store_filter and sid == store_filter:
            store_filter_doc = store

    delivery_users = list(
        mongo.users.find({
            "role": {"$regex": "^delivery$", "$options": "i"}
        }).sort("name", 1)
    )

    delivery_people_map = {}

    for delivery_user in delivery_users:
        did = _adh_safe_str(delivery_user.get("_id"))
        delivery_people_map[did] = {
            "id": did,
            "name": delivery_user.get("name") or delivery_user.get("username") or "Delivery Boy",
            "phone": delivery_user.get("phone") or delivery_user.get("contact") or ""
        }

    raw_orders = list(
        mongo.orders.find({}).sort("updated_at", -1)
    )

    history_orders = []
    rider_summary_map = {}

    for order in raw_orders:
        status = (order.get("status") or "").strip().upper()
        has_rider_cancel_history = _adh_has_rider_cancel(order)

        has_delivery_activity = bool(
            order.get("delivery_partner_id")
            or order.get("previous_delivery_partner_id")
            or order.get("delivery_history")
            or status in allowed_history_statuses
            or has_rider_cancel_history
        )

        if not has_delivery_activity:
            continue

        if status not in allowed_history_statuses and not has_rider_cancel_history:
            continue

        order_store_id = _adh_safe_str(order.get("store_id"))
        order_store_name = (order.get("store_name") or "").strip()

        store_info = store_lookup.get(order_store_id)

        if not store_info and order_store_name:
            store_info = {
                "id": order_store_id,
                "store_name": order_store_name,
                "phone": ""
            }

        if not store_info:
            store_info = {
                "id": order_store_id,
                "store_name": "Unknown Store",
                "phone": ""
            }

        if store_filter:
            if order_store_id != store_filter:
                continue

        effective_partner_id = _adh_effective_partner_id(order)

        if not effective_partner_id:
            continue

        effective_partner_id_str = _adh_safe_str(effective_partner_id)

        if delivery_user_filter and effective_partner_id_str != delivery_user_filter:
            continue

        partner_info = _adh_partner_lookup(
            effective_partner_id_str,
            _adh_effective_partner_name(order),
            _adh_effective_partner_phone(order)
        )

        delivery_people_map[effective_partner_id_str] = partner_info

        payment_method = (order.get("payment_method") or "COD").strip().upper()

        if payment_type_filter == "COD" and payment_method != "COD":
            continue

        if payment_type_filter == "ONLINE" and payment_method == "COD":
            continue

        row = dict(order)
        row["id"] = _adh_safe_str(row.get("_id") or "")
        row["store_id_str"] = store_info.get("id") or order_store_id
        row["store_name"] = store_info.get("store_name") or "Unknown Store"
        row["store_phone"] = store_info.get("phone") or ""

        row["delivery_partner_id"] = effective_partner_id_str
        row["delivery_partner_id_str"] = effective_partner_id_str
        row["delivery_partner_name"] = partner_info.get("name") or "Unknown Delivery Boy"
        row["delivery_partner_phone"] = partner_info.get("phone") or ""

        customer = None

        if row.get("user_id"):
            try:
                customer = mongo.users.find_one({"_id": ObjectId(str(row.get("user_id")))})
            except Exception:
                customer = None

        row["customer_name"] = (
            row.get("customer_name")
            or (customer.get("name") if customer else "")
            or "Customer"
        )

        row["customer_phone"] = (
            row.get("customer_phone")
            or (customer.get("phone") if customer else "")
            or ""
        )

        row = _adh_decorate_order(row)

        record_at = _adh_record_at(order)
        row["record_at"] = record_at

        row = _adh_apply_status_label(row, has_rider_cancel_history)

        if status_filter:
            if status_filter == "RIDER_CANCELLED":
                if row.get("history_type") != "rider_cancelled":
                    continue
            elif status_filter != status and status_filter != (row.get("history_type") or "").upper():
                continue

        if date_from and record_at and str(record_at)[:10] < date_from:
            continue

        if date_to and record_at and str(record_at)[:10] > date_to:
            continue

        if q:
            haystack = " ".join([
                str(row.get("id") or ""),
                str(row.get("store_name") or ""),
                str(row.get("customer_name") or ""),
                str(row.get("customer_phone") or ""),
                str(row.get("delivery_partner_name") or ""),
                str(row.get("delivery_partner_phone") or ""),
                str(row.get("history_label") or ""),
                str(row.get("payment_method") or ""),
                str(row.get("payment_status") or ""),
                str(row.get("delivery_failed_reason") or ""),
                str(row.get("delivery_failed_note") or ""),
                str(row.get("rider_cancel_reason") or "")
            ]).lower()

            if q.lower() not in haystack:
                continue

        if effective_partner_id_str not in rider_summary_map:
            rider_summary_map[effective_partner_id_str] = {
                "delivery_partner_id": effective_partner_id_str,
                "delivery_partner_name": row.get("delivery_partner_name") or "Delivery Boy",
                "delivery_partner_phone": row.get("delivery_partner_phone") or "",
                "stores_served_set": set(),
                "store_names_set": set(),
                "total_orders": 0,
                "delivered": 0,
                "failed": 0,
                "rider_cancelled": 0,
                "active": 0,
                "cancelled": 0,
                "cod_to_collect": 0.0,
                "delivery_fee": 0.0,
                "tip": 0.0,
                "delivery_earning": 0.0,
                "platform_fee": 0.0,
                "store_earning": 0.0,
                "last_record_at": "",
            }

        rider_row = rider_summary_map[effective_partner_id_str]

        rider_row["stores_served_set"].add(row.get("store_id_str") or row.get("store_name") or "")
        rider_row["store_names_set"].add(row.get("store_name") or "Unknown Store")

        rider_row["total_orders"] += 1

        if row.get("history_type") == "delivered":
            rider_row["delivered"] += 1
        elif row.get("history_type") == "failed":
            rider_row["failed"] += 1
        elif row.get("history_type") == "rider_cancelled":
            rider_row["rider_cancelled"] += 1
        elif row.get("history_type") == "active":
            rider_row["active"] += 1
        elif row.get("history_type") == "cancelled":
            rider_row["cancelled"] += 1

        rider_row["cod_to_collect"] += _adh_float(row.get("amount_to_collect"))
        rider_row["delivery_fee"] += _adh_float(row.get("delivery_fee"))
        rider_row["tip"] += _adh_float(row.get("tip_amount"))
        rider_row["delivery_earning"] += _adh_float(row.get("delivery_fee_plus_tip"))
        rider_row["platform_fee"] += _adh_float(row.get("platform_fee"))
        rider_row["store_earning"] += _adh_float(row.get("store_earning"))

        if record_at and str(record_at) > str(rider_row.get("last_record_at") or ""):
            rider_row["last_record_at"] = record_at

        history_orders.append(row)

    history_orders.sort(
        key=lambda x: str(x.get("record_at") or ""),
        reverse=True
    )

    rider_summary_rows = list(rider_summary_map.values())

    for rider_row in rider_summary_rows:
        rider_row["stores_served"] = len([
            sid for sid in rider_row.get("stores_served_set", set())
            if sid
        ])

        rider_row["store_names"] = ", ".join(
            sorted([
                name for name in rider_row.get("store_names_set", set())
                if name
            ])
        )

        rider_row.pop("stores_served_set", None)
        rider_row.pop("store_names_set", None)

        rider_row["cod_to_collect"] = round(_adh_float(rider_row.get("cod_to_collect")), 2)
        rider_row["delivery_fee"] = round(_adh_float(rider_row.get("delivery_fee")), 2)
        rider_row["tip"] = round(_adh_float(rider_row.get("tip")), 2)
        rider_row["delivery_earning"] = round(_adh_float(rider_row.get("delivery_earning")), 2)
        rider_row["platform_fee"] = round(_adh_float(rider_row.get("platform_fee")), 2)
        rider_row["store_earning"] = round(_adh_float(rider_row.get("store_earning")), 2)

    rider_summary_rows.sort(
        key=lambda x: (
            str(x.get("last_record_at") or ""),
            int(x.get("total_orders") or 0)
        ),
        reverse=True
    )

    history_metrics = {
        "total": len(history_orders),
        "total_delivery_boys": len(rider_summary_rows),
        "total_stores": len({
            row.get("store_id_str") or row.get("store_name") or ""
            for row in history_orders
            if row.get("store_id_str") or row.get("store_name")
        }),
        "delivered": sum(1 for r in history_orders if r.get("history_type") == "delivered"),
        "failed": sum(1 for r in history_orders if r.get("history_type") == "failed"),
        "rider_cancelled": sum(1 for r in history_orders if r.get("history_type") == "rider_cancelled"),
        "active": sum(1 for r in history_orders if r.get("history_type") == "active"),
        "cancelled": sum(1 for r in history_orders if r.get("history_type") == "cancelled"),
        "cod_to_collect": round(sum(_adh_float(r.get("amount_to_collect")) for r in history_orders), 2),
        "delivery_fee": round(sum(_adh_float(r.get("delivery_fee")) for r in history_orders), 2),
        "tip": round(sum(_adh_float(r.get("tip_amount")) for r in history_orders), 2),
        "delivery_earning": round(sum(_adh_float(r.get("delivery_fee_plus_tip")) for r in history_orders), 2),
        "platform_fee": round(sum(_adh_float(r.get("platform_fee")) for r in history_orders), 2),
        "store_earning": round(sum(_adh_float(r.get("store_earning")) for r in history_orders), 2),
    }

    return render_template(
        "admin_delivery_history.html",
        user=current_user(),
        active_group="delivery",
        active_page="delivery_history",
        orders=history_orders,
        rider_summary_rows=rider_summary_rows,
        delivery_people=list(delivery_people_map.values()),
        stores=[
            {
                "id": _adh_safe_str(store.get("_id")),
                "store_name": store.get("store_name") or store.get("name") or "Store"
            }
            for store in stores
        ],
        history_metrics=history_metrics,
        q=q,
        status_filter=status_filter,
        delivery_user_filter=delivery_user_filter,
        store_filter=store_filter,
        payment_type_filter=payment_type_filter,
        date_from=date_from,
        date_to=date_to
    )

@app.route('/admin/create-delivery', methods=['GET', 'POST'])
@login_required(role='admin')
def admin_create_delivery():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').lower().strip()
        phone_raw = request.form.get('phone', '').strip()
        password = request.form.get('password', '')

        phone = normalize_phone(phone_raw)

        if not name or not email or not phone or not password:
            flash("Please fill all required fields.", "error")
            return redirect(url_for('admin_create_delivery'))

        if len(password) < 6:
            flash("Password must be at least 6 characters.", "error")
            return redirect(url_for('admin_create_delivery'))

        existing = mongo.users.find_one({
            "$or": [
                {"email": email},
                {"phone": phone}
            ]
        })

        if existing:
            flash("Email or phone already exists. Use different details.", "error")
            return redirect(url_for('admin_create_delivery'))

        try:
            result = mongo.users.insert_one({
                "name": name,
                "email": email,
                "phone": phone,
                "password_hash": generate_password_hash(password),
                "role": "delivery",
                "phone_verified": 1,
                "is_active": 1,
                "created_at": datetime.utcnow().isoformat()
            })

            mongo.delivery_availability.update_one(
                {"user_id": str(result.inserted_id)},
                {
                    "$set": {
                        "user_id": str(result.inserted_id),
                        "active": False,
                        "zone": "Main Zone",
                        "created_at": datetime.utcnow().isoformat(),
                        "updated_at": datetime.utcnow().isoformat()
                    }
                },
                upsert=True
            )

        except DuplicateKeyError:
            flash("This email or phone is already registered. Please use different details.", "error")
            return redirect(url_for('admin_create_delivery'))
        except Exception as e:
            flash(f"Failed to create delivery partner: {str(e)}", "error")
            return redirect(url_for('admin_create_delivery'))

        flash("Delivery partner created.", "success")
        return redirect(url_for('admin_create_delivery'))

    return render_template(
        'admin_create_delivery.html',
        user=current_user(),
        active_group="delivery",
        active_page="create_delivery_person"
    )

@app.route('/admin/delivery/list')
@login_required(role='admin')
def admin_delivery_list():
    search = request.args.get("search", "").strip()
    status = request.args.get("status", "").strip()
    availability = request.args.get("availability", "").strip()

    rows = _ad_delivery_rows()
    rows = _ad_filter_delivery_rows(
        rows,
        search=search,
        status=status,
        availability=availability
    )

    metrics = _ad_delivery_metrics(rows)

    return render_template(
        "admin_delivery_list.html",
        user=current_user(),
        active_group="delivery",
        active_page="delivery_list",
        delivery_users=rows,
        deliverymen=rows,
        metrics=metrics,
        search=search,
        status=status,
        availability=availability,
    )

@app.route('/admin/delivery/reviews')
@login_required(role='admin')
def admin_delivery_reviews():
    delivery_id = request.args.get("delivery_id", "").strip()
    sort_by = request.args.get("sort_by", "").strip()
    search = request.args.get("search", "").strip()

    delivery_options = _ad_delivery_rows()

    rows = _ad_delivery_review_rows()
    rows = _ad_filter_review_rows(
        rows,
        delivery_id=delivery_id,
        sort_by=sort_by,
        search=search
    )

    metrics = _ad_delivery_review_metrics(rows)

    return render_template(
        "admin_delivery_reviews.html",
        user=current_user(),
        active_group="delivery",
        active_page="delivery_reviews",
        reviews=rows,
        delivery_reviews=rows,
        delivery_options=delivery_options,
        metrics=metrics,
        delivery_id=delivery_id,
        sort_by=sort_by,
        search=search,
    )

@app.route('/admin/delivery/export.csv')
@login_required(role='admin')
def admin_delivery_export_csv():
    search = request.args.get("search", "").strip()
    status = request.args.get("status", "").strip()
    availability = request.args.get("availability", "").strip()

    rows = _ad_delivery_rows()
    rows = _ad_filter_delivery_rows(
        rows,
        search=search,
        status=status,
        availability=availability
    )

    return _ad_delivery_csv_response(rows, "delivery_users.csv")

@app.route('/admin/delivery/reviews/export.csv')
@login_required(role='admin')
def admin_delivery_reviews_export_csv():
    delivery_id = request.args.get("delivery_id", "").strip()
    sort_by = request.args.get("sort_by", "").strip()
    search = request.args.get("search", "").strip()

    rows = _ad_delivery_review_rows()
    rows = _ad_filter_review_rows(
        rows,
        delivery_id=delivery_id,
        sort_by=sort_by,
        search=search
    )

    return _ad_delivery_reviews_csv_response(rows, "delivery_reviews.csv")

@app.route('/admin/users/<uid>/enable', methods=['POST'])
@login_required(role='admin')
def admin_user_enable(uid):
    try:
        uid_obj = ObjectId(uid)
    except Exception:
        flash("Invalid user.", "danger")
        return redirect(request.referrer or url_for("admin_users"))

    result = mongo.users.update_one(
        {"_id": uid_obj},
        {"$set": {"is_active": 1}}
    )

    if result.matched_count == 0:
        flash("User not found.", "warning")
    else:
        flash("User activated.", "success")

    return redirect(request.referrer or url_for("admin_users"))

@app.route('/admin/transactions.csv')
@login_required(role='admin')
def admin_transactions_csv():
    transactions = list(
        mongo.transactions.find({}).sort("created_at", -1)
    )

    csv_lines = ['txn_id,created_at,order_id,total_amount,amount,status']

    for t in transactions:
        order_id = t.get("order_id")
        order = None

        if order_id:
            order = mongo.orders.find_one({"_id": order_id})

        txn_id = str(t.get("_id", ""))
        created_at = t.get("created_at", "")
        order_id_str = str(order_id) if order_id else ""
        total_amount = float(order.get("total_amount") or 0) if order else 0
        amount = float(t.get("amount") or 0)
        status = t.get("status", "")

        csv_lines.append(
            f"{txn_id},{created_at},{order_id_str},{total_amount},{amount},{status}"
        )

    data = "\n".join(csv_lines).encode("utf-8")

    return send_file(
        io.BytesIO(data),
        mimetype="text/csv",
        as_attachment=True,
        download_name="transactions.csv"
    )

@app.route('/admin/users/<uid>/transactions.csv')
@login_required(role='admin')
def admin_user_transactions_csv(uid):
    uid_str = str(uid)

    user_orders = list(mongo.orders.find({
        "$or": [
            {"user_id": uid_str},
            {"delivery_partner_id": uid_str}
        ]
    }))

    try:
        uid_obj = ObjectId(uid_str)
    except Exception:
        uid_obj = None

    if uid_obj:
        store = mongo.stores.find_one({"user_id": uid_str})
        if store:
            store_orders = list(mongo.orders.find({"store_id": store["_id"]}))
            user_orders.extend(store_orders)

    seen_order_ids = set()
    order_ids = []

    for order in user_orders:
        oid = order["_id"]
        if str(oid) not in seen_order_ids:
            seen_order_ids.add(str(oid))
            order_ids.append(oid)

    csv_lines = ["txn_id,created_at,order_id,amount,status"]

    if order_ids:
        rows = list(mongo.transactions.find({
            "order_id": {"$in": order_ids}
        }).sort("created_at", -1))

        for r in rows:
            csv_lines.append(
                f"{str(r.get('_id'))},"
                f"{r.get('created_at', '')},"
                f"{str(r.get('order_id', ''))},"
                f"{r.get('amount', 0)},"
                f"{r.get('status', '')}"
            )

    data = "\n".join(csv_lines).encode("utf-8")

    return send_file(
        io.BytesIO(data),
        mimetype="text/csv",
        as_attachment=True,
        download_name=f"user_{uid_str}_transactions.csv"
    )

@app.route('/admin/users/<uid>/export', methods=['GET'])
@login_required(role='admin')
def admin_user_export(uid):
    u = get_user_by_id(uid)
    if not u:
        flash('User not found.','warning')
        return redirect(url_for('admin_users'))
    try:
        data = render_export_to_csv_zip_bytes(uid)
    except Exception as e:
        flash(f'Failed to prepare export: {e}', 'danger')
        return redirect(url_for('admin_users'))
    fn = f"user_{uid}_export_{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}.zip"
    return send_file(BytesIO(data), mimetype='application/zip', as_attachment=True, download_name=fn)

@app.route('/admin/users/<uid>/export.zip', methods=['GET'])
@login_required(role='admin')
def admin_user_export_zip(uid):
    return admin_user_export(uid)

@app.route('/admin/users/<uid>/delete-hard', methods=['POST'])
@login_required(role='admin')
def admin_user_delete_hard(uid):
    ok, reason = can_delete_user_hard(uid)
    if not ok:
        flash(f'Cannot hard-delete: {reason}. The account should remain or be disabled.', 'warning')
        return redirect(request.referrer or url_for('admin_users'))
    try:
        if hard_delete_user(uid):
            flash('User hard-deleted.','success')
        else:
            flash('Hard delete failed.','danger')
    except Exception as e:
        flash(f'Hard delete failed: {e}','danger')
    return redirect(request.referrer or url_for('admin_users'))

@app.route('/admin/complaints')
@login_required(role='admin')
def admin_complaints():
    complaints = list(
        mongo.customer_complaints.find({
            "$or": [
                {"is_active": 1},
                {"is_active": True},
                {"is_active": {"$exists": False}}
            ]
        }).sort("created_at", -1).limit(300)
    )

    for c in complaints:
        _admin_prepare_complaint_row(c)

    complaint_metrics = {
        "total": len(complaints),
        "admin": sum(1 for c in complaints if c.get("assigned_to") == "admin" or c.get("target_type") == "admin"),
        "store": sum(1 for c in complaints if c.get("assigned_to") == "store" or c.get("target_type") == "store"),
        "open": sum(1 for c in complaints if c.get("status") == "open"),
        "in_progress": sum(1 for c in complaints if c.get("status") == "in_progress"),
        "resolved": sum(1 for c in complaints if c.get("status") == "resolved")
    }

    return render_template(
        'admin_complaints.html',
        user=current_user(),
        complaints=complaints,
        complaint_metrics=complaint_metrics,
        active_page="complaints",
        active_group="operations"
    )


@app.route('/admin/complaints/<cid>/status', methods=['POST'])
@login_required(role='admin')
def admin_complaint_set_status(cid):
    try:
        cid_obj = ObjectId(cid)
    except Exception:
        flash("Invalid complaint.", "danger")
        return redirect(url_for("admin_complaints"))

    complaint = mongo.customer_complaints.find_one({"_id": cid_obj})

    if not complaint:
        flash("Complaint not found.", "danger")
        return redirect(url_for("admin_complaints"))

    if _admin_is_store_complaint_doc(complaint):
        flash("This is a store complaint. Admin can only view it unless it is taken over.", "warning")
        return redirect(url_for("admin_complaints"))

    status = (request.form.get("status") or "open").strip().lower()
    progress_status = (request.form.get("progress_status") or status).strip().lower()
    admin_reply = (request.form.get("admin_reply") or "").strip()
    admin_progress_note = (request.form.get("admin_progress_note") or "").strip()

    allowed_status = {
        "open",
        "in_progress",
        "resolved",
        "rejected"
    }

    allowed_progress = {
        "received",
        "in_progress",
        "resolved",
        "rejected"
    }

    if status not in allowed_status:
        flash("Please select a valid complaint status.", "warning")
        return redirect(url_for("admin_complaints"))

    if progress_status not in allowed_progress:
        flash("Please select a valid progress status.", "warning")
        return redirect(url_for("admin_complaints"))

    if status == "resolved":
        progress_status = "resolved"
    elif status == "in_progress":
        progress_status = "in_progress"
    elif status == "rejected":
        progress_status = "rejected"

    if len(admin_reply) > 1000:
        admin_reply = admin_reply[:1000]

    if len(admin_progress_note) > 1000:
        admin_progress_note = admin_progress_note[:1000]

    now = datetime.utcnow().isoformat()
    admin_user = current_user() or {}

    mongo.customer_complaints.update_one(
        {"_id": cid_obj},
        {
            "$set": {
                "status": status,
                "progress_status": progress_status,
                "admin_reply": admin_reply,
                "admin_progress_note": admin_progress_note,
                "admin_updated_at": now,
                "admin_updated_by": str(admin_user.get("id") or admin_user.get("_id") or ""),
                "admin_updated_by_name": admin_user.get("name") or admin_user.get("email") or "Admin",
                "updated_at": now
            },
            "$push": {
                "complaint_history": {
                    "action": "ADMIN_COMPLAINT_STATUS_UPDATED",
                    "status": status,
                    "progress_status": progress_status,
                    "admin_reply": admin_reply,
                    "admin_progress_note": admin_progress_note,
                    "created_at": now,
                    "created_by": str(admin_user.get("id") or admin_user.get("_id") or ""),
                    "created_by_name": admin_user.get("name") or admin_user.get("email") or "Admin"
                }
            }
        }
    )

    flash("Complaint status updated.", "success")
    return redirect(url_for("admin_complaints"))

@app.route('/admin/complaints/<cid>/takeover', methods=['POST'], endpoint='admin_complaint_takeover')
@login_required(role='admin')
def admin_complaint_takeover(cid):
    try:
        cid_obj = ObjectId(cid)
    except Exception:
        flash("Invalid complaint.", "danger")
        return redirect(url_for("admin_complaints"))

    complaint = mongo.customer_complaints.find_one({"_id": cid_obj})

    if not complaint:
        flash("Complaint not found.", "danger")
        return redirect(url_for("admin_complaints"))

    admin_takeover_status = str(
        complaint.get("admin_takeover_status") or ""
    ).strip().upper()

    if admin_takeover_status == "TAKEN_OVER":
        flash("This complaint is already taken over by Admin.", "warning")
        return redirect(url_for("admin_complaints"))

    if not _admin_is_store_complaint_doc(complaint):
        flash("Only store complaints can be taken over.", "warning")
        return redirect(url_for("admin_complaints"))

    assigned_to = str(complaint.get("assigned_to") or "").strip().lower()
    target_type = str(
        complaint.get("target_type")
        or complaint.get("target_kind")
        or ""
    ).strip().lower()

    takeover_reason = (request.form.get("takeover_reason") or "").strip()

    if len(takeover_reason) > 700:
        takeover_reason = takeover_reason[:700]

    now = datetime.utcnow().isoformat()
    admin_user = current_user() or {}

    original_store_id = complaint.get("store_id")
    original_store_id_str = complaint.get("store_id_str") or str(original_store_id or "")
    original_store_name = complaint.get("store_name") or ""

    takeover_event = {
        "action": "ADMIN_TAKEOVER_STORE_COMPLAINT",
        "old_assigned_to": assigned_to,
        "old_target_type": target_type,
        "new_assigned_to": "admin",
        "new_target_type": "admin",
        "original_store_id": original_store_id_str,
        "original_store_name": original_store_name,
        "takeover_reason": takeover_reason,
        "created_at": now,
        "created_by": str(admin_user.get("id") or admin_user.get("_id") or ""),
        "created_by_name": admin_user.get("name") or admin_user.get("email") or "Admin"
    }

    mongo.customer_complaints.update_one(
        {"_id": cid_obj},
        {
            "$set": {
                "assigned_to": "admin",
                "target_type": "admin",
                "admin_takeover_status": "TAKEN_OVER",
                "admin_takeover_at": now,
                "admin_takeover_by": str(admin_user.get("id") or admin_user.get("_id") or ""),
                "admin_takeover_by_name": admin_user.get("name") or admin_user.get("email") or "Admin",
                "admin_takeover_reason": takeover_reason,

                "original_assigned_to": assigned_to,
                "original_target_type": target_type,
                "original_store_id": original_store_id,
                "original_store_id_str": original_store_id_str,
                "original_store_name": original_store_name,

                "progress_status": "in_progress",
                "status": "in_progress",
                "updated_at": now
            },
            "$push": {
                "complaint_history": takeover_event
            }
        }
    )

    flash("Store complaint has been taken over by Admin.", "success")
    return redirect(url_for("admin_complaints"))

@app.route('/admin/users')
@login_required(role='admin')
def admin_users():
    data = _au_user_overview_data()

    return render_template(
        "admin_users_overview.html",
        user=current_user(),
        active_group="users",
        active_page="users_overview",
        metrics=data["metrics"],
        month_labels=data["month_labels"],
        customer_growth_values=data["customer_growth_values"],
        top_deliverymen=data["top_deliverymen"],
        top_store_users=data["top_store_users"],
        recent_users=data["recent_users"],
        current_year=data["current_year"],
    )

@app.route('/admin/users/store-users')
@login_required(role='admin')
def admin_store_users():
    search = request.args.get("search", "").strip()
    status = request.args.get("status", "").strip()

    rows = _au_store_user_rows()
    rows = _au_filter_rows_by_status(rows, status)
    rows = _au_filter_rows_by_search(rows, search)

    metrics = {
        "total": len(rows),
        "active": sum(1 for row in rows if row.get("is_active")),
        "disabled": sum(1 for row in rows if not row.get("is_active")),
        "total_orders": sum(_au_safe_int(row.get("orders")) for row in rows),
        "total_revenue": _au_money(sum(_au_safe_float(row.get("revenue")) for row in rows)),
        "total_products": sum(_au_safe_int(row.get("products")) for row in rows),
    }

    return render_template(
        "admin_store_users.html",
        user=current_user(),
        active_group="users",
        active_page="store_users",
        store_users=rows,
        users=rows,
        metrics=metrics,
        search=search,
        status=status,
    )

@app.route('/admin/users/delivery-users')
@login_required(role='admin')
def admin_delivery_users():
    search = request.args.get("search", "").strip()
    status = request.args.get("status", "").strip()
    availability = request.args.get("availability", "").strip().lower()

    rows = _au_delivery_user_rows()
    rows = _au_filter_rows_by_status(rows, status)
    rows = _au_filter_rows_by_search(rows, search)

    if availability == "online":
        rows = [row for row in rows if row.get("is_online")]
    elif availability == "offline":
        rows = [row for row in rows if not row.get("is_online")]

    metrics = {
        "total": len(rows),
        "active": sum(1 for row in rows if row.get("is_active")),
        "disabled": sum(1 for row in rows if not row.get("is_active")),
        "online": sum(1 for row in rows if row.get("is_online")),
        "offline": sum(1 for row in rows if not row.get("is_online")),
        "completed_orders": sum(_au_safe_int(row.get("total_completed_orders")) for row in rows),
        "assigned_orders": sum(_au_safe_int(row.get("currently_assigned_orders")) for row in rows),
    }

    return render_template(
        "admin_delivery_users.html",
        user=current_user(),
        active_group="users",
        active_page="delivery_users",
        delivery_users=rows,
        users=rows,
        metrics=metrics,
        search=search,
        status=status,
        availability=availability,
    )

@app.route('/admin/users/customers')
@login_required(role='admin')
def admin_customers():
    search = request.args.get("search", "").strip()
    status = request.args.get("status", "").strip()
    sort_by = request.args.get("sort_by", "").strip()
    limit_raw = request.args.get("limit", "").strip()

    rows = _au_customer_rows()
    rows = _au_filter_rows_by_status(rows, status)
    rows = _au_filter_rows_by_search(rows, search)

    if sort_by == "orders_desc":
        rows = sorted(rows, key=lambda row: _au_safe_int(row.get("total_order")), reverse=True)
    elif sort_by == "amount_desc":
        rows = sorted(rows, key=lambda row: _au_safe_float(row.get("total_order_amount")), reverse=True)
    elif sort_by == "joining_new":
        rows = sorted(rows, key=lambda row: _au_parse_date(row.get("created_at")) or datetime.min, reverse=True)
    elif sort_by == "joining_old":
        rows = sorted(rows, key=lambda row: _au_parse_date(row.get("created_at")) or datetime.min)

    if limit_raw:
        try:
            limit = int(limit_raw)
            if limit > 0:
                rows = rows[:limit]
        except Exception:
            pass

    metrics = {
        "total": len(rows),
        "active": sum(1 for row in rows if row.get("is_active")),
        "disabled": sum(1 for row in rows if not row.get("is_active")),
        "total_orders": sum(_au_safe_int(row.get("total_order")) for row in rows),
        "total_amount": _au_money(sum(_au_safe_float(row.get("total_order_amount")) for row in rows)),
    }

    return render_template(
        "admin_customers.html",
        user=current_user(),
        active_group="users",
        active_page="customers",
        customers=rows,
        users=rows,
        metrics=metrics,
        search=search,
        status=status,
        sort_by=sort_by,
        limit=limit_raw,
    )

@app.route('/admin/users/export.csv')
@login_required(role='admin')
def admin_users_export_csv():
    role = (request.args.get("role") or "").strip().lower()

    if role == "store":
        rows = _au_store_user_rows()
        filename = "store_users.csv"
    elif role == "delivery":
        rows = _au_delivery_user_rows()
        filename = "delivery_users.csv"
    elif role == "customer":
        rows = _au_customer_rows()
        filename = "customers.csv"
    else:
        rows = [_au_user_base_row(user_doc) for user_doc in _au_all_users()]
        filename = "users.csv"

    return _au_export_users_csv_response(rows, filename)

@app.route('/admin/users/<uid>/disable', methods=['POST'])
@login_required(role='admin')
def admin_user_disable(uid):
    try:
        uid_obj = ObjectId(uid)
    except Exception:
        flash("Invalid user.", "danger")
        return redirect(request.referrer or url_for("admin_users"))

    mongo.users.update_one(
        {"_id": uid_obj},
        {"$set": {"is_active": 0}}
    )

    flash("User disabled.", "info")
    return redirect(request.referrer or url_for("admin_users"))

@app.route('/admin/users/<uid>/delete', methods=['POST'])
@login_required(role='admin')
def admin_user_delete(uid):
    try:
        uid_obj = ObjectId(uid)
    except Exception:
        flash("Invalid user.", "danger")
        return redirect(request.referrer or url_for("admin_users"))

    udoc = mongo.users.find_one({"_id": uid_obj})

    if not udoc:
        flash("User not found.", "warning")
        return redirect(request.referrer or url_for("admin_users"))

    role = udoc.get("role")

    if role == "admin":
        flash("Refused to delete admin via UI.", "danger")
        return redirect(request.referrer or url_for("admin_users"))

    uid_str = str(uid_obj)

    if role == "store":
        store = mongo.stores.find_one({"user_id": uid_str})
        sid = store["_id"] if store else None

        order_cnt = mongo.orders.count_documents({"store_id": sid}) if sid else 0

        if order_cnt > 0:
            mongo.users.update_one({"_id": uid_obj}, {"$set": {"is_active": 0}})
            flash("Store has orders; user disabled instead of hard delete.", "warning")
            return redirect(request.referrer or url_for("admin_users"))

        if sid:
            mongo.products.delete_many({"store_id": sid})
            mongo.stores.delete_one({"_id": sid})

        mongo.users.delete_one({"_id": uid_obj})
        flash("Store user removed.", "success")
        return redirect(request.referrer or url_for("admin_users"))

    if role == "customer":
        order_cnt = mongo.orders.count_documents({"user_id": uid_str})

        if order_cnt > 0:
            mongo.users.update_one({"_id": uid_obj}, {"$set": {"is_active": 0}})
            flash("Customer has orders; user disabled instead of hard delete.", "warning")
            return redirect(request.referrer or url_for("admin_users"))

        mongo.addresses.delete_many({"user_id": uid_str})
        mongo.users.delete_one({"_id": uid_obj})
        flash("Customer removed.", "success")
        return redirect(request.referrer or url_for("admin_users"))

    if role == "delivery":
        order_cnt = mongo.orders.count_documents({"delivery_partner_id": uid_str})

        if order_cnt > 0:
            mongo.users.update_one({"_id": uid_obj}, {"$set": {"is_active": 0}})
            flash("Delivery partner has order history; user disabled.", "warning")
            return redirect(request.referrer or url_for("admin_users"))

        mongo.users.delete_one({"_id": uid_obj})
        flash("Delivery partner removed.", "success")
        return redirect(request.referrer or url_for("admin_users"))

    mongo.users.delete_one({"_id": uid_obj})
    flash("User removed.", "success")
    return redirect(request.referrer or url_for("admin_users"))

@app.route("/admin/contact-messages", methods=["GET"], endpoint="admin_contact_messages")
@login_required(role="admin")
def admin_contact_messages():
    q = (request.args.get("q") or "").strip()

    query = {}

    if q:
        query["$or"] = [
            {"name": {"$regex": q, "$options": "i"}},
            {"email": {"$regex": q, "$options": "i"}},
            {"phone": {"$regex": q, "$options": "i"}},
            {"subject": {"$regex": q, "$options": "i"}},
            {"message": {"$regex": q, "$options": "i"}}
        ]

    messages = list(
        mongo.contact_messages.find(query).sort("created_at", -1)
    )

    for m in messages:
        m["id"] = str(m["_id"])

    auto_reply_settings = get_contact_auto_reply_settings()

    stats = {
        "total": mongo.contact_messages.count_documents({}),
        "pending_reply": mongo.contact_messages.count_documents({
            "$and": [
                {"auto_reply_sent": {"$ne": True}},
                {"manual_reply_sent": {"$ne": True}}
            ]
        }),
        "auto_sent": mongo.contact_messages.count_documents({"auto_reply_sent": True}),
        "manual_sent": mongo.contact_messages.count_documents({"manual_reply_sent": True})
    }

    return render_template(
        "admin_contact_messages.html",
        user=current_user(),
        messages=messages,
        stats=stats,
        q=q,
        auto_reply_enabled=bool(auto_reply_settings.get("enabled")),
        auto_reply_settings=auto_reply_settings,
        active_page="contact_messages",
        active_group="operations"
    )


@app.route("/admin/contact-messages/<mid>/status", methods=["POST"], endpoint="admin_contact_message_status")
@login_required(role="admin")
def admin_contact_message_status(mid):
    status = (request.form.get("status") or "NEW").strip().upper()
    admin_note = (request.form.get("admin_note") or "").strip()

    if status not in ["NEW", "READ", "RESOLVED"]:
        status = "NEW"

    try:
        mid_obj = ObjectId(mid)
    except Exception:
        flash("Invalid contact message.", "danger")
        return redirect(url_for("admin_contact_messages"))

    now = datetime.utcnow().isoformat()

    update_doc = {
        "status": status,
        "updated_at": now
    }

    if admin_note:
        update_doc["admin_note"] = admin_note

    if status == "READ":
        update_doc["read_at"] = now

    if status == "RESOLVED":
        update_doc["resolved_at"] = now

    mongo.contact_messages.update_one(
        {"_id": mid_obj},
        {
            "$set": update_doc
        }
    )

    flash("Contact message updated successfully.", "success")
    return redirect(request.referrer or url_for("admin_contact_messages"))

@app.route("/admin/contact-messages/auto-reply/toggle", methods=["POST"], endpoint="admin_contact_auto_reply_toggle")
@login_required(role="admin")
def admin_contact_auto_reply_toggle():
    enabled = str(request.form.get("enabled") or "0").strip() == "1"
    admin_user = current_user() or {}
    now = datetime.utcnow().isoformat()

    existing = mongo.platform_settings.find_one({
        "key": CONTACT_AUTO_REPLY_SETTINGS_KEY
    }) or {}

    mongo.platform_settings.update_one(
        {"key": CONTACT_AUTO_REPLY_SETTINGS_KEY},
        {
            "$set": {
                "key": CONTACT_AUTO_REPLY_SETTINGS_KEY,
                "enabled": bool(enabled),
                "subject": existing.get("subject") or "We received your message - NELOCALS",
                "body": existing.get("body") or (
                    "Dear {name},\n\n"
                    "Thank you for contacting NELOCALS.\n\n"
                    "We have received your message regarding: {subject}.\n\n"
                    "Our admin/contact team will review your message and contact you as soon as possible.\n\n"
                    "Thank you,\n"
                    "NELOCALS Admin Team"
                ),
                "updated_at": now,
                "updated_by": str(admin_user.get("_id") or admin_user.get("id") or ""),
                "updated_by_name": admin_user.get("name") or admin_user.get("email") or "Admin"
            },
            "$setOnInsert": {
                "created_at": now
            }
        },
        upsert=True
    )

    flash(
        "Automatic contact acknowledgement email enabled."
        if enabled else
        "Automatic contact acknowledgement email disabled.",
        "success"
    )

    return redirect(request.referrer or url_for("admin_contact_messages"))


@app.route("/admin/contact-messages/auto-reply/settings", methods=["POST"], endpoint="admin_contact_auto_reply_settings_update")
@login_required(role="admin")
def admin_contact_auto_reply_settings_update():
    # Automatic message editing has intentionally been removed from the Admin UI.
    # Keep this route harmless for old cached forms/bookmarks, but do not update
    # the automatic acknowledgement subject/body from POST data anymore.
    flash("Automatic acknowledgement message editing is disabled. Use the ON/OFF switch and manual reply box instead.", "info")
    return redirect(request.referrer or url_for("admin_contact_messages"))


@app.route("/admin/contact-messages/<mid>/auto-reply/send", methods=["POST"], endpoint="admin_contact_auto_reply_send")
@login_required(role="admin")
def admin_contact_auto_reply_send(mid):
    try:
        mid_obj = ObjectId(mid)
    except Exception:
        flash("Invalid contact message.", "danger")
        return redirect(url_for("admin_contact_messages"))

    contact_doc = mongo.contact_messages.find_one({"_id": mid_obj})

    if not contact_doc:
        flash("Contact message not found.", "danger")
        return redirect(url_for("admin_contact_messages"))

    result = send_contact_auto_reply(contact_doc)
    now = datetime.utcnow().isoformat()

    mongo.contact_messages.update_one(
        {"_id": mid_obj},
        {
            "$set": {
                "auto_reply_sent": bool(result.get("sent")),
                "auto_reply_error": result.get("error") or "",
                "auto_reply_sent_at": now if result.get("sent") else contact_doc.get("auto_reply_sent_at", ""),
                "updated_at": now
            },
            "$push": {
                "reply_logs": {
                    "type": "AUTO_ACKNOWLEDGEMENT",
                    "sent": bool(result.get("sent")),
                    "error": result.get("error") or "",
                    "subject": result.get("subject") or "We received your message - NELOCALS",
                    "message": "Automatic acknowledgement email sent to user." if result.get("sent") else "Automatic acknowledgement email was not sent.",
                    "created_at": now,
                    "created_by": str((current_user() or {}).get("_id") or (current_user() or {}).get("id") or ""),
                    "created_by_name": (current_user() or {}).get("name") or (current_user() or {}).get("email") or "Admin"
                }
            }
        }
    )

    if result.get("sent"):
        flash("Automatic acknowledgement email sent to user.", "success")
    elif not result.get("enabled", True):
        flash("Automatic acknowledgement is OFF. Use the manual reply box to email this user.", "warning")
    else:
        flash(result.get("error") or "Could not send automatic email.", "danger")

    return redirect(request.referrer or url_for("admin_contact_messages"))


@app.route("/admin/contact-messages/<mid>/reply", methods=["POST"], endpoint="admin_contact_message_reply")
@login_required(role="admin")
def admin_contact_message_reply(mid):
    try:
        mid_obj = ObjectId(mid)
    except Exception:
        flash("Invalid contact message.", "danger")
        return redirect(url_for("admin_contact_messages"))

    contact_doc = mongo.contact_messages.find_one({"_id": mid_obj})

    if not contact_doc:
        flash("Contact message not found.", "danger")
        return redirect(url_for("admin_contact_messages"))

    to_email = (contact_doc.get("email") or "").strip()
    reply_subject = (request.form.get("reply_subject") or "").strip()
    reply_message = (request.form.get("reply_message") or "").strip()

    if not to_email:
        flash("This contact message has no email address.", "warning")
        return redirect(request.referrer or url_for("admin_contact_messages"))

    if not reply_subject:
        reply_subject = "Reply from NELOCALS Admin"

    if not reply_message:
        flash("Please write a reply message before sending.", "warning")
        return redirect(request.referrer or url_for("admin_contact_messages"))

    safe_name = html.escape(contact_doc.get("name") or "there")
    safe_message = html.escape(reply_message).replace("\n", "<br>")

    email_body = f"""
    <div style="font-family:Arial,sans-serif;line-height:1.6;color:#1F332A;">
      <h2 style="color:#00A859;margin-bottom:8px;">Message from NELOCALS Admin</h2>

      <p>Dear {safe_name},</p>

      <div style="margin:16px 0;padding:14px;border-left:4px solid #00A859;background:#F3FFF8;">
        {safe_message}
      </div>

      <p>
        Thank you,<br>
        <strong>NELOCALS Admin Team</strong>
      </p>
    </div>
    """

    now = datetime.utcnow().isoformat()
    admin_user = current_user() or {}

    try:
        send_email(to_email, reply_subject, email_body)
    except Exception as exc:
        mongo.contact_messages.update_one(
            {"_id": mid_obj},
            {
                "$set": {
                    "manual_reply_error": str(exc),
                    "updated_at": now
                }
            }
        )

        flash(f"Could not send manual email: {exc}", "danger")
        return redirect(request.referrer or url_for("admin_contact_messages"))

    mongo.contact_messages.update_one(
        {"_id": mid_obj},
        {
            "$set": {
                "manual_reply_sent": True,
                "manual_reply_sent_at": now,
                "manual_reply_error": "",
                "last_manual_reply_subject": reply_subject,
                "last_manual_reply_message": reply_message,
                "updated_at": now
            },
            "$push": {
                "reply_logs": {
                    "type": "MANUAL_REPLY",
                    "sent": True,
                    "error": "",
                    "subject": reply_subject,
                    "message": reply_message,
                    "created_at": now,
                    "created_by": str(admin_user.get("_id") or admin_user.get("id") or ""),
                    "created_by_name": admin_user.get("name") or admin_user.get("email") or "Admin"
                }
            }
        }
    )

    flash("Manual email reply sent to user.", "success")
    return redirect(request.referrer or url_for("admin_contact_messages"))




@app.route('/admin/profile', methods=['GET', 'POST'])
@login_required(role='admin')
def admin_profile():
    admin_user = current_user() or {}
    if not admin_user:
        flash("Please log in first.", "warning")
        return redirect(url_for("login"))

    action = (request.form.get("action") or "").strip()
    now = datetime.utcnow().isoformat()

    if request.method == 'POST':
        user_oid = ObjectId(str(admin_user["id"]))

        if action == "profile_details":
            name = (request.form.get("name") or "").strip()
            phone = normalize_phone(request.form.get("phone") or "")

            update_data = {
                "updated_at": now,
                "profile_updated_at": now,
            }
            if name:
                update_data["name"] = name
            if phone:
                update_data["phone"] = phone

            mongo.users.update_one({"_id": user_oid, "role": "admin"}, {"$set": update_data})
            flash("Admin profile updated successfully.", "success")
            return redirect(url_for("admin_profile"))

        if action == "change_email":
            current_password = request.form.get("current_password") or ""
            new_email = (request.form.get("new_email") or "").lower().strip()

            if not check_password_hash(admin_user.get("password_hash", ""), current_password):
                flash("Current password is incorrect. Email was not changed.", "danger")
                return redirect(url_for("admin_profile"))

            if not new_email or "@" not in new_email:
                flash("Please enter a valid new email address.", "warning")
                return redirect(url_for("admin_profile"))

            if new_email == (admin_user.get("email") or "").lower().strip():
                flash("This email is already linked to your admin account.", "info")
                return redirect(url_for("admin_profile"))

            existing = mongo.users.find_one({
                "email": new_email,
                "_id": {"$ne": user_oid}
            })
            if existing:
                flash("This email is already used by another account.", "danger")
                return redirect(url_for("admin_profile"))

            mongo.users.update_one(
                {"_id": user_oid, "role": "admin"},
                {"$set": {
                    "email": new_email,
                    "email_verified": 1,
                    "previous_email": admin_user.get("email", ""),
                    "email_changed_at": now,
                    "updated_at": now,
                }}
            )
            flash("Admin email changed successfully. Use the new email on your next login.", "success")
            return redirect(url_for("admin_profile"))

        if action == "change_password":
            current_password = request.form.get("current_password") or ""
            new_password = request.form.get("new_password") or ""
            confirm_password = request.form.get("confirm_password") or ""

            if not check_password_hash(admin_user.get("password_hash", ""), current_password):
                flash("Current password is incorrect. Password was not changed.", "danger")
                return redirect(url_for("admin_profile"))

            if len(new_password) < 8:
                flash("New password must be at least 8 characters long.", "warning")
                return redirect(url_for("admin_profile"))

            if new_password != confirm_password:
                flash("New password and confirm password do not match.", "warning")
                return redirect(url_for("admin_profile"))

            mongo.users.update_one(
                {"_id": user_oid, "role": "admin"},
                {"$set": {
                    "password_hash": generate_password_hash(new_password),
                    "password_changed_at": now,
                    "updated_at": now,
                }}
            )
            flash("Admin password changed successfully.", "success")
            return redirect(url_for("admin_profile"))

        flash("Invalid profile action.", "warning")
        return redirect(url_for("admin_profile"))

    return render_template(
        "admin_profile.html",
        user=admin_user,
        active_group="account",
        active_page="admin_profile",
    )

