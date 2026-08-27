"""Orders routes extracted from the updated app.py.

Logic, decorators, endpoint names and route paths are intentionally preserved.
Only the file location changed.
"""

from app_core import *
from services.platform_fees import build_order_money_breakdown, calculate_platform_fee
from services.delivery_monthly_settlement import (
    DELIVERY_MONTHLY_STATUS_PENDING_DELIVERY,
    DELIVERY_PAYOUT_MODEL_MONTHLY_V1,
    DELIVERY_PAYOUT_MODEL_NOT_REQUIRED,
)
from services.payment_gateway import (
    get_checkout_payment_gateway_settings,
    get_razorpay_client_from_settings,
    get_server_payment_gateway_settings,
    verify_razorpay_payment_signature,
)
from services.refund_policy import get_return_refund_policy_settings
from zoneinfo import ZoneInfo
from pymongo import ReturnDocument
from services.order_inventory import _release_order_stock_items, _reserve_order_stock_items

ORDER_NUMBER_TIMEZONE = ZoneInfo("Asia/Kolkata")
ORDER_NUMBER_PREFIX = "NEO"


def _next_public_order_number():
    """
    Allocate the next customer-facing order number atomically.

    Format:
        NEO-YYYY-MM-DDSSSSS

    Example:
        NEO-2026-08-1100001

    YYYY / MM / DD come from the real Asia/Kolkata calendar date.
    SSSSS is a daily serial beginning at 00001.

    MongoDB _id remains the internal order identifier used by routes,
    payments, tracking, cancellation, returns and related collections.
    """
    local_now = datetime.now(ORDER_NUMBER_TIMEZONE)
    day_key = local_now.strftime("%Y-%m-%d")

    counter = mongo.order_number_counters.find_one_and_update(
        {"_id": day_key},
        {
            "$inc": {"sequence": 1},
            "$set": {
                "updated_at": local_now.isoformat(),
            },
            "$setOnInsert": {
                "prefix": ORDER_NUMBER_PREFIX,
                "date": day_key,
                "created_at": local_now.isoformat(),
            },
        },
        upsert=True,
        return_document=ReturnDocument.AFTER,
    ) or {}

    sequence = int(counter.get("sequence") or 0)

    if sequence < 1 or sequence > 99999:
        raise RuntimeError(
            f"Daily order-number sequence is outside the supported range for {day_key}."
        )

    return f"{ORDER_NUMBER_PREFIX}-{local_now:%Y-%m}-{local_now:%d}{sequence:05d}"


def _money_float(value, default=0.0):
    try:
        if value is None or str(value).strip() == "":
            return default
        return float(value)
    except Exception:
        return default








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
    payment_collection_channel = (order_doc.get("payment_collection_channel") or "").strip().upper()
    payment_reconciliation_status = (order_doc.get("payment_reconciliation_status") or "").strip().upper()
    upi_delivery_reconciliation_status = (order_doc.get("upi_delivery_reconciliation_status") or "").strip().upper()
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
        payment_method_label = "Cash on Delivery (COD)"
    else:
        payment_method_label = payment_method.replace("_", " ").title() if payment_method else "Payment"

    cod_payment_recorded = bool(
        is_cod_order
        and (
            payment_status in [
                "COLLECTED_BY_RIDER",
                "COD_COLLECTED_BY_RIDER",
                "COD_UPI_RECORDED",
                "COLLECTED_BY_STORE",
                "COLLECTED_BY_EXTERNAL_PARTNER",
                "PAID",
            ]
            or payment_collection_status in [
                "COLLECTED",
                "COLLECTED_BY_STORE",
                "COLLECTED_BY_EXTERNAL_PARTNER",
                "PAID",
            ]
        )
    )

    payment_received_by = (order_doc.get("payment_received_by") or "").strip().upper()
    cod_collection_method = (order_doc.get("cod_collection_method") or "").strip().upper()
    external_cod_remittance_status = (order_doc.get("external_cod_remittance_status") or "").strip().upper()

    if online_payment_paid:
        payment_status_label = "Paid"
        payment_badge_class = "paid"
        payment_notice = "Payment received by NE FRESH through Razorpay."
    elif online_payment_pending:
        payment_status_label = "Payment Pending"
        payment_badge_class = "pending"
        payment_notice = "Please complete online payment to confirm this order."
    elif is_cod_order and payment_received_by == "STORE" and cod_payment_recorded:
        payment_status_label = "Paid on Delivery · Store"
        payment_badge_class = "paid"
        payment_notice = "Your Pay-on-Delivery payment was received by the Store."
    elif is_cod_order and payment_received_by == "EXTERNAL_PARTNER" and cod_payment_recorded:
        payment_status_label = "Paid on Delivery"
        payment_badge_class = "paid"
        payment_notice = "Your Pay-on-Delivery payment was received by the external delivery partner."
    elif is_cod_order and payment_collection_channel == "UPI" and upi_delivery_reconciliation_status == "VERIFIED":
        payment_status_label = "Paid via UPI at Delivery"
        payment_badge_class = "paid"
        payment_notice = "UPI payment received and verified by NE FRESH."
    elif is_cod_order and payment_collection_channel == "UPI" and cod_payment_recorded:
        payment_status_label = "UPI Paid · Verification Pending"
        payment_badge_class = "pending"
        payment_notice = "UPI payment reference was recorded at delivery and is awaiting Admin reconciliation."
    elif is_cod_order and payment_collection_channel == "CASH" and cod_payment_recorded:
        payment_status_label = "Paid on Delivery · Cash"
        payment_badge_class = "paid"
        payment_notice = "Cash payment was collected by the delivery partner."
    elif is_cod_order:
        payment_status_label = "Pay on Delivery (COD)"
        payment_badge_class = "cod"
        payment_notice = "Pay when your order is delivered using cash or supported UPI."
    elif payment_status:
        payment_status_label = payment_status.replace("_", " ").title()
        payment_badge_class = "pending"
        payment_notice = ""
    else:
        payment_status_label = "Pending"
        payment_badge_class = "pending"
        payment_notice = ""

    if payment_received_by == "ADMIN_PLATFORM" and payment_collection_channel == "UPI":
        payment_received_by_label = "NE FRESH / Official UPI"
    elif payment_received_by == "ADMIN_PLATFORM":
        payment_received_by_label = "NE FRESH / Platform"
    elif payment_received_by == "STORE":
        payment_received_by_label = "Store"
    elif payment_received_by == "EXTERNAL_PARTNER":
        payment_received_by_label = "External Delivery Partner"
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
    order_doc["payment_collection_channel"] = payment_collection_channel
    order_doc["payment_reconciliation_status"] = payment_reconciliation_status
    order_doc["upi_delivery_reconciliation_status"] = upi_delivery_reconciliation_status
    order_doc["external_cod_remittance_status"] = external_cod_remittance_status
    order_doc["cod_collection_method"] = cod_collection_method

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

    order_doc["cod_collection_required"] = bool(is_cod_order and not cod_payment_recorded)
    order_doc["amount_to_collect_from_customer"] = (
        float(order_doc.get("total_payable") or order_doc.get("total_amount") or 0)
        if is_cod_order and not cod_payment_recorded
        else 0.0
    )

    return order_doc










    


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


def _format_customer_datetime(value):
    """Customer-facing India date/time: dd/mm/yy hh:mm AM/PM, no seconds."""
    dt = _parse_order_datetime(value)

    if not dt:
        return str(value or "")

    # Current project timestamps are predominantly stored as naive UTC.
    if dt.tzinfo is None:
        dt = dt + timedelta(hours=5, minutes=30)

    return dt.strftime("%d/%m/%y %I:%M %p")


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






def _checkout_safe_object_id(value):
    try:
        if ObjectId.is_valid(str(value)):
            return ObjectId(str(value))
    except Exception:
        pass

    return None


def _checkout_hydrate_cart_product_item(ci):
    product = mongo.products.find_one({"_id": ci.get("product_id")})

    if not product and ci.get("product_id"):
        product_obj_id = _checkout_safe_object_id(ci.get("product_id"))
        if product_obj_id:
            product = mongo.products.find_one({"_id": product_obj_id})

    if not product:
        return None

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

    return {
        "item_type": "product",
        "is_bundle": False,
        "product_id": product["_id"],
        "product_id_str": str(product["_id"]),
        "bundle_id": "",
        "bundle_id_str": "",
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
        "product_name": product.get("name", ""),
        "image_path": product.get("image_path", ""),
        "shipping_weight_kg": product.get("shipping_weight_kg"),
        "shipping_length_cm": product.get("shipping_length_cm"),
        "shipping_breadth_cm": product.get("shipping_breadth_cm"),
        "shipping_height_cm": product.get("shipping_height_cm")
    }


def _checkout_hydrate_cart_bundle_item(ci):
    bundle_id_raw = ci.get("bundle_id") or ci.get("bundle_id_str")
    bundle_obj_id = _checkout_safe_object_id(bundle_id_raw)

    bundle = None

    if bundle_obj_id:
        bundle = mongo.product_bundles.find_one({"_id": bundle_obj_id})

    if not bundle and bundle_id_raw:
        bundle = mongo.product_bundles.find_one({"bundle_id_str": str(bundle_id_raw)})

    if not bundle:
        return None

    bundle = build_live_product_bundle(
        bundle,
        notify_store=True,
        notification_context="checkout"
    ) or bundle

    quantity = int(cart_item_quantity(ci) or 1)

    ok, error = validate_product_bundle_for_cart(bundle, quantity=quantity)

    if not ok:
        return {
            "item_type": "bundle",
            "is_bundle": True,
            "invalid_bundle": True,
            "bundle_error": error,
            "name": bundle.get("bundle_name") or "Product Bundle",
            "bundle_name_snapshot": bundle.get("bundle_name") or "Product Bundle",
            "store_id": bundle.get("store_id"),
            "is_active": int(bundle.get("is_active", 0) or 0),
            "stock_quantity": int(bundle.get("max_bundle_stock") or 0),
            "quantity": quantity,
            "cart_quantity": quantity,
            "line_total": 0,
        }

    bundle_price = float(bundle.get("bundle_price") or 0)
    line_total = round(bundle_price * quantity, 2)

    return {
        "item_type": "bundle",
        "is_bundle": True,
        "invalid_bundle": False,
        "product_id": None,
        "product_id_str": "",
        "bundle_id": bundle.get("_id"),
        "bundle_id_str": str(bundle.get("_id")),
        "bundle_name_snapshot": bundle.get("bundle_name") or "Product Bundle",
        "name": bundle.get("bundle_name") or "Product Bundle",
        "product_name": bundle.get("bundle_name") or "Product Bundle",
        "description": bundle.get("description") or "",
        "bundle_items_snapshot": bundle.get("items") or [],
        "bundle_savings_snapshot": float(bundle.get("savings_amount") or 0),
        "items_total_snapshot": float(bundle.get("items_total") or 0),
        "quantity": quantity,
        "cart_quantity": quantity,
        "unit_type": "COUNT",
        "unit_label": "bundle",
        "price_per_unit": bundle_price,
        "stock_quantity": int(bundle.get("max_bundle_stock") or 0),
        "quantity_min": 1,
        "quantity_step": 1,
        "line_total": line_total,
        "store_id": bundle.get("store_id"),
        "is_active": int(bundle.get("is_active", 1) or 0),
        "image_path": bundle.get("image_path", ""),
        "shipping_weight_kg": None,
        "shipping_length_cm": None,
        "shipping_breadth_cm": None,
        "shipping_height_cm": None
    }


def _checkout_hydrate_cart_item(ci):
    item_type = (ci.get("item_type") or "product").strip().lower()

    if item_type == "bundle" or ci.get("bundle_id"):
        return _checkout_hydrate_cart_bundle_item(ci)

    return _checkout_hydrate_cart_product_item(ci)


def _checkout_order_item_doc_from_item(it):
    if it.get("is_bundle"):
        return {
            "item_type": "bundle",
            "product_id": None,
            "bundle_id": it.get("bundle_id"),
            "bundle_id_str": it.get("bundle_id_str"),
            "bundle_name_snapshot": it.get("bundle_name_snapshot") or it.get("name") or "Product Bundle",
            "product_name": it.get("bundle_name_snapshot") or it.get("name") or "Product Bundle",
            "name": it.get("bundle_name_snapshot") or it.get("name") or "Product Bundle",
            "quantity": int(it.get("quantity") or 1),
            "cart_quantity": int(it.get("quantity") or 1),
            "unit_type": "COUNT",
            "unit_label": "bundle",
            "quantity_min": 1,
            "quantity_step": 1,
            "price_per_unit": float(it.get("price_per_unit") or 0),
            "unit_price": float(it.get("price_per_unit") or 0),
            "line_total": float(it.get("line_total") or 0),
            "image_path": it.get("image_path", ""),
            "bundle_items_snapshot": it.get("bundle_items_snapshot") or [],
            "bundle_savings_snapshot": float(it.get("bundle_savings_snapshot") or 0),
            "items_total_snapshot": float(it.get("items_total_snapshot") or 0),
            "shipping_weight_kg": it.get("shipping_weight_kg"),
            "shipping_length_cm": it.get("shipping_length_cm"),
            "shipping_breadth_cm": it.get("shipping_breadth_cm"),
            "shipping_height_cm": it.get("shipping_height_cm")
        }

    return {
        "item_type": "product",
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
        "line_total": float(it.get("line_total") or (float(it["quantity"]) * float(it["price_per_unit"]))),
        "image_path": it.get("image_path", ""),
        "shipping_weight_kg": it.get("shipping_weight_kg"),
        "shipping_length_cm": it.get("shipping_length_cm"),
        "shipping_breadth_cm": it.get("shipping_breadth_cm"),
        "shipping_height_cm": it.get("shipping_height_cm")
    }

















def _notification_user_key(user_doc):
    role = str((user_doc or {}).get("role") or "").strip().lower()
    raw_id = str((user_doc or {}).get("id") or (user_doc or {}).get("_id") or "").strip()
    return f"{role}:{raw_id}" if role and raw_id else ""


def _notification_state_for(user_key):
    if not user_key:
        return {
            "read_keys": [],
            "cleared_keys": []
        }

    state = mongo.user_notification_states.find_one(
        {"_id": user_key},
        {
            "read_keys": 1,
            "cleared_keys": 1
        }
    ) or {}

    return {
        "read_keys": [
            str(v).strip()
            for v in (state.get("read_keys") or [])
            if str(v).strip()
        ],
        "cleared_keys": [
            str(v).strip()
            for v in (state.get("cleared_keys") or [])
            if str(v).strip()
        ]
    }


def _save_notification_keys(user_key, field_name, keys):
    if not user_key:
        return

    clean_keys = []

    for key in keys or []:
        key = str(key or "").strip()

        if key and key not in clean_keys:
            clean_keys.append(key)

    if not clean_keys:
        return

    mongo.user_notification_states.update_one(
        {"_id": user_key},
        {
            "$addToSet": {
                field_name: {
                    "$each": clean_keys
                }
            },
            "$set": {
                "updated_at": datetime.utcnow().isoformat()
            }
        },
        upsert=True
    )

# Export the complete compatibility namespace, including underscore-prefixed
# legacy helpers, to the domain route modules.  This is transitional and will
# be replaced by explicit imports as app_core.py and route helpers continue to shrink.
__all__ = [name for name in globals() if not name.startswith('__')]
