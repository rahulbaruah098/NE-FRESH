"""Delivery routes extracted from the updated app.py.

Logic, decorators, endpoint names and route paths are intentionally preserved.
Only the file location changed.
"""

from app_core import *
from services.delivery_monthly_settlement import (
    DELIVERY_MONTHLY_BATCH_STATUS_PAID,
    DELIVERY_MONTHLY_STATUS_ACCRUED,
    DELIVERY_MONTHLY_STATUS_PAID,
    DELIVERY_PAYOUT_MODEL_MONTHLY_V1,
    delivery_monthly_current_period,
    delivery_monthly_payment_is_reconciled,
    delivery_monthly_period_from_utc,
    delivery_monthly_period_label,
    delivery_order_uses_monthly_payout,
)
from services.order_lifecycle import DELIVERY_STATUS_ALLOWED, is_delivery_transition_allowed
import qrcode
from urllib.parse import urlencode


def _delivery_disabled_response():
    wants_json = (
        request.path.startswith("/api/")
        or request.path.startswith("/delivery/api/")
        or request.is_json
        or "application/json" in (request.headers.get("Accept") or "")
    )

    if wants_json:
        return jsonify({
            "ok": False,
            "disabled": True,
            "error": "In-house delivery is currently disabled by Admin."
        }), 403

    html = """
    <!doctype html>
    <html>
      <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>Delivery Disabled | NE FRESH</title>
        <style>
          body{
            margin:0;
            min-height:100vh;
            display:flex;
            align-items:center;
            justify-content:center;
            background:#f8fafc;
            font-family:Arial,sans-serif;
            color:#0f172a;
            padding:20px;
          }
          .box{
            width:min(560px,100%);
            background:#fff;
            border:1px solid #e2e8f0;
            border-radius:22px;
            box-shadow:0 18px 50px rgba(15,23,42,.08);
            padding:26px;
            text-align:center;
          }
          h1{
            margin:0 0 10px;
            font-size:1.5rem;
          }
          p{
            margin:0;
            color:#64748b;
            line-height:1.6;
            font-weight:600;
          }
          a{
            display:inline-flex;
            margin-top:18px;
            min-height:42px;
            align-items:center;
            justify-content:center;
            padding:0 16px;
            border-radius:12px;
            background:#00a859;
            color:#fff;
            text-decoration:none;
            font-weight:800;
          }
        </style>
      </head>
      <body>
        <div class="box">
          <h1>In-house delivery is disabled</h1>
          <p>
            Admin has turned off NE FRESH in-house delivery-boy operations.
            Delivery-boy panel, live tracking, COD rider collection and delivery actions are currently unavailable.
          </p>
          <a href="/logout">Logout</a>
        </div>
      </body>
    </html>
    """

    return Response(html, status=403, mimetype="text/html")


@app.before_request
def _block_delivery_panel_when_in_house_disabled():
    endpoint = request.endpoint or ""

    is_delivery_endpoint = (
        endpoint.startswith("delivery_")
        or endpoint == "api_delivery_availability"
    )

    if not is_delivery_endpoint:
        return None

    if is_delivery_feature_enabled("delivery_boy_panel_enabled", True):
        return None

    return _delivery_disabled_response()


def _delivery_portal_endpoint_allowed(endpoint):
    endpoint = (endpoint or "").strip()
    if not endpoint:
        return True
    if endpoint == "static" or endpoint == "logout":
        return True
    if endpoint == "api_delivery_availability":
        return True
    if endpoint.startswith("delivery_"):
        return True
    if endpoint.startswith("dev_"):
        return True
    return False


@app.before_request
def _keep_delivery_accounts_inside_delivery_portal():
    """Keep logged-in Delivery Partner accounts inside their role portal.

    This prevents Delivery accounts from accidentally entering customer-profile,
    shopping/account or public contact shells through internal links. External
    navigation (for example Google Maps directions) is unaffected because it
    never reaches this Flask guard.
    """
    user = current_user()
    if not user or str(user.get("role") or "").strip().lower() != "delivery":
        return None

    endpoint = request.endpoint or ""
    if _delivery_portal_endpoint_allowed(endpoint):
        return None

    if request.method in {"GET", "HEAD", "OPTIONS"}:
        flash("That page is outside the Delivery Partner portal.", "warning")
        return redirect(url_for("delivery_dashboard"))

    abort(403)






def _delivery_notification_user_values(user_id):
    values = [str(user_id)]

    try:
        user_id_str = str(user_id)
        if ObjectId.is_valid(user_id_str):
            values.append(ObjectId(user_id_str))
    except Exception:
        pass

    return values


def _delivery_notification_order_values(order_id):
    values = [str(order_id)]

    try:
        order_id_str = str(order_id)
        if ObjectId.is_valid(order_id_str):
            values.append(ObjectId(order_id_str))
    except Exception:
        pass

    return values

def _delivery_money_float(value, default=0.0):
    try:
        if value is None or str(value).strip() == "":
            return float(default)
        return float(value)
    except Exception:
        return float(default)


def _delivery_pay_on_delivery_upi_settings():
    settings = get_delivery_mode_settings()
    upi_id = (settings.get("pay_on_delivery_upi_id") or "").strip()
    payee_name = (settings.get("pay_on_delivery_upi_name") or "NE LOCALS").strip() or "NE LOCALS"
    enabled = bool(settings.get("pay_on_delivery_upi_enabled", False) and upi_id)

    return {
        "enabled": enabled,
        "upi_id": upi_id,
        "payee_name": payee_name,
    }


def _decorate_delivery_financials(order):
    """
    Adds delivery-boy-facing money fields.

    This does not change database values.
    It only prepares display values for delivery dashboard/templates.
    """
    order = order or {}

    items_subtotal = _delivery_money_float(
        order.get("items_subtotal")
        if order.get("items_subtotal") is not None
        else order.get("total_amount")
    )

    delivery_fee = _delivery_money_float(order.get("delivery_fee"))
    platform_fee = _delivery_money_float(order.get("platform_fee"))
    tip_amount = _delivery_money_float(
        order.get("tip_amount")
        if order.get("tip_amount") is not None
        else order.get("delivery_tip_amount")
    )

    total_payable = _delivery_money_float(
        order.get("total_payable"),
        items_subtotal + delivery_fee + platform_fee + tip_amount
    )

    payment_method = (order.get("payment_method") or "COD").strip().upper()
    payment_status = (order.get("payment_status") or "PENDING").strip().upper()
    payment_collection_status = (order.get("payment_collection_status") or "").strip().upper()
    payment_collection_channel = (order.get("payment_collection_channel") or "").strip().upper()
    upi_delivery_reconciliation_status = (order.get("upi_delivery_reconciliation_status") or "").strip().upper()

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
    is_cod_cash = is_cod_order and not is_cod_upi
    is_cod_collected = bool(
        is_cod_order
        and (
            payment_status in collected_payment_statuses
            or payment_collection_status in {"COLLECTED", "PAID"}
        )
    )

    if is_cod_order and not is_cod_collected:
        amount_to_collect = total_payable
        collect_label = "Collect from customer"
    elif is_cod_collected:
        amount_to_collect = 0.0
        collect_label = "UPI paid at delivery" if is_cod_upi else "COD cash collected"
    else:
        amount_to_collect = 0.0
        collect_label = "No cash collection"

    free_delivery_applied = bool(order.get("free_delivery_above_applied"))

    original_delivery_fee = _delivery_money_float(
        order.get("original_delivery_fee"),
        delivery_fee
    )

    free_delivery_savings = _delivery_money_float(
        order.get("free_delivery_savings"),
        original_delivery_fee if free_delivery_applied else 0
    )

    delivery_boy_expected_earning = delivery_fee + tip_amount

    order["items_subtotal"] = round(items_subtotal, 2)
    order["delivery_fee"] = round(delivery_fee, 2)
    order["platform_fee"] = round(platform_fee, 2)
    order["tip_amount"] = round(tip_amount, 2)
    order["total_payable"] = round(total_payable, 2)

    order["payment_method"] = payment_method
    order["payment_status"] = payment_status
    order["payment_collection_status"] = payment_collection_status
    order["payment_collection_channel"] = payment_collection_channel
    order["upi_delivery_reconciliation_status"] = upi_delivery_reconciliation_status
    order["collection_channel_label"] = (
        "UPI" if is_cod_upi
        else ("Cash" if is_cod_order else ("Razorpay" if payment_collection_channel == "RAZORPAY" else "Online"))
    )

    order["amount_to_collect"] = round(amount_to_collect, 2)
    order["collect_label"] = collect_label
    order["is_cod_order"] = is_cod_order
    order["is_cod_collected"] = is_cod_collected

    order["delivery_boy_expected_earning"] = round(delivery_boy_expected_earning, 2)
    order["delivery_fee_plus_tip"] = round(delivery_boy_expected_earning, 2)

    # ------------------------------------------------------------
    # Delivery-boy-facing COD settlement fields.
    # Read-only for delivery panel. Admin controls settlement.
    # ------------------------------------------------------------
    cod_collected_amount = (
        _delivery_money_float(
            order.get("cod_collected_amount"),
            total_payable
        )
        if is_cod_collected
        else 0.0
    )

    delivery_boy_earning = _delivery_money_float(
        order.get("delivery_boy_earning"),
        delivery_boy_expected_earning
    )

    # MONTHLY_V1 (and any still-undelivered in-house order that will enter
    # MONTHLY_V1 at successful delivery) treats the FULL customer COD amount
    # as business cash. Only already-delivered legacy records retain the old
    # historical net-remittance fallback so they are not rewritten/repaid.
    order_status = (order.get("status") or "").strip().upper()
    is_monthly_delivery_payout = delivery_order_uses_monthly_payout(order)
    uses_full_business_cash_model = bool(
        is_monthly_delivery_payout or order_status != "DELIVERED"
    )
    expected_cash_fallback = (
        total_payable
        if uses_full_business_cash_model
        else max(total_payable - delivery_boy_earning, 0)
    )

    expected_rider_cash_to_submit = (
        0.0
        if is_cod_upi
        else (
            _delivery_money_float(
                order.get("expected_rider_cash_to_submit"),
                expected_cash_fallback
            )
            if is_cod_order
            else 0.0
        )
    )

    rider_cash_to_submit = (
        0.0
        if is_cod_upi
        else (
            _delivery_money_float(
                order.get("rider_cash_to_submit"),
                expected_rider_cash_to_submit
            )
            if is_cod_collected
            else 0.0
        )
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

    order["cod_collected_amount"] = round(cod_collected_amount, 2)
    order["cod_display_amount"] = round(cod_display_amount, 2)
    order["cod_display_label"] = cod_display_label
    order["delivery_boy_earning"] = round(delivery_boy_earning, 2)
    order["is_monthly_delivery_payout"] = is_monthly_delivery_payout
    order["delivery_boy_payout_status"] = order.get("delivery_boy_payout_status") or ""
    order["rider_cash_to_submit"] = round(rider_cash_to_submit, 2)
    order["expected_rider_cash_to_submit"] = round(expected_rider_cash_to_submit, 2)
    order["rider_cash_settlement_status"] = order.get("rider_cash_settlement_status") or "NOT_REQUIRED"
    order["rider_cash_received_at"] = order.get("rider_cash_received_at") or ""
    order["platform_fee_status"] = order.get("platform_fee_status") or ""
    order["order_settlement_status"] = order.get("order_settlement_status") or ""
    order["settlement_status"] = order.get("settlement_status") or ""

    order["free_delivery_above_applied"] = free_delivery_applied
    order["original_delivery_fee"] = round(original_delivery_fee, 2)
    order["free_delivery_savings"] = round(free_delivery_savings, 2)
    order["free_delivery_above"] = _delivery_money_float(order.get("free_delivery_above"))

    return order

def _hydrate_delivery_notification(n):
    if not n:
        return {}

    def _safe_str(value):
        if value is None:
            return ""
        try:
            if isinstance(value, ObjectId):
                return str(value)
        except Exception:
            pass
        return str(value)

    return {
        "id": _safe_str(n.get("_id")),
        "delivery_user_id": _safe_str(n.get("delivery_user_id")),
        "title": n.get("title") or "Notification",
        "message": n.get("message") or "",
        "type": n.get("type") or "system",
        "order_id": _safe_str(n.get("order_id")),
        "order_ref": _safe_str(n.get("order_ref") or n.get("order_id")),
        "store_id": _safe_str(n.get("store_id")),
        "store_name": n.get("store_name") or "",
        "is_read": bool(n.get("is_read")),
        "is_active": bool(n.get("is_active", True)),
        "event_key": n.get("event_key") or "",
        "target_url": n.get("target_url") or "",
        "created_at": n.get("created_at") or "",
        "updated_at": n.get("updated_at") or "",
        "read_at": n.get("read_at") or "",
    }


def _create_delivery_notification(delivery_user_id, title, message, notif_type="system", order=None, event_key=None, target_url=None):
    now = datetime.utcnow().isoformat()

    delivery_user_id_str = str(delivery_user_id)

    if event_key:
        existing = mongo.delivery_notifications.find_one({
            "delivery_user_id": {"$in": _delivery_notification_user_values(delivery_user_id)},
            "event_key": event_key
        })

        if existing:
            return existing

    order_id = None
    store_id = None
    store_name = ""

    if order:
        order_id = order.get("_id") or order.get("id")
        store_id = order.get("store_id")
        store_name = order.get("store_name") or ""

    doc = {
        "delivery_user_id": delivery_user_id_str,
        "title": title,
        "message": message,
        "type": notif_type,
        "order_id": order_id,
        "order_ref": str(order_id) if order_id else "",
        "store_id": store_id,
        "store_name": store_name,
        "target_url": target_url or url_for("delivery_available_orders"),
        "event_key": event_key or f"delivery-notification-{delivery_user_id_str}-{now}",
        "is_read": False,
        "is_active": True,
        "created_at": now,
        "updated_at": now
    }

    result = mongo.delivery_notifications.insert_one(doc)
    doc["_id"] = result.inserted_id

    return doc


def _sync_delivery_available_order_notifications(delivery_user, availability):
    """
    Creates notification rows for available READY_FOR_PICKUP orders
    visible to this delivery boy.

    This does not assign the order.
    It only notifies the delivery boy.
    """
    if not delivery_user or not availability or not availability.get("active"):
        return []

    raw_orders = list(
        mongo.orders.find({
            "$and": [
                {
                    "$or": [
                        {"delivery_partner_id": None},
                        {"delivery_partner_id": ""},
                        {"delivery_partner_id": {"$exists": False}}
                    ]
                },
                {
                    "status": {"$in": DELIVERY_ACTIONABLE_STATUSES}
                }
            ]
        }).sort("updated_at", -1).limit(30)
    )

    synced = []

    for order in raw_orders:
        distance_km = _driver_distance_to_store_km(order, availability)

        if distance_km is not None and distance_km > DELIVERY_ACCEPT_RADIUS_KM:
            continue

        hydrated_order = _decorate_delivery_financials(_hydrate_delivery_order(order))

        oid = str(order["_id"])
        store_name = hydrated_order.get("store_name") or "Store"

        notif = _create_delivery_notification(
            delivery_user_id=delivery_user["id"],
            title="New delivery available",
            message=f"Order #{oid[-6:]} is ready for pickup from {store_name}.",
            notif_type="new_available_order",
            order=hydrated_order,
            event_key=f"new-available-order-{str(delivery_user['id'])}-{oid}",
            target_url=url_for("delivery_available_orders")
        )

        synced.append(notif)

    return synced



















def _delivery_active_orders_for_offline_check(delivery_user_id):
    """
    Returns active orders currently assigned to this delivery boy.
    Used to block going offline while delivery work is still active.
    """

    active_statuses = set()

    try:
        active_statuses.update(DELIVERY_ASSIGNED_ACTIVE_STATUSES)
    except Exception:
        pass

    active_statuses.update([
        "ASSIGNED_TO_DELIVERY",
        "REACHED_STORE",
        "PICKED_UP",
        "OUT_FOR_DELIVERY"
    ])

    return list(
        mongo.orders.find({
            "$and": [
                {
                    "$or": [
                        {"delivery_partner_id": delivery_user_id},
                        {"delivery_partner_id": str(delivery_user_id)}
                    ]
                },
                {
                    "status": {"$in": list(active_statuses)}
                }
            ]
        }).sort("updated_at", -1)
    )

# Export the complete compatibility namespace, including underscore-prefixed
# legacy helpers, to the domain route modules.  This is transitional and will
# be replaced by explicit imports as app_core.py and route helpers continue to shrink.
__all__ = [name for name in globals() if not name.startswith('__')]
