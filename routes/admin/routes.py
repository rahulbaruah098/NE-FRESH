"""Admin routes extracted from the updated app.py.

Logic, decorators, endpoint names and route paths are intentionally preserved.
Only the file location changed.
"""

from app_core import *
from flask import jsonify
import re
from datetime import timedelta

ADMIN_IN_HOUSE_DELIVERY_ENDPOINTS = {
    "admin_delivery_fee_settings",
    "admin_delivery_overview",
    "admin_delivery_history",
    "admin_create_delivery",
    "admin_delivery_list",
    "admin_delivery_reviews",
    "admin_delivery_users",
    "admin_delivery_export_csv",
    "admin_delivery_reviews_export_csv",
}

ADMIN_RETURN_REFUND_ENDPOINTS = {
    "admin_return_refund_policy",
    "admin_refund_processing",
    "admin_refund_admin_review",
    "admin_refund_process",
    "admin_returns_settlements",
    "admin_returns_settlements_export_csv",
}

ADMIN_EXTERNAL_SETTINGS_ENDPOINTS = {
    "admin_external_delivery_settings",
}

ADMIN_THIRD_PARTY_DELIVERY_ENDPOINTS = {
    "admin_external_delivery_orders",
    "admin_external_delivery_book_order",
    "admin_external_delivery_update_status",
}

ADMIN_ONLINE_PAYMENT_ENDPOINTS = {
    "admin_payment_settings",
}

ADMIN_PLATFORM_FEE_OPERATION_ENDPOINTS = {
    "admin_platform_earnings",
    "admin_platform_earnings_export_csv",
}


@app.before_request
def _block_admin_mode_pages_when_disabled():
    endpoint = request.endpoint or ""
    delivery_settings = get_delivery_mode_settings()

    in_house_enabled = bool(delivery_settings.get("in_house_delivery_enabled", True))
    external_local_enabled = bool(delivery_settings.get("external_local_delivery_enabled", False))
    third_party_enabled = bool(delivery_settings.get("third_party_shipping_enabled", False))
    return_refund_enabled = bool(delivery_settings.get("return_refund_enabled", True))
    online_payment_allowed = bool(delivery_settings.get("allow_online_payment", True))

    if endpoint in ADMIN_IN_HOUSE_DELIVERY_ENDPOINTS:
        if in_house_enabled:
            return None

        flash("In-house delivery system is currently disabled and hidden by Admin.", "warning")
        return redirect(url_for("admin_delivery_mode_settings"))

    if endpoint == "admin_users_export_csv" and (request.args.get("role") or "").strip().lower() == "delivery":
        if in_house_enabled:
            return None

        flash("In-house delivery account export is currently disabled and hidden by Admin.", "warning")
        return redirect(url_for("admin_users"))

    if endpoint == "admin_settlement_rider_cash_received":
        if bool(delivery_settings.get("cod_rider_collection_enabled", in_house_enabled)):
            return None

        flash("COD rider cash collection is currently disabled.", "warning")
        return redirect(url_for("admin_settlements"))

    if endpoint in ADMIN_EXTERNAL_SETTINGS_ENDPOINTS:
        if external_local_enabled or third_party_enabled:
            return None

        flash("External delivery settings are hidden because external delivery channels are disabled.", "warning")
        return redirect(url_for("admin_delivery_mode_settings"))

    if endpoint in ADMIN_THIRD_PARTY_DELIVERY_ENDPOINTS:
        if external_local_enabled or third_party_enabled:
            return None

        flash("External delivery orders are hidden because External Local Delivery and Third-Party Shipping are both disabled.", "warning")
        return redirect(url_for("admin_delivery_mode_settings"))

    if endpoint in ADMIN_ONLINE_PAYMENT_ENDPOINTS:
        if online_payment_allowed:
            return None

        flash("Online payment gateway settings are hidden because Online Payment is disabled in Delivery Operation Settings.", "warning")
        return redirect(url_for("admin_delivery_mode_settings"))

    if endpoint in ADMIN_PLATFORM_FEE_OPERATION_ENDPOINTS:
        try:
            platform_fee_enabled = bool(get_platform_fee_settings().get("enabled", False))
        except Exception:
            platform_fee_enabled = False

        if platform_fee_enabled:
            return None

        flash("Platform fee earning pages are hidden because Platform Fee is disabled.", "warning")
        return redirect(url_for("admin_platform_fee_settings"))

    if endpoint in ADMIN_RETURN_REFUND_ENDPOINTS:
        if return_refund_enabled:
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





def _admin_redirect_back(default_endpoint="admin_dashboard"):
    referrer = request.referrer or ""
    try:
        if referrer and referrer.startswith(request.host_url):
            return redirect(referrer)
    except Exception:
        pass
    return redirect(url_for(default_endpoint))


def _admin_save_in_house_delivery_operation(enable_in_house):
    """
    One-click Admin control for showing/hiding the in-house delivery system.
    It reuses the existing delivery mode settings so checkout and protected
    delivery pages stay consistent with what Admin sees in the UI.
    """
    existing_settings = get_delivery_mode_settings()
    admin_user = current_user() or {}

    allow_online_payment = bool(existing_settings.get("allow_online_payment", True))
    allow_pay_online_on_delivery = bool(existing_settings.get("allow_cod_payment", False))

    if not allow_online_payment and not allow_pay_online_on_delivery:
        allow_online_payment = True

    if allow_online_payment and allow_pay_online_on_delivery:
        delivery_payment_methods = DELIVERY_PAYMENT_ONLINE_AND_COD
    elif allow_pay_online_on_delivery:
        delivery_payment_methods = DELIVERY_PAYMENT_COD_ONLY
    else:
        delivery_payment_methods = DELIVERY_PAYMENT_ONLINE_ONLY

    if enable_in_house:
        operation_mode = DELIVERY_OPERATION_IN_HOUSE_ONLY
        routing_mode = DELIVERY_ROUTING_MODE_MANUAL
        active_delivery_mode = DELIVERY_MODE_IN_HOUSE
        in_house_enabled = True
        external_local_enabled = False
        third_party_enabled = False
    else:
        operation_mode = DELIVERY_OPERATION_EXTERNAL_CONNECTED
        routing_mode = DELIVERY_ROUTING_MODE_AUTO
        active_delivery_mode = DELIVERY_MODE_EXTERNAL_LOCAL
        in_house_enabled = False
        external_local_enabled = True
        third_party_enabled = bool(existing_settings.get("third_party_shipping_enabled", True))

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
        "return_refund_enabled": bool(existing_settings.get("return_refund_enabled", True)),
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
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )


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

    settlement_payment_method = (order.get("payment_method") or "").strip().upper()
    settlement_payment_status = (order.get("payment_status") or "").strip().upper()
    settlement_collection_status = (order.get("payment_collection_status") or "").strip().upper()
    settlement_collection_channel = (order.get("payment_collection_channel") or "").strip().upper()
    settlement_upi_reconciliation = (order.get("upi_delivery_reconciliation_status") or "").strip().upper()

    settlement_cod_methods = {
        "COD",
        "CASH_ON_DELIVERY",
        "COD_RIDER_COLLECTION"
    }
    settlement_collected_statuses = {
        "PAID",
        "COLLECTED",
        "COLLECTED_BY_RIDER",
        "COD_COLLECTED_BY_RIDER",
        "COD_UPI_RECORDED",
        "COLLECTED_BY_STORE",
        "COLLECTED_BY_EXTERNAL_PARTNER"
    }

    settlement_is_cod = settlement_payment_method in settlement_cod_methods
    settlement_is_cod_collected = bool(
        settlement_is_cod
        and (
            settlement_payment_status in settlement_collected_statuses
            or settlement_collection_status in {"COLLECTED", "PAID"}
        )
    )

    order["cod_collected_amount"] = (
        _admin_settlement_money(
            order.get("cod_collected_amount"),
            order["total_payable"]
        )
        if settlement_is_cod_collected
        else 0.0
    )

    order["rider_cash_to_submit"] = (
        _admin_settlement_money(
            order.get("rider_cash_to_submit"),
            order.get("expected_rider_cash_to_submit") or 0
        )
        if settlement_is_cod_collected
        else 0.0
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
    order["payment_collection_status"] = settlement_collection_status
    order["payment_collection_channel"] = settlement_collection_channel
    order["upi_delivery_reconciliation_status"] = settlement_upi_reconciliation
    order["collection_channel_label"] = (
        "UPI" if settlement_collection_channel == "UPI"
        else ("Cash" if settlement_is_cod else ("Razorpay" if settlement_collection_channel == "RAZORPAY" else "Online"))
    )
    order["rider_cash_settlement_status"] = (order.get("rider_cash_settlement_status") or "").upper()
    order["platform_fee_status"] = (order.get("platform_fee_status") or "").upper()
    order["store_payout_status"] = (order.get("store_payout_status") or "").upper()
    order["order_settlement_status"] = (order.get("order_settlement_status") or "").upper()

    finance_state = finance_reconciliation_snapshot(order)
    order["finance_reconciliation"] = finance_state
    order["customer_payment_reconciled"] = bool(finance_state.get("customer_payment_reconciled"))
    order["payment_reconciliation_status"] = finance_state.get("payment_reconciliation_status") or order.get("payment_reconciliation_status") or ""
    order["payment_receiver_label"] = finance_state.get("payment_receiver_label") or ""
    order["payment_collection_label"] = finance_state.get("collection_label") or ""
    order["platform_fee_reconciliation_status"] = finance_state.get("platform_fee_reconciliation_status") or order.get("platform_fee_status") or ""
    order["business_reconciliation_complete"] = bool(finance_state.get("business_reconciliation_complete"))
    order["net_platform_fee"] = _admin_settlement_money(finance_state.get("net_platform_fee"), order.get("platform_fee") or 0)
    order["store_payout_required"] = bool(finance_state.get("store_payout_required"))
    order["store_payout_eligible"] = bool(finance_state.get("store_payout_eligible"))
    order["store_payout_block_reason"] = finance_state.get("store_payout_block_reason") or ""
    order["refund_unresolved"] = bool(finance_state.get("refund_unresolved"))

    outstanding_adjustment = finance_store_outstanding_adjustment_total(order.get("store_id"))
    carry_preview = round(min(outstanding_adjustment, adjusted_store_payout), 2)
    order["store_carry_forward_adjustment_outstanding"] = outstanding_adjustment
    order["store_carry_forward_adjustment_preview"] = carry_preview
    order["final_store_payout_preview"] = round(max(adjusted_store_payout - carry_preview, 0), 2)

    if not order["store_payout_required"] and order["customer_payment_reconciled"]:
        order["store_payout_status"] = "NOT_REQUIRED"
        order["store_payout_amount"] = 0.0

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
    - excludes direct Admin complaints saved with store_name = NE Locals Admin
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

    c["display_order_number"] = str(c.get("order_id") or "").strip()


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
        c["assigned_label"] = "NE Locals Admin"
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
    gross_platform_fee = _admin_settlement_money(row.get("platform_fee"), 0)
    net_platform_fee_after_refund = round(max(gross_platform_fee - refund_platform_fee, 0), 2)
    prior_platform_fee_status = (row.get("platform_fee_status") or "").strip().upper()

    if net_platform_fee_after_refund <= 0:
        next_platform_fee_status = "ADJUSTED" if gross_platform_fee > 0 else "NOT_REQUIRED"
    elif prior_platform_fee_status == "RECEIVED":
        # Admin already received the fee and only the refunded component is reversed.
        # The remaining net fee is still reconciled.
        next_platform_fee_status = "RECEIVED"
    else:
        # Preserve the original reconciliation path (Store due, rider cash pending,
        # UPI pending, partner remittance pending, etc.) while reducing only the
        # amount that remains economically earned.
        next_platform_fee_status = prior_platform_fee_status or "PENDING_PAYMENT_RECONCILIATION"

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
    finance_state = finance_reconciliation_snapshot(row)
    store_already_received_order_money = bool(
        store_payout_status == "PAID"
        or (
            not finance_state.get("store_payout_required")
            and finance_state.get("customer_payment_reconciled")
        )
    )

    if is_cancel_refund:
        adjusted_store_payout = 0.0
        store_adjustment_due = 0.0
        settlement_impact = "CANCEL_REFUND_NO_STORE_PAYOUT"
        next_store_payout_status = "NOT_REQUIRED"
    elif store_already_received_order_money:
        # The Store has already received its earning (Admin payout already paid or
        # customer paid Store directly). Recover the Store-funded refund from a
        # future Store payout through the carry-forward adjustment ledger.
        adjusted_store_payout = store_payout_amount
        store_adjustment_due = store_refund_deduction
        settlement_impact = "ADJUST_FROM_NEXT_PAYOUT" if store_adjustment_due > 0 else "NO_ADJUSTMENT"
        next_store_payout_status = store_payout_status or "NOT_REQUIRED"
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
        "platform_fee_status": next_platform_fee_status,
        "net_platform_fee_after_refund": net_platform_fee_after_refund,

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


def _admin_parse_delivery_zone_polygon(raw):
    """
    Normalize saved delivery zone data into Leaflet-ready [[lat, lng], ...].

    Supports the current list format, JSON strings, point objects and
    GeoJSON-style Polygon / Feature data. This keeps old saved store zones
    loadable in Admin edit without changing the database format.
    """
    def _as_float(value, min_value, max_value):
        try:
            if value is None or str(value).strip() == "":
                return None
            number = float(value)
            if number < min_value or number > max_value:
                return None
            return number
        except Exception:
            return None

    def _point_from_pair(pair, geojson=False):
        if not isinstance(pair, (list, tuple)) or len(pair) < 2:
            return None

        if geojson:
            lat = _as_float(pair[1], -90, 90)
            lng = _as_float(pair[0], -180, 180)
        else:
            lat = _as_float(pair[0], -90, 90)
            lng = _as_float(pair[1], -180, 180)

        if lat is None or lng is None:
            return None

        return [round(float(lat), 6), round(float(lng), 6)]

    def _point_from_dict(point):
        if not isinstance(point, dict):
            return None

        lat = (
            point.get("lat")
            if point.get("lat") is not None
            else point.get("latitude")
            if point.get("latitude") is not None
            else point.get("y")
        )
        lng = (
            point.get("lng")
            if point.get("lng") is not None
            else point.get("lon")
            if point.get("lon") is not None
            else point.get("longitude")
            if point.get("longitude") is not None
            else point.get("x")
        )

        lat = _as_float(lat, -90, 90)
        lng = _as_float(lng, -180, 180)

        if lat is not None and lng is not None:
            return [round(float(lat), 6), round(float(lng), 6)]

        coords = point.get("coordinates")
        if isinstance(coords, (list, tuple)) and len(coords) >= 2:
            return _point_from_pair(coords, geojson=True)

        return None

    def _walk(data, geojson=False, depth=0):
        if depth > 8:
            return []

        if data is None:
            return []

        if isinstance(data, str):
            clean = data.strip()
            if not clean:
                return []
            try:
                return _walk(json.loads(clean), geojson=geojson, depth=depth + 1)
            except Exception:
                return []

        if isinstance(data, dict):
            direct_point = _point_from_dict(data)
            if direct_point:
                return [direct_point]

            dtype = str(data.get("type") or "").strip().lower()

            if dtype == "feature":
                return _walk(data.get("geometry"), geojson=True, depth=depth + 1)

            if dtype == "featurecollection":
                for feature in data.get("features") or []:
                    points = _walk(feature, geojson=True, depth=depth + 1)
                    if len(points) >= 3:
                        return points
                return []

            if dtype == "polygon":
                coordinates = data.get("coordinates") or []
                if coordinates and isinstance(coordinates, list):
                    return _walk(coordinates[0], geojson=True, depth=depth + 1)

            if dtype == "multipolygon":
                coordinates = data.get("coordinates") or []
                if coordinates and isinstance(coordinates, list) and coordinates[0]:
                    return _walk(coordinates[0][0], geojson=True, depth=depth + 1)

            for key in [
                "delivery_zone_polygon",
                "delivery_zone",
                "zone_polygon",
                "service_area_polygon",
                "delivery_area_polygon",
                "polygon",
                "points",
                "latlngs",
                "lat_lngs",
                "coordinates",
            ]:
                if key in data:
                    points = _walk(data.get(key), geojson=(key == "coordinates"), depth=depth + 1)
                    if points:
                        return points

            return []

        if isinstance(data, (list, tuple)):
            direct_point = _point_from_pair(data, geojson=geojson)
            if direct_point and not any(isinstance(item, (list, tuple, dict)) for item in data[:2]):
                return [direct_point]

            cleaned = []
            for item in data:
                if isinstance(item, dict):
                    point = _point_from_dict(item)
                    if point:
                        cleaned.append(point)
                        continue

                    nested = _walk(item, geojson=geojson, depth=depth + 1)
                    if nested:
                        cleaned.extend(nested)
                        continue

                if isinstance(item, (list, tuple)):
                    point = _point_from_pair(item, geojson=geojson)
                    if point:
                        cleaned.append(point)
                        continue

                    nested = _walk(item, geojson=geojson, depth=depth + 1)
                    if nested:
                        cleaned.extend(nested)

            return cleaned

        return []

    cleaned = _walk(raw)

    if len(cleaned) > 1 and cleaned[0] == cleaned[-1]:
        cleaned = cleaned[:-1]

    unique_cleaned = []
    for point in cleaned:
        if point not in unique_cleaned:
            unique_cleaned.append(point)

    if len(unique_cleaned) < 3:
        return []

    return unique_cleaned


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


def _admin_delivery_monthly_rows():
    current_period = delivery_monthly_current_period()

    raw_orders = list(mongo.orders.find({
        "status": "DELIVERED",
        "delivery_payout_model": DELIVERY_PAYOUT_MODEL_MONTHLY_V1,
        "delivery_partner_id": {"$ne": None}
    }).sort("delivered_at", -1))

    paid_batches = list(
        mongo.delivery_partner_monthly_settlements.find({}).sort("period", -1)
    )
    batch_map = {
        (str(doc.get("delivery_partner_id_str") or ""), str(doc.get("period") or "")): doc
        for doc in paid_batches
    }

    groups = {}

    for raw in raw_orders:
        row = _admin_hydrate_settlement_order(raw)
        rider_id = str(row.get("delivery_partner_id") or "").strip()
        if not rider_id:
            continue

        period = (row.get("delivery_monthly_period") or "").strip()
        if not period:
            period = delivery_monthly_period_from_utc(row.get("delivered_at") or row.get("updated_at"))

        key = (rider_id, period)
        group = groups.setdefault(key, {
            "delivery_partner_id": rider_id,
            "delivery_partner_name": row.get("delivery_partner_name") or "Delivery Partner",
            "delivery_partner_phone": row.get("delivery_partner_phone") or "",
            "period": period,
            "period_label": delivery_monthly_period_label(period),
            "order_count": 0,
            "delivery_fee": 0.0,
            "tips": 0.0,
            "gross_earning": 0.0,
            "cash_pending_count": 0,
            "cash_pending_amount": 0.0,
            "upi_pending_count": 0,
            "unreconciled_count": 0,
        })

        fee = _admin_settlement_money(
            row.get("delivery_fee_amount") if row.get("delivery_fee_amount") is not None else row.get("delivery_fee")
        )
        tip = _admin_settlement_money(
            row.get("tip_amount") if row.get("tip_amount") is not None else row.get("delivery_tip_amount")
        )
        earning = _admin_settlement_money(
            row.get("delivery_boy_payout_amount") if row.get("delivery_boy_payout_amount") is not None else row.get("delivery_boy_earning"),
            fee + tip
        )

        group["order_count"] += 1
        group["delivery_fee"] += fee
        group["tips"] += tip
        group["gross_earning"] += earning

        if not delivery_monthly_payment_is_reconciled(row):
            group["unreconciled_count"] += 1
            if (row.get("payment_method") or "").upper() == "COD":
                if (row.get("payment_collection_channel") or "").upper() == "UPI":
                    group["upi_pending_count"] += 1
                else:
                    group["cash_pending_count"] += 1
                    group["cash_pending_amount"] += _admin_settlement_money(row.get("rider_cash_to_submit"))

    rows = []
    for key, group in groups.items():
        rider_id, period = key
        batch = batch_map.get(key) or {}
        batch_status = (batch.get("status") or "").upper()
        is_paid = batch_status == DELIVERY_MONTHLY_BATCH_STATUS_PAID
        is_current = period == current_period
        is_closed = delivery_monthly_period_is_closed(period)

        if is_paid:
            display_status = "PAID"
        elif is_current:
            display_status = "ACCRUING"
        elif group["unreconciled_count"] > 0:
            display_status = "PAYMENT_RECONCILIATION_PENDING"
        elif is_closed:
            display_status = "READY"
        else:
            display_status = "ACCRUING"

        group["delivery_fee"] = round(group["delivery_fee"], 2)
        group["tips"] = round(group["tips"], 2)
        group["gross_earning"] = round(group["gross_earning"], 2)
        group["cash_pending_amount"] = round(group["cash_pending_amount"], 2)
        group["status"] = display_status
        group["is_current"] = is_current
        group["is_closed"] = is_closed
        group["can_pay"] = bool(is_closed and not is_paid and group["unreconciled_count"] == 0)
        group["paid_at"] = batch.get("paid_at") or ""
        group["payment_mode"] = batch.get("payment_mode") or ""
        group["reference_no"] = batch.get("reference_no") or ""
        group["paid_amount"] = _admin_settlement_money(batch.get("amount_paid"))
        rows.append(group)

    rows.sort(key=lambda row: (row.get("period") or "", row.get("delivery_partner_name") or ""), reverse=True)

    metrics = {
        "current_period": current_period,
        "current_period_label": delivery_monthly_period_label(current_period),
        "current_accrued_amount": round(sum(
            float(row.get("gross_earning") or 0)
            for row in rows
            if row.get("period") == current_period and row.get("status") != "PAID"
        ), 2),
        "ready_amount": round(sum(
            float(row.get("gross_earning") or 0)
            for row in rows
            if row.get("can_pay")
        ), 2),
        "ready_count": sum(1 for row in rows if row.get("can_pay")),
        "unreconciled_count": sum(int(row.get("unreconciled_count") or 0) for row in rows if row.get("status") != "PAID"),
        "paid_amount": round(sum(
            float(row.get("paid_amount") or 0)
            for row in rows
            if row.get("status") == "PAID"
        ), 2),
    }

    return rows, metrics


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
    event = {
        "action": "STORE_PLATFORM_FEE_RECEIVED_BY_ADMIN",
        "order_id": str(oid_obj),
        "amount_received": net_platform_fee,
        "payment_mode": payment_mode,
        "reference_no": reference,
        "note": note,
        "created_by": str(admin_user.get("id") or admin_user.get("_id") or ""),
        "created_by_name": admin_user.get("name") or admin_user.get("email") or "Admin",
        "created_by_role": "admin",
        "created_at": now,
    }
    update_data = {
        "platform_fee_status": "RECEIVED",
        "platform_fee_received_at": now,
        "platform_fee_received_amount": net_platform_fee,
        "platform_fee_received_reference": reference,
        "platform_fee_received_mode": payment_mode,
        "admin_platform_fee_status": "RECEIVED",
        "store_platform_fee_remittance_status": "RECEIVED",
        "store_platform_fee_remitted_at": now,
        "store_platform_fee_payment_mode": payment_mode,
        "store_platform_fee_reference": reference,
        "store_payout_status": "NOT_REQUIRED",
        "store_settlement_status": "DIRECT_COLLECTION_RECONCILED",
        "order_settlement_status": "BUSINESS_RECONCILED",
        "settlement_status": "BUSINESS_RECONCILED",
        "updated_at": now,
    }

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
        {"$set": {**update_data, "status": "PAID"}}
    )
    mongo.order_events.insert_one({
        "order_id": oid_obj,
        "status": "STORE_PLATFORM_FEE_RECEIVED",
        "note": (
            f"Admin received Store-remitted Platform Fee ₹{net_platform_fee:.2f} "
            f"via {payment_mode}. Reference: {reference or '-'}."
        ),
        "created_at": now,
    })
    flash("Store Platform Fee remittance received. Business reconciliation is complete for this order.", "success")
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

    now = datetime.utcnow().isoformat()
    reference = (request.form.get("reference_no") or "").strip()[:120]
    note = (request.form.get("note") or "").strip()[:250]
    event = {
        "action": "STORE_PLATFORM_FEE_RECEIVED_BY_ADMIN",
        "order_id": str(oid_obj),
        "amount_received": net_platform_fee,
        "reference_no": reference,
        "note": note,
        "created_by": str(admin_user.get("id") or admin_user.get("_id") or ""),
        "created_by_name": admin_user.get("name") or admin_user.get("email") or "Admin",
        "created_by_role": "admin",
        "created_at": now,
    }
    update_data = {
        "platform_fee_status": "RECEIVED",
        "platform_fee_received_at": now,
        "platform_fee_received_amount": net_platform_fee,
        "platform_fee_received_reference": reference,
        "admin_platform_fee_status": "RECEIVED",
        "store_platform_fee_remittance_status": "RECEIVED",
        "store_platform_fee_remitted_at": now,
        "store_platform_fee_reference": reference,
        "store_payout_status": "NOT_REQUIRED",
        "store_settlement_status": "DIRECT_COLLECTION_RECONCILED",
        "order_settlement_status": "BUSINESS_RECONCILED",
        "settlement_status": "BUSINESS_RECONCILED",
        "updated_at": now,
    }
    mongo.orders.update_one({"_id": oid_obj}, {"$set": update_data, "$push": {"settlement_audit_logs": event}})
    mongo.transactions.update_many({"order_id": oid_obj}, {"$set": {**update_data, "status": "PAID"}})
    mongo.order_events.insert_one({
        "order_id": oid_obj,
        "status": "STORE_PLATFORM_FEE_RECEIVED",
        "note": f"Admin received Store-remitted Platform Fee ₹{net_platform_fee:.2f}.",
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

    gross_amount = round(sum(
        _admin_settlement_money(
            row.get("delivery_boy_payout_amount") if row.get("delivery_boy_payout_amount") is not None else row.get("delivery_boy_earning"),
            _admin_settlement_money(row.get("delivery_fee")) + _admin_settlement_money(row.get("tip_amount"))
        )
        for row in hydrated
    ), 2)

    if gross_amount < 0:
        gross_amount = 0.0

    now = datetime.utcnow().isoformat()
    rider_name = next((row.get("delivery_partner_name") for row in hydrated if row.get("delivery_partner_name")), "Delivery Partner")
    rider_phone = next((row.get("delivery_partner_phone") for row in hydrated if row.get("delivery_partner_phone")), "")
    order_ids = [str(row.get("_id")) for row in raw_orders]

    batch_doc = {
        "delivery_partner_id_str": rider_id,
        "delivery_partner_name": rider_name,
        "delivery_partner_phone": rider_phone,
        "period": period,
        "period_label": delivery_monthly_period_label(period),
        "status": DELIVERY_MONTHLY_BATCH_STATUS_PAID,
        "order_count": len(raw_orders),
        "order_ids": order_ids,
        "gross_earning": gross_amount,
        "amount_paid": gross_amount,
        "payment_mode": payout_mode,
        "reference_no": reference_no,
        "note": note,
        "paid_at": now,
        "paid_by": str(admin_user.get("id") or admin_user.get("_id") or ""),
        "paid_by_name": admin_user.get("name") or admin_user.get("email") or "Admin",
        "updated_at": now,
    }

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
    platform_fee = _admin_settlement_money(order.get("platform_fee"))
    store_payout_amount = _admin_settlement_money(order.get("store_payout_amount"))
    amount_received = _admin_settlement_money(
        order.get("cod_collected_amount"),
        order.get("total_payable") or order.get("total_amount") or 0
    )

    settlement_event = {
        "action": "UPI_AT_DELIVERY_VERIFIED_BY_ADMIN",
        "order_id": str(oid_obj),
        "amount_received": amount_received,
        "payment_mode": "UPI",
        "reference_no": reference,
        "upi_reference": reference,
        "settlement_impact": "UPI_VERIFIED_STORE_PAYOUT_UNLOCKED",
        "platform_fee": platform_fee,
        "store_payout_amount": store_payout_amount,
        "created_by": str(admin_user.get("id") or admin_user.get("_id") or ""),
        "created_by_name": admin_user.get("name") or admin_user.get("email") or "Admin",
        "created_by_role": "admin",
        "note": note,
        "created_at": now
    }

    update_data = {
        "payment_status": "PAID",
        "payment_collection_status": "PAID",
        "payment_reconciliation_status": "VERIFIED",
        "cod_collection_status": "UPI_VERIFIED",
        "upi_delivery_reconciliation_status": "VERIFIED",
        "upi_delivery_verified_at": now,
        "upi_delivery_verified_by": str(admin_user.get("id") or admin_user.get("_id") or ""),
        "upi_delivery_verified_by_name": admin_user.get("name") or admin_user.get("email") or "Admin",
        "upi_delivery_reconciliation_note": note,
        "platform_fee_status": "RECEIVED",
        "platform_fee_received_at": now,
        "admin_platform_fee_status": "RECEIVED",
        "rider_cash_settlement_status": "NOT_REQUIRED",
        "rider_cash_to_submit": 0.0,
        "expected_rider_cash_to_submit": 0.0,
        "store_payout_status": "PENDING_AFTER_DELIVERY",
        "store_settlement_status": "PAYOUT_PENDING",
        "order_settlement_status": "STORE_PAYOUT_PENDING",
        "settlement_status": "STORE_PAYOUT_PENDING",
        "last_settlement_event": settlement_event,
        "updated_at": now
    }

    mongo.orders.update_one(
        {"_id": oid_obj},
        {"$set": update_data, "$push": {"settlement_audit_logs": settlement_event}}
    )

    mongo.transactions.update_many(
        {"order_id": oid_obj},
        {"$set": {
            "status": "PAID",
            "amount": amount_received,
            "payment_status": "PAID",
            "payment_received_by": "ADMIN_PLATFORM",
            "payment_collection_status": "PAID",
            "payment_reconciliation_status": "VERIFIED",
            "payment_collection_channel": "UPI",
            "cod_collection_status": "UPI_VERIFIED",
            "upi_delivery_reference": reference,
            "upi_delivery_reconciliation_status": "VERIFIED",
            "upi_delivery_verified_at": now,
            "platform_fee_status": "RECEIVED",
            "platform_fee_received_at": now,
            "admin_platform_fee_status": "RECEIVED",
            "rider_cash_settlement_status": "NOT_REQUIRED",
            "rider_cash_to_submit": 0.0,
            "expected_rider_cash_to_submit": 0.0,
            "store_payout_status": "PENDING_AFTER_DELIVERY",
            "store_settlement_status": "PAYOUT_PENDING",
            "order_settlement_status": "STORE_PAYOUT_PENDING",
            "settlement_status": "STORE_PAYOUT_PENDING",
            "updated_at": now
        }}
    )

    mongo.order_events.insert_one({
        "order_id": oid_obj,
        "status": "UPI_AT_DELIVERY_VERIFIED",
        "note": (
            f"Admin verified UPI payment ₹{amount_received:.2f} received by NE FRESH. "
            f"Reference {reference}. Store payout is now pending."
        ),
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
        max(original_store_payout_amount - store_refund_deduction, 0)
    )
    store_adjustment_due = _admin_settlement_money(order.get("store_adjustment_due"), 0)
    settlement_impact = order.get("settlement_impact") or (
        "DEDUCT_FROM_PENDING_PAYOUT" if store_refund_deduction > 0 else "NO_DEDUCTION"
    )

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


def _admin_platform_earning_row(order):
    row = _admin_hydrate_settlement_order(dict(order or {}))
    state = row.get("finance_reconciliation") or finance_reconciliation_snapshot(row)
    net_fee = _admin_settlement_money(state.get("net_platform_fee"), row.get("platform_fee") or 0)
    row["gross_platform_fee"] = _admin_settlement_money(row.get("platform_fee"), 0)
    row["refund_platform_fee"] = _admin_settlement_money(state.get("refund_platform_fee"), 0)
    row["net_platform_fee"] = net_fee
    row["platform_fee"] = net_fee
    row["platform_earning_status"] = state.get("platform_fee_reconciliation_status") or row.get("platform_fee_status") or "PENDING"
    row["payment_receiver_label"] = state.get("payment_receiver_label") or ""
    row["payment_collection_label"] = state.get("collection_label") or ""
    row["customer_payment_reconciled"] = bool(state.get("customer_payment_reconciled"))
    row["report_date"] = str(row.get("platform_fee_received_at") or row.get("delivered_at") or row.get("payment_collected_at") or row.get("created_at") or "")
    return row


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


def _admin_settlement_action_label(action):
    action = (action or "").strip().upper()
    labels = {
        "UPI_AT_DELIVERY_VERIFIED_BY_ADMIN": "UPI at Delivery Verified",
        "RIDER_CASH_RECEIVED_BY_ADMIN": "Rider Cash Received",
        "DELIVERY_PARTNER_MONTHLY_SETTLEMENT_PAID": "Monthly Delivery Partner Paid",
        "STORE_PAYOUT_PAID_BY_ADMIN": "Store Payout Paid",
        "REFUND_PROCESSED_BY_ADMIN": "Refund Processed",
        "STORE_PLATFORM_FEE_RECEIVED_BY_ADMIN": "Store Platform Fee Received",
        "EXTERNAL_PARTNER_REMITTANCE_RECEIVED": "External Partner Remittance Received",
        "STORE_CUSTOMER_PAYMENT_RECORDED": "Store Customer Payment Recorded",
        "STORE_REFUND_ADJUSTMENT_CREATED": "Store Refund Carry-forward Created",
    }
    return labels.get(action) or (action.replace("_", " ").title() if action else "Settlement Event")


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
    platform_fee_settings = get_platform_fee_settings()

    online_payment_allowed = bool(delivery_mode_settings.get("allow_online_payment", True))
    platform_fee_enabled = bool(platform_fee_settings.get("enabled", False))

    quick_links = [
        {"label": "Store Overview", "endpoint": "admin_store_overview"},
        {"label": "All Store Admin Profiles", "endpoint": "admin_store_list"},
        {"label": "Customers", "endpoint": "admin_customers"},
        {"label": "Customer Complaints", "endpoint": "admin_complaints"},
        {"label": "Store Payouts", "endpoint": "admin_settlements"},
        {"label": "Delivery Routing Settings", "endpoint": "admin_delivery_mode_settings"},
    ]

    if online_payment_allowed:
        quick_links.append({"label": "Online Payment Gateway", "endpoint": "admin_payment_settings"})

    if platform_fee_enabled:
        quick_links.append({"label": "Platform Fee Earnings", "endpoint": "admin_platform_earnings"})

    if delivery_mode_settings.get("return_refund_enabled", True):
        quick_links.extend([
            {"label": "Customer Refund Processing", "endpoint": "admin_refund_processing"},
            {"label": "Return / Refund Settlement Impact", "endpoint": "admin_returns_settlements"},
        ])

    if delivery_mode_settings.get("in_house_delivery_enabled", True):
        quick_links.extend([
            {"label": "In-house Delivery Overview", "endpoint": "admin_delivery_overview"},
            {"label": "Create In-house Delivery Staff", "endpoint": "admin_create_delivery"},
        ])

    if delivery_mode_settings.get("external_local_delivery_enabled", False) or delivery_mode_settings.get("third_party_shipping_enabled", False):
        quick_links.append({"label": "External Delivery Settings", "endpoint": "admin_external_delivery_settings"})

    if delivery_mode_settings.get("third_party_shipping_enabled", False):
        quick_links.append({"label": "Shiprocket / Courier Orders", "endpoint": "admin_external_delivery_orders"})

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
        delivery_mode_settings=delivery_mode_settings,
        delivery_mode_ui=delivery_mode_ui,
        online_payment_allowed=online_payment_allowed,
        platform_fee_enabled=platform_fee_enabled,
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

        if latitude is None or longitude is None:
            flash("Store pickup latitude and longitude are required.", "warning")
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
        created_user_id = None

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
            created_user_id = result.inserted_id

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

            if created_user_id:
                try:
                    mongo.users.delete_one({"_id": created_user_id})
                except Exception:
                    pass

            flash(
                "Email or phone already exists. Please use different details.",
                "danger"
            )

            return redirect(url_for('admin_create_store'))

        except Exception as e:

            if created_user_id:
                try:
                    mongo.users.delete_one({"_id": created_user_id})
                except Exception:
                    pass

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

def _admin_store_overview_money(value, default=0.0):
    try:
        if value is None or str(value).strip() == "":
            return float(default)
        return round(float(value), 2)
    except Exception:
        return float(default)


def _admin_store_overview_dt(value):
    if not value:
        return None

    if isinstance(value, datetime):
        return value

    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)

    clean = str(value).strip()

    if not clean:
        return None

    if clean.endswith("Z"):
        clean = clean[:-1]

    try:
        return datetime.fromisoformat(clean)
    except Exception:
        pass

    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
        try:
            return datetime.strptime(clean, fmt)
        except Exception:
            continue

    return None


def _admin_store_overview_date_filter():
    raw_range = (request.args.get("range") or "all").strip().lower()
    allowed_ranges = {"all", "today", "week", "month", "last30", "custom"}

    if raw_range not in allowed_ranges:
        raw_range = "all"

    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    start_dt = None
    end_dt = None
    start_value = ""
    end_value = ""
    date_label = "All-time records"

    if raw_range == "today":
        start_dt = today
        end_dt = today + timedelta(days=1)
        start_value = today.strftime("%Y-%m-%d")
        end_value = today.strftime("%Y-%m-%d")
        date_label = "Today"

    elif raw_range == "week":
        start_dt = today - timedelta(days=today.weekday())
        end_dt = today + timedelta(days=1)
        start_value = start_dt.strftime("%Y-%m-%d")
        end_value = today.strftime("%Y-%m-%d")
        date_label = "This week"

    elif raw_range == "month":
        start_dt = today.replace(day=1)
        end_dt = today + timedelta(days=1)
        start_value = start_dt.strftime("%Y-%m-%d")
        end_value = today.strftime("%Y-%m-%d")
        date_label = "This month"

    elif raw_range == "last30":
        start_dt = today - timedelta(days=29)
        end_dt = today + timedelta(days=1)
        start_value = start_dt.strftime("%Y-%m-%d")
        end_value = today.strftime("%Y-%m-%d")
        date_label = "Last 30 days"

    elif raw_range == "custom":
        start_value = (request.args.get("start_date") or "").strip()
        end_value = (request.args.get("end_date") or "").strip()

        parsed_start = _admin_store_overview_dt(start_value)
        parsed_end = _admin_store_overview_dt(end_value)

        if parsed_start:
            start_dt = parsed_start.replace(hour=0, minute=0, second=0, microsecond=0)

        if parsed_end:
            end_dt = parsed_end.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)

        if start_dt and end_dt and start_dt >= end_dt:
            start_dt = None
            end_dt = None
            start_value = ""
            end_value = ""
            raw_range = "all"
            date_label = "All-time records"
        elif start_dt and end_dt:
            date_label = f"{start_value} to {end_value}"
        elif start_dt:
            date_label = f"From {start_value}"
        elif end_dt:
            date_label = f"Until {end_value}"
        else:
            raw_range = "all"
            date_label = "All-time records"

    filters = {
        "range": raw_range,
        "start_date": start_value,
        "end_date": end_value,
        "q": (request.args.get("q") or "").strip(),
        "status": (request.args.get("status") or "all").strip().lower(),
        "date_label": date_label,
        "new_store_label": "Created in selected date range" if raw_range != "all" else "Created in the last 30 days",
    }

    if filters["status"] not in {"all", "active", "inactive", "online", "offline", "delivery_on", "delivery_off"}:
        filters["status"] = "all"

    return filters, start_dt, end_dt


def _admin_store_overview_in_date_range(value, start_dt=None, end_dt=None):
    if not start_dt and not end_dt:
        return True

    parsed = _admin_store_overview_dt(value)

    if not parsed:
        return False

    if start_dt and parsed < start_dt:
        return False

    if end_dt and parsed >= end_dt:
        return False

    return True


def _admin_store_overview_store_matches(row, filters):
    q = (filters.get("q") or "").strip().lower()
    status = (filters.get("status") or "all").strip().lower()

    if q:
        haystack = " ".join([
            str(row.get("store_name") or ""),
            str(row.get("owner_name") or ""),
            str(row.get("owner_email") or ""),
            str(row.get("owner_phone") or ""),
            str(row.get("city") or ""),
            str(row.get("state") or ""),
            str(row.get("pincode") or ""),
            str(row.get("id") or ""),
        ]).lower()

        if q not in haystack:
            return False

    is_active = int(row.get("is_active") or 0) == 1
    is_online = int(row.get("is_online", row.get("is_open", 0)) or 0) == 1
    delivery_on = int(row.get("delivery_enabled", 1 if row.get("delivery_available", True) else 0) or 0) == 1

    if status == "active" and not is_active:
        return False

    if status == "inactive" and is_active:
        return False

    if status == "online" and not is_online:
        return False

    if status == "offline" and is_online:
        return False

    if status == "delivery_on" and not delivery_on:
        return False

    if status == "delivery_off" and delivery_on:
        return False

    return True


def _admin_store_overview_order_total(order):
    return _admin_store_overview_money(
        order.get("total_payable"),
        _admin_store_overview_money(
            order.get("total_amount"),
            (
                _admin_store_overview_money(order.get("items_subtotal"))
                + _admin_store_overview_money(order.get("delivery_fee_amount") if order.get("delivery_fee_amount") is not None else order.get("delivery_fee"))
                + _admin_store_overview_money(order.get("platform_fee"))
                + _admin_store_overview_money(order.get("tip_amount") if order.get("tip_amount") is not None else order.get("delivery_tip_amount"))
            )
        )
    )


def _admin_store_overview_items_value(order):
    return _admin_store_overview_money(
        order.get("items_subtotal"),
        _admin_store_overview_money(order.get("store_earning"), order.get("total_amount") or 0)
    )


def _admin_store_overview_platform_fee(order):
    return _admin_store_overview_money(
        order.get("admin_platform_earning"),
        _admin_store_overview_money(order.get("platform_fee"), 0)
    )


def _admin_store_overview_payout_amount(order):
    return _admin_store_overview_money(
        order.get("store_payout_amount"),
        _admin_store_overview_money(order.get("store_earning"), order.get("items_subtotal") or 0)
    )


def _admin_store_overview_is_sale_order(order):
    status = _norm_status(order.get("status"))
    return status not in {"CANCELLED", "CANCELED", "RETURNED", "REFUNDED", "FAILED"}


def _admin_store_overview_is_delivered_or_paid(order):
    status = _norm_status(order.get("status"))
    payment_status = _norm_status(order.get("payment_status"))
    collection_status = _norm_status(order.get("payment_collection_status"))

    return bool(
        status in {"DELIVERED", "COMPLETED", "ORDER_DELIVERED"}
        or payment_status in {"PAID", "SUCCESS", "COMPLETED", "CAPTURED"}
        or collection_status in {"COLLECTED", "RECEIVED", "PAID"}
    )


def _admin_store_overview_is_payout_paid(order):
    return _norm_status(order.get("store_payout_status")) in {
        "PAID",
        "PAID_TO_STORE",
        "SETTLED",
        "COMPLETED",
        "RELEASED",
    }


def _admin_store_overview_build_rows():
    filters, start_dt, end_dt = _admin_store_overview_date_filter()
    base_rows = _admin_store_rows()
    period_rows = {}

    for row in base_rows:
        period_rows[str(row.get("id"))] = {
            "orders": 0,
            "delivered_orders": 0,
            "revenue": 0.0,
            "items_revenue": 0.0,
            "commission_earned": 0.0,
            "store_withdrawals": 0.0,
            "products": 0,
        }

    for order in mongo.orders.find({}):
        if not _admin_store_overview_in_date_range(order.get("created_at") or order.get("updated_at"), start_dt, end_dt):
            continue

        store_id = str(order.get("store_id") or "")

        if not store_id or store_id not in period_rows:
            continue

        if not _admin_store_overview_is_sale_order(order):
            continue

        period_rows[store_id]["orders"] += 1

        if _admin_store_overview_is_delivered_or_paid(order):
            period_rows[store_id]["delivered_orders"] += 1
            period_rows[store_id]["revenue"] += _admin_store_overview_order_total(order)
            period_rows[store_id]["items_revenue"] += _admin_store_overview_items_value(order)
            period_rows[store_id]["commission_earned"] += _admin_store_overview_platform_fee(order)

            if _admin_store_overview_is_payout_paid(order):
                period_rows[store_id]["store_withdrawals"] += _admin_store_overview_payout_amount(order)

    for product in mongo.products.find({}):
        if not _admin_store_overview_in_date_range(product.get("created_at") or product.get("updated_at"), start_dt, end_dt):
            continue

        store_id = str(product.get("store_id") or "")

        if store_id in period_rows:
            period_rows[store_id]["products"] += 1

    all_range = not start_dt and not end_dt
    rows = []

    for row in base_rows:
        store_id = str(row.get("id") or "")
        period = period_rows.get(store_id, {})
        row = dict(row)

        row["all_time_orders"] = row.get("orders", 0)
        row["all_time_delivered_orders"] = row.get("delivered_orders", 0)
        row["all_time_products"] = row.get("products", 0)
        row["all_time_revenue"] = row.get("revenue", 0)

        if not all_range:
            row["orders"] = int(period.get("orders") or 0)
            row["delivered_orders"] = int(period.get("delivered_orders") or 0)
            row["products"] = int(period.get("products") or 0)
            row["revenue"] = round(float(period.get("revenue") or 0), 2)
        else:
            row["orders"] = int(period.get("orders") if period.get("orders") is not None else row.get("orders") or 0)
            row["delivered_orders"] = int(period.get("delivered_orders") if period.get("delivered_orders") is not None else row.get("delivered_orders") or 0)
            row["products"] = int(period.get("products") if period.get("products") is not None else row.get("products") or 0)
            row["revenue"] = round(float(period.get("revenue") if period.get("revenue") is not None else row.get("revenue") or 0), 2)

        row["items_revenue"] = round(float(period.get("items_revenue") or 0), 2)
        row["commission_earned"] = round(float(period.get("commission_earned") or 0), 2)
        row["store_withdrawals"] = round(float(period.get("store_withdrawals") or 0), 2)
        row["created_in_range"] = _admin_store_overview_in_date_range(row.get("created_at"), start_dt, end_dt)
        row["created_in_last_30_days"] = _admin_store_overview_in_date_range(
            row.get("created_at"),
            datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=29),
            datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1),
        )

        if _admin_store_overview_store_matches(row, filters):
            rows.append(row)

    return rows, filters


@app.route("/admin/stores")
@login_required(role="admin")
def admin_store_overview():
    stores, filters = _admin_store_overview_build_rows()

    total_stores = len(stores)
    active_stores = len([s for s in stores if int(s.get("is_active") or 0) == 1])
    inactive_stores = len([s for s in stores if int(s.get("is_active") or 0) != 1])
    if filters.get("range") != "all":
        new_stores = len([s for s in stores if s.get("created_in_range")])
    else:
        new_stores = len([s for s in stores if s.get("created_in_last_30_days")])

    top_selling_stores = sorted(
        stores,
        key=lambda x: (float(x.get("revenue") or 0), int(x.get("orders") or 0)),
        reverse=True
    )[:6]

    most_popular_stores = sorted(
        stores,
        key=lambda x: (int(x.get("orders") or 0), float(x.get("rating") or 0)),
        reverse=True
    )[:6]

    top_product_stores = sorted(
        stores,
        key=lambda x: (int(x.get("products") or 0), int(x.get("orders") or 0)),
        reverse=True
    )[:6]

    metrics = {
        "total_stores": total_stores,
        "active_stores": active_stores,
        "inactive_stores": inactive_stores,
        "new_stores": new_stores,
        "total_transactions": sum(int(s.get("orders") or 0) for s in stores),
        "commission_earned": round(sum(float(s.get("commission_earned") or 0) for s in stores), 2),
        "store_withdrawals": round(sum(float(s.get("store_withdrawals") or 0) for s in stores), 2),
    }

    return render_template(
        "admin_store_overview.html",
        user=current_user(),
        metrics=metrics,
        stores=stores,
        filters=filters,
        top_selling_stores=top_selling_stores,
        most_popular_stores=most_popular_stores,
        top_product_stores=top_product_stores,
        active_group="store",
        active_page="store_overview",
    )



ADMIN_STORE_LIST_STATUS_OPTIONS = [
    {"value": "all", "label": "All statuses"},
    {"value": "active", "label": "Active accounts"},
    {"value": "inactive", "label": "Inactive accounts"},
    {"value": "online", "label": "Online stores"},
    {"value": "offline", "label": "Offline stores"},
    {"value": "delivery_on", "label": "Delivery on"},
    {"value": "delivery_off", "label": "Delivery off"},
    {"value": "zone_ready", "label": "Zone ready"},
    {"value": "zone_missing", "label": "Zone missing"},
]


def _admin_store_list_int(value, default=1, min_value=1, max_value=999999):
    try:
        number = int(float(value))
    except Exception:
        number = int(default)

    if number < min_value:
        number = min_value

    if number > max_value:
        number = max_value

    return number


def _admin_store_list_filters():
    q = (request.args.get("q") or "").strip()
    status = (request.args.get("status") or "all").strip().lower()

    allowed_statuses = {item["value"] for item in ADMIN_STORE_LIST_STATUS_OPTIONS}

    if status not in allowed_statuses:
        status = "all"

    page = _admin_store_list_int(request.args.get("page"), 1, 1, 999999)
    per_page = _admin_store_list_int(request.args.get("per_page"), 20, 5, 100)

    if per_page not in [10, 20, 50, 100]:
        per_page = 20

    return {
        "q": q,
        "status": status,
        "page": page,
        "per_page": per_page,
    }


def _admin_store_list_zone_ready(store):
    """
    Detect whether a store has a usable delivery/service zone saved.

    Older store records may store the same polygon under different keys
    or as JSON / GeoJSON-like data. This keeps Store List and Store Reviews
    readiness display consistent with the Store Edit map loader.
    """
    store = store or {}

    try:
        if int(store.get("delivery_zone_configured") or 0) == 1:
            return True
    except Exception:
        pass

    for zone_key in [
        "delivery_zone_polygon",
        "delivery_zone",
        "zone_polygon",
        "service_area_polygon",
        "delivery_area_polygon",
        "service_area",
        "zone",
    ]:
        raw_polygon = store.get(zone_key)

        try:
            polygon = _admin_parse_delivery_zone_polygon(raw_polygon)
            if isinstance(polygon, list) and len(polygon) >= 3:
                return True
        except Exception:
            if isinstance(raw_polygon, list) and len(raw_polygon) >= 3:
                return True

    return False


def _admin_store_list_matches(store, filters):
    q = (filters.get("q") or "").strip().lower()
    status = (filters.get("status") or "all").strip().lower()

    if q:
        haystack = " ".join([
            str(store.get("store_name") or ""),
            str(store.get("id") or ""),
            str(store.get("address") or ""),
            str(store.get("city") or ""),
            str(store.get("state") or ""),
            str(store.get("pincode") or ""),
            str(store.get("owner_name") or ""),
            str(store.get("owner_email") or ""),
            str(store.get("owner_phone") or ""),
        ]).lower()

        if q not in haystack:
            return False

    is_active = int(store.get("is_active") or 0) == 1
    is_online = int(store.get("is_online", store.get("is_open", 1)) or 0) == 1
    delivery_on = int(store.get("delivery_enabled", 1 if store.get("delivery_available", True) else 0) or 0) == 1
    zone_ready = _admin_store_list_zone_ready(store)

    if status == "active" and not is_active:
        return False

    if status == "inactive" and is_active:
        return False

    if status == "online" and not is_online:
        return False

    if status == "offline" and is_online:
        return False

    if status == "delivery_on" and not delivery_on:
        return False

    if status == "delivery_off" and delivery_on:
        return False

    if status == "zone_ready" and not zone_ready:
        return False

    if status == "zone_missing" and zone_ready:
        return False

    return True


def _admin_store_list_counts(stores):
    counts = {
        "all": len(stores),
        "active": 0,
        "inactive": 0,
        "online": 0,
        "offline": 0,
        "delivery_on": 0,
        "delivery_off": 0,
        "zone_ready": 0,
        "zone_missing": 0,
    }

    for store in stores:
        is_active = int(store.get("is_active") or 0) == 1
        is_online = int(store.get("is_online", store.get("is_open", 1)) or 0) == 1
        delivery_on = int(store.get("delivery_enabled", 1 if store.get("delivery_available", True) else 0) or 0) == 1
        zone_ready = _admin_store_list_zone_ready(store)

        counts["active" if is_active else "inactive"] += 1
        counts["online" if is_online else "offline"] += 1
        counts["delivery_on" if delivery_on else "delivery_off"] += 1
        counts["zone_ready" if zone_ready else "zone_missing"] += 1

    return counts


def _admin_store_list_rows():
    filters = _admin_store_list_filters()
    stores = _admin_store_rows()
    counts = _admin_store_list_counts(stores)
    filtered_stores = [
        store for store in stores
        if _admin_store_list_matches(store, filters)
    ]

    return stores, filtered_stores, filters, counts


def _admin_store_list_paginate(rows, filters):
    total = len(rows)
    per_page = int(filters.get("per_page") or 20)
    pages = max(1, ((total + per_page - 1) // per_page))

    page = int(filters.get("page") or 1)

    if page > pages:
        page = pages

    start = (page - 1) * per_page
    end = start + per_page

    filters["page"] = page

    return rows[start:end], {
        "page": page,
        "per_page": per_page,
        "total": total,
        "pages": pages,
        "has_prev": page > 1,
        "has_next": page < pages,
        "prev_page": page - 1 if page > 1 else 1,
        "next_page": page + 1 if page < pages else pages,
        "start_index": start + 1 if total else 0,
        "end_index": min(end, total),
    }



def _admin_store_wants_json_response():
    try:
        return bool(
            request.headers.get("X-Requested-With") == "XMLHttpRequest"
            or "application/json" in (request.headers.get("Accept") or "")
            or str(request.form.get("ajax") or "").strip() == "1"
        )
    except Exception:
        return False


def _admin_store_action_redirect(default_endpoint="admin_store_list"):
    next_url = (request.form.get("next") or request.args.get("next") or "").strip()

    if next_url and next_url.startswith("/") and not next_url.startswith("//"):
        return redirect(next_url)

    referrer = request.referrer or ""
    try:
        if referrer and referrer.startswith(request.host_url):
            return redirect(referrer)
    except Exception:
        pass

    return redirect(url_for(default_endpoint))


def _admin_store_json_or_redirect(ok, message, category="success", status_code=200, **payload):
    if _admin_store_wants_json_response():
        data = {
            "ok": bool(ok),
            "message": message,
            "category": category,
        }
        data.update(payload)
        return jsonify(data), status_code

    flash(message, category)
    return _admin_store_action_redirect("admin_store_list")


def _admin_store_ajax_counts_payload():
    try:
        return _admin_store_list_counts(_admin_store_rows())
    except Exception:
        return {}


def _admin_store_edit_row(store):
    store = store or {}
    row = dict(store)

    sid = store.get("_id") or store.get("id") or ""
    row["id"] = str(sid)

    user = {}
    user_id = store.get("user_id")
    if user_id:
        try:
            user = mongo.users.find_one({"_id": ObjectId(str(user_id))}) or {}
        except Exception:
            user = {}

    row["owner_name"] = store.get("owner_name") or user.get("name") or ""
    row["owner_email"] = store.get("owner_email") or user.get("email") or ""
    row["owner_phone"] = store.get("owner_phone") or user.get("phone") or ""

    row["store_name"] = store.get("store_name") or ""
    row["address"] = store.get("address") or ""
    row["city"] = store.get("city") or ""
    row["state"] = store.get("state") or "Assam"
    row["pincode"] = store.get("pincode") or ""
    row["latitude"] = store.get("latitude") if store.get("latitude") is not None else ""
    row["longitude"] = store.get("longitude") if store.get("longitude") is not None else ""
    row["delivery_base_fee"] = store.get("delivery_base_fee", 40)

    polygon = _admin_parse_delivery_zone_polygon(store.get("delivery_zone_polygon"))
    if not polygon:
        for zone_key in [
            "delivery_zone",
            "zone_polygon",
            "service_area_polygon",
            "delivery_area_polygon",
            "service_area",
            "zone",
        ]:
            polygon = _admin_parse_delivery_zone_polygon(store.get(zone_key))
            if polygon:
                break

    row["delivery_zone_polygon"] = polygon
    row["delivery_zone_configured"] = 1 if len(polygon) >= 3 else 0
    row["is_online"] = int(store.get("is_online", store.get("is_open", 1)) or 0)
    row["delivery_enabled"] = int(store.get("delivery_enabled", 1 if store.get("delivery_available", True) else 0) or 0)
    row["is_active"] = int(store.get("is_active", 1) or 0)

    return row

@app.route("/admin/stores/list")
@login_required(role="admin")
def admin_store_list():
    all_stores, filtered_stores, filters, store_counts = _admin_store_list_rows()
    stores, pagination = _admin_store_list_paginate(filtered_stores, filters)

    return render_template(
        "admin_store_list.html",
        user=current_user(),
        stores=stores,
        all_store_count=len(all_stores),
        store_counts=store_counts,
        list_filters=filters,
        pagination=pagination,
        status_options=ADMIN_STORE_LIST_STATUS_OPTIONS,
        active_group="store",
        active_page="store_list",
    )

ADMIN_STORE_REVIEW_STATUS_OPTIONS = [
    {"value": "all", "label": "All stores"},
    {"value": "recommended", "label": "Recommended ready"},
    {"value": "active", "label": "Active accounts"},
    {"value": "inactive", "label": "Inactive accounts"},
    {"value": "online", "label": "Online stores"},
    {"value": "offline", "label": "Offline stores"},
    {"value": "delivery_on", "label": "Delivery on"},
    {"value": "delivery_off", "label": "Delivery off"},
    {"value": "zone_ready", "label": "Zone ready"},
    {"value": "zone_missing", "label": "Zone missing"},
    {"value": "has_products", "label": "Has products"},
    {"value": "no_products", "label": "No products"},
]


ADMIN_STORE_REVIEW_SORT_OPTIONS = [
    {"value": "score", "label": "Recommendation score"},
    {"value": "rating", "label": "Highest rating"},
    {"value": "orders", "label": "Most orders"},
    {"value": "products", "label": "Most products"},
    {"value": "name", "label": "Store name A-Z"},
]


def _admin_store_review_int(value, default=0, min_value=None, max_value=None):
    try:
        number = int(float(value))
    except Exception:
        number = int(default)

    if min_value is not None and number < min_value:
        number = int(min_value)

    if max_value is not None and number > max_value:
        number = int(max_value)

    return number


def _admin_store_review_float(value, default=0.0, min_value=None, max_value=None):
    try:
        number = float(value)
    except Exception:
        number = float(default)

    if min_value is not None and number < min_value:
        number = float(min_value)

    if max_value is not None and number > max_value:
        number = float(max_value)

    return number


def _admin_store_review_filters():
    q = (request.args.get("q") or "").strip()
    status = (request.args.get("status") or "all").strip().lower()
    sort_by = (request.args.get("sort_by") or "score").strip().lower()

    allowed_statuses = {item["value"] for item in ADMIN_STORE_REVIEW_STATUS_OPTIONS}
    allowed_sorts = {item["value"] for item in ADMIN_STORE_REVIEW_SORT_OPTIONS}

    if status not in allowed_statuses:
        status = "all"

    if sort_by not in allowed_sorts:
        sort_by = "score"

    page = _admin_store_list_int(request.args.get("page"), 1, 1, 999999)
    per_page = _admin_store_list_int(request.args.get("per_page"), 20, 5, 100)

    if per_page not in [10, 20, 50, 100]:
        per_page = 20

    return {
        "q": q,
        "status": status,
        "sort_by": sort_by,
        "page": page,
        "per_page": per_page,
    }


def _admin_store_review_return_url():
    full_path = request.full_path or request.path

    if full_path.endswith("?"):
        full_path = full_path[:-1]

    return full_path or url_for("admin_store_reviews")


def _admin_store_review_enrich(store):
    row = dict(store or {})
    row["id"] = str(row.get("id") or row.get("_id") or "")

    row["is_active"] = 1 if _admin_store_review_int(row.get("is_active"), 0) == 1 else 0
    row["is_online"] = 1 if _admin_store_review_int(row.get("is_online", row.get("is_open", 1)), 0) == 1 else 0
    row["delivery_enabled"] = 1 if _admin_store_review_int(row.get("delivery_enabled", 1 if row.get("delivery_available", True) else 0), 0) == 1 else 0

    row["products"] = _admin_store_review_int(row.get("products"), 0, 0)
    row["orders"] = _admin_store_review_int(row.get("orders"), 0, 0)
    row["rating"] = round(_admin_store_review_float(row.get("rating"), 0.0, 0, 5), 2)
    row["rating_count"] = _admin_store_review_int(
        row.get("rating_count")
        if row.get("rating_count") is not None
        else row.get("reviews_count")
        if row.get("reviews_count") is not None
        else row.get("ratings_count")
        if row.get("ratings_count") is not None
        else row.get("review_count"),
        0,
        0,
    )

    row["zone_ready"] = bool(_admin_store_list_zone_ready(row))
    row["delivery_zone_configured"] = 1 if row["zone_ready"] else 0

    reasons = []

    if row["is_active"] != 1:
        reasons.append("Inactive")

    if row["products"] <= 0:
        reasons.append("No products")

    if row["delivery_enabled"] != 1:
        reasons.append("Delivery off")

    if not row["zone_ready"]:
        reasons.append("Zone missing")

    row["recommendation_ready"] = not reasons
    row["recommendation_reason"] = "Ready" if not reasons else ", ".join(reasons)
    row["recommendation_badge_class"] = "ready" if row["recommendation_ready"] else "warning"

    effective_rating_count = row["rating_count"] if row["rating_count"] > 0 else (1 if row["rating"] > 0 else 0)
    rating_confidence = min(effective_rating_count, 50) / 50
    orders_score = min(row["orders"], 500) / 500
    products_score = min(row["products"], 200) / 200
    readiness_score = 1 if row["recommendation_ready"] else 0

    row["recommendation_score"] = round(
        (row["rating"] * 35)
        + (orders_score * 30)
        + (products_score * 20)
        + (rating_confidence * 10)
        + (readiness_score * 5),
        2
    )

    return row


def _admin_store_review_matches(store, filters):
    q = (filters.get("q") or "").strip().lower()
    status = (filters.get("status") or "all").strip().lower()

    if q:
        haystack = " ".join([
            str(store.get("store_name") or ""),
            str(store.get("id") or ""),
            str(store.get("address") or ""),
            str(store.get("city") or ""),
            str(store.get("state") or ""),
            str(store.get("pincode") or ""),
            str(store.get("owner_name") or ""),
            str(store.get("owner_email") or ""),
            str(store.get("owner_phone") or ""),
        ]).lower()

        if q not in haystack:
            return False

    is_active = int(store.get("is_active") or 0) == 1
    is_online = int(store.get("is_online", store.get("is_open", 1)) or 0) == 1
    delivery_on = int(store.get("delivery_enabled", 1 if store.get("delivery_available", True) else 0) or 0) == 1
    zone_ready = bool(store.get("zone_ready"))
    has_products = int(store.get("products") or 0) > 0
    recommended_ready = bool(store.get("recommendation_ready"))

    if status == "recommended" and not recommended_ready:
        return False

    if status == "active" and not is_active:
        return False

    if status == "inactive" and is_active:
        return False

    if status == "online" and not is_online:
        return False

    if status == "offline" and is_online:
        return False

    if status == "delivery_on" and not delivery_on:
        return False

    if status == "delivery_off" and delivery_on:
        return False

    if status == "zone_ready" and not zone_ready:
        return False

    if status == "zone_missing" and zone_ready:
        return False

    if status == "has_products" and not has_products:
        return False

    if status == "no_products" and has_products:
        return False

    return True


def _admin_store_review_sort(rows, filters):
    sort_by = (filters.get("sort_by") or "score").strip().lower()

    if sort_by == "name":
        return sorted(rows, key=lambda row: (str(row.get("store_name") or "").lower(), str(row.get("id") or "")))

    if sort_by == "rating":
        return sorted(
            rows,
            key=lambda row: (
                float(row.get("rating") or 0),
                int(row.get("rating_count") or 0),
                int(row.get("orders") or 0),
                int(row.get("products") or 0),
            ),
            reverse=True,
        )

    if sort_by == "orders":
        return sorted(
            rows,
            key=lambda row: (
                int(row.get("orders") or 0),
                float(row.get("rating") or 0),
                int(row.get("products") or 0),
            ),
            reverse=True,
        )

    if sort_by == "products":
        return sorted(
            rows,
            key=lambda row: (
                int(row.get("products") or 0),
                int(row.get("orders") or 0),
                float(row.get("rating") or 0),
            ),
            reverse=True,
        )

    return sorted(
        rows,
        key=lambda row: (
            1 if row.get("recommendation_ready") else 0,
            float(row.get("recommendation_score") or 0),
            float(row.get("rating") or 0),
            int(row.get("orders") or 0),
            int(row.get("products") or 0),
        ),
        reverse=True,
    )


def _admin_store_review_counts(stores):
    counts = {
        "all": len(stores),
        "recommended": 0,
        "active": 0,
        "inactive": 0,
        "online": 0,
        "offline": 0,
        "delivery_on": 0,
        "delivery_off": 0,
        "zone_ready": 0,
        "zone_missing": 0,
        "has_products": 0,
        "no_products": 0,
    }

    for store in stores:
        is_active = int(store.get("is_active") or 0) == 1
        is_online = int(store.get("is_online", store.get("is_open", 1)) or 0) == 1
        delivery_on = int(store.get("delivery_enabled", 1 if store.get("delivery_available", True) else 0) or 0) == 1
        zone_ready = bool(store.get("zone_ready"))
        has_products = int(store.get("products") or 0) > 0

        if store.get("recommendation_ready"):
            counts["recommended"] += 1

        counts["active" if is_active else "inactive"] += 1
        counts["online" if is_online else "offline"] += 1
        counts["delivery_on" if delivery_on else "delivery_off"] += 1
        counts["zone_ready" if zone_ready else "zone_missing"] += 1
        counts["has_products" if has_products else "no_products"] += 1

    return counts


def _admin_store_review_average_rating(stores):
    weighted_rating_total = 0.0
    rating_count_total = 0
    simple_ratings = []

    for store in stores:
        rating = _admin_store_review_float(store.get("rating"), 0.0, 0, 5)
        rating_count = _admin_store_review_int(store.get("rating_count"), 0, 0)

        if rating_count > 0:
            weighted_rating_total += rating * rating_count
            rating_count_total += rating_count
        elif rating > 0:
            simple_ratings.append(rating)

    if rating_count_total > 0:
        return round(weighted_rating_total / rating_count_total, 2)

    if simple_ratings:
        return round(sum(simple_ratings) / len(simple_ratings), 2)

    return 0.0


@app.route("/admin/stores/reviews")
@login_required(role="admin")
def admin_store_reviews():
    filters = _admin_store_review_filters()

    stores = [
        _admin_store_review_enrich(store)
        for store in _admin_store_rows()
    ]

    recommended_ready_stores = [
        store for store in stores
        if store.get("recommendation_ready")
    ]

    recommended_stores = _admin_store_review_sort(
        stores,
        {"sort_by": "score"}
    )

    filtered_stores = [
        store for store in stores
        if _admin_store_review_matches(store, filters)
    ]
    filtered_stores = _admin_store_review_sort(filtered_stores, filters)
    review_stores, pagination = _admin_store_list_paginate(filtered_stores, filters)

    review_counts = _admin_store_review_counts(stores)
    active_stores = len([store for store in stores if int(store.get("is_active") or 0) == 1])
    inactive_stores = len(stores) - active_stores

    review_metrics = {
        "total_stores": len(stores),
        "filtered_stores": len(filtered_stores),
        "recommended_stores": len(recommended_ready_stores),
        "active_stores": active_stores,
        "inactive_stores": inactive_stores,
        "total_products": sum(int(store.get("products") or 0) for store in stores),
        "total_orders": sum(int(store.get("orders") or 0) for store in stores),
        "avg_rating": _admin_store_review_average_rating(stores),
    }

    return render_template(
        "admin_store_reviews.html",
        user=current_user(),
        stores=stores,
        recommended_stores=recommended_stores,
        review_stores=review_stores,
        review_filters=filters,
        review_status_options=ADMIN_STORE_REVIEW_STATUS_OPTIONS,
        review_sort_options=ADMIN_STORE_REVIEW_SORT_OPTIONS,
        review_counts=review_counts,
        review_metrics=review_metrics,
        pagination=pagination,
        review_return_url=_admin_store_review_return_url(),
        active_group="store",
        active_page="store_reviews",
    )

def _admin_store_overview_csv_response(stores, filename):
    rows = [[
        "SL",
        "Store Name",
        "Store ID",
        "Owner Name",
        "Owner Email",
        "Owner Phone",
        "City",
        "State",
        "Status",
        "Online Status",
        "Delivery Status",
        "Orders",
        "Delivered/Paid Orders",
        "Revenue",
        "Platform Fee / Commission",
        "Store Payout Withdrawals",
        "Products",
        "Rating",
        "Created At",
    ]]

    for idx, store in enumerate(stores, start=1):
        rows.append([
            idx,
            store.get("store_name", ""),
            store.get("id", ""),
            store.get("owner_name", ""),
            store.get("owner_email", ""),
            store.get("owner_phone", ""),
            store.get("city", ""),
            store.get("state", ""),
            "Active" if int(store.get("is_active") or 0) == 1 else "Inactive",
            "Online" if int(store.get("is_online", store.get("is_open", 0)) or 0) == 1 else "Offline",
            "Delivery On" if int(store.get("delivery_enabled", 1 if store.get("delivery_available", True) else 0) or 0) == 1 else "Delivery Off",
            store.get("orders", 0),
            store.get("delivered_orders", 0),
            "%.2f" % float(store.get("revenue") or 0),
            "%.2f" % float(store.get("commission_earned") or 0),
            "%.2f" % float(store.get("store_withdrawals") or 0),
            store.get("products", 0),
            store.get("rating", 0),
            store.get("created_at", ""),
        ])

    def csv_escape(value):
        value = "" if value is None else str(value)
        return '"' + value.replace('"', '""') + '"'

    csv_data = "\n".join(",".join(csv_escape(col) for col in row) for row in rows)

    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@app.route("/admin/stores/export.csv")
@login_required(role="admin")
def admin_stores_export_csv():
    all_stores, stores, filters, store_counts = _admin_store_list_rows()

    rows = [
        [
            "SL",
            "Store Name",
            "Store ID",
            "Owner Name",
            "Owner Email",
            "Owner Phone",
            "City",
            "State",
            "Pincode",
            "Account Status",
            "Online Status",
            "Delivery Status",
            "Delivery Zone",
            "Orders",
            "Products",
            "Revenue",
            "Rating",
            "Created At",
        ]
    ]

    for idx, store in enumerate(stores, start=1):
        is_online = int(store.get("is_online", store.get("is_open", 1)) or 0) == 1
        delivery_on = int(store.get("delivery_enabled", 1 if store.get("delivery_available", True) else 0) or 0) == 1
        zone_ready = _admin_store_list_zone_ready(store)

        rows.append([
            idx,
            store.get("store_name", ""),
            store.get("id", ""),
            store.get("owner_name", ""),
            store.get("owner_email", ""),
            store.get("owner_phone", ""),
            store.get("city", ""),
            store.get("state", ""),
            store.get("pincode", ""),
            "Active" if int(store.get("is_active") or 0) == 1 else "Inactive",
            "Online" if is_online else "Offline",
            "Delivery On" if delivery_on else "Delivery Off",
            "Zone Ready" if zone_ready else "Zone Missing",
            store.get("orders", 0),
            store.get("products", 0),
            "%.2f" % float(store.get("revenue") or 0),
            store.get("rating", 0),
            store.get("created_at", ""),
        ])

    def csv_escape(value):
        value = "" if value is None else str(value)
        return '"' + value.replace('"', '""') + '"'

    csv_data = "\n".join(",".join(csv_escape(col) for col in row) for row in rows)

    suffix_parts = [
        (filters.get("status") or "all").replace(" ", "_"),
    ]

    if filters.get("q"):
        suffix_parts.append("searched")

    filename = "nelocals_stores_" + "_".join(suffix_parts) + ".csv"

    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@app.route("/admin/stores/overview/export.csv")
@login_required(role="admin")
def admin_store_overview_export_csv():
    stores, filters = _admin_store_overview_build_rows()
    suffix = (filters.get("range") or "all").replace(" ", "_")
    return _admin_store_overview_csv_response(stores, f"nelocals_store_overview_{suffix}.csv")


@app.route("/admin/stores/<store_id>/toggle", methods=["POST"])
@login_required(role="admin")
def admin_store_toggle(store_id):
    try:
        sid = ObjectId(store_id)
    except Exception:
        return _admin_store_json_or_redirect(False, "Invalid store.", "danger", 400)

    store = mongo.stores.find_one({"_id": sid})

    if not store:
        return _admin_store_json_or_redirect(False, "Store not found.", "warning", 404)

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

    return _admin_store_json_or_redirect(
        True,
        "Store status updated successfully.",
        "success",
        store_id=str(sid),
        field="is_active",
        value=next_status,
        store_counts=_admin_store_ajax_counts_payload(),
    )

@app.route("/admin/stores/<store_id>/online-toggle", methods=["POST"])
@login_required(role="admin")
def admin_store_online_toggle(store_id):
    try:
        sid = ObjectId(store_id)
    except Exception:
        return _admin_store_json_or_redirect(False, "Invalid store.", "danger", 400)

    store = mongo.stores.find_one({"_id": sid})

    if not store:
        return _admin_store_json_or_redirect(False, "Store not found.", "warning", 404)

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

    return _admin_store_json_or_redirect(
        True,
        "Store is now online." if next_status else "Store is now offline.",
        "success",
        store_id=str(sid),
        field="is_online",
        value=next_status,
        store_counts=_admin_store_ajax_counts_payload(),
    )

@app.route("/admin/stores/<store_id>/delivery-toggle", methods=["POST"])
@login_required(role="admin")
def admin_store_delivery_toggle(store_id):
    try:
        sid = ObjectId(store_id)
    except Exception:
        return _admin_store_json_or_redirect(False, "Invalid store.", "danger", 400)

    store = mongo.stores.find_one({"_id": sid})

    if not store:
        return _admin_store_json_or_redirect(False, "Store not found.", "warning", 404)

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

    return _admin_store_json_or_redirect(
        True,
        "Store delivery is now enabled." if next_status else "Store delivery is now disabled.",
        "success",
        store_id=str(sid),
        field="delivery_enabled",
        value=next_status,
        store_counts=_admin_store_ajax_counts_payload(),
    )

@app.route("/admin/stores/<store_id>/edit", methods=["GET"], endpoint="admin_store_edit")
@login_required(role="admin")
def admin_store_edit(store_id):
    try:
        sid = ObjectId(store_id)
    except Exception:
        flash("Invalid store.", "danger")
        return redirect(url_for("admin_store_list"))

    store = mongo.stores.find_one({"_id": sid})

    if not store:
        flash("Store not found.", "warning")
        return redirect(url_for("admin_store_list"))

    return render_template(
        "admin_store_edit.html",
        user=current_user(),
        store=_admin_store_edit_row(store),
        active_group="store",
        active_page="store_list",
    )

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

    latitude_raw = request.form.get("latitude")
    longitude_raw = request.form.get("longitude")

    latitude = _admin_float_or_none(
        latitude_raw,
        -90,
        90
    )
    longitude = _admin_float_or_none(
        longitude_raw,
        -180,
        180
    )

    if latitude is None and str(latitude_raw or "").strip() == "":
        latitude = _admin_float_or_none(store.get("latitude"), -90, 90)

    if longitude is None and str(longitude_raw or "").strip() == "":
        longitude = _admin_float_or_none(store.get("longitude"), -180, 180)

    is_active = _admin_bool_from_form(
        "is_active",
        bool(int(store.get("is_active", 1) or 0))
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

    raw_delivery_zone_polygon = request.form.get("delivery_zone_polygon")
    existing_delivery_zone_polygon = _admin_parse_delivery_zone_polygon(
        store.get("delivery_zone_polygon")
    )
    if not existing_delivery_zone_polygon:
        for zone_key in [
            "delivery_zone",
            "zone_polygon",
            "service_area_polygon",
            "delivery_area_polygon",
            "service_area",
            "zone",
        ]:
            existing_delivery_zone_polygon = _admin_parse_delivery_zone_polygon(store.get(zone_key))
            if existing_delivery_zone_polygon:
                break
    submitted_delivery_zone_polygon = _admin_parse_delivery_zone_polygon(
        raw_delivery_zone_polygon if raw_delivery_zone_polygon is not None else ""
    )

    if (
        delivery_enabled
        and not submitted_delivery_zone_polygon
        and existing_delivery_zone_polygon
    ):
        delivery_zone_polygon = existing_delivery_zone_polygon
    else:
        delivery_zone_polygon = submitted_delivery_zone_polygon

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

    if latitude is None or longitude is None:
        flash("Store pickup latitude and longitude are required.", "warning")
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

                "is_active": 1 if is_active else 0,
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
        update_user["is_active"] = 1 if is_active else 0

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
        return _admin_store_json_or_redirect(False, "Invalid store.", "danger", 400)

    store = mongo.stores.find_one({"_id": sid})

    if not store:
        return _admin_store_json_or_redirect(False, "Store not found.", "warning", 404)

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

        return _admin_store_json_or_redirect(
            True,
            "Store has orders, so it was disabled instead of deleted.",
            "warning",
            store_id=str(sid),
            mode="disabled",
            field="is_active",
            value=0,
            store_counts=_admin_store_ajax_counts_payload(),
        )

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

    return _admin_store_json_or_redirect(
        True,
        "Store deleted successfully.",
        "success",
        store_id=str(sid),
        mode="deleted",
        store_counts=_admin_store_ajax_counts_payload(),
    )

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
        payment_collection_status = (row.get("payment_collection_status") or "").strip().upper()
        payment_collection_channel = (row.get("payment_collection_channel") or "").strip().upper()
        upi_delivery_reconciliation_status = (row.get("upi_delivery_reconciliation_status") or "").strip().upper()

        cod_payment_methods = {
            "COD",
            "CASH_ON_DELIVERY",
            "COD_RIDER_COLLECTION"
        }
        collected_payment_statuses = {
            "PAID",
            "COLLECTED",
            "ONLINE_PAID",
            "COLLECTED_BY_RIDER",
            "COD_COLLECTED_BY_RIDER",
            "COD_UPI_RECORDED"
        }

        is_cod_order = payment_method in cod_payment_methods
        is_cod_upi = is_cod_order and payment_collection_channel == "UPI"
        is_cod_collected = bool(
            is_cod_order
            and (
                payment_status in collected_payment_statuses
                or payment_collection_status in {"COLLECTED", "PAID"}
            )
        )

        if is_cod_order and not is_cod_collected:
            amount_to_collect = total_payable
        else:
            amount_to_collect = 0.0

        cod_collected_amount = (
            _adh_float(row.get("cod_collected_amount"), total_payable)
            if is_cod_collected
            else 0.0
        )

        if is_cod_collected:
            cod_display_amount = cod_collected_amount
            if is_cod_upi and upi_delivery_reconciliation_status == "VERIFIED":
                cod_display_label = "UPI verified"
            elif is_cod_upi:
                cod_display_label = "UPI recorded · verification pending"
            else:
                cod_display_label = "Cash collected"
        elif is_cod_order:
            cod_display_amount = amount_to_collect
            cod_display_label = "To collect"
        else:
            cod_display_amount = 0.0
            cod_display_label = "Not applicable"

        delivery_fee_plus_tip = delivery_fee + tip_amount

        row["items_subtotal"] = round(items_subtotal, 2)
        row["delivery_fee"] = round(delivery_fee, 2)
        row["platform_fee"] = round(platform_fee, 2)
        row["tip_amount"] = round(tip_amount, 2)
        row["total_payable"] = round(total_payable, 2)
        row["payment_method"] = payment_method
        row["payment_status"] = payment_status
        row["payment_collection_status"] = payment_collection_status
        row["payment_collection_channel"] = payment_collection_channel
        row["upi_delivery_reconciliation_status"] = upi_delivery_reconciliation_status
        row["collection_channel_label"] = (
            "UPI" if is_cod_upi
            else ("Cash" if is_cod_order else ("Razorpay" if payment_collection_channel == "RAZORPAY" else "Online"))
        )
        row["is_cod_order"] = is_cod_order
        row["is_cod_collected"] = is_cod_collected
        row["amount_to_collect"] = round(amount_to_collect, 2)
        row["cod_collected_amount"] = round(cod_collected_amount, 2)
        row["cod_display_amount"] = round(cod_display_amount, 2)
        row["cod_display_label"] = cod_display_label
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
                "cod_collected": 0.0,
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

        rider_row["cod_collected"] += _adh_float(row.get("cod_collected_amount"))
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

        rider_row["cod_collected"] = round(_adh_float(rider_row.get("cod_collected")), 2)
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
        "cod_collected": round(sum(_adh_float(r.get("cod_collected_amount")) for r in history_orders), 2),
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

    # Resolve linked orders in one query so complaint cards use the same public NEO-*
    # reference shown on the customer Orders page without adding per-row database calls.
    complaint_order_refs = {str(c.get("order_id") or "").strip() for c in complaints if str(c.get("order_id") or "").strip()}
    complaint_order_object_ids = []
    complaint_public_refs = []
    for ref in complaint_order_refs:
        if ref.upper().startswith("NEO-"):
            complaint_public_refs.append(ref)
        else:
            try:
                complaint_order_object_ids.append(ObjectId(ref))
            except Exception:
                complaint_public_refs.append(ref)

    order_lookup = {}
    order_matchers = []
    if complaint_order_object_ids:
        order_matchers.append({"_id": {"$in": complaint_order_object_ids}})
    if complaint_public_refs:
        order_matchers.append({"order_number": {"$in": complaint_public_refs}})

    if order_matchers:
        try:
            for order_row in mongo.orders.find({"$or": order_matchers}, {"_id": 1, "order_number": 1}):
                public_number = str(order_row.get("order_number") or "").strip()
                if not public_number:
                    continue
                order_lookup[str(order_row.get("_id") or "")] = public_number
                order_lookup[public_number] = public_number
        except Exception:
            order_lookup = {}

    for c in complaints:
        raw_ref = str(c.get("order_id") or "").strip()
        c["display_order_number"] = order_lookup.get(raw_ref, raw_ref)

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



# =========================================================
# ADMIN USERS OVERVIEW - SAFE DATA HELPERS
# =========================================================
# These helpers are intentionally guarded. If app_core already provides the
# original helpers, those existing helpers remain untouched. The fallback block
# only prevents this extracted admin route file from crashing when the helpers
# are not available in the loaded module.

if "_au_safe_int" not in globals():
    def _au_safe_int(value, default=0):
        try:
            if value is None or str(value).strip() == "":
                return int(default)
            return int(float(value))
        except Exception:
            return int(default)

if "_au_safe_float" not in globals():
    def _au_safe_float(value, default=0.0):
        try:
            if value is None or str(value).strip() == "":
                return float(default)
            return float(value)
        except Exception:
            return float(default)

if "_au_money" not in globals():
    def _au_money(value):
        return round(_au_safe_float(value), 2)

if "_au_parse_date" not in globals():
    def _au_parse_date(value):
        try:
            if isinstance(value, datetime):
                return value
            if not value:
                return None
            text = str(value).strip().replace("Z", "")
            if not text:
                return None
            return datetime.fromisoformat(text)
        except Exception:
            return None

if "_au_format_datetime" not in globals():
    def _au_format_datetime(value):
        dt_obj = _au_parse_date(value)
        if not dt_obj:
            return ""
        try:
            return dt_obj.strftime("%d %b %Y, %I:%M %p")
        except Exception:
            return str(value or "")

if "_au_mask_email" not in globals():
    def _au_mask_email(email):
        email = str(email or "").strip()
        if not email or "@" not in email:
            return email
        name, domain = email.split("@", 1)
        if len(name) <= 2:
            masked_name = name[:1] + "*"
        else:
            masked_name = name[:2] + "*" * min(max(len(name) - 2, 1), 5)
        return masked_name + "@" + domain

if "_au_mask_phone" not in globals():
    def _au_mask_phone(phone):
        phone = str(phone or "").strip()
        if not phone:
            return ""
        if len(phone) <= 4:
            return phone
        return "*" * max(len(phone) - 4, 0) + phone[-4:]

if "_au_user_display_name" not in globals():
    def _au_user_display_name(user_doc):
        user_doc = user_doc or {}
        return (
            user_doc.get("name")
            or user_doc.get("full_name")
            or user_doc.get("store_name")
            or user_doc.get("email")
            or user_doc.get("phone")
            or "User"
        )

if "_au_all_users" not in globals():
    def _au_all_users():
        try:
            return list(mongo.users.find({}).sort("created_at", -1))
        except Exception:
            return []

if "_au_user_base_row" not in globals():
    def _au_user_base_row(user_doc):
        user_doc = user_doc or {}
        uid = str(user_doc.get("_id") or user_doc.get("id") or "")
        role = str(user_doc.get("role") or "").strip().lower() or "user"
        is_active = bool(_au_safe_int(user_doc.get("is_active"), 1))
        return {
            "id": uid,
            "_id": uid,
            "user_id": uid,
            "name": _au_user_display_name(user_doc),
            "email": user_doc.get("email") or "",
            "phone": user_doc.get("phone") or "",
            "email_masked": _au_mask_email(user_doc.get("email") or ""),
            "phone_masked": _au_mask_phone(user_doc.get("phone") or ""),
            "role": role,
            "is_active": is_active,
            "created_at": user_doc.get("created_at") or "",
            "created_at_display": _au_format_datetime(user_doc.get("created_at")),
        }



def _admin_full_user_row(user_doc):
    user_doc = user_doc or {}
    row = _au_user_base_row(user_doc)
    email = str(user_doc.get("email") or row.get("email") or "").strip()
    phone = str(user_doc.get("phone") or row.get("phone") or "").strip()
    role = str(user_doc.get("role") or row.get("role") or "user").strip().lower() or "user"
    row.update({
        "email": email,
        "phone": phone,
        "email_display": email,
        "phone_display": phone,
        "role": role,
        "status_label": "Active" if row.get("is_active") else "Disabled",
    })
    return row

if "_au_filter_rows_by_status" not in globals():
    def _au_filter_rows_by_status(rows, status):
        status = str(status or "").strip().lower()
        if not status:
            return list(rows or [])
        if status in ["active", "enabled"]:
            return [row for row in rows if row.get("is_active")]
        if status in ["inactive", "disabled", "blocked"]:
            return [row for row in rows if not row.get("is_active")]
        return list(rows or [])

if "_au_filter_rows_by_search" not in globals():
    def _au_filter_rows_by_search(rows, search):
        q = str(search or "").strip().lower()
        if not q:
            return list(rows or [])
        filtered = []
        for row in rows or []:
            haystack = " ".join([
                str(row.get("name") or ""),
                str(row.get("store_name") or ""),
                str(row.get("email") or ""),
                str(row.get("phone") or ""),
                str(row.get("role") or ""),
                str(row.get("zone") or ""),
            ]).lower()
            if q in haystack:
                filtered.append(row)
        return filtered

if "_au_store_user_rows" not in globals():
    def _au_store_user_rows():
        rows = []
        try:
            store_users = list(mongo.users.find({"role": "store"}).sort("created_at", -1))
        except Exception:
            store_users = []

        for user_doc in store_users:
            row = _au_user_base_row(user_doc)
            uid = row.get("id")
            store = None
            try:
                store = mongo.stores.find_one({
                    "$or": [
                        {"user_id": uid},
                        {"owner_id": uid},
                        {"owner_user_id": uid},
                        {"user_id": user_doc.get("_id")},
                    ]
                }) or {}
            except Exception:
                store = {}

            store_id = store.get("_id") if store else None
            orders = 0
            revenue = 0.0
            try:
                order_query = {"$or": [{"store_id": store_id}, {"store_id": str(store_id)}]} if store_id else {"store_user_id": uid}
                orders = mongo.orders.count_documents(order_query)
                pipeline = [
                    {"$match": order_query},
                    {"$group": {"_id": None, "amount": {"$sum": {"$ifNull": ["$total_amount", 0]}}}}
                ]
                agg = list(mongo.orders.aggregate(pipeline))
                revenue = _au_safe_float(agg[0].get("amount")) if agg else 0.0
            except Exception:
                pass

            products = 0
            try:
                if store_id:
                    products = mongo.products.count_documents({"$or": [{"store_id": store_id}, {"store_id": str(store_id)}]})
            except Exception:
                pass

            row.update({
                "store_name": store.get("store_name") or store.get("name") or row.get("name"),
                "orders": orders,
                "revenue": _au_money(revenue),
                "products": products,
            })
            rows.append(row)
        return rows

if "_au_extract_lat_lng" not in globals():
    def _au_extract_lat_lng(*sources):
        def to_float(value):
            try:
                if value is None or str(value).strip() == "":
                    return None
                return float(value)
            except Exception:
                return None

        for source in sources:
            source = source or {}
            candidate_pairs = [
                (source.get("latitude"), source.get("longitude")),
                (source.get("lat"), source.get("lng")),
                (source.get("lat"), source.get("lon")),
                (source.get("current_lat"), source.get("current_lng")),
                (source.get("current_latitude"), source.get("current_longitude")),
                (source.get("last_lat"), source.get("last_lng")),
                (source.get("last_latitude"), source.get("last_longitude")),
                (source.get("live_lat"), source.get("live_lng")),
            ]

            for lat_raw, lng_raw in candidate_pairs:
                lat = to_float(lat_raw)
                lng = to_float(lng_raw)
                if lat is not None and lng is not None and abs(lat) <= 90 and abs(lng) <= 180:
                    return lat, lng

            for key in ["current_location", "last_location", "location", "coordinates", "geo"]:
                loc = source.get(key)
                if isinstance(loc, dict):
                    lat = to_float(loc.get("latitude") or loc.get("lat"))
                    lng = to_float(loc.get("longitude") or loc.get("lng") or loc.get("lon"))
                    if lat is not None and lng is not None and abs(lat) <= 90 and abs(lng) <= 180:
                        return lat, lng

                    coords = loc.get("coordinates")
                    if isinstance(coords, (list, tuple)) and len(coords) >= 2:
                        lng = to_float(coords[0])
                        lat = to_float(coords[1])
                        if lat is not None and lng is not None and abs(lat) <= 90 and abs(lng) <= 180:
                            return lat, lng

                if isinstance(loc, (list, tuple)) and len(loc) >= 2:
                    first = to_float(loc[0])
                    second = to_float(loc[1])
                    if first is not None and second is not None:
                        # Prefer [lat, lng] when it looks valid, otherwise GeoJSON [lng, lat].
                        if abs(first) <= 90 and abs(second) <= 180:
                            return first, second
                        if abs(second) <= 90 and abs(first) <= 180:
                            return second, first

        return None, None

if "_au_clean_zone_name" not in globals():
    def _au_clean_zone_name(zone):
        zone = str(zone or "").strip()
        return zone if zone else "Main Zone"

if "_au_delivery_user_rows" not in globals():
    def _au_delivery_user_rows():
        rows = []
        try:
            delivery_users = list(mongo.users.find({"role": {"$in": ["delivery", "delivery_boy", "delivery_partner"]}}).sort("created_at", -1))
        except Exception:
            delivery_users = []

        for user_doc in delivery_users:
            row = _au_user_base_row(user_doc)
            uid = row.get("id")
            availability = {}
            try:
                availability = mongo.delivery_availability.find_one({"user_id": uid}) or {}
            except Exception:
                availability = {}

            total_completed_orders = 0
            currently_assigned_orders = 0
            try:
                total_completed_orders = mongo.orders.count_documents({
                    "$or": [
                        {"delivery_partner_id": uid},
                        {"delivery_user_id": uid},
                        {"delivery_boy_id": uid},
                    ],
                    "status": {"$in": ["DELIVERED", "delivered", "completed", "COMPLETED"]}
                })
                currently_assigned_orders = mongo.orders.count_documents({
                    "$or": [
                        {"delivery_partner_id": uid},
                        {"delivery_user_id": uid},
                        {"delivery_boy_id": uid},
                    ],
                    "status": {"$in": ["ASSIGNED", "assigned", "OUT_FOR_DELIVERY", "out_for_delivery", "picked_up", "PICKED_UP"]}
                })
            except Exception:
                pass

            lat, lng = _au_extract_lat_lng(availability, user_doc)
            zone = _au_clean_zone_name(availability.get("zone") or user_doc.get("zone"))
            is_online = bool(
                availability.get("active")
                or availability.get("is_online")
                or availability.get("online")
                or availability.get("available")
                or availability.get("is_available")
                or str(availability.get("status") or "").strip().lower() == "online"
            )

            row.update({
                "zone": zone,
                "is_online": is_online,
                "latitude": lat,
                "longitude": lng,
                "rating": _au_safe_float(availability.get("rating") or user_doc.get("rating"), 0),
                "total_completed_orders": total_completed_orders,
                "currently_assigned_orders": currently_assigned_orders,
                "assigned_orders": currently_assigned_orders,
            })
            rows.append(row)
        return rows

if "_au_customer_rows" not in globals():
    def _au_customer_rows():
        try:
            customer_users = list(mongo.users.find({"role": {"$in": ["customer", "user"]}}).sort("created_at", -1))
        except Exception:
            customer_users = []

        rows = []
        for user_doc in customer_users:
            row = _au_user_base_row(user_doc)
            uid = row.get("id")
            total_order = 0
            total_amount = 0.0
            try:
                order_query = {"$or": [{"user_id": uid}, {"customer_id": uid}, {"customer_user_id": uid}]}
                total_order = mongo.orders.count_documents(order_query)
                pipeline = [
                    {"$match": order_query},
                    {"$group": {"_id": None, "amount": {"$sum": {"$ifNull": ["$total_amount", 0]}}}}
                ]
                agg = list(mongo.orders.aggregate(pipeline))
                total_amount = _au_safe_float(agg[0].get("amount")) if agg else 0.0
            except Exception:
                pass
            row.update({
                "total_order": total_order,
                "total_order_amount": _au_money(total_amount),
            })
            rows.append(row)
        return rows

if "_au_review_metrics" not in globals():
    def _au_review_metrics():
        rating_values = []
        for collection_name in ["customer_reviews", "reviews", "product_reviews", "store_reviews"]:
            try:
                collection = getattr(mongo, collection_name)
                for review in collection.find({}, {"rating": 1, "stars": 1, "score": 1}):
                    rating = _au_safe_float(review.get("rating") or review.get("stars") or review.get("score"), 0)
                    if rating > 0:
                        rating_values.append(rating)
            except Exception:
                pass

        total = len(rating_values)
        if total <= 0:
            return {
                "review_received": 0,
                "positive_pct": 0,
                "good_pct": 0,
                "neutral_pct": 0,
                "negative_pct": 0,
            }

        positive = sum(1 for rating in rating_values if rating >= 4.5)
        good = sum(1 for rating in rating_values if 4.0 <= rating < 4.5)
        neutral = sum(1 for rating in rating_values if 3.0 <= rating < 4.0)
        negative = max(total - positive - good - neutral, 0)

        def pct(value):
            return round((value / total) * 100)

        return {
            "review_received": total,
            "positive_pct": pct(positive),
            "good_pct": pct(good),
            "neutral_pct": pct(neutral),
            "negative_pct": pct(negative),
        }

if "_au_user_overview_data" not in globals():
    def _au_user_overview_data():
        all_users = [_au_user_base_row(user_doc) for user_doc in _au_all_users()]
        customers = _au_customer_rows()
        delivery_users = _au_delivery_user_rows()
        store_users = _au_store_user_rows()

        now = datetime.utcnow()
        current_year = now.year
        new_cutoff = now - timedelta(days=30)

        month_labels = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        customer_growth_values = [0] * 12
        for row in customers:
            dt_obj = _au_parse_date(row.get("created_at"))
            if dt_obj and dt_obj.year == current_year and 1 <= dt_obj.month <= 12:
                customer_growth_values[dt_obj.month - 1] += 1

        def is_new(row):
            dt_obj = _au_parse_date(row.get("created_at"))
            return bool(dt_obj and dt_obj >= new_cutoff)

        metrics = {
            "total_users": len(all_users),
            "total_customers": len(customers),
            "active_customers": sum(1 for row in customers if row.get("is_active")),
            "blocked_customers": sum(1 for row in customers if not row.get("is_active")),
            "new_customers": sum(1 for row in customers if is_new(row)),
            "total_delivery_users": len(delivery_users),
            "active_delivery_users": sum(1 for row in delivery_users if row.get("is_active")),
            "inactive_delivery_users": sum(1 for row in delivery_users if not row.get("is_active")),
            "blocked_delivery_users": sum(1 for row in delivery_users if not row.get("is_active")),
            "new_delivery_users": sum(1 for row in delivery_users if is_new(row)),
            "total_store_users": len(store_users),
            "active_store_users": sum(1 for row in store_users if row.get("is_active")),
            "blocked_store_users": sum(1 for row in store_users if not row.get("is_active")),
            "new_store_users": sum(1 for row in store_users if is_new(row)),
        }
        metrics.update(_au_review_metrics())

        return {
            "metrics": metrics,
            "month_labels": month_labels,
            "customer_growth_values": customer_growth_values,
            "top_deliverymen": sorted(delivery_users, key=lambda row: _au_safe_int(row.get("total_completed_orders")), reverse=True)[:6],
            "top_store_users": sorted(store_users, key=lambda row: _au_safe_int(row.get("orders")), reverse=True)[:6],
            "recent_users": all_users[:5],
            "current_year": current_year,
        }

if "_au_export_users_csv_response" not in globals():
    def _au_export_users_csv_response(rows, filename):
        export_rows = [["Name", "Email", "Phone", "Role", "Status", "Joined"]]
        for row in rows or []:
            export_rows.append([
                row.get("store_name") or row.get("name") or "",
                row.get("email") or "",
                row.get("phone") or "",
                row.get("role") or "",
                "Active" if row.get("is_active") else "Disabled",
                row.get("created_at") or "",
            ])
        return _admin_csv_response(export_rows, filename)



def _admin_attach_full_contact_rows(rows):
    hydrated = []
    for row in rows or []:
        row = dict(row or {})
        email = str(row.get("email") or row.get("email_display") or "").strip()
        phone = str(row.get("phone") or row.get("phone_display") or "").strip()

        if not email or not phone:
            uid = str(row.get("id") or row.get("_id") or row.get("user_id") or row.get("owner_id") or "").strip()
            user_doc = None
            if uid:
                try:
                    user_doc = mongo.users.find_one({"_id": ObjectId(uid)})
                except Exception:
                    try:
                        user_doc = mongo.users.find_one({"_id": uid})
                    except Exception:
                        user_doc = None

            user_doc = user_doc or {}
            email = email or str(user_doc.get("email") or "").strip()
            phone = phone or str(user_doc.get("phone") or "").strip()

        row["email"] = email
        row["phone"] = phone
        row["email_display"] = email
        row["phone_display"] = phone
        hydrated.append(row)
    return hydrated

def _admin_user_overview_selected_zone():
    return str(request.args.get("zone") or "").strip()


def _admin_user_overview_delivery_zone_options(seed_rows=None):
    zones = []

    def add_zone(value):
        value = str(value or "").strip()
        if value and value.lower() not in [z.lower() for z in zones]:
            zones.append(value)

    for row in seed_rows or []:
        add_zone(row.get("zone"))

    try:
        for zone in mongo.delivery_availability.distinct("zone"):
            add_zone(zone)
    except Exception:
        pass

    try:
        for store in mongo.stores.find({}, {"zone": 1, "zone_name": 1, "delivery_zone_name": 1, "service_zone_name": 1}):
            add_zone(store.get("delivery_zone_name") or store.get("service_zone_name") or store.get("zone_name") or store.get("zone"))
    except Exception:
        pass

    return sorted(zones, key=lambda item: item.lower())


def _admin_user_overview_delivery_rows_for_zone(selected_zone):
    selected_zone = str(selected_zone or "").strip().lower()
    try:
        rows = list(_au_delivery_user_rows())
    except Exception:
        rows = []

    if not selected_zone:
        return rows

    availability_by_user_id = {}
    try:
        for availability in mongo.delivery_availability.find({}, {"user_id": 1, "zone": 1}):
            availability_by_user_id[str(availability.get("user_id") or "")] = availability
    except Exception:
        pass

    filtered = []
    for row in rows:
        uid = str(row.get("id") or row.get("_id") or row.get("user_id") or "")
        availability = availability_by_user_id.get(uid) or {}
        row_zone = str(row.get("zone") or availability.get("zone") or "").strip()
        if row_zone.lower() == selected_zone:
            row["zone"] = row_zone
            filtered.append(row)
    return filtered


def _admin_user_overview_active_delivery_locations(selected_zone=""):
    selected_zone_lower = str(selected_zone or "").strip().lower()
    rows = []

    try:
        availability_rows = list(mongo.delivery_availability.find({}))
    except Exception:
        availability_rows = []

    for availability in availability_rows:
        user_id = str(availability.get("user_id") or availability.get("delivery_user_id") or availability.get("delivery_partner_id") or "").strip()
        if not user_id:
            continue

        is_online = bool(
            availability.get("active")
            or availability.get("is_online")
            or availability.get("online")
            or availability.get("available")
            or availability.get("is_available")
            or str(availability.get("status") or "").strip().lower() in ["online", "active", "available"]
        )

        if not is_online:
            continue

        zone = _au_clean_zone_name(availability.get("zone"))
        if selected_zone_lower and zone.lower() != selected_zone_lower:
            continue

        user_doc = None
        try:
            user_doc = mongo.users.find_one({"_id": ObjectId(user_id)})
        except Exception:
            try:
                user_doc = mongo.users.find_one({"_id": user_id})
            except Exception:
                user_doc = None

        user_doc = user_doc or {}
        if user_doc and str(user_doc.get("role") or "").strip().lower() not in ["delivery", "delivery_boy", "delivery_partner"]:
            continue

        lat, lng = _au_extract_lat_lng(availability, user_doc)
        if lat is None or lng is None:
            continue

        assigned_orders = 0
        completed_orders = 0
        try:
            assigned_orders = mongo.orders.count_documents({
                "$or": [
                    {"delivery_partner_id": user_id},
                    {"delivery_user_id": user_id},
                    {"delivery_boy_id": user_id},
                ],
                "status": {"$in": ["ASSIGNED", "assigned", "OUT_FOR_DELIVERY", "out_for_delivery", "picked_up", "PICKED_UP"]}
            })
            completed_orders = mongo.orders.count_documents({
                "$or": [
                    {"delivery_partner_id": user_id},
                    {"delivery_user_id": user_id},
                    {"delivery_boy_id": user_id},
                ],
                "status": {"$in": ["DELIVERED", "delivered", "completed", "COMPLETED"]}
            })
        except Exception:
            assigned_orders = _au_safe_int(availability.get("assigned_orders") or availability.get("currently_assigned_orders"), 0)
            completed_orders = _au_safe_int(availability.get("completed_orders") or availability.get("total_completed_orders"), 0)

        phone_value = str(user_doc.get("phone") or availability.get("phone") or "").strip()
        email_value = str(user_doc.get("email") or availability.get("email") or "").strip()

        rows.append({
            "id": user_id,
            "name": _au_user_display_name(user_doc) if user_doc else (availability.get("name") or "Delivery Staff"),
            "phone": phone_value,
            "email": email_value,
            "phone_masked": _au_mask_phone(phone_value),
            "email_masked": _au_mask_email(email_value),
            "zone": zone,
            "latitude": lat,
            "longitude": lng,
            "rating": _au_safe_float(availability.get("rating") or user_doc.get("rating"), 0),
            "assigned_orders": assigned_orders,
            "total_completed_orders": completed_orders,
        })

    rows.sort(key=lambda row: (_au_safe_int(row.get("assigned_orders")), _au_safe_int(row.get("total_completed_orders"))), reverse=True)
    return rows


@app.route('/admin/users')
@login_required(role='admin')
def admin_users():
    selected_delivery_zone = _admin_user_overview_selected_zone()
    delivery_settings = get_delivery_mode_settings()
    in_house_delivery_enabled = bool(delivery_settings.get("in_house_delivery_enabled", True))

    data = _au_user_overview_data()

    try:
        data["recent_users"] = [_admin_full_user_row(user_doc) for user_doc in _au_all_users()[:5]]
    except Exception:
        data["recent_users"] = (data.get("recent_users") or [])[:5]

    data["top_deliverymen"] = _admin_attach_full_contact_rows(data.get("top_deliverymen") or [])
    data["top_store_users"] = _admin_attach_full_contact_rows(data.get("top_store_users") or [])

    delivery_zone_options = _admin_user_overview_delivery_zone_options(
        data.get("top_deliverymen") or []
    )

    if selected_delivery_zone and selected_delivery_zone.lower() not in [zone.lower() for zone in delivery_zone_options]:
        selected_delivery_zone = ""

    if selected_delivery_zone:
        zone_delivery_rows = _admin_user_overview_delivery_rows_for_zone(selected_delivery_zone)
        data["top_deliverymen"] = sorted(
            zone_delivery_rows,
            key=lambda row: _au_safe_int(row.get("total_completed_orders")),
            reverse=True
        )[:6]

        data["top_deliverymen"] = _admin_attach_full_contact_rows(data.get("top_deliverymen") or [])

        data["metrics"].update({
            "total_delivery_users": len(zone_delivery_rows),
            "active_delivery_users": sum(1 for row in zone_delivery_rows if row.get("is_active")),
            "inactive_delivery_users": sum(1 for row in zone_delivery_rows if not row.get("is_active")),
            "blocked_delivery_users": sum(1 for row in zone_delivery_rows if not row.get("is_active")),
            "new_delivery_users": sum(
                1
                for row in zone_delivery_rows
                if (_au_parse_date(row.get("created_at")) or datetime.min) >= (datetime.utcnow() - timedelta(days=30))
            ),
        })

    active_delivery_locations = _admin_user_overview_active_delivery_locations(selected_delivery_zone)

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
        in_house_delivery_enabled=in_house_delivery_enabled,
        delivery_zone_options=delivery_zone_options,
        selected_delivery_zone=selected_delivery_zone,
        active_delivery_locations=active_delivery_locations,
    )


@app.route('/admin/users/all')
@login_required(role='admin')
def admin_all_users():
    search = request.args.get("search", "").strip()
    role_filter = request.args.get("role", "").strip().lower()
    status_filter = request.args.get("status", "").strip().lower()

    try:
        rows = [_admin_full_user_row(user_doc) for user_doc in _au_all_users()]
    except Exception:
        rows = []

    role_options = sorted(
        {str(row.get("role") or "user").strip().lower() for row in rows if str(row.get("role") or "").strip()},
        key=lambda item: item.lower()
    )

    if role_filter:
        rows = [row for row in rows if str(row.get("role") or "").strip().lower() == role_filter]

    rows = _au_filter_rows_by_status(rows, status_filter)
    rows = _au_filter_rows_by_search(rows, search)

    metrics = {
        "total": len(rows),
        "active": sum(1 for row in rows if row.get("is_active")),
        "disabled": sum(1 for row in rows if not row.get("is_active")),
        "roles": len(role_options),
    }

    return render_template(
        "admin_all_users.html",
        user=current_user(),
        active_group="users",
       active_page="all_users",
        users=rows,
        metrics=metrics,
        search=search,
        role_filter=role_filter,
        status_filter=status_filter,
        role_options=role_options,
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
    new_only = request.args.get("new", "").strip().lower()

    rows = _au_customer_rows()
    rows = _au_filter_rows_by_status(rows, status)
    rows = _au_filter_rows_by_search(rows, search)

    if new_only in ["1", "true", "yes"]:
        new_cutoff = datetime.utcnow() - timedelta(days=30)
        rows = [
            row for row in rows
            if (_au_parse_date(row.get("created_at")) or datetime.min) >= new_cutoff
        ]

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
        new_only=new_only,
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




def _admin_profile_clean_email(value):
    return (value or "").strip().lower()


def _admin_profile_email_is_valid(email):
    email = _admin_profile_clean_email(email)

    if not email:
        return False

    if len(email) > 254:
        return False

    if " " in email or "\t" in email or "\n" in email:
        return False

    if email.count("@") != 1:
        return False

    local, domain = email.split("@", 1)

    if not local or not domain:
        return False

    if len(local) > 64:
        return False

    if "." not in domain:
        return False

    if domain.startswith(".") or domain.endswith("."):
        return False

    if ".." in email:
        return False

    return bool(re.match(r"^[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}$", email, re.IGNORECASE))


def _admin_profile_email_exists_for_other_user(email, user_oid):
    email = _admin_profile_clean_email(email)

    if not email:
        return False

    existing = mongo.users.find_one({
        "_id": {"$ne": user_oid},
        "email": {
            "$regex": "^" + re.escape(email) + "$",
            "$options": "i"
        }
    })

    return bool(existing)


def _admin_profile_password_error(new_password, admin_user):
    password = new_password or ""

    if not password:
        return "Please enter a new password."

    if len(password) < 8:
        return "New password must be at least 8 characters long."

    if len(password) > 128:
        return "New password cannot be more than 128 characters long."

    if re.search(r"\s", password):
        return "New password cannot contain spaces."

    if not re.search(r"[A-Za-z]", password):
        return "New password must include at least one letter."

    if not re.search(r"\d", password):
        return "New password must include at least one number."

    password_lower = password.lower()
    email = (admin_user.get("email") or "").strip().lower()
    name = (admin_user.get("name") or "").strip().lower()
    phone = str(admin_user.get("phone") or "").strip().lower()

    if email and password_lower == email:
        return "New password cannot be the same as your email."

    if name and len(name) >= 3 and password_lower == name:
        return "New password cannot be the same as your name."

    if phone and len(phone) >= 6 and phone in password_lower:
        return "New password cannot contain your phone number."

    common_passwords = {
        "password",
        "password123",
        "admin123",
        "admin1234",
        "12345678",
        "123456789",
        "qwerty123",
        "welcome123",
    }

    if password_lower in common_passwords:
        return "Please choose a stronger password."

    return ""


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
            phone_raw = request.form.get("phone") or ""
            phone = normalize_phone(phone_raw)

            if not name:
                flash("Admin name is required.", "warning")
                return redirect(url_for("admin_profile"))

            if len(name) > 120:
                flash("Admin name cannot be more than 120 characters.", "warning")
                return redirect(url_for("admin_profile"))

            if phone_raw.strip() and (len(phone) < 7 or len(phone) > 15):
                flash("Please enter a valid phone number.", "warning")
                return redirect(url_for("admin_profile"))

            update_data = {
                "name": name,
                "updated_at": now,
                "profile_updated_at": now,
            }

            if phone:
                update_data["phone"] = phone

            mongo.users.update_one({"_id": user_oid, "role": "admin"}, {"$set": update_data})
            flash("Admin profile updated successfully.", "success")
            return redirect(url_for("admin_profile"))

        if action == "change_email":
            current_password = request.form.get("current_password") or ""
            new_email = _admin_profile_clean_email(request.form.get("new_email") or "")
            current_email = _admin_profile_clean_email(admin_user.get("email") or "")

            if not current_password:
                flash("Current password is required to change email.", "warning")
                return redirect(url_for("admin_profile"))

            if not check_password_hash(admin_user.get("password_hash", ""), current_password):
                flash("Current password is incorrect. Email was not changed.", "danger")
                return redirect(url_for("admin_profile"))

            if not _admin_profile_email_is_valid(new_email):
                flash("Please enter a valid new email address.", "warning")
                return redirect(url_for("admin_profile"))

            if new_email == current_email:
                flash("Current email cannot be used as the new email.", "warning")
                return redirect(url_for("admin_profile"))

            if _admin_profile_email_exists_for_other_user(new_email, user_oid):
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
            password_hash = admin_user.get("password_hash", "")

            if not current_password:
                flash("Current password is required to change password.", "warning")
                return redirect(url_for("admin_profile"))

            if not check_password_hash(password_hash, current_password):
                flash("Current password is incorrect. Password was not changed.", "danger")
                return redirect(url_for("admin_profile"))

            if not new_password or not confirm_password:
                flash("Please enter and confirm the new password.", "warning")
                return redirect(url_for("admin_profile"))

            if new_password != confirm_password:
                flash("New password and confirm password do not match.", "warning")
                return redirect(url_for("admin_profile"))

            if check_password_hash(password_hash, new_password):
                flash("New password cannot be the same as your current password.", "warning")
                return redirect(url_for("admin_profile"))

            password_error = _admin_profile_password_error(new_password, admin_user)
            if password_error:
                flash(password_error, "warning")
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
