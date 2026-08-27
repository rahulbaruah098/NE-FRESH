"""Store routes extracted from the updated app.py.

Logic, decorators, endpoint names and route paths are intentionally preserved.
Only the file location changed.
"""

from app_core import *
from services.finance_reconciliation import finance_reconciliation_snapshot
from services.store_finance_adjustments import finance_store_outstanding_adjustment_total
from services.order_inventory import _release_order_stock_items
from services.order_lifecycle import is_store_order_active, store_order_visible_to_store
from bson.binary import Binary
from PIL import Image, ImageOps



STORE_IN_HOUSE_DELIVERY_ENDPOINTS = {
    "store_delivery_toggle",
    "store_delivery",
    "store_delivery_history",
    "store_order_ready_for_pickup",
    "store_order_assign_delivery",
    "store_order_reassign_delivery",
    "store_order_reschedule_failed_delivery",
    "store_order_cancel_failed_delivery",
    "store_order_clear_delivery",
    "store_order_delivery_options",
}

STORE_RETURN_REFUND_ENDPOINTS = {
    "store_returns",
    "store_return_review",
}


STORE_PRODUCT_CARD_THUMBNAIL_MAX_SIZE = (960, 1440)
STORE_PRODUCT_CARD_THUMBNAIL_QUALITY = 84


def _store_generate_product_card_thumbnail(image_path):
    """
    Generate one optimized WebP thumbnail for product catalogue cards.

    - Keeps the original upload untouched.
    - Preserves the complete source image.
    - Preserves the original aspect ratio.
    - Never crops, stretches, distorts or adds a blurred background.
    - Limits thumbnail dimensions for faster catalogue loading.
    - Applies EXIF orientation before resizing.
    - Returns a relative uploads/... path, or None if generation fails.
    """
    if not image_path:
        return None

    normalized_path = str(image_path).replace("\\", "/").lstrip("/")

    if normalized_path.startswith("uploads/"):
        relative_from_upload_root = normalized_path[len("uploads/"):]
    else:
        relative_from_upload_root = normalized_path

    source_path = os.path.join(
        app.config["UPLOAD_FOLDER"],
        relative_from_upload_root
    )

    if not os.path.isfile(source_path):
        app.logger.warning(
            "Product thumbnail source image not found: %s",
            source_path
        )
        return None

    thumbnail_folder = os.path.join(
        app.config["UPLOAD_FOLDER"],
        "product_thumbnails"
    )
    os.makedirs(thumbnail_folder, exist_ok=True)

    source_stem = os.path.splitext(os.path.basename(source_path))[0]
    thumbnail_name = f"{source_stem}_960x600.webp"
    thumbnail_path = os.path.join(thumbnail_folder, thumbnail_name)

    try:
        with Image.open(source_path) as source_image:
            image = ImageOps.exif_transpose(source_image)

            if image.mode in ("RGBA", "LA") or (
                image.mode == "P" and "transparency" in image.info
            ):
                rgba = image.convert("RGBA")
                white_background = Image.new(
                    "RGBA",
                    rgba.size,
                    (255, 255, 255, 255)
                )
                white_background.alpha_composite(rgba)
                image = white_background.convert("RGB")
            else:
                image = image.convert("RGB")

            thumbnail = image.copy()
            thumbnail.thumbnail(
                STORE_PRODUCT_CARD_THUMBNAIL_MAX_SIZE,
                Image.Resampling.LANCZOS
            )

            thumbnail.save(
                thumbnail_path,
                format="WEBP",
                quality=STORE_PRODUCT_CARD_THUMBNAIL_QUALITY,
                method=6
            )

        return f"uploads/product_thumbnails/{thumbnail_name}"

    except Exception:
        app.logger.exception(
            "Failed to generate product card thumbnail for %s",
            source_path
        )
        return None


@app.before_request
def _block_store_delivery_and_returns_when_disabled():
    endpoint = request.endpoint or ""

    if endpoint in STORE_IN_HOUSE_DELIVERY_ENDPOINTS:
        if is_delivery_feature_enabled("delivery_assignment_enabled", True):
            return None

        if endpoint == "store_order_delivery_options" or request.is_json:
            return jsonify({
                "ok": False,
                "disabled": True,
                "error": "In-house delivery is currently disabled by Admin."
            }), 403

        flash("In-house delivery is currently disabled by Admin.", "warning")
        return redirect(url_for("store_orders"))

    if endpoint in STORE_RETURN_REFUND_ENDPOINTS:
        if is_delivery_feature_enabled("return_refund_enabled", True):
            return None

        flash("Return/refund module is currently disabled by Admin.", "warning")
        return redirect(url_for("store_orders"))

    return None

def _store_bool_from_form(name, default=False):
    values = request.form.getlist(name)

    if not values:
        return bool(default)

    clean_values = [
        str(v).strip().lower()
        for v in values
        if v is not None
    ]

    return any(v in ["1", "true", "yes", "on"] for v in clean_values)


def _store_float_or_none(value, min_value=None, max_value=None):
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


def _store_money_or_default(value, default=0):
    try:
        if value is None or str(value).strip() == "":
            return float(default)

        number = float(value)

        if number < 0:
            return float(default)

        return round(number, 2)
    except Exception:
        return float(default)
    

def _store_cancelled_money(value, default=0.0):
    try:
        if value is None or str(value).strip() == "":
            return float(default)

        return round(float(value), 2)
    except Exception:
        return float(default)


def _store_cancelled_text(value, fallback=""):
    if value is None:
        return fallback

    value = str(value).strip()

    return value if value else fallback


def _store_order_id_values(order_id):
    values = []

    if order_id is None:
        return values

    values.append(order_id)
    values.append(str(order_id))

    try:
        if ObjectId.is_valid(str(order_id)):
            values.append(ObjectId(str(order_id)))
    except Exception:
        pass

    return values


def _store_cancelled_source_query(cancel_type):
    cancel_type = (cancel_type or "customer").strip().lower()

    if cancel_type == "store":
        return {
            "$or": [
                {"cancelled_by": "store"},
                {"cancelled_by_role": "store"},
                {"cancel_source": "store"},
                {"cancelled_source": "store"},
                {"cancelled_by_role": "STORE"},
                {"cancelled_by": "STORE"}
            ]
        }

    return {
        "$or": [
            {"cancelled_by": "customer"},
            {"cancelled_by_role": "customer"},
            {"cancel_source": "customer"},
            {"cancelled_source": "customer"},
            {"cancelled_by": "user"},
            {"cancelled_by_role": "user"},
            {"cancelled_by": "CUSTOMER"},
            {"cancelled_by_role": "CUSTOMER"}
        ]
    }


def _store_prepare_cancelled_order_row(order, store=None):
    order = order or {}

    order_id = order.get("_id") or order.get("id")
    order["id"] = str(order_id) if order_id else ""

    order["short_id"] = order["id"][-6:] if order["id"] else ""

    order["status"] = _store_cancelled_text(order.get("status"), "CANCELLED").upper()
    order["created_at"] = _store_cancelled_text(order.get("created_at"))
    order["cancelled_at"] = _store_cancelled_text(order.get("cancelled_at"))

    order["cancelled_by"] = _store_cancelled_text(
        order.get("cancelled_by_role") or order.get("cancelled_by"),
        "Unknown"
    ).replace("_", " ").title()

    order["cancelled_by_name"] = _store_cancelled_text(
        order.get("cancelled_by_name"),
        order["cancelled_by"]
    )

    order["cancel_reason"] = _store_cancelled_text(
        order.get("cancellation_reason") or order.get("cancel_reason") or order.get("refund_reason"),
        "No reason recorded"
    )

    order["customer_name"] = _store_cancelled_text(order.get("customer_name"), "Customer")
    order["customer_phone"] = _store_cancelled_text(order.get("customer_phone"), "")

    if order.get("user_id") and (not order["customer_phone"] or order["customer_name"] == "Customer"):
        try:
            customer = mongo.users.find_one({"_id": ObjectId(str(order.get("user_id")))})
        except Exception:
            customer = mongo.users.find_one({"_id": str(order.get("user_id"))})

        if customer:
            order["customer_name"] = customer.get("name") or order["customer_name"]
            order["customer_phone"] = customer.get("phone") or order["customer_phone"]

    order["items_subtotal"] = _store_cancelled_money(
        order.get("items_subtotal"),
        order.get("store_earning") or order.get("total_amount") or 0
    )

    order["delivery_fee"] = _store_cancelled_money(
        order.get("delivery_fee_amount")
        if order.get("delivery_fee_amount") is not None
        else order.get("delivery_fee"),
        0
    )

    order["platform_fee"] = _store_cancelled_money(order.get("platform_fee"), 0)
    order["tip_amount"] = _store_cancelled_money(
        order.get("tip_amount")
        if order.get("tip_amount") is not None
        else order.get("delivery_tip_amount"),
        0
    )

    order["total_payable"] = _store_cancelled_money(
        order.get("total_payable"),
        order.get("total_amount") or (
            order["items_subtotal"]
            + order["delivery_fee"]
            + order["platform_fee"]
            + order["tip_amount"]
        )
    )

    order["payment_method"] = _store_cancelled_text(order.get("payment_method"), "COD").upper()
    order["payment_status"] = _store_cancelled_text(order.get("payment_status"), "PENDING").upper()
    order["payment_collection_status"] = _store_cancelled_text(order.get("payment_collection_status"), "").upper()
    order["refund_status"] = _store_cancelled_text(order.get("refund_status"), "NOT_REQUIRED").upper()
    order["order_settlement_status"] = _store_cancelled_text(order.get("order_settlement_status"), "").upper()

    order_item_count = mongo.order_items.count_documents({
        "order_id": {
            "$in": _store_order_id_values(order_id)
        }
    })

    order["items_count"] = order_item_count

    if store:
        order["store_name"] = store.get("store_name") or store.get("name") or "Store"
    else:
        order["store_name"] = _store_cancelled_text(order.get("store_name"), "Store")

    return order


def _parse_store_delivery_fee_slabs_from_form(existing_slabs=None):
    """
    Parses store delivery fee slab rows from store profile form.

    Expected input names from template:
        slab_min_km[]
        slab_max_km[]
        slab_fee[]

    max_km can be blank for the final open-ended slab.

    If the new fields are not present in the form yet, this keeps existing slabs.
    This prevents older profile forms from accidentally clearing delivery slabs.
    """

    if (
        "slab_min_km[]" not in request.form
        and "slab_max_km[]" not in request.form
        and "slab_fee[]" not in request.form
    ):
        return existing_slabs or []

    min_values = request.form.getlist("slab_min_km[]")
    max_values = request.form.getlist("slab_max_km[]")
    fee_values = request.form.getlist("slab_fee[]")

    max_len = max(len(min_values), len(max_values), len(fee_values), 0)

    cleaned = []

    for index in range(max_len):
        min_raw = min_values[index] if index < len(min_values) else ""
        max_raw = max_values[index] if index < len(max_values) else ""
        fee_raw = fee_values[index] if index < len(fee_values) else ""

        min_km = _store_float_or_none(min_raw, 0, 999999)
        max_km = _store_float_or_none(max_raw, 0, 999999)
        fee = _store_money_or_default(fee_raw, -1)

        # Skip completely blank row.
        if str(min_raw).strip() == "" and str(max_raw).strip() == "" and str(fee_raw).strip() == "":
            continue

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

    # Remove overlapping invalid rows.
    final_rows = []
    previous_max = None

    for row in cleaned:
        min_km = float(row.get("min_km") or 0)
        max_km = row.get("max_km")

        if previous_max is not None and min_km < previous_max:
            continue

        final_rows.append(row)

        if max_km is None:
            previous_max = None
        else:
            previous_max = float(max_km)

    return final_rows


def _parse_delivery_zone_polygon(raw):
    """
    Expected hidden input format:
    [
      [26.12345, 91.12345],
      [26.12400, 91.13000],
      [26.11800, 91.13200]
    ]

    Returns clean polygon list or [].
    """
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

            lat = _store_float_or_none(point[0], -90, 90)
            lng = _store_float_or_none(point[1], -180, 180)

            if lat is not None and lng is not None:
                cleaned.append([lat, lng])

        # Polygon needs at least 3 points.
        if len(cleaned) < 3:
            return []

        return cleaned
    except Exception:
        return []






















def _store_order_id_or_redirect(oid):
    try:
        return ObjectId(str(oid))
    except Exception:
        return None


def _get_store_owned_order(store, oid):
    oid_obj = _store_order_id_or_redirect(oid)

    if not oid_obj:
        return None, None

    store_id = store.get("_id")
    store_id_str = str(store_id)

    order = mongo.orders.find_one({
        "_id": oid_obj,
        "$or": [
            {"store_id": store_id},
            {"store_id": store_id_str}
        ]
    })

    return oid_obj, order


def _hydrate_store_delivery_people_for_template(store):
    """
    Online delivery boys available for this store.
    Uses app_core.py helper added in Step 1.
    """
    try:
        return get_online_delivery_people_near_store(
            store,
            max_km=DELIVERY_ACCEPT_RADIUS_KM
        )
    except Exception:
        return []
    

def _store_delivery_money_float(value, default=0.0):
    try:
        if value is None or str(value).strip() == "":
            return float(default)
        return float(value)
    except Exception:
        return float(default)


def _store_delivery_safe_str(value):
    if value is None:
        return ""

    try:
        if isinstance(value, ObjectId):
            return str(value)
    except Exception:
        pass

    return str(value)


def _store_delivery_latest_history_entry(order, action_name):
    entries = []

    for h in order.get("delivery_history") or []:
        if not isinstance(h, dict):
            continue

        if h.get("action") == action_name:
            entries.append(h)

    return entries[-1] if entries else {}


def _store_delivery_assignment_source_label(source):
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


def _decorate_store_delivery_order(order):
    """
    Store-side delivery/order display helper.

    This does not update database values.
    It only prepares safe financial + delivery boy fields for store templates.
    """
    order = order or {}

    items_subtotal = _store_delivery_money_float(
        order.get("items_subtotal")
        if order.get("items_subtotal") is not None
        else order.get("total_amount")
    )

    delivery_fee = _store_delivery_money_float(order.get("delivery_fee"))
    platform_fee = _store_delivery_money_float(order.get("platform_fee"))
    tip_amount = _store_delivery_money_float(
        order.get("tip_amount")
        if order.get("tip_amount") is not None
        else order.get("delivery_tip_amount")
    )

    total_payable = _store_delivery_money_float(
        order.get("total_payable"),
        items_subtotal + delivery_fee + platform_fee + tip_amount
    )

    payment_method = (order.get("payment_method") or "COD").strip().upper()
    payment_status = (order.get("payment_status") or "PENDING").strip().upper()
    payment_collection_status = (order.get("payment_collection_status") or "").strip().upper()
    payment_collection_channel = (order.get("payment_collection_channel") or "").strip().upper()
    upi_delivery_reconciliation_status = (order.get("upi_delivery_reconciliation_status") or "").strip().upper()
    payment_received_by = (order.get("payment_received_by") or "").strip().upper()
    external_cod_remittance_status = (order.get("external_cod_remittance_status") or "").strip().upper()

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
        "COD_UPI_RECORDED",
        "COLLECTED_BY_STORE",
        "COLLECTED_BY_EXTERNAL_PARTNER"
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
        _store_delivery_money_float(
            order.get("cod_collected_amount"),
            total_payable
        )
        if is_cod_collected
        else 0.0
    )

    if is_cod_collected:
        cod_display_amount = cod_collected_amount
        if payment_received_by == "STORE":
            cod_display_label = "Received by Store"
        elif payment_received_by == "EXTERNAL_PARTNER" and external_cod_remittance_status in {"RECEIVED", "VERIFIED", "SETTLED", "PAID"}:
            cod_display_label = "External partner payment reconciled"
        elif payment_received_by == "EXTERNAL_PARTNER":
            cod_display_label = "Received by external partner · remittance pending"
        elif is_cod_upi and upi_delivery_reconciliation_status == "VERIFIED":
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

    free_delivery_applied = bool(order.get("free_delivery_above_applied"))

    original_delivery_fee = _store_delivery_money_float(
        order.get("original_delivery_fee"),
        delivery_fee
    )

    free_delivery_savings = _store_delivery_money_float(
        order.get("free_delivery_savings"),
        original_delivery_fee if free_delivery_applied else 0
    )

    delivery_fee_plus_tip = delivery_fee + tip_amount

    # Store earning means product/items subtotal only.
    # Platform fee is separate and belongs to platform/admin.
    store_earning = items_subtotal
    admin_platform_earning = platform_fee

    delivery_partner_id = order.get("delivery_partner_id") or order.get("previous_delivery_partner_id")
    delivery_partner_name = (
        order.get("delivery_partner_name")
        or order.get("previous_delivery_partner_name")
        or ""
    )
    delivery_partner_phone = (
        order.get("delivery_partner_phone")
        or order.get("previous_delivery_partner_phone")
        or ""
    )

    # Fallback lookup if order has rider id but name/phone missing.
    if delivery_partner_id and (not delivery_partner_name or not delivery_partner_phone):
        delivery_user = None

        try:
            if ObjectId.is_valid(str(delivery_partner_id)):
                delivery_user = mongo.users.find_one({"_id": ObjectId(str(delivery_partner_id))})
        except Exception:
            delivery_user = None

        if not delivery_user:
            try:
                delivery_user = mongo.users.find_one({"_id": str(delivery_partner_id)})
            except Exception:
                delivery_user = None

        if delivery_user:
            delivery_partner_name = delivery_partner_name or delivery_user.get("name") or delivery_user.get("username") or ""
            delivery_partner_phone = delivery_partner_phone or delivery_user.get("phone") or delivery_user.get("contact") or ""

    rider_cancel_entry = _store_delivery_latest_history_entry(order, "cancelled_by_delivery_partner")

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
    finance_state = finance_reconciliation_snapshot(order)
    order["collection_channel_label"] = finance_state.get("collection_label") or (
        "UPI" if is_cod_upi
        else ("Cash" if is_cod_order else ("Razorpay" if payment_collection_channel == "RAZORPAY" else "Online"))
    )
    order["payment_received_by"] = payment_received_by
    order["payment_receiver_label"] = finance_state.get("payment_receiver_label") or ""
    order["payment_collection_label"] = finance_state.get("collection_label") or order["collection_channel_label"]
    order["payment_reconciliation_status"] = finance_state.get("payment_reconciliation_status") or order.get("payment_reconciliation_status") or ""
    order["customer_payment_reconciled"] = bool(finance_state.get("customer_payment_reconciled"))
    order["platform_fee_reconciliation_status"] = finance_state.get("platform_fee_reconciliation_status") or order.get("platform_fee_status") or ""
    order["store_payout_required"] = bool(finance_state.get("store_payout_required"))
    order["store_payout_eligible"] = bool(finance_state.get("store_payout_eligible"))
    order["store_payout_block_reason"] = finance_state.get("store_payout_block_reason") or ""
    order["external_cod_remittance_status"] = external_cod_remittance_status
    order["is_cod_order"] = is_cod_order
    order["is_cod_collected"] = is_cod_collected
    order["amount_to_collect"] = round(amount_to_collect, 2)
    order["cod_collected_amount"] = round(cod_collected_amount, 2)
    order["cod_display_amount"] = round(cod_display_amount, 2)
    order["cod_display_label"] = cod_display_label

    order["free_delivery_above_applied"] = free_delivery_applied
    order["original_delivery_fee"] = round(original_delivery_fee, 2)
    order["free_delivery_savings"] = round(free_delivery_savings, 2)
    order["free_delivery_above"] = _store_delivery_money_float(order.get("free_delivery_above"))

    order["delivery_fee_plus_tip"] = round(delivery_fee_plus_tip, 2)
    order["delivery_boy_expected_earning"] = round(delivery_fee_plus_tip, 2)
    order["store_earning"] = round(store_earning, 2)
    order["admin_platform_earning"] = round(admin_platform_earning, 2)

    order["delivery_partner_id_str"] = _store_delivery_safe_str(delivery_partner_id)
    order["delivery_partner_name"] = delivery_partner_name or "Not assigned"
    order["delivery_partner_phone"] = delivery_partner_phone or ""
    order["has_delivery_partner"] = bool(delivery_partner_id)

    order["delivery_assignment_source_label"] = _store_delivery_assignment_source_label(
        order.get("delivery_assignment_source")
    )

    order["assigned_at"] = order.get("delivery_assigned_at") or order.get("assigned_at") or ""
    order["reached_store_at"] = order.get("reached_store_at") or ""
    order["picked_up_at"] = order.get("picked_up_at") or ""
    order["out_for_delivery_at"] = order.get("out_for_delivery_at") or ""
    order["delivered_at"] = order.get("delivered_at") or ""
    order["delivery_failed_at"] = order.get("delivery_failed_at") or ""
    order["delivery_failed_reason"] = order.get("delivery_failed_reason") or ""
    order["delivery_failed_note"] = order.get("delivery_failed_note") or ""

    order["rider_cancel_reason"] = (
        rider_cancel_entry.get("reason")
        or order.get("delivery_cancel_reason")
        or ""
    )
    order["rider_cancelled_at"] = (
        rider_cancel_entry.get("at")
        or order.get("delivery_cancelled_at")
        or ""
    )
    order["rider_cancelled_status_from"] = (
        rider_cancel_entry.get("status_before_cancel")
        or order.get("delivery_cancelled_status_from")
        or ""
    )

    order = decorate_order_delivery_mode_display(order)

    return order












































def _store_category_ajax_request():
    """Return True only for the Store Categories page AJAX actions."""
    return (
        request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or request.args.get("_format") == "json"
    )


def _store_category_response(
    message,
    feedback_type="success",
    status_code=200,
    payload=None,
    redirect_endpoint="store_categories"
):
    """
    Preserve the original flash + redirect behaviour for normal requests,
    while returning JSON to the updated categories page.

    `feedback_type` is intentionally separate from the `category` payload key.
    This prevents the category object from overwriting the alert CSS class name.
    """
    if _store_category_ajax_request():
        response_data = {
            "ok": status_code < 400,
            "message": message,
            "feedback_type": feedback_type,
        }

        if payload:
            response_data.update(payload)

        return jsonify(response_data), status_code

    flash(message, feedback_type)
    return redirect(url_for(redirect_endpoint))


def _store_category_payload(category_doc, store_id):
    """Create the JSON-safe category shape used by the categories page."""
    if not category_doc:
        return None

    category_id = str(category_doc.get("_id") or "")
    category_name = (category_doc.get("name") or "").strip()
    image_path = (
        category_doc.get("category_image_path")
        or category_doc.get("image_path")
        or ""
    )

    return {
        "id": category_id,
        "name": category_name,
        "slug": category_doc.get("slug") or _category_slug(category_name),
        "sub_categories": category_doc.get("sub_categories") or [],
        "image_path": image_path,
        "category_image_path": image_path,
        "is_active": 1 if int(category_doc.get("is_active") or 0) == 1 else 0,
        "product_count": _get_category_product_count(store_id, category_name),
        "update_url": url_for("store_category_update", cid=category_id),
        "toggle_url": url_for("store_category_toggle", cid=category_id),
        "delete_url": url_for("store_category_delete", cid=category_id),
    }










# =========================================================
# STORE PRODUCT BUNDLES
# =========================================================
def _store_bundle_get_current_store():
    u = current_user()
    store = mongo.stores.find_one({"user_id": u["id"]})
    return u, store


def _store_bundle_product_ids_from_form(form):
    raw_ids = []

    for key in [
        "bundle_product_ids[]",
        "product_ids[]",
        "products[]",
        "bundle_product_ids",
        "product_ids",
        "products",
    ]:
        values = form.getlist(key)
        if values:
            raw_ids.extend(values)

    # Supports comma-separated fallback from a hidden input.
    if not raw_ids:
        hidden_raw = form.get("selected_product_ids") or form.get("bundle_products") or ""
        raw_ids = [x.strip() for x in str(hidden_raw).split(",") if x.strip()]

    return normalize_bundle_product_ids(raw_ids)


def _store_bundle_quantities_from_form(form, product_ids):
    quantities = {}

    for pid in product_ids:
        qty_value = (
            form.get(f"bundle_quantity_{pid}")
            or form.get(f"quantity_{pid}")
            or form.get(f"qty_{pid}")
            or form.get(f"bundle_qty_{pid}")
            or 1
        )

        quantities[pid] = _bundle_quantity_float(qty_value, 1)

    return quantities


def _store_bundle_products_for_store(store, product_ids):
    if not product_ids:
        return []

    object_ids = [ObjectId(pid) for pid in product_ids if ObjectId.is_valid(str(pid))]

    products = list(
        mongo.products.find({
            "$and": [
                {"_id": {"$in": object_ids}},
                {
                    "$or": [
                        {"store_id": store["_id"]},
                        {"store_id": str(store["_id"])}
                    ]
                },
                {
                    "$or": [
                        {"is_deleted": {"$exists": False}},
                        {"is_deleted": 0},
                        {"is_deleted": False}
                    ]
                }
            ]
        })
    )

    product_map = {str(p.get("_id")): p for p in products}
    return [product_map[pid] for pid in product_ids if pid in product_map]


def _store_bundle_upload_image(field_name="image"):
    image = request.files.get(field_name)

    if not image or not image.filename:
        image = request.files.get("bundle_image")

    if not image or not image.filename:
        return None, None

    if not allowed_file(image.filename):
        return None, "Invalid image file type."

    fn = secure_filename(image.filename)
    save_as = datetime.utcnow().strftime("%Y%m%d%H%M%S_") + fn
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
    image.save(os.path.join(app.config["UPLOAD_FOLDER"], save_as))

    return f"uploads/{save_as}", None


def _store_bundle_find(store, bundle_id):
    try:
        bid_obj = ObjectId(str(bundle_id))
    except Exception:
        return None, None

    bundle = mongo.product_bundles.find_one({
        "_id": bid_obj,
        "$or": [
            {"store_id": store["_id"]},
            {"store_id": str(store["_id"])},
            {"store_id_str": str(store["_id"])}
        ]
    })

    return bid_obj, bundle


def _store_bundle_page_context(store, edit_bundle=None):
    page_context = _build_store_split_page_context(store) or {}

    store_id = store["_id"]
    store_id_str = str(store_id)

    products = list(
        mongo.products.find({
            "$or": [
                {"store_id": store_id},
                {"store_id": store_id_str}
            ],
            "$and": [
                {
                    "$or": [
                        {"is_deleted": {"$exists": False}},
                        {"is_deleted": 0},
                        {"is_deleted": False}
                    ]
                }
            ]
        }).sort("name", 1)
    )

    for product in products:
        product["id"] = str(product["_id"])
        hydrate_product_unit_fields(product)

    bundles = list(
        mongo.product_bundles.find({
            "$and": [
                {
                    "$or": [
                        {"store_id": store_id},
                        {"store_id": store_id_str},
                        {"store_id_str": store_id_str}
                    ]
                },
                {
                    "$or": [
                        {"is_deleted": {"$exists": False}},
                        {"is_deleted": 0},
                        {"is_deleted": False}
                    ]
                }
            ]
        }).sort("updated_at", -1)
    )

    for bundle in bundles:
        bundle["id"] = str(bundle["_id"])

        # Rebuild from the live child-product records first. The store table now
        # uses the same live stock state that customer pages use, so a stale
        # snapshot cannot show LOW_STOCK while the bundle is actually unavailable.
        customer_bundle = build_live_product_bundle(
            dict(bundle),
            notify_store=False,
            notification_context="store_bundle_admin"
        ) or dict(bundle)

        bundle["max_bundle_stock"] = int(customer_bundle.get("max_bundle_stock") or 0)
        bundle["stock_status"] = (
            customer_bundle.get("stock_status") or "OUT_OF_STOCK"
        ).upper()

        # Deduplicate any underlying blocker text. The template shows one concise
        # customer-facing reason instead of repeating the same warning underneath.
        live_blockers = []
        for blocker in customer_bundle.get("stock_blockers") or []:
            blocker_text = str(blocker).strip()
            if blocker_text and blocker_text not in live_blockers:
                live_blockers.append(blocker_text)

        bundle["stock_blockers"] = live_blockers
        bundle["customer_visible"] = bool(
            is_product_bundle_customer_available(customer_bundle)
        )
        bundle["customer_hidden_reason"] = ""
        bundle["stock_note"] = ""

        max_bundle_stock = int(bundle.get("max_bundle_stock") or 0)
        stock_status = bundle.get("stock_status") or "OUT_OF_STOCK"

        if stock_status == "LOW_STOCK" and max_bundle_stock > 0:
            bundle["stock_note"] = (
                f"Only {max_bundle_stock} complete bundle"
                f"{'' if max_bundle_stock == 1 else 's'} can currently be sold."
            )

        if not bundle["customer_visible"]:
            stock_issue_details = []

            # Explain the actual shortage using live quantity values. A product
            # can still have stock greater than zero but not enough for the
            # quantity required by one bundle; that is "insufficient stock",
            # not an inaccurate "product is out of stock" explanation.
            for item in customer_bundle.get("items") or []:
                if not isinstance(item, dict):
                    continue

                product_name = (
                    item.get("product_name_snapshot")
                    or "Product"
                )
                unit_label = (
                    item.get("unit_label_snapshot")
                    or "unit"
                )

                try:
                    required_qty = float(item.get("quantity") or 1)
                except (TypeError, ValueError):
                    required_qty = 1.0

                try:
                    available_qty = float(
                        item.get("stock_quantity_snapshot") or 0
                    )
                except (TypeError, ValueError):
                    available_qty = 0.0

                is_active = int(
                    item.get("is_active_snapshot", 1) or 0
                ) == 1

                required_text = f"{required_qty:g}"
                available_text = f"{available_qty:g}"

                if not is_active:
                    detail = f"{product_name} is inactive."
                elif available_qty <= 0:
                    detail = (
                        f"{product_name} has no stock available; "
                        f"{required_text} {unit_label} is required per bundle."
                    )
                elif available_qty < required_qty:
                    detail = (
                        f"{product_name} has only {available_text} {unit_label} available, "
                        f"but {required_text} {unit_label} is required per bundle."
                    )
                else:
                    detail = ""

                if detail and detail not in stock_issue_details:
                    stock_issue_details.append(detail)

            # Keep missing/deleted-product blockers if the live rebuild could not
            # produce an item row for them.
            for blocker_text in live_blockers:
                if (
                    blocker_text
                    and blocker_text not in stock_issue_details
                    and (
                        "missing" in blocker_text.lower()
                        or "deleted" in blocker_text.lower()
                    )
                ):
                    stock_issue_details.append(blocker_text)

            if int(customer_bundle.get("is_deleted", 0) or 0) == 1:
                hidden_reason = "Bundle is deleted."
            elif int(customer_bundle.get("is_active", 0) or 0) != 1:
                hidden_reason = "Bundle is inactive."
            elif stock_issue_details:
                hidden_reason = " ".join(stock_issue_details)
            elif max_bundle_stock <= 0 or stock_status == "OUT_OF_STOCK":
                hidden_reason = (
                    "Available child-product stock is below the quantity required "
                    "to build one complete bundle."
                )
            else:
                hidden_reason = (
                    "Bundle does not currently meet customer availability requirements."
                )

            bundle["customer_hidden_reason"] = hidden_reason

    selected_product_ids = set()

    if edit_bundle:
        edit_bundle["id"] = str(edit_bundle["_id"])
        for item in edit_bundle.get("items") or []:
            if item.get("product_id_str"):
                selected_product_ids.add(str(item.get("product_id_str")))

    active_categories = _get_store_categories(store_id, active_only=True)

    bundle_metrics = {
        "total": len(bundles),
        "active": sum(1 for b in bundles if int(b.get("is_active", 0) or 0) == 1),
        "inactive": sum(1 for b in bundles if int(b.get("is_active", 0) or 0) != 1),
        "out_of_stock": sum(1 for b in bundles if (b.get("stock_status") or "") == "OUT_OF_STOCK"),
    }

    page_context.update({
        "products": products,
        "bundles": bundles,
        "edit_bundle": edit_bundle,
        "selected_product_ids": selected_product_ids,
        "active_categories": active_categories,
        "bundle_metrics": bundle_metrics,
    })

    return page_context

# Export the complete compatibility namespace, including underscore-prefixed
# legacy helpers, to the domain route modules.  This is transitional and will
# be replaced by explicit imports as app_core.py and route helpers continue to shrink.
__all__ = [name for name in globals() if not name.startswith('__')]
