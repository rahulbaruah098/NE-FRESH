"""Admin routes extracted from the updated app.py.

Logic, decorators, endpoint names and route paths are intentionally preserved.
Only the file location changed.
"""

from app_core import *
from services.payment_gateway import (
    _admin_get_payment_gateway_settings,
    _admin_get_razorpay_env_status,
)
from services.refund_policy import _admin_get_return_refund_policy_settings
from services.finance_actions import (
    build_delivery_monthly_batch_doc,
    build_rider_cash_received_state,
    build_store_platform_fee_received_state,
    build_upi_delivery_verified_state,
    calculate_refund_finance_state,
    calculate_store_payout_base,
)
from services.platform_fees import calculate_platform_fee, get_platform_fee_settings
from services.finance_reconciliation import finance_reconciliation_snapshot
from services.store_finance_adjustments import (
    FINANCE_STORE_ADJUSTMENT_OPEN,
    FINANCE_STORE_ADJUSTMENT_PARTIAL,
    finance_apply_store_adjustments,
    finance_create_store_adjustment,
    finance_rollback_store_adjustments,
    finance_store_outstanding_adjustment_total,
)
from services.delivery_monthly_settlement import (
    DELIVERY_MONTHLY_BATCH_STATUS_PAID,
    DELIVERY_MONTHLY_STATUS_PAID,
    DELIVERY_PAYOUT_MODEL_MONTHLY_V1,
    delivery_monthly_current_period,
    delivery_monthly_payment_is_reconciled,
    delivery_monthly_period_from_utc,
    delivery_monthly_period_is_closed,
    delivery_monthly_period_label,
    delivery_partner_id_values,
)
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













# =========================================================
# ADMIN - ONLINE PAYMENT / RAZORPAY SETTINGS
# =========================================================







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

# Export the complete compatibility namespace, including underscore-prefixed
# legacy helpers, to the domain route modules.  This is transitional and will
# be replaced by explicit imports as app_core.py and route helpers continue to shrink.
__all__ = [name for name in globals() if not name.startswith('__')]
