"""Orders routes extracted from the updated app.py.

Logic, decorators, endpoint names and route paths are intentionally preserved.
Only the file location changed.
"""

from app_core import *
import razorpay

def _money_float(value, default=0.0):
    try:
        if value is None or str(value).strip() == "":
            return default
        return float(value)
    except Exception:
        return default


def _reserve_order_stock_items(order_items):
    """Atomically reserve product stock for an order.

    This prevents two simultaneous checkouts/payment confirmations from pushing
    stock below zero. If any product cannot be reserved, already reserved items
    are rolled back immediately.
    """
    reserved_items = []

    for item in order_items or []:
        product_id = item.get("product_id")
        qty = _money_float(item.get("quantity") if item.get("quantity") is not None else item.get("cart_quantity"), 0.0)

        if not product_id or qty <= 0:
            continue

        result = mongo.products.update_one(
            {
                "_id": product_id,
                "is_active": 1,
                "stock_quantity": {"$gte": qty},
            },
            {"$inc": {"stock_quantity": -qty}}
        )

        if result.modified_count != 1:
            _release_order_stock_items(reserved_items)
            return False, f"{item.get('product_name') or 'One product'} is out of stock or quantity is no longer available."

        reserved_items.append({"product_id": product_id, "quantity": qty})

        updated_product = mongo.products.find_one({"_id": product_id})
        if updated_product and float(updated_product.get("stock_quantity") or 0) <= 0:
            mongo.products.update_one(
                {"_id": product_id},
                {"$set": {"stock_quantity": 0, "is_active": 0}}
            )

    return True, ""


def _release_order_stock_items(order_items):
    """Return stock for a cancelled/expired/rolled-back order attempt."""
    for item in order_items or []:
        product_id = item.get("product_id")
        qty = _money_float(item.get("quantity") if item.get("quantity") is not None else item.get("cart_quantity"), 0.0)

        if product_id and qty > 0:
            mongo.products.update_one(
                {"_id": product_id},
                {
                    "$inc": {"stock_quantity": qty},
                    "$set": {"is_active": 1},
                }
            )


def normalize_order_money_fields(order_doc):
    """
    Normalizes money fields for old and new orders.

    Correct meaning:
    - items_subtotal = only product/items subtotal
    - store_earning = only store payable amount before refund/adjustment
    - total_amount = final customer payable amount
    - total_payable = final customer payable amount

    Backward support:
    - Old orders may have total_amount as items subtotal.
    - New orders should have total_payable saved.
    """
    if not order_doc:
        return order_doc

    delivery_fee = _money_float(
        order_doc.get("delivery_fee_amount")
        if order_doc.get("delivery_fee_amount") is not None
        else order_doc.get("delivery_fee"),
        0.0
    )
    platform_fee = _money_float(order_doc.get("platform_fee"), 0.0)
    tip_amount = _money_float(
        order_doc.get("tip_amount")
        if order_doc.get("tip_amount") is not None
        else order_doc.get("delivery_tip_amount"),
        0.0
    )

    raw_total_payable = order_doc.get("total_payable")
    raw_total_amount = order_doc.get("total_amount")
    raw_items_subtotal = order_doc.get("items_subtotal")
    raw_store_earning = order_doc.get("store_earning")

    if raw_items_subtotal is not None:
        items_subtotal = _money_float(raw_items_subtotal, 0.0)
    elif raw_store_earning is not None:
        items_subtotal = _money_float(raw_store_earning, 0.0)
    elif raw_total_payable is not None:
        items_subtotal = max(
            _money_float(raw_total_payable, 0.0) - delivery_fee - platform_fee - tip_amount,
            0.0
        )
    else:
        # Old orders stored total_amount as subtotal.
        items_subtotal = _money_float(raw_total_amount, 0.0)

    if raw_total_payable is not None:
        final_total = _money_float(raw_total_payable, 0.0)
    elif raw_total_amount is not None:
        # Old orders without total_payable used total_amount as subtotal.
        final_total = (
            _money_float(raw_total_amount, 0.0)
            + delivery_fee
            + platform_fee
            + tip_amount
        )
    else:
        final_total = items_subtotal + delivery_fee + platform_fee + tip_amount

    store_earning = _money_float(raw_store_earning, items_subtotal)

    order_doc["items_subtotal"] = round(items_subtotal, 2)
    order_doc["store_earning"] = round(store_earning, 2)
    order_doc["delivery_fee"] = round(delivery_fee, 2)
    order_doc["delivery_fee_amount"] = round(delivery_fee, 2)
    order_doc["platform_fee"] = round(platform_fee, 2)
    order_doc["tip_amount"] = round(tip_amount, 2)
    order_doc["delivery_tip_amount"] = round(
        _money_float(order_doc.get("delivery_tip_amount"), tip_amount),
        2
    )

    # Both should now mean final customer payable.
    order_doc["total_amount"] = round(final_total, 2)
    order_doc["total_payable"] = round(final_total, 2)

    return order_doc



def decorate_customer_payment_display(order_doc):
    """
    Adds customer-facing payment display fields.

    This does not change database.
    It only prepares safe labels/flags for orders.html and order_track.html.
    """
    order_doc = order_doc or {}

    payment_method = (order_doc.get("payment_method") or "COD").strip().upper()
    payment_status = (order_doc.get("payment_status") or "").strip().upper()
    payment_collection_status = (order_doc.get("payment_collection_status") or "").strip().upper()
    status = (order_doc.get("status") or "").strip().upper()

    is_cod_order = payment_method in [
        "COD",
        "CASH_ON_DELIVERY",
        "COD_RIDER_COLLECTION"
    ]

    is_online_order = payment_method in [
        "ONLINE",
        "ONLINE_PAYMENT",
        "RAZORPAY"
    ]

    online_pending_statuses = [
        "PENDING_PAYMENT",
        "PAYMENT_PENDING",
        "ONLINE_PENDING"
    ]

    paid_statuses = [
        "PAID",
        "ONLINE_PAID",
        "SUCCESS"
    ]

    online_payment_pending = bool(
        is_online_order
        and (
            status in online_pending_statuses
            or payment_status in online_pending_statuses
            or payment_collection_status in online_pending_statuses
        )
    )

    online_payment_paid = bool(
        is_online_order
        and (
            payment_status in paid_statuses
            or payment_collection_status in paid_statuses
        )
    )

    if is_online_order:
        payment_method_label = "Online Payment"
    elif is_cod_order:
        payment_method_label = "Pay Online on Delivery"
    else:
        payment_method_label = payment_method.replace("_", " ").title() if payment_method else "Payment"

    if online_payment_paid:
        payment_status_label = "Paid"
        payment_badge_class = "paid"
        payment_notice = "Payment received by NE FRESH through Razorpay."
    elif online_payment_pending:
        payment_status_label = "Payment Pending"
        payment_badge_class = "pending"
        payment_notice = "Please complete online payment to confirm this order."
    elif is_cod_order:
        payment_status_label = "Pay Online on Delivery"
        payment_badge_class = "cod"
        payment_notice = "Please complete payment at delivery as per the selected route. External Local should be paid online/UPI to Store/NE FRESH, not cash to the external rider."
    elif payment_status:
        payment_status_label = payment_status.replace("_", " ").title()
        payment_badge_class = "pending"
        payment_notice = ""
    else:
        payment_status_label = "Pending"
        payment_badge_class = "pending"
        payment_notice = ""

    payment_received_by = (order_doc.get("payment_received_by") or "").strip().upper()

    if payment_received_by == "ADMIN_PLATFORM":
        payment_received_by_label = "NE FRESH / Platform"
    elif payment_received_by:
        payment_received_by_label = payment_received_by.replace("_", " ").title()
    elif is_cod_order:
        payment_received_by_label = "Delivery Boy at delivery time"
    else:
        payment_received_by_label = "Pending"

    order_id = str(order_doc.get("_id") or order_doc.get("id") or "")

    order_doc["payment_method"] = payment_method
    order_doc["payment_status"] = payment_status
    order_doc["payment_collection_status"] = payment_collection_status

    order_doc["is_cod_order"] = is_cod_order
    order_doc["is_online_order"] = is_online_order
    order_doc["online_payment_pending"] = online_payment_pending
    order_doc["online_payment_paid"] = online_payment_paid

    order_doc["can_pay_online_now"] = bool(is_online_order and online_payment_pending and order_id)
    order_doc["pay_now_url"] = url_for("order_payment", oid=order_id) if order_doc["can_pay_online_now"] else ""

    order_doc["payment_method_label"] = payment_method_label
    order_doc["payment_status_label"] = payment_status_label
    order_doc["payment_badge_class"] = payment_badge_class
    order_doc["payment_notice"] = payment_notice
    order_doc["payment_received_by_label"] = payment_received_by_label

    order_doc["cod_collection_required"] = bool(is_cod_order and not online_payment_paid)
    order_doc["amount_to_collect_from_customer"] = (
        float(order_doc.get("total_payable") or order_doc.get("total_amount") or 0)
        if is_cod_order
        else 0.0
    )

    return order_doc


PAYMENT_GATEWAY_SETTINGS_KEY = "payment_gateway_settings"
RETURN_REFUND_POLICY_SETTINGS_KEY = "return_refund_policy_settings"


def _get_razorpay_env_keys(mode="TEST"):
    mode = (mode or "TEST").strip().upper()

    if mode == "LIVE":
        return {
            "key_id": (os.getenv("RAZORPAY_LIVE_KEY_ID") or "").strip(),
            "key_secret": (os.getenv("RAZORPAY_LIVE_KEY_SECRET") or "").strip(),
        }

    return {
        "key_id": (os.getenv("RAZORPAY_TEST_KEY_ID") or "").strip(),
        "key_secret": (os.getenv("RAZORPAY_TEST_KEY_SECRET") or "").strip(),
    }


def get_checkout_payment_gateway_settings():
    settings = mongo.platform_settings.find_one({
        "key": PAYMENT_GATEWAY_SETTINGS_KEY
    }) or {}

    mode = (settings.get("mode") or "TEST").strip().upper()

    if mode not in ["TEST", "LIVE"]:
        mode = "TEST"

    gateway = (settings.get("gateway") or "RAZORPAY").strip().upper()

    if gateway not in ["RAZORPAY"]:
        gateway = "RAZORPAY"

    env_keys = _get_razorpay_env_keys(mode)

    return {
        "enabled": bool(settings.get("enabled", False)),
        "gateway": gateway,
        "mode": mode,

        # Public key only. This can go to frontend Razorpay Checkout.
        "razorpay_key_id": env_keys.get("key_id") or "",

        "auto_capture_enabled": bool(settings.get("auto_capture_enabled", True)),
    }


def get_server_payment_gateway_settings():
    settings = mongo.platform_settings.find_one({
        "key": PAYMENT_GATEWAY_SETTINGS_KEY
    }) or {}

    mode = (settings.get("mode") or "TEST").strip().upper()

    if mode not in ["TEST", "LIVE"]:
        mode = "TEST"

    gateway = (settings.get("gateway") or "RAZORPAY").strip().upper()

    if gateway not in ["RAZORPAY"]:
        gateway = "RAZORPAY"

    env_keys = _get_razorpay_env_keys(mode)

    return {
        "enabled": bool(settings.get("enabled", False)),
        "gateway": gateway,
        "mode": mode,

        # Read from .env only. Do not store/use secret from MongoDB.
        "razorpay_key_id": env_keys.get("key_id") or "",
        "razorpay_key_secret": env_keys.get("key_secret") or "",

        "auto_capture_enabled": bool(settings.get("auto_capture_enabled", True)),
    }


def get_razorpay_client_from_settings(settings=None):
    settings = settings or get_server_payment_gateway_settings()

    if not settings.get("enabled"):
        return None, "Online payment is disabled by Admin."

    if settings.get("gateway") != "RAZORPAY":
        return None, "Only Razorpay gateway is supported right now."

    key_id = (settings.get("razorpay_key_id") or "").strip()
    key_secret = (settings.get("razorpay_key_secret") or "").strip()

    if not key_id or not key_secret:
        return None, (
            "Razorpay credentials are missing in .env. "
            "Please set RAZORPAY_TEST_KEY_ID and RAZORPAY_TEST_KEY_SECRET for TEST mode."
        )

    try:
        client = razorpay.Client(auth=(key_id, key_secret))
        return client, ""
    except Exception as exc:
        return None, f"Unable to initialize Razorpay client: {str(exc)}"
    


def _order_attempt_id_values(attempt_id):
    values = []

    if attempt_id is None:
        return values

    values.append(attempt_id)
    values.append(str(attempt_id))

    try:
        if ObjectId.is_valid(str(attempt_id)):
            values.append(ObjectId(str(attempt_id)))
    except Exception:
        pass

    return values


def get_return_refund_policy_settings():
    """
    Admin-controlled return/refund policy.

    enabled = False means:
    - no return option visible
    - backend blocks return request
    """
    settings = mongo.platform_settings.find_one({
        "key": RETURN_REFUND_POLICY_SETTINGS_KEY
    }) or {}

    enabled = bool(settings.get("enabled", False))

    try:
        return_window_hours = int(settings.get("return_window_hours") or 24)
    except Exception:
        return_window_hours = 24

    if return_window_hours < 1:
        return_window_hours = 1

    if return_window_hours > 720:
        return_window_hours = 720

    return {
        "enabled": enabled,
        "return_window_hours": return_window_hours,
        "default_refund_items": bool(settings.get("default_refund_items", True)),
        "default_refund_delivery_fee": bool(settings.get("default_refund_delivery_fee", False)),
        "default_refund_platform_fee": bool(settings.get("default_refund_platform_fee", False)),
        "default_refund_tip": bool(settings.get("default_refund_tip", False)),
        "policy_note": settings.get("policy_note") or "",
    }


def _parse_order_datetime(value):
    """
    Safely parse ISO datetime strings saved in existing order docs.
    """
    if not value:
        return None

    if isinstance(value, datetime):
        return value

    try:
        raw = str(value).strip()

        if not raw:
            return None

        if raw.endswith("Z"):
            raw = raw[:-1]

        # Remove timezone offset if present because current project stores mostly naive UTC isoformat.
        if "+" in raw:
            raw = raw.split("+")[0]

        return datetime.fromisoformat(raw)
    except Exception:
        return None


def get_order_return_eligibility(order_doc, settings=None):
    """
    UI + backend return eligibility.

    Return allowed only when:
    - admin enabled return/refund policy
    - order is delivered
    - return window is not expired
    - no open/completed return/refund already exists
    """
    settings = settings or get_return_refund_policy_settings()

    if not settings.get("enabled"):
        return {
            "allowed": False,
            "reason": "Return/refund is currently disabled by NE FRESH.",
            "policy_enabled": False,
            "return_window_hours": settings.get("return_window_hours", 24),
            "deadline": "",
        }

    if not order_doc:
        return {
            "allowed": False,
            "reason": "Order not found.",
            "policy_enabled": True,
            "return_window_hours": settings.get("return_window_hours", 24),
            "deadline": "",
        }

    order_status = (order_doc.get("status") or "").strip().upper()
    payment_status = (order_doc.get("payment_status") or "").strip().upper()
    return_status = (order_doc.get("return_status") or "").strip().upper()
    refund_status = (order_doc.get("refund_status") or "").strip().upper()

    if order_status != "DELIVERED":
        return {
            "allowed": False,
            "reason": "Return is allowed only after delivery.",
            "policy_enabled": True,
            "return_window_hours": settings.get("return_window_hours", 24),
            "deadline": "",
        }

    if payment_status == "REFUNDED" or refund_status in ["PROCESSED", "ADJUSTED", "REJECTED"]:
        return {
            "allowed": False,
            "reason": "This order already has refund status.",
            "policy_enabled": True,
            "return_window_hours": settings.get("return_window_hours", 24),
            "deadline": "",
        }

    blocked_return_statuses = [
        "RETURN_REQUESTED",
        "REQUESTED",
        "STORE_REVIEWED",
        "STORE_APPROVED",
        "STORE_REJECTED",
        "APPROVED",
        "REJECTED",
        "NEED_ADMIN_REVIEW",
        "PENDING_ASSIGNMENT",
        "ASSIGNED",
        "PICKUP_STARTED",
        "PICKED_UP_FROM_CUSTOMER",
        "RETURNED_TO_STORE",
        "VERIFIED_BY_STORE",
        "RETURN_COMPLETED",
        "COMPLETED"
    ]

    if return_status in blocked_return_statuses:
        return {
            "allowed": False,
            "reason": "Return request already exists for this order.",
            "policy_enabled": True,
            "return_window_hours": settings.get("return_window_hours", 24),
            "deadline": "",
        }

    delivered_at = (
        order_doc.get("delivered_at")
        or order_doc.get("delivery_completed_at")
        or order_doc.get("updated_at")
        or order_doc.get("created_at")
    )

    delivered_dt = _parse_order_datetime(delivered_at)

    if not delivered_dt:
        return {
            "allowed": False,
            "reason": "Delivery time is not available for return window validation.",
            "policy_enabled": True,
            "return_window_hours": settings.get("return_window_hours", 24),
            "deadline": "",
        }

    return_window_hours = int(settings.get("return_window_hours") or 24)
    deadline_dt = delivered_dt + timedelta(hours=return_window_hours)
    now_dt = datetime.utcnow()

    if now_dt > deadline_dt:
        return {
            "allowed": False,
            "reason": f"Return window expired. Return is allowed within {return_window_hours} hours after delivery.",
            "policy_enabled": True,
            "return_window_hours": return_window_hours,
            "deadline": deadline_dt.isoformat(),
        }

    return {
        "allowed": True,
        "reason": "",
        "policy_enabled": True,
        "return_window_hours": return_window_hours,
        "deadline": deadline_dt.isoformat(),
    }


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

    for line in order_items:
        product_id = line.get("product_id")
        restore_qty = float(line.get("quantity") or line.get("cart_quantity") or 0)

        if product_id and restore_qty > 0:
            mongo.products.update_one(
                {"_id": product_id},
                {
                    "$inc": {
                        "stock_quantity": restore_qty
                    },
                    "$set": {
                        "is_active": 1
                    }
                }
            )

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


@app.route('/checkout', methods=['GET', 'POST'])
@login_required()
def checkout():
    u = current_user()
    cid = get_or_create_cart(u["id"])

    store_lat = None
    store_lng = None

    payment_settings = get_checkout_payment_gateway_settings()
    online_payment_enabled = bool(
        payment_settings.get("enabled")
        and payment_settings.get("gateway") == "RAZORPAY"
        and payment_settings.get("razorpay_key_id")
    )

    cart_items = list(mongo.cart_items.find({"cart_id": cid}))

    items = []

    for ci in cart_items:
        product = mongo.products.find_one({"_id": ci.get("product_id")})
        if not product:
            continue

        hydrate_product_unit_fields(product)

        quantity = cart_item_quantity(ci)
        unit_type = ci.get("unit_type") or product.get("unit_type") or "WEIGHT"
        unit_label = ci.get("unit_label") or product.get("unit_label") or "kg"

        price_per_unit = float(
            ci.get("price_per_unit_snapshot")
            if ci.get("price_per_unit_snapshot") is not None
            else product.get("price_per_unit") or 0
        )

        stock_quantity = float(product.get("stock_quantity") or 0)
        line_total = float(quantity or 0) * float(price_per_unit or 0)

        item = {
            "product_id": product["_id"],
            "product_id_str": str(product["_id"]),
            "quantity": quantity,
            "cart_quantity": quantity,
            "unit_type": unit_type,
            "unit_label": unit_label,
            "price_per_unit": price_per_unit,
            "stock_quantity": stock_quantity,
            "quantity_min": float(product.get("quantity_min") or 1),
            "quantity_step": float(product.get("quantity_step") or 1),
            "line_total": line_total,
            "store_id": product.get("store_id"),
            "is_active": int(product.get("is_active") or 0),
            "name": product.get("name", ""),
            "image_path": product.get("image_path", ""),
            "shipping_weight_kg": product.get("shipping_weight_kg"),
            "shipping_length_cm": product.get("shipping_length_cm"),
            "shipping_breadth_cm": product.get("shipping_breadth_cm"),
            "shipping_height_cm": product.get("shipping_height_cm")
        }

        items.append(item)

    store_ids = sorted(set([str(it["store_id"]) for it in items if it.get("store_id")]))
    cart_store_count = len(store_ids)

    if cart_store_count > 1:
        flash("Your cart contains items from multiple stores. Please clear the cart and order from one store at a time.", "danger")
        return redirect(url_for("cart_page"))

    for it in items:
        if int(it["is_active"] or 0) != 1:
            flash("One or more items are sold out.", "danger")
            return redirect(url_for("cart_page"))

        if float(it["stock_quantity"] or 0) <= 0:
            flash("One or more items are sold out.", "danger")
            return redirect(url_for("cart_page"))

        quantity_min = float(it.get("quantity_min") or 1)

        if float(it["quantity"] or 0) < quantity_min:
            flash(
                f"{it.get('name', 'One item')} requires minimum order quantity of {quantity_min:g} {it.get('unit_label', 'unit')}. Please update your cart.",
                "danger"
            )
            return redirect(url_for("cart_page"))

        if float(it["quantity"] or 0) > float(it["stock_quantity"] or 0):
            flash(
                "Requested amount is not available in stock. Please change the amount.",
                "danger"
            )
            return redirect(url_for("cart_page"))

    addresses = list(
        mongo.addresses.find({"user_id": u["id"]}).sort([
            ("is_default", -1),
            ("created_at", -1)
        ])
    )

    for a in addresses:
        a["id"] = str(a["_id"])

    if items:
        store = mongo.stores.find_one({"_id": items[0]["store_id"]})
        if store:
            store_lat = store.get("latitude")
            store_lng = store.get("longitude")

    if request.method == "POST":
        if not items:
            flash("Your cart is empty.", "warning")
            return redirect(url_for("cart_page"))

        if cart_store_count > 1:
            flash("Your cart contains items from multiple stores. Please order from one store at a time.", "danger")
            return redirect(url_for("cart_page"))

        if not addresses:
            flash("Please add delivery address before checkout.", "warning")
            return redirect(url_for("profile"))

        for it in items:
            if int(it["is_active"] or 0) != 1:
                flash("One or more items are sold out.", "danger")
                return redirect(url_for("cart_page"))

            if float(it["stock_quantity"] or 0) <= 0:
                flash("One or more items are sold out.", "danger")
                return redirect(url_for("cart_page"))

            quantity_min = float(it.get("quantity_min") or 1)

            if float(it["quantity"] or 0) < quantity_min:
                flash(
                    f"{it.get('name', 'One item')} requires minimum order quantity of {quantity_min:g} {it.get('unit_label', 'unit')}. Please update your cart.",
                    "danger"
                )
                return redirect(url_for("cart_page"))

            if float(it["quantity"] or 0) > float(it["stock_quantity"] or 0):
                flash(
                    "Requested amount is not available in stock. Please change the amount.",
                    "danger"
                )
                return redirect(url_for("cart_page"))

        addr_id = request.form.get("address_id")

        if not addr_id:
            flash("Please select a delivery address.", "warning")
            return redirect(url_for("checkout"))

        try:
            addr_obj_id = ObjectId(addr_id)
        except Exception:
            flash("Invalid address selected.", "danger")
            return redirect(url_for("checkout"))

        sel = mongo.addresses.find_one({
            "_id": addr_obj_id,
            "user_id": u["id"]
        })

        if not sel:
            flash("Invalid address selected.", "danger")
            return redirect(url_for("checkout"))

        sel_pin = (sel.get("pincode") or "").strip()

        if not is_serviceable_pincode(sel_pin):
            flash("Please enter a valid 6-digit pincode.", "danger")
            return redirect(url_for("checkout"))

        if not is_assam_state(sel.get("state")):
            flash("Delivery is currently available only within Assam.", "danger")
            return redirect(url_for("checkout"))

        items_total = sum([
            float(it.get("line_total") or 0)
            for it in items
        ])

        store_id = items[0]["store_id"]
        store = mongo.stores.find_one({"_id": store_id}) or {}

        store_lat = store.get("latitude")
        store_lng = store.get("longitude")

        def _safe_float(value):
            try:
                if value is None or str(value).strip() == "":
                    return None
                return float(value)
            except Exception:
                return None

        # Location priority:
        # 1. Fresh checkout GPS/current-location hidden fields
        # 2. Session location from navbar/checkout location API
        # 3. Saved address coordinates
        form_lat = _safe_float(request.form.get("resolved_lat"))
        form_lng = _safe_float(request.form.get("resolved_lng"))

        session_lat = _safe_float(session.get("location_lat"))
        session_lng = _safe_float(session.get("location_lng"))

        saved_lat = _safe_float(sel.get("latitude"))
        saved_lng = _safe_float(sel.get("longitude"))

        final_lat = form_lat if form_lat is not None else session_lat if session_lat is not None else saved_lat
        final_lng = form_lng if form_lng is not None else session_lng if session_lng is not None else saved_lng

        if form_lat is not None and form_lng is not None:
            location_source = "checkout_gps"
        elif session_lat is not None and session_lng is not None:
            location_source = session.get("location_source") or "session_location"
        elif saved_lat is not None and saved_lng is not None:
            location_source = "saved_address"
        else:
            location_source = "missing_coordinates"

        requested_payment_method = (request.form.get("payment_method") or "COD").strip().upper()

        if requested_payment_method not in ["COD", "ONLINE"]:
            requested_payment_method = "COD"

        serviceability = check_checkout_delivery_quote(
            store=store,
            customer_lat=final_lat,
            customer_lng=final_lng,
            customer_pincode=sel_pin,
            items_total=items_total,
            payment_method=requested_payment_method,
            cart_items=items
        )

        if not serviceability.get("serviceable"):
            flash(
                serviceability.get("message") or "Delivery is not available for your selected location.",
                "danger"
            )
            return redirect(url_for("checkout"))

        km = serviceability.get("distance_km")
        delivery_fee = serviceability.get("delivery_fee", 0)
        delivery_fee_source = serviceability.get("delivery_fee_source") or "unknown"
        delivery_fee_slab = serviceability.get("delivery_fee_slab")
        delivery_base_fee = float(serviceability.get("delivery_base_fee") or 0)
        delivery_fee_details = serviceability.get("delivery_fee_details") or {}

        free_delivery_above_applied = delivery_fee_source == "admin_free_delivery_above"
        free_delivery_above = float(
            delivery_fee_details.get("free_delivery_above")
            or 0
        )
        original_delivery_fee = float(
            delivery_fee_details.get("original_delivery_fee")
            or delivery_fee
            or 0
        )
        free_delivery_savings = float(
            delivery_fee_details.get("free_delivery_savings")
            or 0
        )

        tip_amount_raw = (
            request.form.get("tip_amount")
            or request.form.get("tip")
            or request.form.get("delivery_tip")
            or "0"
        )

        try:
            tip_amount = float(tip_amount_raw or 0)
        except (TypeError, ValueError):
            tip_amount = 0.0

        if tip_amount < 0:
            tip_amount = 0.0

        if tip_amount > 10000:
            tip_amount = 10000.0

        tip_amount = round(tip_amount, 2)

        now = datetime.utcnow().isoformat()

        delivery_mode_snapshot = get_order_delivery_mode_snapshot(serviceability.get("active_delivery_mode"))
        active_delivery_mode = delivery_mode_snapshot.get("active_delivery_mode") or DELIVERY_MODE_IN_HOUSE
        in_house_delivery_enabled_at_order = bool(delivery_mode_snapshot.get("in_house_delivery_enabled_at_order", True))
        external_delivery_enabled_at_order = bool(delivery_mode_snapshot.get("external_delivery_enabled_at_order", False))
        external_payment_rule = delivery_mode_snapshot.get("external_payment_rule") or EXTERNAL_PAYMENT_RULE_COD_STORE
        delivery_payment_methods = delivery_mode_snapshot.get("delivery_payment_methods") or DELIVERY_PAYMENT_ONLINE_AND_COD
        allow_online_payment_for_mode = bool(delivery_mode_snapshot.get("allow_online_payment", True))
        allow_cod_payment_for_mode = bool(delivery_mode_snapshot.get("allow_cod_payment", True))
        cod_collection_method = delivery_mode_snapshot.get("cod_collection_method") or COD_COLLECTION_DELIVERY_BOY
        delivery_type = delivery_mode_snapshot.get("delivery_type") or "OWN_DELIVERY"
        external_delivery_provider_type = delivery_mode_snapshot.get("external_delivery_provider_type") or "IN_HOUSE"
        external_delivery_status_initial = delivery_mode_snapshot.get("external_delivery_status") or "NOT_APPLICABLE"

        # Use the latest serviceability response for payment availability because it
        # also reflects the selected active delivery mode and external quote path.
        allow_online_payment_for_mode = bool(serviceability.get("online_allowed", allow_online_payment_for_mode))
        allow_cod_payment_for_mode = bool(serviceability.get("cod_allowed", allow_cod_payment_for_mode))

        if requested_payment_method == "COD" and not allow_cod_payment_for_mode:
            flash("Pay Online on Delivery is currently disabled for this delivery route. Please choose Online Payment.", "warning")
            return redirect(url_for("checkout"))

        if requested_payment_method == "ONLINE" and not allow_online_payment_for_mode:
            flash("Online payment is currently disabled for this delivery route. Please choose Pay Online on Delivery.", "warning")
            return redirect(url_for("checkout"))

        store_allows_online = bool(store.get("allow_online_payment", True))

        if requested_payment_method == "ONLINE":
            if not online_payment_enabled:
                flash("Online payment is currently not enabled by Admin. Please use Pay Online on Delivery.", "warning")
                return redirect(url_for("checkout"))

            if not store_allows_online:
                flash("Online payment is currently not enabled for this store. Please use Pay Online on Delivery.", "warning")
                return redirect(url_for("checkout"))

            payment_method = "ONLINE"
        else:
            payment_method = "COD"

        money_breakdown = build_order_money_breakdown(
            items_total=items_total,
            delivery_fee=delivery_fee,
            tip_amount=tip_amount,
            payment_method=payment_method
        )

        total_payable = float(money_breakdown.get("total_payable") or 0)

        # ------------------------------------------------------------
        # Platform-controlled payment / settlement base fields
        # ------------------------------------------------------------
        # COD_RIDER_COLLECTION = customer pays rider, rider submits remaining cash to Admin.
        # ONLINE_PLATFORM = customer pays NE FRESH through platform gateway.
        # ------------------------------------------------------------
        is_online_order = payment_method == "ONLINE"

        # delivery_mode_snapshot was frozen before payment calculation.
        # It lets old orders keep their original delivery mode even if Admin changes the global mode later.

        if is_online_order:
            platform_payment_flow = "ONLINE_PLATFORM"
            initial_payment_status = "PENDING_PAYMENT"
            payment_collection_status = "ONLINE_PENDING"
            cod_collection_status = "NOT_REQUIRED"
            platform_fee_status = "PENDING_PAYMENT"
            rider_cash_settlement_status = "NOT_REQUIRED"
            expected_rider_cash_to_submit = 0.0
            rider_cash_to_submit = 0.0
            flash_message = "Order created. Please complete online payment in the next step."
        else:
            if in_house_delivery_enabled_at_order:
                platform_payment_flow = "COD_RIDER_COLLECTION"
                payment_collection_status = "PENDING"
                cod_collection_status = "PENDING"
                platform_fee_status = "PENDING_COLLECTION"
                rider_cash_settlement_status = "NOT_COLLECTED_YET"
            else:
                if external_payment_rule == EXTERNAL_PAYMENT_RULE_COD_PARTNER:
                    platform_payment_flow = "COD_PARTNER_COLLECTION"
                    payment_collection_status = "PENDING_PARTNER_COLLECTION"
                    cod_collection_status = "PENDING_PARTNER_COLLECTION"
                    platform_fee_status = "PENDING_PARTNER_REMITTANCE"
                else:
                    if active_delivery_mode == DELIVERY_MODE_EXTERNAL_LOCAL:
                        platform_payment_flow = "PAY_ON_DELIVERY_STORE_ONLINE"
                        payment_collection_status = "PENDING_PAY_ON_DELIVERY"
                        cod_collection_status = "PENDING_PAY_ON_DELIVERY"
                    else:
                        platform_payment_flow = "COD_STORE_COLLECTION"
                        payment_collection_status = "PENDING_STORE_COLLECTION"
                        cod_collection_status = "PENDING_STORE_COLLECTION"
                    platform_fee_status = "DUE_FROM_STORE"

                rider_cash_settlement_status = "NOT_APPLICABLE"

            initial_payment_status = "PENDING"
            flash_message = "Order placed! Pay Online on Delivery selected."

        items_subtotal_amount = round(float(money_breakdown.get("items_subtotal") or items_total or 0), 2)
        delivery_fee_amount_final = round(float(money_breakdown.get("delivery_fee") or delivery_fee or 0), 2)
        platform_fee_amount = round(float(money_breakdown.get("platform_fee") or 0), 2)
        tip_amount_final = round(float(money_breakdown.get("tip_amount") or tip_amount or 0), 2)

        store_earning_amount = round(float(money_breakdown.get("store_earning") or items_subtotal_amount or 0), 2)
        delivery_boy_earning_amount = (
            round(delivery_fee_amount_final + tip_amount_final, 2)
            if in_house_delivery_enabled_at_order
            else 0.0
        )

        external_delivery_fee_amount = (
            0.0
            if in_house_delivery_enabled_at_order
            else round(delivery_fee_amount_final + tip_amount_final, 2)
        )
        admin_platform_earning_amount = round(
            float(money_breakdown.get("admin_platform_earning") or platform_fee_amount or 0),
            2
        )

        if not is_online_order and in_house_delivery_enabled_at_order:
            expected_rider_cash_to_submit = round(
                max(float(total_payable or 0) - float(delivery_boy_earning_amount or 0), 0),
                2
            )
            rider_cash_to_submit = expected_rider_cash_to_submit
        elif not is_online_order:
            expected_rider_cash_to_submit = 0.0
            rider_cash_to_submit = 0.0

        order_items_docs = []

        for it in items:
            line_total = float(it["quantity"]) * float(it["price_per_unit"])

            order_items_docs.append({
                "product_id": it["product_id"],
                "product_name": it.get("name", ""),
                "quantity": float(it["quantity"]),
                "cart_quantity": float(it["quantity"]),
                "unit_type": it.get("unit_type") or "WEIGHT",
                "unit_label": it.get("unit_label") or "kg",
                "quantity_min": float(it.get("quantity_min") or 1),
                "quantity_step": float(it.get("quantity_step") or 1),
                "price_per_unit": float(it["price_per_unit"]),
                "unit_price": float(it["price_per_unit"]),
                "line_total": line_total,
                "image_path": it.get("image_path", ""),
                "shipping_weight_kg": it.get("shipping_weight_kg"),
                "shipping_length_cm": it.get("shipping_length_cm"),
                "shipping_breadth_cm": it.get("shipping_breadth_cm"),
                "shipping_height_cm": it.get("shipping_height_cm")
            })

        order_result = mongo.orders.insert_one({
            "user_id": u["id"],
            "customer_name": u.get("name"),
            "customer_phone": u.get("phone"),
            "store_id": store_id,
            "store_name": store.get("store_name", ""),

            "items_subtotal": items_subtotal_amount,

            # Final payable amount including items + delivery fee + platform fee + tip.
            # Keep this same as total_payable so old pages using total_amount do not show only subtotal.
            "total_amount": float(total_payable),

            "status": "PENDING_PAYMENT" if is_online_order else "PLACED",
            "payment_status": initial_payment_status,
            "payment_method": payment_method,

            # ------------------------------------------------------------
            # New platform-controlled payment flow fields
            # ------------------------------------------------------------
            "payment_flow": platform_payment_flow,
            "official_payment_mode": platform_payment_flow,
            "delivery_type": delivery_type,
            "active_delivery_mode": active_delivery_mode,
            "in_house_delivery_enabled_at_order": bool(in_house_delivery_enabled_at_order),
            "external_delivery_enabled_at_order": bool(external_delivery_enabled_at_order),
            "external_payment_rule": external_payment_rule,
            "delivery_payment_methods": delivery_payment_methods,
            "allow_online_payment": bool(allow_online_payment_for_mode),
            "allow_cod_payment": bool(allow_cod_payment_for_mode),
            "cod_collection_method": cod_collection_method,

            "external_delivery_status": external_delivery_status_initial,
            "external_delivery_provider_type": external_delivery_provider_type,
            "external_delivery_partner_type": external_delivery_provider_type,
            "external_delivery_partner_name": "",
            "external_delivery_provider": (
                serviceability.get("external_delivery_provider")
                or delivery_mode_snapshot.get("external_delivery_provider")
                or (delivery_mode_snapshot.get("third_party_provider") if active_delivery_mode == DELIVERY_MODE_THIRD_PARTY else delivery_mode_snapshot.get("external_local_provider"))
            ) if external_delivery_enabled_at_order else "",
            "external_delivery_fee_amount": external_delivery_fee_amount,
            "external_delivery_charge": external_delivery_fee_amount,
            "external_delivery_booking_status": (
                "NOT_REQUIRED" if in_house_delivery_enabled_at_order
                else ("ORDER_REFERENCE_ONLY" if active_delivery_mode == DELIVERY_MODE_EXTERNAL_LOCAL else "PENDING_SHIPROCKET_BOOKING")
            ),
            "external_order_id": "",
            "external_shipment_id": "",
            "external_awb": "",
            "external_tracking_url": "",
            "external_label_url": "",
            "external_manifest_url": "",
            "external_cod_amount": float(total_payable) if (not is_online_order and external_delivery_enabled_at_order) else 0.0,
            "external_cod_remittance_status": "NOT_REQUIRED",
            "external_delivery_payload": {},
            "external_delivery_response": {},
            "external_delivery_quote": serviceability.get("external_delivery_quote") or {},
            "external_package_snapshot": serviceability.get("external_package_snapshot") or (serviceability.get("external_delivery_quote") or {}).get("package") or {},
            "external_delivery_quote_status": (serviceability.get("external_delivery_quote") or {}).get("quote_status") or "",
            "external_delivery_quote_message": (serviceability.get("external_delivery_quote") or {}).get("message") or "",
            "external_delivery_quote_source": serviceability.get("delivery_fee_source") or "",
            "external_delivery_eta_minutes": serviceability.get("eta_minutes"),
            "external_status_history": [],

            # At order creation, COD money is not collected yet.
            # It will be collected by delivery boy at delivery time.
            "payment_received_by": None,
            "payment_collected_at": None,
            "payment_collection_status": payment_collection_status,
            "cod_collection_status": cod_collection_status,

            "delivery_partner_id": None,

            "delivery_fee": float(money_breakdown.get("delivery_fee") or delivery_fee),
            "delivery_fee_amount": float(money_breakdown.get("delivery_fee_amount") or delivery_fee),
            "delivery_fee_source": delivery_fee_source,
            "delivery_fee_slab": delivery_fee_slab,
            "delivery_base_fee": delivery_base_fee,
            "delivery_fee_details": delivery_fee_details,
            "free_delivery_above_applied": bool(free_delivery_above_applied),
            "free_delivery_above": float(free_delivery_above or 0),
            "original_delivery_fee": float(original_delivery_fee or 0),
            "free_delivery_savings": float(free_delivery_savings or 0),
            "delivery_fee_settings_snapshot": {
                "delivery_fee_settings_source": delivery_fee_details.get("delivery_fee_settings_source") or "admin_platform_settings",
                "admin_delivery_base_fee": delivery_base_fee,
                "admin_free_delivery_above": float(free_delivery_above or 0),
                "admin_delivery_fee_source": delivery_fee_source,
                "admin_delivery_fee_slab": delivery_fee_slab,
                "admin_original_delivery_fee": float(original_delivery_fee or 0),
                "admin_free_delivery_savings": float(free_delivery_savings or 0),
            },

            "platform_fee": platform_fee_amount,
            "admin_platform_earning": admin_platform_earning_amount,
            "platform_fee_source": money_breakdown.get("platform_fee_source") or "disabled",
            "platform_fee_settings_snapshot": money_breakdown.get("platform_fee_settings_snapshot") or {},

            "store_earning": store_earning_amount,
            "delivery_tip_amount": tip_amount_final,

            # ------------------------------------------------------------
            # Existing settlement fields preserved for current pages
            # ------------------------------------------------------------
            "settlement_status": money_breakdown.get("settlement_status") or "PENDING",
            "store_settlement_status": money_breakdown.get("store_settlement_status") or "PENDING",
            "admin_platform_fee_status": money_breakdown.get("admin_platform_fee_status") or "DUE",
            "delivery_settlement_status": money_breakdown.get("delivery_settlement_status") or "PENDING",

            # ------------------------------------------------------------
            # New platform-controlled settlement fields
            # ------------------------------------------------------------
            "platform_fee_status": platform_fee_status,
            "platform_fee_received_at": None,

            "store_payout_amount": store_earning_amount,
            "store_payout_status": "PENDING_AFTER_DELIVERY",
            "store_payout_paid_at": None,
            "store_payout_marked_by": None,
            "store_payout_note": "",

            "delivery_boy_earning": delivery_boy_earning_amount,
            "delivery_boy_payout_amount": delivery_boy_earning_amount,
            "delivery_boy_payout_status": "PENDING_DELIVERY",
            "delivery_boy_payout_paid_at": None,
            "delivery_boy_payout_marked_by": None,
            "delivery_boy_payout_note": "",

            "cod_collected_amount": float(total_payable) if not is_online_order else 0.0,
            "expected_rider_cash_to_submit": expected_rider_cash_to_submit,
            "rider_cash_to_submit": rider_cash_to_submit,
            "rider_cash_settlement_status": rider_cash_settlement_status,
            "rider_cash_received_at": None,
            "rider_cash_received_by": None,
            "rider_cash_settlement_note": "",

            "order_settlement_status": "NOT_STARTED",
            "settlement_audit_logs": [],

            "distance_km": float(km) if km is not None else None,
            "delivery_zone_matched": True,
            "delivery_serviceability_reason": serviceability.get("reason"),
            "delivery_serviceability_message": serviceability.get("message"),
            "delivery_routing_reason": serviceability.get("routing_reason") or serviceability.get("reason"),

            "store_latitude": store.get("latitude"),
            "store_longitude": store.get("longitude"),
            "store_online_at_order": int(store.get("is_online", store.get("is_open", 1)) or 0),
            "delivery_enabled_at_order": int(store.get("delivery_enabled", 1 if store.get("delivery_available", False) else 0) or 0),

            "tip_amount": tip_amount_final,
            "total_payable": float(total_payable),

    # Final checkout delivery location used for fee calculation.
            "delivery_latitude": final_lat,
            "delivery_longitude": final_lng,
            "delivery_location_source": location_source,

    # Session/global detected location info, if available.
            "delivery_location_address": session.get("location_address"),
            "delivery_location_pincode": session.get("location_pincode"),
            "delivery_location_city": session.get("location_city"),
            "delivery_location_state": session.get("location_state"),

            "created_at": now
        })

        oid = order_result.inserted_id

        if active_delivery_mode == DELIVERY_MODE_EXTERNAL_LOCAL:
            local_reference = f"NEF-{str(oid)[-8:].upper()}"
            mongo.orders.update_one(
                {"_id": oid},
                {
                    "$set": {
                        "external_order_id": local_reference,
                        "external_delivery_tracking_code": local_reference,
                        "customer_delivery_reference": local_reference,
                        "external_delivery_booking_status": "ORDER_REFERENCE_ONLY",
                        "external_delivery_status": "ORDER_PLACED_EXTERNAL_LOCAL",
                    },
                    "$push": {
                        "external_status_history": {
                            "status": "ORDER_REFERENCE_ONLY",
                            "note": "External local delivery uses the NE FRESH order reference only. No Rapido/Ola/Uber API booking record is created.",
                            "at": now,
                            "payload": {},
                        }
                    }
                }
            )

        stock_reserved, stock_message = _reserve_order_stock_items(order_items_docs)

        if not stock_reserved:
            mongo.orders.delete_one({"_id": oid})
            mongo.order_events.delete_many({"order_id": oid})
            flash(stock_message or "Requested stock is no longer available. Please update your cart.", "danger")
            return redirect(url_for("cart_page"))

        for order_item in order_items_docs:
            order_item["order_id"] = oid
            mongo.order_items.insert_one(order_item)

        mongo.transactions.insert_one({
            "order_id": oid,
            "amount": float(total_payable),
            "items_subtotal": items_subtotal_amount,
            "delivery_fee": delivery_fee_amount_final,
            "delivery_fee_source": delivery_fee_source,
            "delivery_fee_slab": delivery_fee_slab,
            "delivery_base_fee": delivery_base_fee,
            "platform_fee": platform_fee_amount,
            "tip_amount": tip_amount_final,

            "payment_method": payment_method,
            "payment_flow": platform_payment_flow,
            "official_payment_mode": platform_payment_flow,

            "delivery_type": delivery_type,
            "active_delivery_mode": active_delivery_mode,
            "in_house_delivery_enabled_at_order": bool(in_house_delivery_enabled_at_order),
            "external_delivery_enabled_at_order": bool(external_delivery_enabled_at_order),
            "external_payment_rule": external_payment_rule,
            "delivery_payment_methods": delivery_payment_methods,
            "allow_online_payment": bool(allow_online_payment_for_mode),
            "allow_cod_payment": bool(allow_cod_payment_for_mode),
            "cod_collection_method": cod_collection_method,
            "external_delivery_provider_type": external_delivery_provider_type,
            "external_delivery_fee_amount": external_delivery_fee_amount,

            "payment_received_by": None,
            "payment_collection_status": payment_collection_status,

            "store_earning": store_earning_amount,
            "delivery_boy_earning": delivery_boy_earning_amount,
            "admin_platform_earning": admin_platform_earning_amount,

            "store_payout_amount": store_earning_amount,
            "store_payout_status": "PENDING_AFTER_DELIVERY",

            "delivery_boy_payout_amount": delivery_boy_earning_amount,
            "delivery_boy_payout_status": "PENDING_DELIVERY",

            "expected_rider_cash_to_submit": expected_rider_cash_to_submit,
            "rider_cash_to_submit": rider_cash_to_submit,
            "rider_cash_settlement_status": rider_cash_settlement_status,

            "platform_fee_status": platform_fee_status,

            "status": initial_payment_status,
            "settlement_status": "PENDING",
            "order_settlement_status": "NOT_STARTED",
            "admin_platform_fee_status": money_breakdown.get("admin_platform_fee_status") or "DUE",
            "created_at": now
        })

        mongo.order_addresses.insert_one({
            "order_id": oid,
            "line1": sel.get("line1"),
            "line2": sel.get("line2"),
            "city": sel.get("city"),
            "state": sel.get("state"),
            "pincode": sel.get("pincode"),

            # Final coordinates actually used at checkout.
            "latitude": final_lat,
            "longitude": final_lng,
            "location_source": location_source,

            # Original saved-address coordinates for reference.
            "saved_address_latitude": saved_lat,
            "saved_address_longitude": saved_lng,

            "created_at": now
        })

        mongo.order_events.insert_one({
            "order_id": oid,
            "status": "PENDING_PAYMENT" if is_online_order else "PLACED",
            "note": (
                f"{'Online payment attempt prepared' if is_online_order else 'Order placed'}. "
                f"Items: ₹{float(money_breakdown.get('items_subtotal') or items_total):.2f}, "
                f"Delivery: ₹{float(money_breakdown.get('delivery_fee') or delivery_fee):.2f}, "
                f"Platform fee: ₹{float(money_breakdown.get('platform_fee') or 0):.2f}, "
                f"Tip: ₹{float(money_breakdown.get('tip_amount') or tip_amount):.2f}, "
                f"Total: ₹{float(total_payable):.2f}."
            ),
            "created_at": now
        })

        # ------------------------------------------------------------
        # ONLINE PAYMENT FIX:
        # Online payment is NOT a real order until Razorpay success.
        #
        # We temporarily created the order above only to reuse the
        # existing checkout calculation/snapshot logic.
        # Now move that snapshot into payment_attempts and remove it
        # from real order collections.
        #
        # Result:
        # - No unpaid online order appears in My Orders.
        # - No unpaid online order appears in Store Orders.
        # - Stock is restored until payment succeeds.
        # - Cart is NOT cleared until payment succeeds.
        # ------------------------------------------------------------
        if is_online_order:
            order_doc_snapshot = mongo.orders.find_one({"_id": oid}) or {}
            order_items_snapshot = list(mongo.order_items.find({"order_id": oid}))
            transactions_snapshot = list(mongo.transactions.find({"order_id": oid}))
            order_address_snapshot = mongo.order_addresses.find_one({"order_id": oid}) or {}

            payment_attempt_doc = {
                "_id": oid,
                "attempt_id": str(oid),
                "user_id": u["id"],
                "customer_name": u.get("name"),
                "customer_phone": u.get("phone"),
                "customer_email": u.get("email") or "",
                "cart_id": cid,

                "status": "PENDING_PAYMENT",
                "payment_method": "ONLINE",
                "payment_gateway": "RAZORPAY",
                "payment_status": "PENDING_PAYMENT",
                "payment_collection_status": "ONLINE_PENDING",

                "order_doc": order_doc_snapshot,
                "order_items": order_items_snapshot,
                "transactions": transactions_snapshot,
                "order_address": order_address_snapshot,

                "total_payable": float(total_payable),
                "amount": float(total_payable),
                "currency": "INR",

                "created_at": now,
                "updated_at": now,
                "expires_at": (datetime.utcnow() + timedelta(hours=24)).isoformat()
            }

            mongo.payment_attempts.replace_one(
                {"_id": oid},
                payment_attempt_doc,
                upsert=True
            )

            # Restore stock because this is not a real order yet.
            _release_order_stock_items(order_items_snapshot)

            # Remove temporary real-order records.
            mongo.orders.delete_one({"_id": oid})
            mongo.order_items.delete_many({"order_id": oid})
            mongo.transactions.delete_many({"order_id": oid})
            mongo.order_addresses.delete_many({"order_id": oid})
            mongo.order_events.delete_many({"order_id": oid})

            flash("Please complete online payment to place your order.", "success")
            return redirect(url_for("order_payment", oid=str(oid)))

        mongo.cart_items.delete_many({"cart_id": cid})

        flash(flash_message, "success")

        if is_online_order:
            return redirect(url_for("order_payment", oid=str(oid)))

        return redirect(url_for("orders"))

    total = sum([
        float(it.get("line_total") or 0)
        for it in items
    ])

    return render_template(
        "checkout.html",
        user=u,
        addresses=addresses,
        items=items,
        total=total,
        base_fee=BASE_DELIVERY_FEE_INR,
        slabs=DELIVERY_SURCHARGE_SLABS,
        max_km=None,
        delivery_mode="STORE_POLYGON_ZONE",
        delivery_message="Delivery availability depends on the selected store delivery zone. Final fee is calculated after serviceability check.",
        store_lat=store_lat,
        store_lng=store_lng,
        cart_store_count=cart_store_count,
        online_payment_enabled=online_payment_enabled,
        payment_gateway_settings=payment_settings,
    )


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
        client.utility.verify_payment_signature({
            "razorpay_order_id": razorpay_order_id,
            "razorpay_payment_id": razorpay_payment_id,
            "razorpay_signature": razorpay_signature
        })
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
    order_doc["status"] = "PLACED"
    order_doc["payment_status"] = "PAID"
    order_doc["payment_collection_status"] = "PAID"
    order_doc["payment_received_by"] = "ADMIN_PLATFORM"
    order_doc["payment_collected_at"] = now

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


@app.route("/api/checkout/serviceability", methods=["POST"])
@login_required()
def api_checkout_serviceability():
    u = current_user()
    cid = get_or_create_cart(u["id"])

    data = request.get_json(silent=True) or {}

    customer_lat = data.get("lat")
    customer_lng = data.get("lng")
    customer_pincode = (data.get("pincode") or "").strip()

    cart_items = list(mongo.cart_items.find({"cart_id": cid}))

    if not cart_items:
        return jsonify({
            "ok": False,
            "serviceable": False,
            "reason": "EMPTY_CART",
            "message": "Your cart is empty."
        }), 400

    store_ids = []
    items_total = 0.0
    quote_items = []

    for ci in cart_items:
        product = mongo.products.find_one({"_id": ci.get("product_id")})

        if not product:
            continue

        hydrate_product_unit_fields(product)

        store_id = product.get("store_id")

        if store_id:
            store_ids.append(str(store_id))

        quantity = cart_item_quantity(ci)

        price_per_unit = float(
            ci.get("price_per_unit_snapshot")
            if ci.get("price_per_unit_snapshot") is not None
            else product.get("price_per_unit") or 0
        )

        items_total += float(quantity or 0) * float(price_per_unit or 0)

        quote_items.append({
            "product_id": product.get("_id"),
            "quantity": quantity,
            "cart_quantity": quantity,
            "shipping_weight_kg": product.get("shipping_weight_kg"),
            "shipping_length_cm": product.get("shipping_length_cm"),
            "shipping_breadth_cm": product.get("shipping_breadth_cm"),
            "shipping_height_cm": product.get("shipping_height_cm"),
        })

    unique_store_ids = sorted(set(store_ids))

    if not unique_store_ids:
        return jsonify({
            "ok": False,
            "serviceable": False,
            "reason": "STORE_MISSING",
            "message": "Store information is missing from cart items."
        }), 400

    if len(unique_store_ids) > 1:
        return jsonify({
            "ok": False,
            "serviceable": False,
            "reason": "MULTI_STORE_CART",
            "message": "Your cart contains items from multiple stores. Please order from one store at a time."
        }), 400

    store_id_raw = unique_store_ids[0]

    try:
        store_id = ObjectId(store_id_raw)
    except Exception:
        store_id = store_id_raw

    store = mongo.stores.find_one({
        "$or": [
            {"_id": store_id},
            {"_id": store_id_raw}
        ]
    })

    if not store:
        return jsonify({
            "ok": False,
            "serviceable": False,
            "reason": "STORE_NOT_FOUND",
            "message": "Store not found."
        }), 404

    serviceability = check_checkout_delivery_quote(
        store=store,
        customer_lat=customer_lat,
        customer_lng=customer_lng,
        customer_pincode=customer_pincode,
        items_total=items_total,
        payment_method=(data.get("payment_method") or ""),
        cart_items=quote_items
    )

    delivery_fee = float(serviceability.get("delivery_fee") or 0)
    platform_result = calculate_platform_fee(items_total)
    platform_settings = platform_result.get("platform_fee_settings") or {}
    platform_fee = float(platform_result.get("platform_fee") or 0)

    total_payable = round(
        float(items_total or 0)
        + float(delivery_fee or 0)
        + float(platform_fee or 0),
        2
    )

    return jsonify({
        "ok": True,
        "serviceable": bool(serviceability.get("serviceable")),
        "reason": serviceability.get("reason"),
        "message": serviceability.get("message"),
        "distance_km": serviceability.get("distance_km"),

        "items_total": round(float(items_total or 0), 2),
        "delivery_fee": round(float(delivery_fee or 0), 2),

        "delivery_fee_source": serviceability.get("delivery_fee_source") or "unknown",
        "delivery_fee_slab": serviceability.get("delivery_fee_slab"),
        "delivery_base_fee": float(serviceability.get("delivery_base_fee") or 0),
        "delivery_fee_details": serviceability.get("delivery_fee_details") or {},
        "free_delivery_above_applied": serviceability.get("delivery_fee_source") == "admin_free_delivery_above",
        "free_delivery_above": float((serviceability.get("delivery_fee_details") or {}).get("free_delivery_above") or 0),
        "original_delivery_fee": float((serviceability.get("delivery_fee_details") or {}).get("original_delivery_fee") or serviceability.get("delivery_fee") or 0),
        "free_delivery_savings": float((serviceability.get("delivery_fee_details") or {}).get("free_delivery_savings") or 0),

        "platform_fee": round(float(platform_fee or 0), 2),
        "platform_fee_label": platform_settings.get("display_name") or "Platform Fee",
        "platform_fee_description": platform_settings.get("description") or "",
        "platform_fee_source": platform_result.get("platform_fee_source") or "disabled",
        "total_payable": total_payable,

        "active_delivery_mode": serviceability.get("active_delivery_mode") or DELIVERY_MODE_IN_HOUSE,
        "delivery_type": serviceability.get("delivery_type") or "OWN_DELIVERY",
        "external_delivery_enabled": bool(serviceability.get("external_delivery_enabled", False)),
        "external_delivery_provider": serviceability.get("external_delivery_provider") or "IN_HOUSE",
        "external_delivery_provider_type": serviceability.get("external_delivery_provider_type") or "IN_HOUSE",
        "external_payment_rule": serviceability.get("external_payment_rule") or EXTERNAL_PAYMENT_RULE_COD_STORE,
        "cod_allowed": bool(serviceability.get("cod_allowed", True)),
        "online_allowed": bool(serviceability.get("online_allowed", True)),
        "eta_minutes": serviceability.get("eta_minutes"),
        "external_delivery_quote": serviceability.get("external_delivery_quote") or {},

        "store": {
            "id": str(store.get("_id")),
            "store_name": store.get("store_name", ""),
            "is_online": int(store.get("is_online", store.get("is_open", 1)) or 0),
            "delivery_enabled": int(store.get("delivery_enabled", 1 if store.get("delivery_available", False) else 0) or 0),
            "delivery_zone_configured": 1 if len(store.get("delivery_zone_polygon") or []) >= 3 else int(store.get("delivery_zone_configured", 0) or 0)
        }
    })

@app.route("/orders", endpoint="orders")
@login_required()
def my_orders():
    u = current_user()

    orders = list(
        mongo.orders.find({"user_id": u["id"]}).sort("created_at", -1)
    )

    for o in orders:
        o["id"] = str(o["_id"])
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

@app.route("/api/orders/<oid>/status", methods=["GET"], endpoint="api_order_status")
@login_required()
def api_order_status(oid):
    u = current_user()

    data = get_order_full(
        oid,
        for_user_id=u["id"] if u["role"] == "customer" else None
    )

    if not data:
        return jsonify({
            "ok": False,
            "error": "not found"
        }), 404

    o = normalize_order_money_fields(data["order"])
    o = decorate_order_delivery_mode_display(o)

    events = []
    for e in data.get("events", []):
        events.append({
            "id": str(e.get("_id")) if e.get("_id") else e.get("id"),
            "status": e.get("status"),
            "note": e.get("note", ""),
            "created_at": e.get("created_at")
        })

    return jsonify({
        "ok": True,
        "id": o.get("id"),
        "status": o.get("status"),
        "payment_status": o.get("payment_status"),

        "total_amount": float(o.get("total_amount") or 0),
        "items_subtotal": float(o.get("items_subtotal") or o.get("total_amount") or 0),
        "delivery_fee": float(o.get("delivery_fee") or 0),
        "delivery_fee_amount": float(o.get("delivery_fee_amount") or o.get("delivery_fee") or 0),
        "delivery_fee_source": o.get("delivery_fee_source") or "",
        "delivery_fee_slab": o.get("delivery_fee_slab") or {},
        "delivery_fee_details": o.get("delivery_fee_details") or {},
        "active_delivery_mode": o.get("active_delivery_mode") or DELIVERY_MODE_IN_HOUSE,
        "delivery_mode_label": o.get("delivery_mode_label") or "",
        "delivery_mode_short_label": o.get("delivery_mode_short_label") or "",
        "delivery_fee_label": o.get("delivery_fee_label") or "Delivery Charge",
        "delivery_provider_label": o.get("delivery_provider_label") or "",
        "external_delivery_enabled_at_order": bool(o.get("external_delivery_enabled_at_order", False)),
        "external_delivery_provider": o.get("external_delivery_provider") or "",
        "external_delivery_provider_type": o.get("external_delivery_provider_type") or "",
        "external_delivery_status": o.get("external_delivery_status") or "",
        "external_delivery_status_label": o.get("external_delivery_status_label") or "",
        "external_delivery_booking_status": o.get("external_delivery_booking_status") or "",
        "external_order_id": o.get("external_order_id") or "",
        "external_shipment_id": o.get("external_shipment_id") or "",
        "external_awb": o.get("external_awb") or "",
        "external_tracking_url": o.get("external_tracking_url") or "",
        "external_tracking_code": o.get("external_tracking_code") or "",
        "external_tracking_available": bool(o.get("external_tracking_available")),
        "external_delivery_eta_minutes": o.get("external_delivery_eta_minutes"),
        "free_delivery_above_applied": bool(o.get("free_delivery_above_applied")),
        "free_delivery_above": float(o.get("free_delivery_above") or 0),
        "original_delivery_fee": float(o.get("original_delivery_fee") or o.get("delivery_fee") or 0),
        "free_delivery_savings": float(o.get("free_delivery_savings") or 0),

        "platform_fee": float(o.get("platform_fee") or 0),
        "tip_amount": float(o.get("tip_amount") or 0),
        "total_payable": float(o.get("total_payable") or o.get("total_amount") or 0),
        "admin_platform_fee_status": o.get("admin_platform_fee_status") or "",
        "platform_fee_source": o.get("platform_fee_source") or "disabled",

        "delivery_partner_id": str(o.get("delivery_partner_id")) if o.get("delivery_partner_id") else "",
        "delivery_partner_name": o.get("delivery_partner_name") or "",
        "delivery_partner_phone": o.get("delivery_partner_phone") or "",
        "needs_reassignment": bool(o.get("needs_reassignment")),
        "delivery_cancelled_by_partner": bool(o.get("delivery_cancelled_by_partner")),
        "delivery_reassigned_at": o.get("delivery_reassigned_at"),

        "delivery_failed_reason": o.get("delivery_failed_reason") or "",
        "delivery_failed_note": o.get("delivery_failed_note") or "",
        "delivery_failed_at": o.get("delivery_failed_at") or "",
        "delivery_failed_requires_store_action": bool(o.get("delivery_failed_requires_store_action", False)),
        "delivery_failed_store_decision": o.get("delivery_failed_store_decision") or "",

        "delivery_rescheduled": bool(o.get("delivery_rescheduled", False)),
        "delivery_rescheduled_for": o.get("delivery_rescheduled_for") or "",
        "delivery_rescheduled_note": o.get("delivery_rescheduled_note") or "",

        "assigned_at": o.get("assigned_at"),
        "ready_for_pickup_at": o.get("ready_for_pickup_at"),
        "reached_store_at": o.get("reached_store_at"),
        "picked_up_at": o.get("picked_up_at"),
        "out_for_delivery_at": o.get("out_for_delivery_at"),
        "delivered_at": o.get("delivered_at"),

        "events": events
    })

@app.route('/api/orders', methods=['GET'])
@api_login_required
def api_orders_list(user_id):
    orders = list(
        mongo.orders.find({"user_id": str(user_id)}).sort("created_at", -1)
    )

    result = []

    for o in orders:
        o = normalize_order_money_fields(o)
        o = decorate_order_delivery_mode_display(o)
        result.append({
            "id": str(o["_id"]),
            "store_name": o.get("store_name", ""),
            "total_amount": float(o.get("total_amount") or 0),
            "items_subtotal": float(o.get("items_subtotal") or o.get("total_amount") or 0),

            "delivery_fee": float(o.get("delivery_fee") or 0),
            "delivery_fee_amount": float(o.get("delivery_fee_amount") or o.get("delivery_fee") or 0),
            "delivery_fee_source": o.get("delivery_fee_source") or "",
            "delivery_fee_slab": o.get("delivery_fee_slab") or {},
            "delivery_fee_details": o.get("delivery_fee_details") or {},
            "free_delivery_above_applied": bool(o.get("free_delivery_above_applied")),
            "free_delivery_above": float(o.get("free_delivery_above") or 0),
            "original_delivery_fee": float(o.get("original_delivery_fee") or o.get("delivery_fee") or 0),
            "free_delivery_savings": float(o.get("free_delivery_savings") or 0),

            "platform_fee": float(o.get("platform_fee") or 0),
            "admin_platform_earning": float(o.get("admin_platform_earning") or o.get("platform_fee") or 0),
            "platform_fee_source": o.get("platform_fee_source") or "disabled",

            "tip_amount": float(o.get("tip_amount") or 0),
            "delivery_tip_amount": float(o.get("delivery_tip_amount") or o.get("tip_amount") or 0),

            "store_earning": float(o.get("store_earning") or o.get("total_amount") or 0),

            "total_payable": float(o.get("total_payable") or o.get("total_amount") or 0),

            "admin_platform_fee_status": o.get("admin_platform_fee_status") or "",
            "settlement_status": o.get("settlement_status") or "",
            "status": o.get("status", ""),
            "payment_status": o.get("payment_status", ""),
            "created_at": o.get("created_at", ""),

            "needs_reassignment": bool(o.get("needs_reassignment")),
            "delivery_cancelled_by_partner": bool(o.get("delivery_cancelled_by_partner")),
            "delivery_reassigned_at": o.get("delivery_reassigned_at"),

            "delivery_failed_reason": o.get("delivery_failed_reason") or "",
            "delivery_failed_note": o.get("delivery_failed_note") or "",
            "delivery_failed_at": o.get("delivery_failed_at") or "",
            "delivery_failed_requires_store_action": bool(o.get("delivery_failed_requires_store_action", False)),
            "delivery_failed_store_decision": o.get("delivery_failed_store_decision") or "",

            "delivery_rescheduled": bool(o.get("delivery_rescheduled", False)),
            "delivery_rescheduled_for": o.get("delivery_rescheduled_for") or "",
            "delivery_rescheduled_note": o.get("delivery_rescheduled_note") or ""
        })

    return jsonify({
        "success": True,
        "orders": result
    })



@app.route("/api/customer/order-alerts", methods=["GET"], endpoint="api_customer_order_alerts")
@login_required()
def api_customer_order_alerts():
    u = current_user()

    if not u or u.get("role") != "customer":
        return jsonify({
            "ok": True,
            "alerts": [],
            "count": 0
        })

    active_statuses = [
        "PLACED",
        "CONFIRMED",
        "PREPARING",
        "READY_FOR_PICKUP",
        "ASSIGNED_TO_DELIVERY",
        "REACHED_STORE",
        "PICKED_UP",
        "OUT_FOR_DELIVERY",
        "DELIVERY_FAILED"
    ]

    orders = list(
        mongo.orders.find({
            "user_id": u["id"],
            "status": {"$in": active_statuses}
        }).sort("updated_at", -1).limit(8)
    )

    alerts = []

    for o in orders:
        oid = str(o["_id"])
        status = (o.get("status") or "").strip().upper()

        needs_reassignment = bool(
            o.get("needs_reassignment")
            or o.get("delivery_cancelled_by_partner")
        )

        if status == "DELIVERY_FAILED":
            title = "Delivery attempt failed"
            message = f"Order #{oid[-6:]} could not be delivered. The store will reschedule or contact you shortly."
            alert_type = "delivery_failed"

        elif needs_reassignment:
            title = "Delivery partner is being reassigned"
            message = f"Order #{oid[-6:]} is safe. The store is assigning another delivery partner."
            alert_type = "reassigning"

        elif status == "ASSIGNED_TO_DELIVERY":
            title = "Delivery partner assigned"
            message = f"Order #{oid[-6:]} has been assigned to {o.get('delivery_partner_name') or 'a delivery partner'}."
            alert_type = "assigned"

        elif status == "OUT_FOR_DELIVERY":
            title = "Order is out for delivery"
            message = f"Order #{oid[-6:]} is on the way."
            alert_type = "out_for_delivery"

        elif status == "READY_FOR_PICKUP":
            title = "Order ready for pickup"
            message = f"Order #{oid[-6:]} is ready and waiting for delivery assignment."
            alert_type = "ready"

        else:
            continue

        alerts.append({
            "id": oid,
            "title": title,
            "message": message,
            "type": alert_type,
            "status": status,
            "track_url": url_for("order_track", oid=oid),
            "created_at": o.get("updated_at") or o.get("created_at") or ""
        })

    return jsonify({
        "ok": True,
        "alerts": alerts,
        "count": len(alerts)
    })


@app.route('/api/orders/<oid>', methods=['GET'])
@api_login_required
def api_order_detail(user_id, oid):
    data = get_order_full(oid, for_user_id=str(user_id))

    if not data:
        return jsonify({
            "success": False,
            "error": "Order not found"
        }), 404

    o = normalize_order_money_fields(data["order"])

    items = []

    for item in data.get("items", []):
        quantity = float(item.get("quantity") or item.get("cart_quantity") or 0)
        unit_label = item.get("unit_label") or "unit"
        unit_type = item.get("unit_type") or "COUNT"
        price_per_unit = float(
            item.get("price_per_unit")
            or item.get("unit_price")
            or 0
        )

        items.append({
            "product_id": str(item.get("product_id")) if item.get("product_id") else "",
            "name": item.get("product_name") or item.get("name", ""),
            "quantity": quantity,
            "cart_quantity": quantity,
            "unit_type": unit_type,
            "unit_label": unit_label,
            "price_per_unit": price_per_unit,
            "unit_price": price_per_unit,
            "line_total": float(item.get("line_total") or (quantity * price_per_unit)),
            "image_path": item.get("image_path", "")
        })

    address = data.get("address") or {}

    if address and address.get("_id"):
        address["id"] = str(address["_id"])
        address.pop("_id", None)

    events = []

    for e in data.get("events", []):
        events.append({
            "id": str(e.get("_id")) if e.get("_id") else e.get("id", ""),
            "status": e.get("status", ""),
            "note": e.get("note", ""),
            "created_at": e.get("created_at", "")
        })

    return jsonify({
        "success": True,
        "order": {
            "id": o.get("id") or str(o.get("_id")),
            "store_name": o.get("store_name", ""),
            "total_amount": float(o.get("total_amount") or 0),
            "items_subtotal": float(o.get("items_subtotal") or o.get("total_amount") or 0),

            "delivery_fee": float(o.get("delivery_fee") or 0),
            "delivery_fee_amount": float(o.get("delivery_fee_amount") or o.get("delivery_fee") or 0),
            "delivery_fee_source": o.get("delivery_fee_source") or "",
            "delivery_fee_slab": o.get("delivery_fee_slab") or {},
            "delivery_fee_details": o.get("delivery_fee_details") or {},
            "free_delivery_above_applied": bool(o.get("free_delivery_above_applied")),
            "free_delivery_above": float(o.get("free_delivery_above") or 0),
            "original_delivery_fee": float(o.get("original_delivery_fee") or o.get("delivery_fee") or 0),
            "free_delivery_savings": float(o.get("free_delivery_savings") or 0),

            "platform_fee": float(o.get("platform_fee") or 0),
            "admin_platform_earning": float(o.get("admin_platform_earning") or o.get("platform_fee") or 0),
            "platform_fee_source": o.get("platform_fee_source") or "disabled",

            "tip_amount": float(o.get("tip_amount") or 0),
            "delivery_tip_amount": float(o.get("delivery_tip_amount") or o.get("tip_amount") or 0),

            "store_earning": float(o.get("store_earning") or o.get("total_amount") or 0),

            "total_payable": float(o.get("total_payable") or o.get("total_amount") or 0),

            "admin_platform_fee_status": o.get("admin_platform_fee_status") or "",
            "settlement_status": o.get("settlement_status") or "",
            "status": o.get("status", ""),
            "payment_status": o.get("payment_status", ""),
            "created_at": o.get("created_at", ""),
            "delivery_partner_id": str(o.get("delivery_partner_id")) if o.get("delivery_partner_id") else "",
            "delivery_partner_name": o.get("delivery_partner_name", ""),
            "delivery_partner_phone": o.get("delivery_partner_phone", ""),

            "assigned_at": o.get("assigned_at"),
            "ready_for_pickup_at": o.get("ready_for_pickup_at"),
            "reached_store_at": o.get("reached_store_at"),
            "picked_up_at": o.get("picked_up_at"),
            "out_for_delivery_at": o.get("out_for_delivery_at"),
            "delivered_at": o.get("delivered_at"),

            "items": items,
            "address": address,
            "events": events
        }
    })
