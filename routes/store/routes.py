"""Store routes extracted from the updated app.py.

Logic, decorators, endpoint names and route paths are intentionally preserved.
Only the file location changed.
"""

from app_core import *
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


@app.route("/api/store/<store_id>/location")
def api_store_location(store_id):
    try:
        store_obj_id = ObjectId(store_id)
    except Exception:
        return jsonify({
            "ok": False,
            "error": "Invalid store id"
        }), 400

    store = mongo.stores.find_one({"_id": store_obj_id})

    if not store:
        return jsonify({
            "ok": False,
            "error": "Store not found"
        }), 404

    if store.get("latitude") is None or store.get("longitude") is None:
        return jsonify({
            "ok": False,
            "error": "Store coordinates not available"
        }), 400

    return jsonify({
        "ok": True,
        "store_id": str(store["_id"]),
        "store_name": store.get("store_name", ""),
        "latitude": float(store.get("latitude")),
        "longitude": float(store.get("longitude"))
    })

@app.route('/api/store/orders/<oid>', methods=['GET'])
@login_required(role='store')
def api_store_order_detail(oid):
    u = current_user()

    store = mongo.stores.find_one({"user_id": u["id"]})
    if not store:
        return jsonify({
            "ok": False,
            "error": "store not found"
        }), 404

    try:
        oid_obj = ObjectId(oid)
    except Exception:
        return jsonify({
            "ok": False,
            "error": "invalid order id"
        }), 400

    o = mongo.orders.find_one({
        "_id": oid_obj,
        "store_id": store["_id"]
    })

    if not o:
        return jsonify({
            "ok": False,
            "error": "not found"
        }), 404

    customer = None
    if o.get("user_id"):
        try:
            customer = mongo.users.find_one({"_id": ObjectId(o.get("user_id"))})
        except Exception:
            customer = None

    addr = mongo.order_addresses.find_one({"order_id": oid_obj})

    return jsonify({
        "ok": True,
        "order": {
            "id": str(o["_id"]),
            "created_at": o.get("created_at"),
            "status": o.get("status"),
            "payment_status": o.get("payment_status"),
            "items_subtotal": float(o.get("items_subtotal") or o.get("total_amount") or 0),
            "total_amount": float(o.get("total_amount") or 0),
            "delivery_fee": float(o.get("delivery_fee") or 0),
            "platform_fee": float(o.get("platform_fee") or 0),
            "tip_amount": float(o.get("tip_amount") or 0),
            "total_payable": float(
                o.get("total_payable")
                or (
                    float(o.get("items_subtotal") or o.get("total_amount") or 0)
                    + float(o.get("delivery_fee") or 0)
                    + float(o.get("platform_fee") or 0)
                    + float(o.get("tip_amount") or 0)
                )
            ),
            "delivery_partner_name": o.get("delivery_partner_name") or "",
            "delivery_partner_phone": o.get("delivery_partner_phone") or "",
            "delivery_assignment_source": o.get("delivery_assignment_source") or "",
            "customer_name": customer.get("name") if customer else o.get("customer_name"),
            "customer_phone": customer.get("phone") if customer else o.get("customer_phone"),
            "addr_line1": addr.get("line1") if addr else "",
            "addr_line2": addr.get("line2") if addr else "",
            "addr_city": addr.get("city") if addr else "",
            "addr_state": addr.get("state") if addr else "",
            "addr_pincode": addr.get("pincode") if addr else "",
            "addr_lat": addr.get("latitude") if addr else None,
            "addr_lng": addr.get("longitude") if addr else None,
        }
    })

@app.route("/stores/<sid>")
def store_catalog(sid):
    user = current_user()

    try:
        sid_obj = ObjectId(sid)
    except Exception:
        flash("Store not found.", "warning")
        return redirect(url_for("products"))

    store = mongo.stores.find_one({"_id": sid_obj})

    if not store:
        flash("Store not found.", "warning")
        return redirect(url_for("products"))

    store["id"] = str(store["_id"])
    store["store_name"] = store.get("store_name", "Store")
    store["address"] = store.get("address", "")
    store["description"] = store.get("description", "")
    store["logo_path"] = store.get("logo_path", "")
    store["banner_path"] = store.get("banner_path", "")
    store["opening_time"] = store.get("opening_time", "")
    store["closing_time"] = store.get("closing_time", "")
    store["is_open"] = int(store.get("is_open", 1))
    store["is_active"] = int(store.get("is_active", 1))

    allow, pin = _session_pin_is_serviceable()

    products = []
    product_bundles = []
    categories = []
    category_counts = {}
    store_reviews = []
    store_avg_rating = 0
    store_rating_count = 0
    can_review_store = bool(user and user.get("role") == "customer")

    if session.get("service_area") and not allow:
        flash("Please enter a valid 6-digit pincode.", "warning")
    else:
        products = list(
            mongo.products.find({
                "$and": [
                    {
                        "$or": [
                            {"store_id": sid_obj},
                            {"store_id": str(sid_obj)}
                        ]
                    },
                    {
                        "is_active": 1
                    },
                    {
                        "stock_quantity": {"$gt": 0}
                    }
                ]
            }).sort("created_at", -1)
        )

        cart_lookup = {}
        bundle_cart_lookup = {}

        if user and user.get("role") == "customer":
            cid = get_or_create_cart(user["id"])

            cart_items = list(mongo.cart_items.find({
                "cart_id": cid
            }))

            for ci in cart_items:
                item_type = (ci.get("item_type") or "product").strip().lower()

                if item_type == "bundle":
                    bundle_id_value = ci.get("bundle_id") or ci.get("bundle_id_str")

                    if bundle_id_value:
                        bundle_cart_lookup[str(bundle_id_value)] = {
                            "cart_item_id": str(ci.get("_id")),
                            "cart_quantity": cart_item_quantity(ci),
                        }

                    continue

                product_id_value = ci.get("product_id")

                if product_id_value:
                    cart_lookup[str(product_id_value)] = {
                        "cart_item_id": str(ci.get("_id")),
                        "cart_quantity": cart_item_quantity(ci),
                        "unit_type": ci.get("unit_type"),
                        "unit_label": ci.get("unit_label")
                    }

        for p in products:
            p["id"] = str(p["_id"])
            p["name"] = (p.get("name") or "Product").strip()
            p["category"] = (p.get("category") or "Uncategorized").strip()
            p["sub_category"] = (p.get("sub_category") or "").strip()
            p["image_path"] = p.get("image_path", "")
            hydrate_product_unit_fields(p)
            p["store_id"] = str(sid_obj)
            p["store_name"] = store.get("store_name", "")

            cart_info = cart_lookup.get(str(p["_id"]))

            if cart_info:
                p["in_cart"] = True
                p["cart_item_id"] = cart_info.get("cart_item_id", "")
                p["cart_quantity"] = cart_info.get("cart_quantity", p.get("quantity_min") or 1)
            else:
                p["in_cart"] = False
                p["cart_item_id"] = ""
                p["cart_quantity"] = 0

            product_ratings = list(mongo.product_ratings.find({
                "product_id": p["_id"]
            }))

            product_rating_count = len(product_ratings)
            product_total_rating = 0

            for r in product_ratings:
                try:
                    product_total_rating += float(r.get("rating") or 0)
                except (TypeError, ValueError):
                    pass

            if product_rating_count > 0:
                p["avg_rating"] = round(product_total_rating / product_rating_count, 1)
            else:
                p["avg_rating"] = 0

            p["rating_count"] = product_rating_count

            cat = p["category"] or "Uncategorized"

            if cat not in category_counts:
                category_counts[cat] = 0

            category_counts[cat] += 1

        raw_bundles = list(
            mongo.product_bundles.find({
                "$and": [
                    {
                        "$or": [
                            {"store_id": sid_obj},
                            {"store_id": str(sid_obj)},
                            {"store_id_str": str(sid_obj)}
                        ]
                    },
                    {
                        "$or": [
                            {"is_deleted": {"$exists": False}},
                            {"is_deleted": 0},
                            {"is_deleted": False}
                        ]
                    },
                    {
                        "is_active": 1
                    }
                ]
            }).sort("updated_at", -1)
        )

        for bundle in raw_bundles:
            bundle = build_live_product_bundle(
                bundle,
                notify_store=True,
                notification_context="store_catalog"
            ) or bundle

            if not is_product_bundle_customer_available(bundle):
                continue

            bundle["id"] = str(bundle.get("_id"))
            bundle["bundle_name"] = bundle.get("bundle_name") or "Product Bundle"
            bundle["description"] = bundle.get("description") or ""
            bundle["store_name"] = bundle.get("store_name") or store.get("store_name", "Store")
            bundle["image_path"] = bundle.get("image_path") or ""

            cart_info = bundle_cart_lookup.get(str(bundle.get("_id")))

            if cart_info:
                bundle["in_cart"] = True
                bundle["cart_item_id"] = cart_info.get("cart_item_id", "")
                bundle["cart_quantity"] = cart_info.get("cart_quantity", 1)
            else:
                bundle["in_cart"] = False
                bundle["cart_item_id"] = ""
                bundle["cart_quantity"] = 0

            product_bundles.append(bundle)

        categories = [
            {
                "name": name,
                "count": count
            }
            for name, count in sorted(category_counts.items())
        ]

    store_reviews = list(
        mongo.store_ratings.find({
            "$or": [
                {"store_id": sid_obj},
                {"store_id": str(sid_obj)}
            ]
        }).sort("created_at", -1).limit(20)
    )

    store_rating_count = len(store_reviews)
    store_total_rating = 0

    for r in store_reviews:
        r["id"] = str(r["_id"])

        try:
            store_total_rating += float(r.get("rating") or 0)
        except (TypeError, ValueError):
            pass

        if r.get("user_id"):
            reviewer = None

            try:
                reviewer = mongo.users.find_one({"_id": ObjectId(str(r.get("user_id")))})
            except Exception:
                reviewer = mongo.users.find_one({"_id": str(r.get("user_id"))})

            r["reviewer_name"] = reviewer.get("name", "Customer") if reviewer else r.get("reviewer_name", "Customer")
        else:
            r["reviewer_name"] = r.get("reviewer_name", "Customer")

    if store_rating_count > 0:
        store_avg_rating = round(store_total_rating / store_rating_count, 1)

    store["avg_rating"] = store_avg_rating
    store["rating_count"] = store_rating_count
    store["product_count"] = len(products)
    store["bundle_count"] = len(product_bundles)

    return render_template(
        "store_catalog.html",
        user=user,
        store=store,
        products=products,
        product_bundles=product_bundles,
        categories=categories,
        store_reviews=store_reviews,
        store_avg_rating=store_avg_rating,
        store_rating_count=store_rating_count,
        can_review_store=can_review_store
    )

@app.route("/stores/<sid>/review", methods=["POST"])
@login_required()
def submit_store_review(sid):
    u = current_user()

    if not u:
        flash("Please log in first.", "warning")
        return redirect(url_for("login"))

    if u.get("role") != "customer":
        flash("Only customer accounts can submit store reviews.", "warning")
        return redirect(url_for("store_catalog", sid=sid))

    try:
        sid_obj = ObjectId(sid)
    except Exception:
        flash("Invalid store.", "danger")
        return redirect(url_for("products"))

    store = mongo.stores.find_one({"_id": sid_obj})

    if not store:
        flash("Store not found.", "danger")
        return redirect(url_for("products"))

    try:
        rating = float(request.form.get("rating") or 0)
    except (TypeError, ValueError):
        rating = 0

    review = (request.form.get("review") or "").strip()

    if rating < 1 or rating > 5:
        flash("Please select a valid rating between 1 and 5.", "warning")
        return redirect(url_for("store_catalog", sid=sid))

    if len(review) > 800:
        flash("Review is too long. Please keep it within 800 characters.", "warning")
        return redirect(url_for("store_catalog", sid=sid))

    now = datetime.utcnow().isoformat()

    existing_review = mongo.store_ratings.find_one({
        "store_id": sid_obj,
        "user_id": str(u["_id"])
    })

    review_doc = {
        "store_id": sid_obj,
        "store_name": store.get("store_name", ""),
        "user_id": str(u["_id"]),
        "reviewer_name": u.get("name", "Customer"),
        "rating": rating,
        "review": review,
        "comment": review,
        "is_active": 1,
        "updated_at": now
    }

    if existing_review:
        mongo.store_ratings.update_one(
            {"_id": existing_review["_id"]},
            {"$set": review_doc}
        )
        flash("Your store review has been updated.", "success")
    else:
        review_doc["created_at"] = now
        mongo.store_ratings.insert_one(review_doc)
        flash("Thank you! Your store review has been submitted.", "success")

    return redirect(url_for("store_catalog", sid=sid))

@app.route('/rate/store/<int:sid>', methods=['POST'])
@login_required()
def rate_store_disabled(sid):
    flash('Please rate from the order page after your delivery is completed.', 'info')
    return redirect(request.referrer or url_for('orders'))


@app.route('/store/profile-image/<store_id>', methods=['GET'], endpoint='store_profile_image')
def store_profile_image(store_id):
    """Serve the active store profile picture stored in MongoDB."""
    try:
        store_obj_id = ObjectId(str(store_id))
    except Exception:
        return "Store image not found", 404

    store = mongo.stores.find_one({"_id": store_obj_id})

    if not store:
        return "Store image not found", 404

    image_doc = None
    profile_image_id = store.get("profile_image_id")

    if profile_image_id:
        try:
            profile_image_obj_id = ObjectId(str(profile_image_id))
        except Exception:
            profile_image_obj_id = profile_image_id

        image_doc = mongo.store_profile_images.find_one({
            "_id": profile_image_obj_id,
            "store_id": store_obj_id,
            "is_active": 1
        })

    if not image_doc:
        image_doc = mongo.store_profile_images.find_one(
            {
                "$or": [
                    {"store_id": store_obj_id},
                    {"store_id": str(store_obj_id)}
                ],
                "is_active": 1
            },
            sort=[("created_at", -1)]
        )

    if not image_doc or not image_doc.get("data"):
        return "Store image not found", 404

    image_data = image_doc.get("data")

    if not isinstance(image_data, (bytes, bytearray)):
        try:
            image_data = bytes(image_data)
        except Exception:
            return "Store image not found", 404

    response = send_file(
        io.BytesIO(image_data),
        mimetype=image_doc.get("mime_type") or "image/jpeg",
        as_attachment=False,
        download_name=image_doc.get("filename") or "store-profile-image"
    )
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return response

@app.route('/store/dashboard')
@login_required(role='store')
def store_dashboard():
    u, store = _get_current_store_or_redirect()

    if not store:
        flash("Store not found.", "danger")
        return redirect(url_for("login"))

    store["id"] = str(store["_id"])

    page_context = _build_store_split_page_context(store)

    return render_template(
        "store_dashboard.html",
        user=u,
        store=store,
        **page_context
    )


@app.route("/store/online-toggle", methods=["POST"], endpoint="store_online_toggle")
@login_required(role="store")
def store_online_toggle():
    u, store = _get_current_store_or_redirect()

    if not store:
        return jsonify({
            "ok": False,
            "message": "Store not found."
        }), 404

    current_status = int(store.get("is_online", store.get("is_open", 1)) or 0)
    next_status = 0 if current_status == 1 else 1

    now = datetime.utcnow().isoformat()

    mongo.stores.update_one(
        {"_id": store["_id"]},
        {
            "$set": {
                "is_online": next_status,
                "is_open": next_status,
                "updated_at": now,
                "online_status_updated_at": now
            }
        }
    )

    return jsonify({
        "ok": True,
        "is_online": next_status,
        "message": "Store is now online." if next_status else "Store is now offline."
    })


@app.route("/store/delivery-toggle", methods=["POST"], endpoint="store_delivery_toggle")
@login_required(role="store")
def store_delivery_toggle():
    u, store = _get_current_store_or_redirect()

    if not store:
        return jsonify({
            "ok": False,
            "message": "Store not found."
        }), 404

    current_status = int(store.get("delivery_enabled", 1 if store.get("delivery_available", True) else 0) or 0)
    next_status = 0 if current_status == 1 else 1

    now = datetime.utcnow().isoformat()

    mongo.stores.update_one(
        {"_id": store["_id"]},
        {
            "$set": {
                "delivery_enabled": next_status,
                "delivery_available": bool(next_status),
                "updated_at": now,
                "delivery_status_updated_at": now
            }
        }
    )

    return jsonify({
        "ok": True,
        "delivery_enabled": next_status,
        "message": "Delivery is now enabled." if next_status else "Delivery is now disabled."
    })


@app.route('/store/settings', methods=['GET', 'POST'], endpoint='store_settings')
@login_required(role='store')
def store_settings_page():
    u, store = _get_current_store_or_redirect()

    if not store:
        flash("Store not found.", "danger")
        return redirect(url_for("store_dashboard"))

    if request.method == "POST":
        now = datetime.utcnow().isoformat()

        def _settings_int_or_default(value, default=0, min_value=None, max_value=None):
            try:
                if value is None or str(value).strip() == "":
                    number = int(default)
                else:
                    number = int(float(value))

                if min_value is not None and number < min_value:
                    return int(default)

                if max_value is not None and number > max_value:
                    return int(default)

                return number
            except Exception:
                return int(default)

        def _settings_text(name, limit=500):
            value = (request.form.get(name) or "").strip()
            if len(value) > limit:
                value = value[:limit]
            return value

        existing_is_online = bool(int(store.get("is_online", store.get("is_open", 1)) or 0))
        existing_delivery_enabled = bool(
            int(store.get("delivery_enabled", 1 if store.get("delivery_available", True) else 0) or 0)
        )

        is_online = _store_bool_from_form("is_online", existing_is_online)
        accepting_orders = _store_bool_from_form(
            "accepting_orders",
            bool(int(store.get("accepting_orders", 1) or 0))
        )

        delivery_enabled = _store_bool_from_form("delivery_enabled", existing_delivery_enabled)

        allow_cod = _store_bool_from_form(
            "allow_cod",
            bool(int(store.get("allow_cod", 1) or 0))
        )

        allow_online_payment = _store_bool_from_form(
            "allow_online_payment",
            bool(int(store.get("allow_online_payment", 1) or 0))
        )


        hide_out_of_stock = _store_bool_from_form(
            "hide_out_of_stock",
            bool(int(store.get("hide_out_of_stock", 0) or 0))
        )

        allow_preorder = _store_bool_from_form(
            "allow_preorder",
            bool(int(store.get("allow_preorder", 0) or 0))
        )

        opening_time = _settings_text("opening_time", 20)
        closing_time = _settings_text("closing_time", 20)
        weekly_off_day = _settings_text("weekly_off_day", 40)
        temporary_close_message = _settings_text("temporary_close_message", 250)

        min_order_amount = _store_money_or_default(
            request.form.get("min_order_amount"),
            store.get("min_order_amount", 0)
        )


        lat_raw = (request.form.get("latitude") or "").strip()
        lng_raw = (request.form.get("longitude") or "").strip()

        latitude = _store_float_or_none(lat_raw, -90, 90)
        longitude = _store_float_or_none(lng_raw, -180, 180)

        estimated_delivery_time = _settings_int_or_default(
            request.form.get("estimated_delivery_time"),
            store.get("estimated_delivery_time", 45) or 45,
            0,
            300
        )

        low_stock_alert_quantity = _settings_int_or_default(
            request.form.get("low_stock_alert_quantity"),
            store.get("low_stock_alert_quantity", 5) or 5,
            0,
            100000
        )

        rider_instructions = _settings_text("rider_instructions", 500)

        notification_preferences = {
            "new_order_alert": _store_bool_from_form("new_order_alert", True),
            "order_cancel_alert": _store_bool_from_form("order_cancel_alert", True),
            "low_stock_alert": _store_bool_from_form("low_stock_alert", True),
            "new_review_alert": _store_bool_from_form("new_review_alert", True),
            "delivery_alert": _store_bool_from_form("delivery_alert", True),
            "email_alert": _store_bool_from_form("email_alert", False),
            "dashboard_alert": _store_bool_from_form("dashboard_alert", True),
        }

        update_data = {
            "is_online": 1 if is_online else 0,
            "is_open": 1 if is_online else 0,
            "accepting_orders": 1 if accepting_orders else 0,
            "temporary_close_message": temporary_close_message,

            "opening_time": opening_time,
            "closing_time": closing_time,
            "weekly_off_day": weekly_off_day,

            "min_order_amount": min_order_amount,
            "allow_cod": 1 if allow_cod else 0,
            "allow_online_payment": 1 if allow_online_payment else 0,

            "delivery_enabled": 1 if delivery_enabled else 0,
            "delivery_available": bool(delivery_enabled),

            # Delivery fee/rate/slab/minimum order are Admin-controlled only.
            "estimated_delivery_time": estimated_delivery_time,
            "rider_instructions": rider_instructions,

            "low_stock_alert_quantity": low_stock_alert_quantity,
            "hide_out_of_stock": 1 if hide_out_of_stock else 0,
            "allow_preorder": 1 if allow_preorder else 0,

            "notification_preferences": notification_preferences,
            "settings_updated_at": now,
            "updated_at": now,
        }

        mongo.stores.update_one(
            {"_id": store["_id"]},
            {"$set": update_data}
        )

        mongo.store_notification_settings.update_one(
            {"store_id": store["_id"]},
            {
                "$set": {
                    "store_id": store["_id"],
                    "enabled": bool(notification_preferences.get("dashboard_alert")),
                    "preferences": notification_preferences,
                    "updated_at": now
                },
                "$setOnInsert": {
                    "created_at": now
                }
            },
            upsert=True
        )

        flash("Store settings updated successfully.", "success")
        return redirect(url_for("store_settings"))

    store["id"] = str(store["_id"])

    notification_settings = mongo.store_notification_settings.find_one({
        "store_id": store["_id"]
    }) or {}

    page_context = _build_store_split_page_context(store)

    return render_template(
        "store_settings.html",
        user=u,
        store=store,
        notification_settings=notification_settings,
        **page_context
    )


@app.route('/store/delivered-orders')
@login_required(role='store')
def store_delivered_orders():
    """Show all delivered orders for this store."""
    u = current_user()

    store = mongo.stores.find_one({"user_id": u["id"]})

    if not store:
        flash("Store not found.", "danger")
        return redirect(url_for("store_dashboard"))

    delivered_docs = list(
        mongo.orders.find({
            "store_id": store["_id"],
            "status": "DELIVERED"
        }).sort("created_at", -1)
    )

    delivered = []

    for o in delivered_docs:
        customer = None

        if o.get("user_id"):
            try:
                customer = mongo.users.find_one({"_id": ObjectId(o.get("user_id"))})
            except Exception:
                customer = None

        addr = mongo.order_addresses.find_one({"order_id": o["_id"]})

        row = dict(o)
        row["id"] = str(o["_id"])
        row["customer_name"] = customer.get("name") if customer else o.get("customer_name", "")
        row["customer_phone"] = customer.get("phone") if customer else o.get("customer_phone", "")

        row["addr_line1"] = addr.get("line1") if addr else ""
        row["addr_line2"] = addr.get("line2") if addr else ""
        row["addr_city"] = addr.get("city") if addr else ""
        row["addr_state"] = addr.get("state") if addr else ""
        row["addr_pincode"] = addr.get("pincode") if addr else ""
        row["addr_lat"] = addr.get("latitude") if addr else None
        row["addr_lng"] = addr.get("longitude") if addr else None

        row = _decorate_store_delivery_order(row)

        delivered.append(row)

    store_view = dict(store)
    store_view["id"] = str(store["_id"])

    return render_template(
        "store_delivered_orders.html",
        user=u,
        store=store_view,
        orders=delivered
    )


@app.route('/store/payouts', methods=['GET'], endpoint='store_payouts')
@login_required(role='store')
def store_payouts_page():
    """
    Store-side read-only payout/settlement view.

    Important:
    - Store can only view payout status.
    - Store cannot mark payout paid.
    - Only Admin controls rider cash settlement and store payout settlement.
    """
    u, store = _get_current_store_or_redirect()

    if not store:
        flash("Store not found.", "danger")
        return redirect(url_for("store_dashboard"))

    store_id = store.get("_id")
    store_id_str = str(store_id)

    q = (request.args.get("q") or "").strip()
    status_filter = (request.args.get("status") or "").strip().upper()

    payout_docs = list(
        mongo.orders.find({
            "$and": [
                {
                    "$or": [
                        {"store_id": store_id},
                        {"store_id": store_id_str}
                    ]
                },
                {
                    "status": "DELIVERED"
                }
            ]
        }).sort("delivered_at", -1)
    )

    payout_rows = []

    for order in payout_docs:
        row = dict(order)

        row["id"] = str(row.get("_id") or row.get("id") or "")

        # Existing helper safely prepares money, rider and store earning values.
        row = _decorate_store_delivery_order(row)

        items_subtotal = _store_delivery_money_float(
            row.get("items_subtotal"),
            row.get("store_earning") or 0
        )

        store_refund_deduction = _store_delivery_money_float(
            row.get("store_refund_deduction")
            if row.get("store_refund_deduction") is not None
            else (
                row.get("refund_deduction")
                if row.get("refund_deduction") is not None
                else row.get("refund_adjustment_amount")
            ),
            0
        )

        store_adjustment_due = _store_delivery_money_float(
            row.get("store_adjustment_due"),
            0
        )

        original_store_payout_amount = _store_delivery_money_float(
            row.get("original_store_payout_amount")
            if row.get("original_store_payout_amount") is not None
            else row.get("store_earning"),
            items_subtotal
        )

        store_payout_amount = _store_delivery_money_float(
            row.get("store_payout_amount"),
            original_store_payout_amount
        )

        adjusted_store_payout = _store_delivery_money_float(
            row.get("adjusted_store_payout"),
            max(original_store_payout_amount - store_refund_deduction, 0)
        )

        payout_status_upper = (row.get("store_payout_status") or "").strip().upper()

        if payout_status_upper == "PAID":
            net_store_earning = round(store_payout_amount, 2)
        else:
            net_store_earning = round(adjusted_store_payout, 2)

        settlement_impact = (
            row.get("settlement_impact")
            or (
                "ADJUST_FROM_NEXT_PAYOUT"
                if store_adjustment_due > 0
                else (
                    "DEDUCT_FROM_PENDING_PAYOUT"
                    if store_refund_deduction > 0
                    else "NO_DEDUCTION"
                )
            )
        )

        row["items_subtotal"] = round(items_subtotal, 2)

        row["original_store_payout_amount"] = round(original_store_payout_amount, 2)

        row["store_refund_deduction"] = round(store_refund_deduction, 2)
        row["refund_deduction"] = round(store_refund_deduction, 2)

        row["adjusted_store_payout"] = round(adjusted_store_payout, 2)
        row["store_adjustment_due"] = round(store_adjustment_due, 2)

        row["net_store_earning"] = net_store_earning
        row["store_payout_amount"] = round(store_payout_amount, 2)

        row["settlement_impact"] = settlement_impact

        row["store_payout_status"] = (
            row.get("store_payout_status")
            or "PENDING_AFTER_DELIVERY"
        )

        row["store_settlement_status"] = (
            row.get("store_settlement_status")
            or "PAYOUT_PENDING"
        )

        row["order_settlement_status"] = (
            row.get("order_settlement_status")
            or "STORE_PAYOUT_PENDING"
        )

        row["rider_cash_settlement_status"] = (
            row.get("rider_cash_settlement_status")
            or "NOT_REQUIRED"
        )

        row["platform_fee_status"] = row.get("platform_fee_status") or ""
        row["store_payout_paid_at"] = row.get("store_payout_paid_at") or ""
        row["store_payout_reference_no"] = row.get("store_payout_reference_no") or ""
        row["store_payout_mode"] = row.get("store_payout_mode") or ""
        row["store_payout_note"] = row.get("store_payout_note") or ""

        if q:
            haystack = " ".join([
                str(row.get("id") or ""),
                str(row.get("customer_name") or ""),
                str(row.get("customer_phone") or ""),
                str(row.get("payment_method") or ""),
                str(row.get("store_payout_status") or ""),
                str(row.get("order_settlement_status") or ""),
                str(row.get("store_payout_reference_no") or "")
            ]).lower()

            if q.lower() not in haystack:
                continue

        if status_filter:
            payout_status = (row.get("store_payout_status") or "").strip().upper()
            settlement_status = (row.get("order_settlement_status") or "").strip().upper()

            if status_filter == "PENDING":
                if payout_status in ["PAID", "SETTLED"] or settlement_status == "SETTLED":
                    continue
            elif status_filter == "PAID":
                if payout_status != "PAID":
                    continue
            elif status_filter not in [payout_status, settlement_status]:
                continue

        payout_rows.append(row)

    metrics = {
        "total_orders": len(payout_rows),

        "pending_orders": sum(
            1 for r in payout_rows
            if (r.get("store_payout_status") or "").upper() != "PAID"
        ),

        "paid_orders": sum(
            1 for r in payout_rows
            if (r.get("store_payout_status") or "").upper() == "PAID"
        ),

        "pending_amount": round(sum(
            float(r.get("net_store_earning") or 0)
            for r in payout_rows
            if (r.get("store_payout_status") or "").upper() != "PAID"
        ), 2),

        "paid_amount": round(sum(
            float(r.get("net_store_earning") or 0)
            for r in payout_rows
            if (r.get("store_payout_status") or "").upper() == "PAID"
        ), 2),

        "total_store_earning": round(sum(
            float(r.get("original_store_payout_amount") or 0)
            for r in payout_rows
        ), 2),

        "total_refund_deduction": round(sum(
            float(r.get("store_refund_deduction") or 0)
            for r in payout_rows
        ), 2),

        "total_adjusted_payout": round(sum(
            float(r.get("adjusted_store_payout") or 0)
            for r in payout_rows
        ), 2),

        "total_adjustment_due": round(sum(
            float(r.get("store_adjustment_due") or 0)
            for r in payout_rows
        ), 2),
    }

    store_view = dict(store)
    store_view["id"] = str(store["_id"])

    return render_template(
        "store_payouts.html",
        user=u,
        store=store_view,
        payouts=payout_rows,
        metrics=metrics,
        q=q,
        status_filter=status_filter
    )

@app.route('/store/products/new', methods=['GET'], endpoint='store_add_product')
@login_required(role='store')
def store_add_product_page():
    u, store = _get_current_store_or_redirect()

    if not store:
        return redirect(url_for("store_dashboard"))

    page_context = _build_store_split_page_context(store)

    return render_template(
        "store_add_product.html",
        user=u,
        store=store,
        unit_options=UNIT_OPTIONS,
        unit_type_labels=UNIT_TYPE_LABELS,
        **page_context
    )

@app.route('/store/products', methods=['GET'], endpoint='store_products')
@login_required(role='store')
def store_products_page():
    u, store = _get_current_store_or_redirect()

    if not store:
        return redirect(url_for("store_dashboard"))

    page_context = _build_store_split_page_context(store)

    store_id = store.get("_id")
    store_id_str = str(store_id)

    active_bundles_count = mongo.product_bundles.count_documents({
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
            },
            {
                "$or": [
                    {"is_active": 1},
                    {"is_active": True}
                ]
            }
        ]
    })

    return render_template(
        "store_products.html",
        user=u,
        store=store,
        active_bundles_count=active_bundles_count,
        **page_context
    )

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

    collected_payment_statuses = [
        "PAID",
        "COLLECTED",
        "ONLINE_PAID",
        "COLLECTED_BY_RIDER",
        "COD_COLLECTED_BY_RIDER"
    ]

    if payment_method == "COD" and payment_status not in collected_payment_statuses:
        amount_to_collect = total_payable
    else:
        amount_to_collect = 0.0

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
    order["is_cod_order"] = payment_method == "COD"
    order["amount_to_collect"] = round(amount_to_collect, 2)

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


def _store_should_show_order_to_store(order):
    """
    Store should not see unpaid online orders.

    Online order flow:
    - Before payment success: status/payment_status = PENDING_PAYMENT
    - After Razorpay verification: status = PLACED, payment_status = PAID

    COD orders can be shown immediately because payment is collected later by rider.
    """
    order = order or {}

    status = (order.get("status") or "").strip().upper()
    payment_method = (order.get("payment_method") or "COD").strip().upper()
    payment_status = (order.get("payment_status") or "").strip().upper()
    payment_collection_status = (order.get("payment_collection_status") or "").strip().upper()

    blocked_statuses = {
        "PENDING_PAYMENT",
        "PAYMENT_PENDING",
        "ONLINE_PENDING"
    }

    if status in blocked_statuses:
        return False

    is_cod = payment_method in {
        "COD",
        "CASH_ON_DELIVERY",
        "COD_RIDER_COLLECTION"
    }

    if is_cod:
        return True

    paid_statuses = {
        "PAID",
        "ONLINE_PAID",
        "SUCCESS"
    }

    paid_collection_statuses = {
        "PAID",
        "ONLINE_PAID",
        "COLLECTED",
        "PAID_REFUND_PENDING"
    }

    if payment_status in paid_statuses:
        return True

    if payment_collection_status in paid_collection_statuses:
        return True

    return False



@app.route('/store/orders', methods=['GET'], endpoint='store_orders')
@login_required(role='store')
def store_orders_page():
    u, store = _get_current_store_or_redirect()

    if not store:
        return redirect(url_for("store_dashboard"))

    page_context = _build_store_split_page_context(store) or {}

    available_delivery_people = _hydrate_store_delivery_people_for_template(store)

    decorated_orders = []

    for order in page_context.get("orders") or []:
        if not _store_should_show_order_to_store(order):
            continue

        row = dict(order)

        row["_id"] = row.get("_id") or row.get("id")
        row["id"] = str(row.get("_id") or row.get("id") or "")

        # Ensure old/new order money fields are safely available in Store Orders page.
        row = _decorate_store_delivery_order(row)

        decorated_orders.append(row)

    page_context["orders"] = decorated_orders
    page_context["available_delivery_people"] = available_delivery_people
    page_context["delivery_accept_radius_km"] = DELIVERY_ACCEPT_RADIUS_KM

    return render_template(
        "store_orders.html",
        user=u,
        store=store,
        **page_context
    )


@app.route('/store/delivery', methods=['GET'], endpoint='store_delivery')
@login_required(role='store')
def store_delivery_page():
    u, store = _get_current_store_or_redirect()

    if not store:
        flash("Store not found.", "danger")
        return redirect(url_for("store_dashboard"))

    page_context = _build_store_split_page_context(store) or {}
    available_delivery_people = _hydrate_store_delivery_people_for_template(store)

    store_id = store.get("_id")
    store_id_str = str(store_id)
    store_name = (store.get("store_name") or store.get("name") or "").strip().lower()

    def _safe_float(value, default=0):
        try:
            return float(value or default)
        except Exception:
            return float(default)

    def _order_belongs_to_store(order):
        order_store_id = order.get("store_id")
        order_store_name = (order.get("store_name") or "").strip().lower()

        if order_store_id and str(order_store_id) == store_id_str:
            return True

        if store_name and order_store_name and order_store_name == store_name:
            return True

        return False

    def _hydrate_store_delivery_order(order):
        row = dict(order)

        oid_value = row.get("_id") or row.get("id")
        row["id"] = str(oid_value)

        customer = None
        if row.get("user_id"):
            try:
                customer = mongo.users.find_one({"_id": ObjectId(str(row.get("user_id")))})
            except Exception:
                customer = None

        addr = None
        try:
            addr = mongo.order_addresses.find_one({
                "$or": [
                    {"order_id": row.get("_id")},
                    {"order_id": str(row.get("_id") or row.get("id") or "")}
                ]
            })
        except Exception:
            addr = None

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

        row["addr_line1"] = row.get("addr_line1") or (addr.get("line1") if addr else "")
        row["addr_line2"] = row.get("addr_line2") or (addr.get("line2") if addr else "")
        row["addr_city"] = row.get("addr_city") or (addr.get("city") if addr else "")
        row["addr_state"] = row.get("addr_state") or (addr.get("state") if addr else "")
        row["addr_pincode"] = row.get("addr_pincode") or (addr.get("pincode") if addr else "")
        row["addr_lat"] = row.get("addr_lat") or (addr.get("latitude") if addr else None)
        row["addr_lng"] = row.get("addr_lng") or (addr.get("longitude") if addr else None)

        row["items_subtotal"] = _safe_float(
            row.get("items_subtotal")
            if row.get("items_subtotal") is not None
            else row.get("total_amount")
        )
        row["total_amount"] = _safe_float(row.get("total_amount"))
        row["delivery_fee"] = _safe_float(row.get("delivery_fee"))
        row["platform_fee"] = _safe_float(row.get("platform_fee"))
        row["tip_amount"] = _safe_float(row.get("tip_amount"))

        if row.get("total_payable") is None:
            row["total_payable"] = (
                row["items_subtotal"]
                + row["delivery_fee"]
                + row["platform_fee"]
                + row["tip_amount"]
            )
        else:
            row["total_payable"] = _safe_float(row.get("total_payable"))

        row = _decorate_store_delivery_order(row)

        return row

    def _delivery_status(order):
        return (order.get("status") or "").strip().upper()

    def _has_delivery_partner(order):
        return bool(order.get("delivery_partner_id"))

    def _is_today(value):
        if not value:
            return False

        try:
            raw = str(value).replace("Z", "")
            dt = datetime.fromisoformat(raw)
            return dt.date() == datetime.utcnow().date()
        except Exception:
            return False

    raw_orders_by_id = {}

    # 1. Add orders already prepared by app_core/store order page context.
    for order in page_context.get("orders") or []:
        oid = str(order.get("_id") or order.get("id") or "")
        if oid:
            raw_orders_by_id[oid] = order

    # 2. Add all orders that match this store by ObjectId/string/name.
    direct_store_orders = list(
        mongo.orders.find({
            "$and": [
                {
                    "$or": [
                        {"store_id": store_id},
                        {"store_id": store_id_str},
                        {"store_name": store.get("store_name")},
                        {"store_name": store.get("name")}
                    ]
                },
                {
                    "status": {
                        "$nin": [
                            "PENDING_PAYMENT",
                            "PAYMENT_PENDING",
                            "ONLINE_PENDING"
                        ]
                    }
                }
            ]
        }).sort("updated_at", -1)
    )

    for order in direct_store_orders:
        oid = str(order.get("_id") or order.get("id") or "")
        if oid:
            raw_orders_by_id[oid] = order

    # 3. Hard safety: scan all DELIVERY_FAILED orders and filter ownership in Python.
    # This is the important part that fixes your current issue.
    failed_candidates = list(
        mongo.orders.find({
            "status": "DELIVERY_FAILED"
        }).sort("updated_at", -1)
    )

    for order in failed_candidates:
        if not _order_belongs_to_store(order):
            continue

        oid = str(order.get("_id") or order.get("id") or "")
        if oid:
            raw_orders_by_id[oid] = order

    orders = [
        _hydrate_store_delivery_order(order)
        for order in raw_orders_by_id.values()
        if _order_belongs_to_store(order) and _store_should_show_order_to_store(order)
    ]

    delivery_metrics = {
        "total_orders": len(orders),
        "ready_for_pickup": 0,
        "needs_rider": 0,
        "reassignment_needed": 0,
        "failed_delivery": 0,
        "failed_action_required": 0,
        "assigned": 0,
        "reached_store": 0,
        "picked_up": 0,
        "out_for_delivery": 0,
        "active_delivery_orders": 0,
        "delivered_today": 0,
        "cancelled": 0,
        "online_riders": len(available_delivery_people),
    }

    ready_orders = []
    needs_rider_orders = []
    failed_delivery_orders = []
    active_delivery_orders = []
    recent_delivered_orders = []
    attention_orders = []

    for order in orders:
        status = _delivery_status(order)
        has_rider = _has_delivery_partner(order)

        needs_reassignment = bool(
            order.get("needs_reassignment")
            or order.get("delivery_cancelled_by_partner")
        )

        if status == "READY_FOR_PICKUP":
            delivery_metrics["ready_for_pickup"] += 1
            ready_orders.append(order)

        if status == "READY_FOR_PICKUP" and (not has_rider or needs_reassignment):
            delivery_metrics["needs_rider"] += 1

            if needs_reassignment:
                delivery_metrics["reassignment_needed"] += 1

            needs_rider_orders.append(order)
            attention_orders.append(order)

        if status in {"ASSIGNED_TO_DELIVERY", "ACCEPTED_BY_DELIVERY_MAN"}:
            delivery_metrics["assigned"] += 1

        if status == "REACHED_STORE":
            delivery_metrics["reached_store"] += 1

        if status == "PICKED_UP":
            delivery_metrics["picked_up"] += 1

        if status == "OUT_FOR_DELIVERY":
            delivery_metrics["out_for_delivery"] += 1

        if status == "DELIVERY_FAILED":
            delivery_metrics["failed_delivery"] += 1

            if order.get("delivery_failed_requires_store_action", True):
                delivery_metrics["failed_action_required"] += 1

            failed_delivery_orders.append(order)
            attention_orders.append(order)

        if status in {
            "ASSIGNED_TO_DELIVERY",
            "ACCEPTED_BY_DELIVERY_MAN",
            "REACHED_STORE",
            "PICKED_UP",
            "OUT_FOR_DELIVERY"
        }:
            delivery_metrics["active_delivery_orders"] += 1
            active_delivery_orders.append(order)

        if status == "DELIVERED":
            recent_delivered_orders.append(order)

            if _is_today(order.get("delivered_at") or order.get("updated_at") or order.get("created_at")):
                delivery_metrics["delivered_today"] += 1

        if status == "CANCELLED":
            delivery_metrics["cancelled"] += 1

    recent_delivered_orders = recent_delivered_orders[:10]
    attention_orders = attention_orders[:10]

    log_debug(
        "[STORE DELIVERY PAGE DEBUG]",
        "store_id=", store_id_str,
        "store_name=", store_name,
        "all_orders=", len(orders),
        "failed_candidates=", len(failed_candidates),
        "failed_for_store=", len(failed_delivery_orders)
    )

    page_context["available_delivery_people"] = available_delivery_people
    page_context["delivery_accept_radius_km"] = DELIVERY_ACCEPT_RADIUS_KM
    page_context["delivery_metrics"] = delivery_metrics
    page_context["ready_orders"] = ready_orders
    page_context["needs_rider_orders"] = needs_rider_orders
    page_context["failed_delivery_orders"] = failed_delivery_orders
    page_context["active_delivery_orders"] = active_delivery_orders
    page_context["recent_delivered_orders"] = recent_delivered_orders
    page_context["attention_orders"] = attention_orders

    return render_template(
        "store_delivery.html",
        user=u,
        store=store,
        **page_context
    )

@app.route('/store/orders/<oid>/ready-for-pickup', methods=['POST'], endpoint='store_order_ready_for_pickup')
@login_required(role='store')
def store_order_ready_for_pickup(oid):
    u, store = _get_current_store_or_redirect()

    if not store:
        flash("Store not found.", "danger")
        return redirect(url_for("store_dashboard"))

    oid_obj, order = _get_store_owned_order(store, oid)

    if not oid_obj or not order:
        flash("Order not found for your store.", "danger")
        return redirect(url_for("store_orders"))

    status = (order.get("status") or "").strip().upper()

    if order.get("delivery_partner_id"):
        flash("This order already has a delivery boy assigned.", "warning")
        return redirect(url_for("store_orders"))

    allowed_shipment_ready_statuses = {
        "CONFIRMED",
        "PREPARING",   # legacy support
        "PACKAGING"
    }

    shipment_ready_statuses = {
        "SHIPMENT_READY",
        "READY_FOR_PICKUP"  # legacy support
    }

    if status in shipment_ready_statuses:
        flash("This order is already marked shipment ready.", "info")
        return redirect(url_for("store_orders"))

    if status not in allowed_shipment_ready_statuses:
        flash("Only confirmed/packaging orders can be marked shipment ready.", "warning")
        return redirect(url_for("store_orders"))

    now = datetime.utcnow().isoformat()

    result = mongo.orders.update_one(
        {
            "_id": oid_obj,
            "status": {"$in": list(allowed_shipment_ready_statuses)},
            "$or": [
                {"delivery_partner_id": {"$exists": False}},
                {"delivery_partner_id": None},
                {"delivery_partner_id": ""}
            ]
        },
        {
            "$set": {
                "status": "SHIPMENT_READY",
                "shipment_ready_at": now,

                # legacy timestamp kept so old pages/reports do not break
                "ready_for_pickup_at": now,

                "updated_at": now
            }
        }
    )

    if result.modified_count < 1:
        flash("This order status changed recently. Please refresh and try again.", "warning")
        return redirect(url_for("store_orders"))

    add_order_event(
        oid_obj,
        "SHIPMENT_READY",
        "Marked shipment ready by store.",
        u
    )

    _create_store_notification(
        store,
        title="Order shipment ready",
        message=f"Order #{str(oid_obj)[-6:]} is shipment ready for delivery pickup.",
        notif_type="delivery",
        order=order,
        event_key=f"shipment-ready-{str(oid_obj)}-{now}"
    )

    flash("Order marked shipment ready.", "success")
    return redirect(url_for("store_orders"))


@app.route('/store/orders/<oid>/assign-delivery', methods=['POST'], endpoint='store_order_assign_delivery')
@login_required(role='store')
def store_order_assign_delivery(oid):
    u, store = _get_current_store_or_redirect()

    if not store:
        flash("Store not found.", "danger")
        return redirect(request.referrer or url_for("store_delivery"))

    oid_obj, order = _get_store_owned_order(store, oid)

    if not oid_obj or not order:
        flash("Order not found for your store.", "danger")
        return redirect(request.referrer or url_for("store_delivery"))

    delivery_user_id = (request.form.get("delivery_user_id") or "").strip()

    if not delivery_user_id:
        flash("Please select a delivery boy.", "warning")
        return redirect(request.referrer or url_for("store_delivery"))
    
    status = (order.get("status") or "").strip().upper()

    if status not in ["SHIPMENT_READY", "READY_FOR_PICKUP"]:
        flash("Please mark this order shipment ready before assigning a delivery boy.", "warning")
        return redirect(request.referrer or url_for("store_delivery"))

    result = assign_delivery_partner_to_order(
        order_id=oid_obj,
        delivery_user_id=delivery_user_id,
        actor=u,
        source="store_manual",
        allow_reassign=False
    )

    if not result.get("ok"):
        flash(result.get("error") or "Could not assign delivery boy.", "danger")
        return redirect(request.referrer or url_for("store_delivery"))

    _create_store_notification(
        store,
        title="Delivery boy assigned",
        message=f"Order #{str(oid_obj)[-6:]} assigned to {result.get('delivery_partner', {}).get('name', 'delivery boy')}.",
        notif_type="delivery",
        order=order,
        event_key=f"delivery-assign-{str(oid_obj)}-{delivery_user_id}-{datetime.utcnow().isoformat()}"
    )

    flash("Delivery boy assigned successfully.", "success")
    return redirect(request.referrer or url_for("store_delivery"))


@app.route('/store/orders/<oid>/reassign-delivery', methods=['POST'], endpoint='store_order_reassign_delivery')
@login_required(role='store')
def store_order_reassign_delivery(oid):
    u, store = _get_current_store_or_redirect()

    if not store:
        flash("Store not found.", "danger")
        return redirect(url_for("store_dashboard"))

    oid_obj, order = _get_store_owned_order(store, oid)

    if not oid_obj or not order:
        flash("Order not found for your store.", "danger")
        return redirect(request.referrer or url_for("store_delivery"))

    status = (order.get("status") or "").strip().upper()

    if status in {"PICKED_UP", "OUT_FOR_DELIVERY", "DELIVERED", "CANCELLED"}:
        flash("Delivery boy cannot be changed after pickup/out for delivery.", "warning")
        return redirect(request.referrer or url_for("store_delivery"))

    delivery_user_id = (request.form.get("delivery_user_id") or "").strip()

    if not delivery_user_id:
        flash("Please select a delivery boy.", "warning")
        return redirect(request.referrer or url_for("store_delivery"))

    result = assign_delivery_partner_to_order(
        order_id=oid_obj,
        delivery_user_id=delivery_user_id,
        actor=u,
        source="store_reassign",
        allow_reassign=True
    )

    if not result.get("ok"):
        flash(result.get("error") or "Could not reassign delivery boy.", "danger")
        return redirect(request.referrer or url_for("store_delivery"))

    _create_store_notification(
        store,
        title="Delivery boy reassigned",
        message=f"Order #{str(oid_obj)[-6:]} reassigned to {result.get('delivery_partner', {}).get('name', 'delivery boy')}.",
        notif_type="delivery",
        order=order,
        event_key=f"delivery-reassign-{str(oid_obj)}-{delivery_user_id}-{datetime.utcnow().isoformat()}"
    )

    flash("Delivery boy reassigned successfully.", "success")
    return redirect(request.referrer or url_for("store_delivery"))


@app.route('/store/orders/<oid>/reschedule-failed-delivery', methods=['POST'], endpoint='store_order_reschedule_failed_delivery')
@login_required(role='store')
def store_order_reschedule_failed_delivery(oid):
    u, store = _get_current_store_or_redirect()

    if not store:
        flash("Store not found.", "danger")
        return redirect(url_for("store_dashboard"))

    oid_obj, order = _get_store_owned_order(store, oid)

    if not oid_obj or not order:
        flash("Order not found for your store.", "danger")
        return redirect(request.referrer or url_for("store_delivery"))

    status = (order.get("status") or "").strip().upper()

    if status != "DELIVERY_FAILED":
        flash("Only failed delivery orders can be rescheduled from here.", "warning")
        return redirect(request.referrer or url_for("store_delivery"))

    now = datetime.utcnow().isoformat()

    rescheduled_for = (request.form.get("rescheduled_for") or "").strip()
    reschedule_note = (request.form.get("reschedule_note") or "").strip()

    if len(rescheduled_for) > 80:
        rescheduled_for = rescheduled_for[:80]

    if len(reschedule_note) > 500:
        reschedule_note = reschedule_note[:500]

    old_partner_id = order.get("delivery_partner_id")
    old_partner_name = order.get("delivery_partner_name") or ""
    old_partner_phone = order.get("delivery_partner_phone") or ""

    history_entry = {
        "action": "delivery_failed_rescheduled_by_store",
        "previous_delivery_partner_id": str(old_partner_id) if old_partner_id else "",
        "previous_delivery_partner_name": old_partner_name,
        "previous_delivery_partner_phone": old_partner_phone,
        "failed_reason": order.get("delivery_failed_reason") or "",
        "rescheduled_for": rescheduled_for,
        "reschedule_note": reschedule_note,
        "at": now,
        "by": "store",
        "actor_id": str(u.get("_id") or u.get("id")),
        "actor_name": u.get("name") or "Store User"
    }

    mongo.orders.update_one(
        {"_id": oid_obj},
        {
            "$set": {
                "status": "READY_FOR_PICKUP",

                "delivery_partner_id": None,
                "delivery_partner_name": "",
                "delivery_partner_phone": "",
                "delivery_assignment_source": "",

                "needs_reassignment": True,
                "delivery_cancelled_by_partner": False,

                "delivery_failed_requires_store_action": False,
                "delivery_failed_store_decision": "RESCHEDULED",
                "delivery_failed_resolved_at": now,

                "delivery_rescheduled": True,
                "delivery_rescheduled_at": now,
                "delivery_rescheduled_for": rescheduled_for,
                "delivery_rescheduled_note": reschedule_note,

                "ready_for_pickup_at": now,
                "updated_at": now
            },
            "$push": {
                "delivery_history": history_entry
            }
        }
    )

    if old_partner_id:
        mongo.delivery_availability.update_one(
            {
                "user_id": str(old_partner_id),
                "current_order_id": str(oid_obj)
            },
            {
                "$set": {
                    "current_order_id": None,
                    "updated_at": now
                }
            }
        )

    add_order_event(
        oid_obj,
        "READY_FOR_PICKUP",
        "Failed delivery rescheduled by store. Order sent back for delivery assignment.",
        u
    )

    _create_store_notification(
        store,
        title="Failed delivery rescheduled",
        message=f"Order #{str(oid_obj)[-6:]} was rescheduled and is ready for rider assignment.",
        notif_type="delivery",
        order=order,
        event_key=f"failed-delivery-rescheduled-{str(oid_obj)}-{now}"
    )

    flash("Failed delivery has been rescheduled and sent back for rider assignment.", "success")
    return redirect(request.referrer or url_for("store_delivery"))


@app.route('/store/orders/<oid>/cancel-failed-delivery', methods=['POST'], endpoint='store_order_cancel_failed_delivery')
@login_required(role='store')
def store_order_cancel_failed_delivery(oid):
    u, store = _get_current_store_or_redirect()

    if not store:
        flash("Store not found.", "danger")
        return redirect(url_for("store_dashboard"))

    oid_obj, order = _get_store_owned_order(store, oid)

    if not oid_obj or not order:
        flash("Order not found for your store.", "danger")
        return redirect(request.referrer or url_for("store_delivery"))

    status = (order.get("status") or "").strip().upper()

    if status != "DELIVERY_FAILED":
        flash("Only failed delivery orders can be cancelled from here.", "warning")
        return redirect(request.referrer or url_for("store_delivery"))

    cancel_reason = (request.form.get("cancel_reason") or "").strip()
    cancel_note = (request.form.get("cancel_note") or "").strip()

    if not cancel_reason:
        flash("Please select/write a cancellation reason.", "warning")
        return redirect(request.referrer or url_for("store_delivery"))

    if len(cancel_reason) > 120:
        cancel_reason = cancel_reason[:120]

    if len(cancel_note) > 500:
        cancel_note = cancel_note[:500]

    now = datetime.utcnow().isoformat()

    old_partner_id = order.get("delivery_partner_id")
    old_partner_name = order.get("delivery_partner_name") or ""
    old_partner_phone = order.get("delivery_partner_phone") or ""

    history_entry = {
        "action": "delivery_failed_cancelled_by_store",
        "previous_delivery_partner_id": str(old_partner_id) if old_partner_id else "",
        "previous_delivery_partner_name": old_partner_name,
        "previous_delivery_partner_phone": old_partner_phone,
        "failed_reason": order.get("delivery_failed_reason") or "",
        "cancel_reason": cancel_reason,
        "cancel_note": cancel_note,
        "at": now,
        "by": "store",
        "actor_id": str(u.get("_id") or u.get("id")),
        "actor_name": u.get("name") or "Store User"
    }

    mongo.orders.update_one(
        {"_id": oid_obj},
        {
            "$set": {
                "status": "CANCELLED",

                "cancelled_by": "store",
                "cancelled_by_id": str(u.get("_id") or u.get("id")),
                "cancelled_by_name": u.get("name") or "Store User",
                "cancel_reason": cancel_reason,
                "cancel_note": cancel_note,
                "cancelled_at": now,

                "delivery_failed_requires_store_action": False,
                "delivery_failed_store_decision": "CANCELLED",
                "delivery_failed_resolved_at": now,

                "updated_at": now
            },
            "$push": {
                "delivery_history": history_entry
            }
        }
    )

    if old_partner_id:
        mongo.delivery_availability.update_one(
            {
                "user_id": str(old_partner_id),
                "current_order_id": str(oid_obj)
            },
            {
                "$set": {
                    "current_order_id": None,
                    "updated_at": now
                }
            }
        )

    add_order_event(
        oid_obj,
        "CANCELLED",
        f"Order cancelled by store after failed delivery. Reason: {cancel_reason}",
        u
    )

    _create_store_notification(
        store,
        title="Order cancelled after failed delivery",
        message=f"Order #{str(oid_obj)[-6:]} was cancelled after failed delivery. Reason: {cancel_reason}",
        notif_type="delivery",
        order=order,
        event_key=f"failed-delivery-cancelled-{str(oid_obj)}-{now}"
    )

    flash("Order cancelled after failed delivery.", "success")
    return redirect(request.referrer or url_for("store_delivery"))

@app.route('/store/orders/<oid>/clear-delivery', methods=['POST'], endpoint='store_order_clear_delivery')
@login_required(role='store')
def store_order_clear_delivery(oid):
    u, store = _get_current_store_or_redirect()

    if not store:
        flash("Store not found.", "danger")
        return redirect(url_for("store_dashboard"))

    oid_obj, order = _get_store_owned_order(store, oid)

    if not oid_obj or not order:
        flash("Order not found for your store.", "danger")
        return redirect(url_for("store_orders"))

    result = clear_delivery_assignment(
        order_id=oid_obj,
        actor=u,
        reason="Delivery assignment cleared by store."
    )

    if not result.get("ok"):
        flash(result.get("error") or "Could not clear delivery assignment.", "danger")
        return redirect(url_for("store_orders"))

    _create_store_notification(
        store,
        title="Delivery assignment cleared",
        message=f"Delivery assignment cleared for order #{str(oid_obj)[-6:]}.",
        notif_type="delivery",
        order=order,
        event_key=f"delivery-clear-{str(oid_obj)}-{datetime.utcnow().isoformat()}"
    )

    flash("Delivery assignment cleared.", "success")
    return redirect(url_for("store_orders"))


@app.route('/store/orders/<oid>/delivery-options', methods=['GET'], endpoint='store_order_delivery_options')
@login_required(role='store')
def store_order_delivery_options(oid):
    u, store = _get_current_store_or_redirect()

    if not store:
        return jsonify({
            "ok": False,
            "error": "Store not found."
        }), 404

    oid_obj, order = _get_store_owned_order(store, oid)

    if not oid_obj or not order:
        return jsonify({
            "ok": False,
            "error": "Order not found for your store."
        }), 404

    people = _hydrate_store_delivery_people_for_template(store)

    return jsonify({
        "ok": True,
        "order_id": str(oid_obj),
        "delivery_people": people
    })


@app.route('/store/delivery-history', methods=['GET'], endpoint='store_delivery_history')
@login_required(role='store')
def store_delivery_history_page():
    """
    Store Delivery Boy History.

    This keeps the existing route and endpoint:
        /store/delivery-history
        endpoint='store_delivery_history'

    It shows delivery-boy-wise history only for the currently logged-in store.
    It is read-only and does not affect delivery assignment/reassignment/cancel flows.
    """
    u, store = _get_current_store_or_redirect()

    if not store:
        flash("Store not found.", "danger")
        return redirect(url_for("store_dashboard"))

    store_id = store.get("_id")
    store_id_str = str(store_id)
    store_name = (store.get("store_name") or store.get("name") or "").strip().lower()

    q = (request.args.get("q") or "").strip()
    status_filter = (request.args.get("status") or "").strip().upper()
    delivery_user_filter = (request.args.get("delivery_user_id") or "").strip()
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

    def _store_history_float(value, default=0.0):
        try:
            if value is None or str(value).strip() == "":
                return float(default)
            return float(value)
        except Exception:
            return float(default)

    def _store_history_belongs_to_store(order):
        order_store_id = order.get("store_id")
        order_store_name = (order.get("store_name") or "").strip().lower()

        if order_store_id and str(order_store_id) == store_id_str:
            return True

        if store_name and order_store_name and order_store_name == store_name:
            return True

        return False

    def _store_history_entries(order):
        entries = order.get("delivery_history") or []
        return [entry for entry in entries if isinstance(entry, dict)]

    def _store_history_latest_action(order, action_name):
        matched = []

        for entry in _store_history_entries(order):
            if entry.get("action") == action_name:
                matched.append(entry)

        return matched[-1] if matched else {}

    def _store_history_latest_value(order, *keys):
        for entry in reversed(_store_history_entries(order)):
            for key in keys:
                value = entry.get(key)
                if value not in [None, ""]:
                    return value

        return ""

    def _store_history_effective_partner_id(order):
        return (
            order.get("delivery_partner_id")
            or order.get("previous_delivery_partner_id")
            or _store_history_latest_value(
                order,
                "delivery_partner_id",
                "previous_delivery_partner_id",
                "old_delivery_partner_id"
            )
            or ""
        )

    def _store_history_effective_partner_name(order):
        return (
            order.get("delivery_partner_name")
            or order.get("previous_delivery_partner_name")
            or _store_history_latest_value(
                order,
                "delivery_partner_name",
                "previous_delivery_partner_name",
                "old_delivery_partner_name"
            )
            or "Unknown Delivery Boy"
        )

    def _store_history_effective_partner_phone(order):
        return (
            order.get("delivery_partner_phone")
            or order.get("previous_delivery_partner_phone")
            or _store_history_latest_value(
                order,
                "delivery_partner_phone",
                "previous_delivery_partner_phone",
                "old_delivery_partner_phone"
            )
            or ""
        )

    def _store_history_has_rider_cancel(order):
        if order.get("delivery_cancelled_by_partner"):
            return True

        if order.get("delivery_cancelled_at") or order.get("delivery_cancel_reason"):
            return True

        if _store_history_latest_action(order, "cancelled_by_delivery_partner"):
            return True

        return False

    def _store_history_record_at(order):
        rider_cancel_entry = _store_history_latest_action(order, "cancelled_by_delivery_partner")

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

    def _store_history_apply_status_label(row, has_rider_cancel_history):
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

    base_query = {
        "$or": [
            {"store_id": store_id},
            {"store_id": store_id_str},
            {"store_name": store.get("store_name")},
            {"store_name": store.get("name")}
        ]
    }

    raw_orders = list(
        mongo.orders.find(base_query).sort("updated_at", -1)
    )

    history_orders = []
    delivery_people_map = {}
    rider_summary_map = {}

    for order in raw_orders:
        if not _store_history_belongs_to_store(order):
            continue

        status = (order.get("status") or "").strip().upper()
        has_rider_cancel_history = _store_history_has_rider_cancel(order)

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

        effective_partner_id = _store_history_effective_partner_id(order)

        if not effective_partner_id:
            continue

        effective_partner_id_str = str(effective_partner_id)

        if delivery_user_filter and effective_partner_id_str != delivery_user_filter:
            continue

        payment_method = (order.get("payment_method") or "COD").strip().upper()

        if payment_type_filter == "COD" and payment_method != "COD":
            continue

        if payment_type_filter == "ONLINE" and payment_method == "COD":
            continue

        row = dict(order)
        row["id"] = str(row.get("_id") or "")
        row["delivery_partner_id"] = effective_partner_id_str
        row["delivery_partner_id_str"] = effective_partner_id_str
        row["delivery_partner_name"] = _store_history_effective_partner_name(order)
        row["delivery_partner_phone"] = _store_history_effective_partner_phone(order)

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

        row = _decorate_store_delivery_order(row)

        row["delivery_partner_id"] = effective_partner_id_str
        row["delivery_partner_id_str"] = effective_partner_id_str
        row["delivery_partner_name"] = row.get("delivery_partner_name") or _store_history_effective_partner_name(order)
        row["delivery_partner_phone"] = row.get("delivery_partner_phone") or _store_history_effective_partner_phone(order)

        record_at = _store_history_record_at(order)
        row["record_at"] = record_at

        row = _store_history_apply_status_label(row, has_rider_cancel_history)

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

        delivery_people_map[effective_partner_id_str] = {
            "id": effective_partner_id_str,
            "name": row.get("delivery_partner_name") or "Delivery Boy",
            "phone": row.get("delivery_partner_phone") or ""
        }

        if effective_partner_id_str not in rider_summary_map:
            rider_summary_map[effective_partner_id_str] = {
                "delivery_partner_id": effective_partner_id_str,
                "delivery_partner_name": row.get("delivery_partner_name") or "Delivery Boy",
                "delivery_partner_phone": row.get("delivery_partner_phone") or "",
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

        rider_row["cod_to_collect"] += _store_history_float(row.get("amount_to_collect"))
        rider_row["delivery_fee"] += _store_history_float(row.get("delivery_fee"))
        rider_row["tip"] += _store_history_float(row.get("tip_amount"))
        rider_row["delivery_earning"] += _store_history_float(row.get("delivery_fee_plus_tip"))
        rider_row["platform_fee"] += _store_history_float(row.get("platform_fee"))
        rider_row["store_earning"] += _store_history_float(row.get("store_earning"))

        if record_at and str(record_at) > str(rider_row.get("last_record_at") or ""):
            rider_row["last_record_at"] = record_at

        history_orders.append(row)

    history_orders.sort(
        key=lambda x: str(x.get("record_at") or ""),
        reverse=True
    )

    rider_summary_rows = list(rider_summary_map.values())

    for rider_row in rider_summary_rows:
        rider_row["cod_to_collect"] = round(_store_history_float(rider_row.get("cod_to_collect")), 2)
        rider_row["delivery_fee"] = round(_store_history_float(rider_row.get("delivery_fee")), 2)
        rider_row["tip"] = round(_store_history_float(rider_row.get("tip")), 2)
        rider_row["delivery_earning"] = round(_store_history_float(rider_row.get("delivery_earning")), 2)
        rider_row["platform_fee"] = round(_store_history_float(rider_row.get("platform_fee")), 2)
        rider_row["store_earning"] = round(_store_history_float(rider_row.get("store_earning")), 2)

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
        "delivered": sum(1 for r in history_orders if r.get("history_type") == "delivered"),
        "failed": sum(1 for r in history_orders if r.get("history_type") == "failed"),
        "rider_cancelled": sum(1 for r in history_orders if r.get("history_type") == "rider_cancelled"),
        "active": sum(1 for r in history_orders if r.get("history_type") == "active"),
        "cancelled": sum(1 for r in history_orders if r.get("history_type") == "cancelled"),
        "cod_to_collect": round(sum(_store_history_float(r.get("amount_to_collect")) for r in history_orders), 2),
        "delivery_fee": round(sum(_store_history_float(r.get("delivery_fee")) for r in history_orders), 2),
        "tip": round(sum(_store_history_float(r.get("tip_amount")) for r in history_orders), 2),
        "delivery_earning": round(sum(_store_history_float(r.get("delivery_fee_plus_tip")) for r in history_orders), 2),
        "platform_fee": round(sum(_store_history_float(r.get("platform_fee")) for r in history_orders), 2),
        "store_earning": round(sum(_store_history_float(r.get("store_earning")) for r in history_orders), 2),
    }

    store_view = dict(store)
    store_view["id"] = str(store["_id"])

    return render_template(
        "store_delivery_history.html",
        user=u,
        store=store_view,
        orders=history_orders,
        rider_summary_rows=rider_summary_rows,
        delivery_people=list(delivery_people_map.values()),
        history_metrics=history_metrics,
        q=q,
        status_filter=status_filter,
        delivery_user_filter=delivery_user_filter,
        payment_type_filter=payment_type_filter,
        date_from=date_from,
        date_to=date_to,
        active_page="delivery_history"
    )


@app.route('/store/returns', methods=['GET'], endpoint='store_returns')
@login_required(role='store')
def store_returns_page():
    """
    Store-side return/refund request page.

    Store can:
    - View own return requests
    - Recommend APPROVE / REJECT / NEED_ADMIN_REVIEW
    - Add store remark

    Store cannot process refund.
    Admin has final authority.
    """
    u, store = _get_current_store_or_redirect()

    if not store:
        flash("Store not found.", "danger")
        return redirect(url_for("store_dashboard"))

    store_id = store["_id"]
    store_id_str = str(store_id)

    q = (request.args.get("q") or "").strip()
    return_filter = (request.args.get("return_status") or "").strip().upper()
    review_filter = (request.args.get("review_status") or "").strip().upper()
    if review_filter == "APPROVE":
        review_filter = "APPROVED"

    if review_filter == "REJECT":
        review_filter = "REJECTED"
    date_from = (request.args.get("from") or "").strip()
    date_to = (request.args.get("to") or "").strip()

    raw_orders = list(
        mongo.orders.find({
            "$and": [
                {
                    "$or": [
                        {"store_id": store_id},
                        {"store_id": store_id_str}
                    ]
                },
                {
                    "$or": [
                        {"return_status": {"$exists": True, "$ne": ""}},
                        {"return_requested_at": {"$exists": True}},
                        {"refund_status": {"$exists": True, "$ne": ""}},
                        {"return_audit_logs": {"$exists": True, "$ne": []}}
                    ]
                }
            ]
        }).sort("return_requested_at", -1)
    )

    rows = []

    for order in raw_orders:
        row = dict(order)
        row["id"] = str(row.get("_id") or "")

        row = _decorate_store_delivery_order(row)

        row["return_status"] = (row.get("return_status") or "RETURN_REQUESTED").strip().upper()
        row["refund_status"] = (row.get("refund_status") or "PENDING").strip().upper()
        row["return_reason"] = row.get("return_reason") or ""
        row["return_note"] = row.get("return_note") or ""
        row["return_requested_at"] = row.get("return_requested_at") or row.get("updated_at") or row.get("created_at") or ""

        row["refund_amount"] = _store_delivery_money_float(row.get("refund_amount"), 0)
        row["refund_items_amount"] = _store_delivery_money_float(row.get("refund_items_amount"), 0)
        row["refund_delivery_fee"] = _store_delivery_money_float(row.get("refund_delivery_fee"), 0)
        row["refund_platform_fee"] = _store_delivery_money_float(row.get("refund_platform_fee"), 0)
        row["refund_tip_amount"] = _store_delivery_money_float(row.get("refund_tip_amount"), 0)

        row["refund_method"] = row.get("refund_method") or ""
        row["refund_reference"] = row.get("refund_reference") or ""
        row["refund_processed_at"] = row.get("refund_processed_at") or ""
        row["refund_processed_by_name"] = row.get("refund_processed_by_name") or ""

        row["store_refund_deduction"] = _store_delivery_money_float(
            row.get("store_refund_deduction")
            if row.get("store_refund_deduction") is not None
            else row.get("refund_deduction"),
            row["refund_items_amount"]
        )

        row["store_adjustment_due"] = _store_delivery_money_float(
            row.get("store_adjustment_due"),
            0
        )

        row["original_store_payout_amount"] = _store_delivery_money_float(
            row.get("original_store_payout_amount")
            if row.get("original_store_payout_amount") is not None
            else row.get("store_earning"),
            row.get("items_subtotal") or 0
        )

        row["adjusted_store_payout"] = _store_delivery_money_float(
            row.get("adjusted_store_payout"),
            max(
                float(row.get("original_store_payout_amount") or 0)
                - float(row.get("store_refund_deduction") or 0),
                0
            )
        )

        row["settlement_impact"] = (
            row.get("settlement_impact")
            or (
                "ADJUST_FROM_NEXT_PAYOUT"
                if row["store_adjustment_due"] > 0
                else (
                    "DEDUCT_FROM_PENDING_PAYOUT"
                    if row["store_refund_deduction"] > 0
                    else "NO_DEDUCTION"
                )
            )
        )

        row["store_return_review_status"] = (
            row.get("store_return_review_status")
            or row.get("store_review_status")
            or "PENDING"
        ).strip().upper()

        # Backward support for old values used before Store final decision flow.
        if row["store_return_review_status"] == "APPROVE":
            row["store_return_review_status"] = "APPROVED"

        if row["store_return_review_status"] == "REJECT":
            row["store_return_review_status"] = "REJECTED"

        row["store_return_review_remark"] = (
            row.get("store_return_review_remark")
            or row.get("store_review_note")
            or row.get("store_return_review_note")
            or ""
        )

        row["store_reviewed_at"] = row.get("store_reviewed_at") or ""
        row["admin_return_review_status"] = (
            row.get("admin_return_review_status")
            or row.get("admin_decision")
            or (
                "NOT_REQUIRED"
                if row["store_return_review_status"] in ["APPROVED", "REJECTED"]
                else "PENDING"
            )
        ).strip().upper()

        report_date = str(row.get("return_requested_at") or "")

        if date_from and report_date and report_date[:10] < date_from:
            continue

        if date_to and report_date and report_date[:10] > date_to:
            continue

        if return_filter and row.get("return_status") != return_filter:
            continue

        if review_filter and row.get("store_return_review_status") != review_filter:
            continue

        if q:
            haystack = " ".join([
                str(row.get("id") or ""),
                str(row.get("customer_name") or ""),
                str(row.get("customer_phone") or ""),
                str(row.get("return_reason") or ""),
                str(row.get("return_note") or ""),
                str(row.get("return_status") or ""),
                str(row.get("refund_status") or ""),
                str(row.get("refund_method") or ""),
                str(row.get("refund_reference") or ""),
                str(row.get("store_return_review_status") or ""),
                str(row.get("admin_return_review_status") or ""),
                str(row.get("settlement_impact") or "")
            ]).lower()

            if q.lower() not in haystack:
                continue

        rows.append(row)

        metrics = {
        "total": len(rows),

        "pending_review": sum(
            1 for r in rows
            if (r.get("store_return_review_status") or "") == "PENDING"
        ),

        "approved": sum(
            1 for r in rows
            if (r.get("store_return_review_status") or "") in ["APPROVED", "APPROVE"]
        ),

        "rejected": sum(
            1 for r in rows
            if (r.get("store_return_review_status") or "") in ["REJECTED", "REJECT"]
        ),

        "need_admin_review": sum(
            1 for r in rows
            if (r.get("store_return_review_status") or "") == "NEED_ADMIN_REVIEW"
        ),

        "ready_for_refund": sum(
            1 for r in rows
            if (r.get("refund_status") or "") == "READY_FOR_REFUND"
        ),

        "refund_processed": sum(
            1 for r in rows
            if (r.get("refund_status") or "") in ["PROCESSED", "ADJUSTED"]
        ),

        "refund_amount": round(
            sum(float(r.get("refund_amount") or 0) for r in rows),
            2
        ),

        "items_refund_amount": round(
            sum(float(r.get("refund_items_amount") or 0) for r in rows),
            2
        ),

        "store_refund_deduction": round(
            sum(float(r.get("store_refund_deduction") or 0) for r in rows),
            2
        ),

        "store_adjustment_due": round(
            sum(float(r.get("store_adjustment_due") or 0) for r in rows),
            2
        ),
    }

    store_view = dict(store)
    store_view["id"] = str(store["_id"])

    return render_template(
        "store_returns.html",
        user=u,
        store=store_view,
        returns=rows,
        metrics=metrics,
        q=q,
        return_filter=return_filter,
        review_filter=review_filter,
        date_from=date_from,
        date_to=date_to,
        active_page="returns"
    )


@app.route('/store/returns/<oid>/review', methods=['POST'], endpoint='store_return_review')
@login_required(role='store')
def store_return_review(oid):
    """
    Store final return decision.

    Store can:
    - Approve return
    - Reject return
    - Send to Admin review

    Store cannot process customer refund money.
    Admin/NE FRESH processes refund and platform settlement after store approval.
    """
    u, store = _get_current_store_or_redirect()

    if not store:
        flash("Store not found.", "danger")
        return redirect(url_for("store_dashboard"))

    oid_obj, order = _get_store_owned_order(store, oid)

    if not oid_obj or not order:
        flash("Return order not found for your store.", "danger")
        return redirect(url_for("store_returns"))

    return_status = (order.get("return_status") or "").strip().upper()
    refund_status = (order.get("refund_status") or "").strip().upper()

    if refund_status in ["PROCESSED", "ADJUSTED"]:
        flash("Refund is already processed for this order.", "warning")
        return redirect(url_for("store_returns"))

    if return_status not in [
        "RETURN_REQUESTED",
        "REQUESTED",
        "STORE_REVIEWED",
        "NEED_ADMIN_REVIEW"
    ]:
        flash("This order does not have an active return request for store decision.", "warning")
        return redirect(url_for("store_returns"))

    decision_raw = (request.form.get("store_review_decision") or "").strip().upper()
    remark = (request.form.get("store_review_remark") or "").strip()

    # Backward support for old template values.
    if decision_raw == "APPROVE":
        decision = "APPROVED"
    elif decision_raw == "REJECT":
        decision = "REJECTED"
    else:
        decision = decision_raw

    if decision not in ["APPROVED", "REJECTED", "NEED_ADMIN_REVIEW"]:
        flash("Please select a valid store return decision.", "warning")
        return redirect(url_for("store_returns"))

    if len(remark) > 700:
        remark = remark[:700]

    now = datetime.utcnow().isoformat()

    old_store_review_status = (
        order.get("store_return_review_status")
        or order.get("store_review_status")
        or "PENDING"
    )

    refund_amount = _store_delivery_money_float(order.get("refund_amount"), 0)
    refund_items_amount = _store_delivery_money_float(order.get("refund_items_amount"), refund_amount)
    refund_delivery_fee = _store_delivery_money_float(order.get("refund_delivery_fee"), 0)
    refund_platform_fee = _store_delivery_money_float(order.get("refund_platform_fee"), 0)
    refund_tip_amount = _store_delivery_money_float(order.get("refund_tip_amount"), 0)

    if decision == "APPROVED":
        next_return_status = "STORE_APPROVED"
        next_refund_status = "READY_FOR_REFUND"
        next_admin_review_status = "NOT_REQUIRED"
        return_pickup_required = True
        return_pickup_status = "PENDING_ASSIGNMENT"
        store_refund_deduction = refund_items_amount
        settlement_impact = "DEDUCT_FROM_PENDING_PAYOUT"
        order_settlement_status = "REFUND_PENDING"
        event_note = "Store approved the return. Refund is ready for Admin/NE FRESH processing."

    elif decision == "REJECTED":
        next_return_status = "STORE_REJECTED"
        next_refund_status = "REJECTED"
        next_admin_review_status = "NOT_REQUIRED"
        return_pickup_required = False
        return_pickup_status = "NOT_REQUIRED"
        store_refund_deduction = 0.0
        settlement_impact = "NO_DEDUCTION"
        order_settlement_status = "RETURN_REJECTED"
        event_note = "Store rejected the return request."

    else:
        next_return_status = "NEED_ADMIN_REVIEW"
        next_refund_status = "PENDING"
        next_admin_review_status = "PENDING"
        return_pickup_required = False
        return_pickup_status = "PENDING_ADMIN_REVIEW"
        store_refund_deduction = 0.0
        settlement_impact = "PENDING_ADMIN_REVIEW"
        order_settlement_status = "ADMIN_RETURN_REVIEW_PENDING"
        event_note = "Store requested Admin review for this return."

    return_event = {
        "action": "RETURN_DECIDED_BY_STORE",
        "order_id": str(oid_obj),
        "store_id": str(store.get("_id") or ""),
        "store_name": store.get("store_name") or "",
        "old_status": old_store_review_status,
        "new_status": decision,
        "return_status": next_return_status,
        "refund_status": next_refund_status,
        "refund_amount": refund_amount,
        "refund_items_amount": refund_items_amount,
        "refund_delivery_fee": refund_delivery_fee,
        "refund_platform_fee": refund_platform_fee,
        "refund_tip_amount": refund_tip_amount,
        "store_refund_deduction": store_refund_deduction,
        "settlement_impact": settlement_impact,
        "order_settlement_status": order_settlement_status,
        "note": remark,
        "created_by": str(u.get("_id") or u.get("id") or ""),
        "created_by_name": u.get("name") or store.get("store_name") or "Store",
        "created_by_role": "store",
        "created_at": now
    }

    update_data = {
        "return_status": next_return_status,

        "store_return_review_status": decision,
        "store_review_status": decision,
        "store_return_review_remark": remark,
        "store_review_note": remark,
        "store_reviewed_by": str(u.get("_id") or u.get("id") or ""),
        "store_reviewed_by_name": u.get("name") or store.get("store_name") or "Store",
        "store_reviewed_at": now,

        "admin_return_review_status": next_admin_review_status,
        "refund_status": next_refund_status,

        "return_pickup_required": return_pickup_required,
        "return_pickup_status": return_pickup_status,

        # Store only decides product return validity.
        # Admin/NE FRESH still handles final money/refund settlement.
        "refund_amount": refund_amount,
        "refund_items_amount": refund_items_amount,
        "refund_delivery_fee": refund_delivery_fee,
        "refund_platform_fee": refund_platform_fee,
        "refund_tip_amount": refund_tip_amount,

        "store_refund_deduction": store_refund_deduction,
        "refund_deduction": store_refund_deduction,
        "settlement_impact": settlement_impact,
        "order_settlement_status": order_settlement_status,
        "settlement_status": order_settlement_status,

        "last_return_event": return_event,
        "updated_at": now
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
                "return_status": next_return_status,
                "store_return_review_status": decision,
                "store_review_status": decision,
                "admin_return_review_status": next_admin_review_status,
                "refund_status": next_refund_status,
                "refund_amount": refund_amount,
                "refund_items_amount": refund_items_amount,
                "store_refund_deduction": store_refund_deduction,
                "refund_deduction": store_refund_deduction,
                "settlement_impact": settlement_impact,
                "order_settlement_status": order_settlement_status,
                "settlement_status": order_settlement_status,
                "updated_at": now
            }
        }
    )

    mongo.order_events.insert_one({
        "order_id": oid_obj,
        "status": next_return_status,
        "note": f"{event_note} {remark}".strip(),
        "created_at": now
    })

    if decision == "APPROVED":
        flash("Return approved by Store. Refund is now ready for Admin/NE FRESH processing.", "success")
    elif decision == "REJECTED":
        flash("Return rejected by Store.", "success")
    else:
        flash("Return sent to Admin review.", "success")

    return redirect(url_for("store_returns"))


@app.route('/store/inventory', methods=['GET'], endpoint='store_inventory')
@login_required(role='store')
def store_inventory_page():
    u, store = _get_current_store_or_redirect()

    if not store:
        return redirect(url_for("store_dashboard"))

    page_context = _build_store_split_page_context(store)

    return render_template(
        "store_inventory.html",
        user=u,
        store=store,
        **page_context
    )

@app.route('/store/categories', methods=['GET'], endpoint='store_categories')
@login_required(role='store')
def store_categories_page():
    u, store = _get_current_store_or_redirect()

    if not store:
        return redirect(url_for("store_dashboard"))

    page_context = _build_store_split_page_context(store)

    return render_template(
        "store_categories.html",
        user=u,
        store=store,
        **page_context
    )

@app.route('/store/reviews', methods=['GET'], endpoint='store_reviews')
@login_required(role='store')
def store_reviews_page():
    u, store = _get_current_store_or_redirect()

    if not store:
        return redirect(url_for("store_dashboard"))

    page_context = _build_store_split_page_context(store) or {}

    store_id = store["_id"]
    store_id_str = str(store_id)

    reviews = list(
        mongo.store_ratings.find({
            "$and": [
                {
                    "$or": [
                        {"store_id": store_id},
                        {"store_id": store_id_str}
                    ]
                },
                {
                    "$or": [
                        {"is_active": 1},
                        {"is_active": True},
                        {"is_active": {"$exists": False}}
                    ]
                }
            ]
        }).sort("created_at", -1)
    )

    total_reviews = len(reviews)
    total_rating = 0.0

    rating_breakdown = {
        5: 0,
        4: 0,
        3: 0,
        2: 0,
        1: 0
    }

    positive_reviews = 0
    low_reviews = 0

    for r in reviews:
        r["id"] = str(r["_id"])

        try:
            rating_value = float(r.get("rating") or r.get("stars") or 0)
        except (TypeError, ValueError):
            rating_value = 0.0

        if rating_value < 0:
            rating_value = 0.0

        if rating_value > 5:
            rating_value = 5.0

        r["rating"] = rating_value
        total_rating += rating_value

        rating_bucket = int(round(rating_value))
        if rating_bucket < 1 and rating_value > 0:
            rating_bucket = 1
        if rating_bucket > 5:
            rating_bucket = 5

        if rating_bucket in rating_breakdown:
            rating_breakdown[rating_bucket] += 1

        if rating_value >= 4:
            positive_reviews += 1

        if rating_value > 0 and rating_value <= 2:
            low_reviews += 1

        reviewer = None

        if r.get("user_id"):
            try:
                reviewer = mongo.users.find_one({"_id": ObjectId(str(r.get("user_id")))})
            except Exception:
                reviewer = mongo.users.find_one({"_id": str(r.get("user_id"))})

        if reviewer:
            r["reviewer_name"] = reviewer.get("name", "Customer")
            r["reviewer_email"] = reviewer.get("email", "")
            r["reviewer_phone"] = reviewer.get("phone", "")
        else:
            r["reviewer_name"] = r.get("reviewer_name", "Customer")
            r["reviewer_email"] = r.get("reviewer_email", "")
            r["reviewer_phone"] = r.get("reviewer_phone", "")

        r["review_text"] = r.get("review") or r.get("comment") or ""

        created_at = r.get("created_at") or r.get("updated_at") or ""
        r["created_at_display"] = created_at

        try:
            if isinstance(created_at, str) and created_at:
                clean_dt = created_at.replace("Z", "")
                dt_obj = datetime.fromisoformat(clean_dt)
                r["created_at_display"] = dt_obj.strftime("%d %b %Y, %I:%M %p")
        except Exception:
            pass

    avg_rating = round(total_rating / total_reviews, 1) if total_reviews else 0

    review_metrics = {
    "total_reviews": total_reviews,
    "avg_rating": avg_rating,
    "positive_reviews": sum(1 for r in reviews if float(r.get("rating") or 0) >= 4),
    "low_reviews": sum(1 for r in reviews if float(r.get("rating") or 0) > 0 and float(r.get("rating") or 0) <= 2)
    }

    return render_template(
    "store_reviews.html",
    user=u,
    store=store,
    reviews=reviews,
    recent_reviews=reviews[:6],
    rating_breakdown=rating_breakdown,
    review_metrics=review_metrics,
    **page_context
    )

@app.route('/store/product-reviews', methods=['GET'], endpoint='store_product_reviews')
@login_required(role='store')
def store_product_reviews_page():
    u, store = _get_current_store_or_redirect()

    if not store:
        return redirect(url_for("store_dashboard"))

    page_context = _build_store_split_page_context(store) or {}

    store_id = store["_id"]
    store_id_str = str(store_id)

    store_products = list(mongo.products.find({
        "$or": [
            {"store_id": store_id},
            {"store_id": store_id_str}
        ]
    }))

    product_map = {}
    product_ids = []

    for p in store_products:
        pid = p["_id"]
        pid_str = str(pid)

        product_ids.append(pid)
        product_ids.append(pid_str)

        product_map[pid_str] = {
            "id": pid_str,
            "name": p.get("name", "Product"),
            "image_path": p.get("image_path", ""),
            "category": p.get("category", ""),
            "stock_quantity": float(p.get("stock_quantity") or 0),
            "price_per_unit": float(p.get("price_per_unit") or 0),
            "unit_type": p.get("unit_type") or "WEIGHT",
            "unit_label": p.get("unit_label") or "kg"
        }

    reviews = []

    if product_ids:
        reviews = list(
            mongo.product_ratings.find({
                "$and": [
                    {
                        "$or": [
                            {"product_id": {"$in": product_ids}},
                            {"store_id": store_id},
                            {"store_id": store_id_str}
                        ]
                    },
                    {
                        "$or": [
                            {"is_active": 1},
                            {"is_active": True},
                            {"is_active": {"$exists": False}}
                        ]
                    }
                ]
            }).sort("created_at", -1)
        )

    total_reviews = len(reviews)
    total_rating = 0.0
    positive_reviews = 0
    low_reviews = 0

    rating_breakdown = {
        5: 0,
        4: 0,
        3: 0,
        2: 0,
        1: 0
    }

    product_review_counts = {}

    for r in reviews:
        r["id"] = str(r["_id"])

        try:
            rating_value = float(r.get("rating") or r.get("stars") or 0)
        except (TypeError, ValueError):
            rating_value = 0.0

        if rating_value < 0:
            rating_value = 0.0

        if rating_value > 5:
            rating_value = 5.0

        r["rating"] = rating_value
        total_rating += rating_value

        rating_bucket = int(round(rating_value))
        if rating_bucket < 1 and rating_value > 0:
            rating_bucket = 1
        if rating_bucket > 5:
            rating_bucket = 5

        if rating_bucket in rating_breakdown:
            rating_breakdown[rating_bucket] += 1

        if rating_value >= 4:
            positive_reviews += 1

        if rating_value > 0 and rating_value <= 2:
            low_reviews += 1

        pid_raw = r.get("product_id")
        pid_str = str(pid_raw) if pid_raw else ""

        product_data = product_map.get(pid_str)

        if product_data:
            r["product_name"] = product_data.get("name", "Product")
            r["product_image_path"] = product_data.get("image_path", "")
            r["product_category"] = product_data.get("category", "")
        else:
            r["product_name"] = r.get("product_name", "Product")
            r["product_image_path"] = ""
            r["product_category"] = ""

        if pid_str:
            product_review_counts[pid_str] = product_review_counts.get(pid_str, 0) + 1

        reviewer = None

        if r.get("user_id"):
            try:
                reviewer = mongo.users.find_one({"_id": ObjectId(str(r.get("user_id")))})
            except Exception:
                reviewer = mongo.users.find_one({"_id": str(r.get("user_id"))})

        if reviewer:
            r["reviewer_name"] = reviewer.get("name", "Customer")
            r["reviewer_email"] = reviewer.get("email", "")
            r["reviewer_phone"] = reviewer.get("phone", "")
        else:
            r["reviewer_name"] = r.get("reviewer_name", "Customer")
            r["reviewer_email"] = r.get("reviewer_email", "")
            r["reviewer_phone"] = r.get("reviewer_phone", "")

        r["review_text"] = r.get("review") or r.get("comment") or ""

        created_at = r.get("created_at") or r.get("updated_at") or ""
        r["created_at_display"] = created_at

        try:
            if isinstance(created_at, str) and created_at:
                clean_dt = created_at.replace("Z", "")
                dt_obj = datetime.fromisoformat(clean_dt)
                r["created_at_display"] = dt_obj.strftime("%d %b %Y, %I:%M %p")
        except Exception:
            pass

    avg_rating = round(total_rating / total_reviews, 1) if total_reviews else 0

    product_review_metrics = {
        "total_reviews": total_reviews,
        "avg_rating": avg_rating,
        "positive_reviews": positive_reviews,
        "low_reviews": low_reviews,
        "reviewed_products": len(product_review_counts)
    }

    return render_template(
        "store_product_reviews.html",
        user=u,
        store=store,
        reviews=reviews,
        rating_breakdown=rating_breakdown,
        product_review_metrics=product_review_metrics,
        **page_context
    )

@app.route('/store/complaints', methods=['GET'], endpoint='store_complaints')
@login_required(role='store')
def store_complaints_page():
    u, store = _get_current_store_or_redirect()

    if not store:
        return redirect(url_for("store_dashboard"))

    page_context = _build_store_split_page_context(store) or {}

    store_id = store["_id"]
    store_id_str = str(store_id)

    complaints = list(
        mongo.customer_complaints.find({
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
                        {"is_active": 1},
                        {"is_active": True},
                        {"is_active": {"$exists": False}}
                    ]
                }
            ]
        }).sort("created_at", -1)
    )

    for c in complaints:
        c["id"] = str(c["_id"])
        c["complaint_image_path"] = c.get("complaint_image_path") or c.get("image_path") or ""

        status = str(c.get("status") or "open").strip().lower()
        progress_status = str(c.get("progress_status") or "received").strip().lower()
        admin_takeover_status = str(c.get("admin_takeover_status") or "").strip().upper()

        c["status"] = status
        c["progress_status"] = progress_status
        c["status_label"] = status.replace("_", " ").title()
        c["progress_status_label"] = progress_status.replace("_", " ").title()

        c["admin_takeover_status"] = admin_takeover_status
        c["is_admin_taken_over"] = admin_takeover_status == "TAKEN_OVER"
        c["admin_takeover_reason"] = c.get("admin_takeover_reason") or ""
        c["admin_takeover_by_name"] = c.get("admin_takeover_by_name") or "NE FRESH Admin"
        c["admin_takeover_at"] = c.get("admin_takeover_at") or ""

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

    complaint_metrics = {
        "total": len(complaints),
        "open": sum(1 for c in complaints if c.get("status") == "open"),
        "in_progress": sum(1 for c in complaints if c.get("status") == "in_progress"),
        "resolved": sum(1 for c in complaints if c.get("status") == "resolved")
    }

    return render_template(
        "store_complaints.html",
        user=u,
        store=store,
        complaints=complaints,
        complaint_metrics=complaint_metrics,
        **page_context
    )

@app.route('/store/complaints/<cid>/update', methods=['POST'], endpoint='store_complaint_update')
@login_required(role='store')
def store_complaint_update(cid):
    u, store = _get_current_store_or_redirect()

    if not store:
        return redirect(url_for("store_dashboard"))

    try:
        cid_obj = ObjectId(cid)
    except Exception:
        flash("Invalid complaint.", "danger")
        return redirect(url_for("store_complaints"))

    store_id = store["_id"]
    store_id_str = str(store_id)

    complaint = mongo.customer_complaints.find_one({
        "_id": cid_obj,
        "$or": [
            {"store_id": store_id},
            {"store_id": store_id_str},
            {"store_id_str": store_id_str}
        ]
    })

    if not complaint:
        flash("Complaint not found for your store.", "danger")
        return redirect(url_for("store_complaints"))
    
    admin_takeover_status = str(
        complaint.get("admin_takeover_status") or ""
    ).strip().upper()

    if admin_takeover_status == "TAKEN_OVER":
        flash("This complaint has been taken over by NE FRESH Admin. Store updates are disabled.", "warning")
        return redirect(url_for("store_complaints"))

    progress_status = (request.form.get("progress_status") or "").strip().lower()
    store_reply = (request.form.get("store_reply") or "").strip()
    store_progress_note = (request.form.get("store_progress_note") or "").strip()

    allowed_progress = {
        "received",
        "in_progress",
        "resolved"
    }

    if progress_status not in allowed_progress:
        flash("Please select a valid progress status.", "warning")
        return redirect(url_for("store_complaints"))

    if len(store_reply) > 1000:
        flash("Store reply is too long. Please keep it within 1000 characters.", "warning")
        return redirect(url_for("store_complaints"))

    if len(store_progress_note) > 1000:
        flash("Progress note is too long. Please keep it within 1000 characters.", "warning")
        return redirect(url_for("store_complaints"))

    if progress_status == "resolved":
        final_status = "resolved"
    elif progress_status == "in_progress":
        final_status = "in_progress"
    else:
        final_status = "open"

    now = datetime.utcnow().isoformat()

    

    mongo.customer_complaints.update_one(
        {"_id": cid_obj},
        {
            "$set": {
                "progress_status": progress_status,
                "status": final_status,
                "store_reply": store_reply,
                "store_progress_note": store_progress_note,
                "store_updated_by": str(u["_id"]),
                "store_updated_by_name": u.get("name", "Store User"),
                "updated_at": now
            }
        }
    )

    flash("Complaint progress updated successfully.", "success")
    return redirect(url_for("store_complaints"))

@app.route('/store/profile', methods=['GET'], endpoint='store_profile')
@login_required(role='store')
def store_profile_page():
    u, store = _get_current_store_or_redirect()

    if not store:
        return redirect(url_for("store_dashboard"))

    owner = mongo.users.find_one({"_id": ObjectId(str(store.get("user_id")))}) if store.get("user_id") else u
    if not owner:
        owner = u

    store["id"] = str(store["_id"])

    page_context = _build_store_split_page_context(store)
    profile_context = _build_store_profile_context(store, owner)

    return render_template(
        "store_profile.html",
        user=u,
        store=store,
        store_owner=owner,
        **page_context,
        **profile_context
    )

@app.route('/store/profile/update', methods=['POST'], endpoint='store_profile_update')
@login_required(role='store')
def store_profile_update():
    u, store = _get_current_store_or_redirect()

    if not store:
        flash("Store not found.", "danger")
        return redirect(url_for("store_dashboard"))

    now = datetime.utcnow().isoformat()

    store_name = (request.form.get("store_name") or "").strip()
    owner_name = (request.form.get("owner_name") or "").strip()
    phone_raw = (request.form.get("phone") or "").strip()
    phone = normalize_phone(phone_raw)

    address = (request.form.get("address") or "").strip()
    banner = request.files.get("banner")
    logo = request.files.get("logo")
    image = request.files.get("image")
    profile_image = request.files.get("profile_image")
    city = (request.form.get("city") or "").strip()
    state = (request.form.get("state") or "Assam").strip()
    pincode = _clean_pin(request.form.get("pincode") or "")

    description = (request.form.get("description") or "").strip()
    profile_intro = (request.form.get("profile_intro") or "").strip()
    opening_time = (request.form.get("opening_time") or "").strip()
    closing_time = (request.form.get("closing_time") or "").strip()
    working_days = request.form.getlist("working_days")

    min_order_amount_raw = (request.form.get("min_order_amount") or "").strip()

       # Delivery enabled/off.
    # IMPORTANT:
    # If the new delivery_enabled field is not submitted by some form,
    # keep the existing DB value instead of silently turning delivery off.
    existing_delivery_enabled = bool(
        int(
            store.get(
                "delivery_enabled",
                1 if store.get("delivery_available", False) else 0
            ) or 0
        )
    )

    delivery_enabled = _store_bool_from_form(
        "delivery_enabled",
        existing_delivery_enabled
    )

    # Keep old field in sync with new field.
    delivery_available = bool(delivery_enabled)

    # Store operational status. Separate from is_active.
    is_online = _store_bool_from_form(
        "is_online",
        bool(int(store.get("is_online", store.get("is_open", 1)) or 0))
    )

    delivery_mode = (request.form.get("delivery_mode") or "polygon").strip().lower()
    if delivery_mode not in ["polygon"]:
        delivery_mode = "polygon"

    existing_delivery_zone_polygon = store.get("delivery_zone_polygon") or []

    if "delivery_zone_polygon" in request.form:
        delivery_zone_raw = (request.form.get("delivery_zone_polygon") or "").strip()
        delivery_zone_polygon = _parse_delivery_zone_polygon(delivery_zone_raw)
    else:
        delivery_zone_polygon = existing_delivery_zone_polygon


    lat_raw = (request.form.get("latitude") or "").strip()
    lng_raw = (request.form.get("longitude") or "").strip()

    latitude = _store_float_or_none(lat_raw, -90, 90)
    longitude = _store_float_or_none(lng_raw, -180, 180)


    try:
        min_order_amount = float(min_order_amount_raw) if min_order_amount_raw else None
    except Exception:
        min_order_amount = None

    if not store_name:
        flash("Store name is required.", "warning")
        return redirect(url_for("store_profile"))

    if not owner_name:
        flash("Owner name is required.", "warning")
        return redirect(url_for("store_profile"))

    if not phone:
        flash("Phone number is required.", "warning")
        return redirect(url_for("store_profile"))

    if not address:
        flash("Store address is required.", "warning")
        return redirect(url_for("store_profile"))
    
    if pincode and not is_serviceable_pincode(pincode):
        flash("Please enter a valid 6-digit store pincode.", "warning")
        return redirect(url_for("store_profile"))

    if state and not is_assam_state(state):
        flash("Store state must be Assam for delivery operations.", "warning")
        return redirect(url_for("store_profile"))

    if delivery_enabled and delivery_mode == "polygon" and not delivery_zone_polygon:
        flash("Delivery zone polygon is required when delivery is enabled.", "warning")
        return redirect(url_for("store_profile"))
    
    update_data = {
        "store_name": store_name,
        "owner_name": owner_name,
        "phone": phone,

        "address": address,
        "city": city,
        "state": state,
        "pincode": pincode,

        "description": description,
        "profile_intro": profile_intro,

        "latitude": latitude,
        "longitude": longitude,

        "opening_time": opening_time,
        "closing_time": closing_time,
        "working_days": working_days,
        "min_order_amount": min_order_amount,

        # Backward compatibility with old field.
        "delivery_available": bool(delivery_enabled),

        # New delivery/serviceability fields.
        "is_online": 1 if is_online else 0,
        "is_open": 1 if is_online else 0,
        "delivery_enabled": 1 if delivery_enabled else 0,
        "delivery_mode": delivery_mode,
        "delivery_zone_polygon": delivery_zone_polygon,
        "delivery_zone_configured": 1 if delivery_zone_polygon else 0,

        "profile_updated_at": now,
        "updated_at": now
    }

    profile_image = request.files.get("profile_image")

    if profile_image and profile_image.filename:
        if not allowed_file(profile_image.filename):
            flash("Invalid store profile image file type.", "warning")
            return redirect(url_for("store_profile"))

        image_bytes = profile_image.read()

        if not image_bytes:
            flash("Please upload a valid store profile image.", "warning")
            return redirect(url_for("store_profile"))

        if len(image_bytes) > 4 * 1024 * 1024:
            flash("Store profile image must be 4 MB or smaller.", "warning")
            return redirect(url_for("store_profile"))

        safe_name = secure_filename(profile_image.filename)
        mime_type = profile_image.mimetype or "image/jpeg"

        image_doc = {
            "store_id": store["_id"],
            "store_id_str": str(store["_id"]),
            "filename": safe_name,
            "mime_type": mime_type,
            "data": Binary(image_bytes),
            "is_active": 1,
            "uploaded_by": str(u.get("_id") or u.get("id") or ""),
            "created_at": now,
            "updated_at": now
        }

        inserted_image = mongo.store_profile_images.insert_one(image_doc)

        mongo.store_profile_images.update_many(
            {
                "store_id": store["_id"],
                "_id": {"$ne": inserted_image.inserted_id}
            },
            {
                "$set": {
                    "is_active": 0,
                    "updated_at": now
                }
            }
        )

        update_data["profile_image_id"] = inserted_image.inserted_id
        update_data["profile_image_filename"] = safe_name
        update_data["profile_image_mime_type"] = mime_type
        update_data["profile_image_updated_at"] = now

    logo = request.files.get("logo")

    if logo and logo.filename:
        if not allowed_file(logo.filename):
            flash("Invalid logo/image file type.", "warning")
            return redirect(url_for("store_profile"))

        safe_name = secure_filename(logo.filename)
        stored_name = datetime.utcnow().strftime("%Y%m%d%H%M%S_") + safe_name
        folder = os.path.join(app.config["UPLOAD_FOLDER"], "store_profiles")
        os.makedirs(folder, exist_ok=True)

        logo.save(os.path.join(folder, stored_name))
        update_data["logo_path"] = f"uploads/store_profiles/{stored_name}"

        logo = request.files.get("logo")
        image = request.files.get("image")

        banner = request.files.get("banner")

    if banner and banner.filename:
        if not allowed_file(banner.filename):
            flash("Invalid banner image file type.", "warning")
            return redirect(url_for("store_profile"))

        fn = secure_filename(banner.filename)
        save_as = "store_banner_" + str(store["_id"]) + "_" + datetime.utcnow().strftime("%Y%m%d%H%M%S_") + fn
        os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
        banner.save(os.path.join(app.config["UPLOAD_FOLDER"], save_as))
        update_data["banner_path"] = f"uploads/{save_as}"

    mongo.stores.update_one(
        {"_id": store["_id"]},
        {"$set": update_data}
    )

    if store.get("user_id"):
        try:
            mongo.users.update_one(
                {"_id": ObjectId(str(store.get("user_id")))},
                {
                    "$set": {
                        "name": owner_name,
                        "phone": phone,
                        "updated_at": now
                    }
                }
            )
        except Exception:
            mongo.users.update_one(
                {"_id": store.get("user_id")},
                {
                    "$set": {
                        "name": owner_name,
                        "phone": phone,
                        "updated_at": now
                    }
                }
            )

    flash("Store profile updated successfully.", "success")
    return redirect(url_for("store_profile"))

@app.route('/store/notifications', methods=['GET'], endpoint='store_notifications')
@login_required(role='store')
def store_notifications_page():
    u, store = _get_current_store_or_redirect()

    if not store:
        return redirect(url_for("store_dashboard"))

    _sync_store_order_notifications(store)

    store_id_values = _store_id_values(store["_id"])

    notifications = list(
        mongo.store_notifications.find({
            "store_id": {"$in": store_id_values}
        }).sort("created_at", -1).limit(150)
    )

    notifications = [_hydrate_store_notification(n) for n in notifications]

    active_orders = list(
        mongo.orders.find({
            "store_id": {"$in": store_id_values},
            "status": {"$nin": ["DELIVERED", "CANCELLED"]}
        }).sort("created_at", -1).limit(30)
    )

    active_notifications = []

    for order in active_orders:
        oid = str(order["_id"])
        status = (order.get("status") or "PLACED").upper()

        total_payable = (
            float(order.get("total_amount") or 0)
            + float(order.get("delivery_fee") or 0)
            + float(order.get("tip_amount") or 0)
        )

        active_notifications.append({
            "id": oid,
            "title": f"Order #{oid[-6:]} needs attention",
            "message": f"Current status: {status}. Payable amount ₹ {total_payable:.2f}.",
            "type": "active_order",
            "order_id": oid,
            "created_at": order.get("created_at", "")
        })

    notification_settings = mongo.store_notification_settings.find_one({
        "store_id": store["_id"]
    }) or {
        "enabled": False
    }

    page_context = _build_store_split_page_context(store)

    return render_template(
        "store_notifications.html",
        user=u,
        store=store,
        notifications=notifications,
        active_notifications=active_notifications,
        notification_settings=notification_settings,
        notification_stats=_store_notification_stats(store["_id"]),
        **page_context
    )

@app.route('/store/notifications/toggle', methods=['POST'], endpoint='store_notifications_toggle')
@login_required(role='store')
def store_notifications_toggle():
    u, store = _get_current_store_or_redirect()

    if not store:
        return jsonify({"ok": False, "message": "Store not found"}), 404

    data = request.get_json(silent=True) or {}
    enabled = bool(data.get("enabled"))
    now = datetime.utcnow().isoformat()

    mongo.store_notification_settings.update_one(
        {"store_id": store["_id"]},
        {
            "$set": {
                "store_id": store["_id"],
                "enabled": enabled,
                "updated_at": now
            },
            "$setOnInsert": {
                "created_at": now
            }
        },
        upsert=True
    )

    _create_store_notification(
        store,
        title="Notifications enabled" if enabled else "Notifications disabled",
        message="Live order alerts were enabled for this store." if enabled else "Live order alerts were disabled for this store.",
        notif_type="system",
        event_key=f"notification-toggle-{store['_id']}-{now}"
    )

    return jsonify({
        "ok": True,
        "enabled": enabled,
        "stats": _store_notification_stats(store["_id"])
    })

@app.route('/store/notifications/poll', methods=['GET'], endpoint='store_notifications_poll')
@login_required(role='store')
def store_notifications_poll():
    u, store = _get_current_store_or_redirect()

    if not store:
        return jsonify({"ok": False, "notifications": []}), 404

    _sync_store_order_notifications(store)

    notifications = list(
        mongo.store_notifications.find({
            "store_id": {"$in": _store_id_values(store["_id"])}
        }).sort("created_at", -1).limit(20)
    )

    hydrated_notifications = []

    for n in notifications:
        row = _hydrate_store_notification(n)

        # IMPORTANT:
        # _hydrate_store_notification() adds string-safe fields,
        # but the original Mongo "_id": ObjectId(...) still remains in the dict.
        # Flask jsonify cannot serialize ObjectId, so remove the raw Mongo field.
        row.pop("_id", None)

        hydrated_notifications.append(row)

    return jsonify({
        "ok": True,
        "notifications": hydrated_notifications,
        "stats": _store_notification_stats(store["_id"])
    })

@app.route('/store/notifications/<nid>/read', methods=['POST'], endpoint='store_notification_mark_read')
@login_required(role='store')
def store_notification_mark_read(nid):
    u, store = _get_current_store_or_redirect()

    if not store:
        return jsonify({"ok": False}), 404

    try:
        nid_obj = ObjectId(nid)
    except Exception:
        return jsonify({"ok": False}), 400

    mongo.store_notifications.update_one(
        {
            "_id": nid_obj,
            "store_id": {"$in": _store_id_values(store["_id"])}
        },
        {
            "$set": {
                "is_read": True,
                "read_at": datetime.utcnow().isoformat(),
                "updated_at": datetime.utcnow().isoformat()
            }
        }
    )

    return jsonify({
        "ok": True,
        "stats": _store_notification_stats(store["_id"])
    })

@app.route('/store/notifications/read-all', methods=['POST'], endpoint='store_notifications_mark_all_read')
@login_required(role='store')
def store_notifications_mark_all_read():
    u, store = _get_current_store_or_redirect()

    if not store:
        return jsonify({"ok": False}), 404

    mongo.store_notifications.update_many(
        {
            "store_id": {"$in": _store_id_values(store["_id"])},
            "is_read": False
        },
        {
            "$set": {
                "is_read": True,
                "read_at": datetime.utcnow().isoformat(),
                "updated_at": datetime.utcnow().isoformat()
            }
        }
    )

    return jsonify({
        "ok": True,
        "stats": _store_notification_stats(store["_id"])
    })

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


@app.route('/store/categories/new', methods=['POST'], endpoint='store_category_new')
@login_required(role='store')
def store_category_new():
    u, store = _get_current_store_or_redirect()

    if not store:
        if _store_category_ajax_request():
            return jsonify({
                "ok": False,
                "message": "Store not found.",
                "feedback_type": "danger"
            }), 404

        return redirect(url_for("store_dashboard"))

    name = (request.form.get("name") or "").strip()
    sub_categories_raw = (request.form.get("sub_categories") or "").strip()

    if not name:
        return _store_category_response(
            "Category name is required.",
            "warning",
            400
        )

    slug = _category_slug(name)

    if not slug:
        return _store_category_response(
            "Enter a valid category name.",
            "warning",
            400
        )

    existing = mongo.store_categories.find_one({
        "store_id": store["_id"],
        "slug": slug
    })

    if existing:
        return _store_category_response(
            "This category already exists.",
            "warning",
            409
        )

    sub_categories = [
        item.strip()
        for item in sub_categories_raw.split(",")
        if item.strip()
    ]

    now = datetime.utcnow().isoformat()

    category_image_path = ""
    category_image = request.files.get("category_image")

    if category_image and category_image.filename:
        if not allowed_file(category_image.filename):
            return _store_category_response(
                "Only JPG, JPEG, PNG or WEBP images are allowed for category image.",
                "warning",
                400
            )

        category_image_path = _save_store_category_image(
            category_image,
            store["_id"],
            slug
        )

    insert_result = mongo.store_categories.insert_one({
        "store_id": store["_id"],
        "name": name,
        "slug": slug,
        "sub_categories": sub_categories,
        "image_path": category_image_path,
        "category_image_path": category_image_path,
        "emoji": "🛒",
        "is_active": 1,
        "is_default": 0,
        "created_at": now,
        "updated_at": now,
    })

    created_category = mongo.store_categories.find_one({
        "_id": insert_result.inserted_id,
        "store_id": store["_id"]
    })

    return _store_category_response(
        "Category added.",
        "success",
        200,
        {
            "category": _store_category_payload(
                created_category,
                store["_id"]
            )
        }
    )


@app.route('/store/categories/<cid>/update', methods=['POST'], endpoint='store_category_update')
@login_required(role='store')
def store_category_update(cid):
    u, store = _get_current_store_or_redirect()

    if not store:
        if _store_category_ajax_request():
            return jsonify({
                "ok": False,
                "message": "Store not found.",
                "feedback_type": "danger"
            }), 404

        return redirect(url_for("store_dashboard"))

    cat = _get_store_category_by_id(store["_id"], cid)

    if not cat:
        return _store_category_response(
            "Category not found.",
            "danger",
            404
        )

    old_name = cat.get("name", "")
    name = (request.form.get("name") or "").strip()
    sub_categories_raw = (request.form.get("sub_categories") or "").strip()

    if not name:
        return _store_category_response(
            "Category name is required.",
            "warning",
            400
        )

    slug = _category_slug(name)

    duplicate = mongo.store_categories.find_one({
        "_id": {"$ne": cat["_id"]},
        "store_id": store["_id"],
        "slug": slug
    })

    if duplicate:
        return _store_category_response(
            "Another category with this name already exists.",
            "warning",
            409
        )

    sub_categories = [
        item.strip()
        for item in sub_categories_raw.split(",")
        if item.strip()
    ]

    now = datetime.utcnow().isoformat()

    update_data = {
        "name": name,
        "slug": slug,
        "sub_categories": sub_categories,
        "updated_at": now,
    }

    category_image = request.files.get("category_image")

    if category_image and category_image.filename:
        if not allowed_file(category_image.filename):
            return _store_category_response(
                "Only JPG, JPEG, PNG or WEBP images are allowed for category image.",
                "warning",
                400
            )

        category_image_path = _save_store_category_image(
            category_image,
            store["_id"],
            slug
        )

        update_data["image_path"] = category_image_path
        update_data["category_image_path"] = category_image_path

    mongo.store_categories.update_one(
        {"_id": cat["_id"]},
        {
            "$set": update_data
        }
    )

    if old_name and old_name != name:
        mongo.products.update_many(
            {
                "store_id": store["_id"],
                "category": old_name
            },
            {
                "$set": {
                    "category": name,
                    "updated_at": now
                }
            }
        )

    updated_category = mongo.store_categories.find_one({
        "_id": cat["_id"],
        "store_id": store["_id"]
    })

    return _store_category_response(
        "Category updated.",
        "success",
        200,
        {
            "category": _store_category_payload(
                updated_category,
                store["_id"]
            )
        }
    )


@app.route('/store/categories/<cid>/toggle', methods=['POST'], endpoint='store_category_toggle')
@login_required(role='store')
def store_category_toggle(cid):
    u, store = _get_current_store_or_redirect()

    if not store:
        if _store_category_ajax_request():
            return jsonify({
                "ok": False,
                "message": "Store not found.",
                "feedback_type": "danger"
            }), 404

        return redirect(url_for("store_dashboard"))

    cat = _get_store_category_by_id(store["_id"], cid)

    if not cat:
        return _store_category_response(
            "Category not found.",
            "danger",
            404
        )

    new_status = 0 if int(cat.get("is_active") or 0) == 1 else 1
    now = datetime.utcnow().isoformat()

    mongo.store_categories.update_one(
        {"_id": cat["_id"]},
        {
            "$set": {
                "is_active": new_status,
                "updated_at": now
            }
        }
    )

    updated_category = mongo.store_categories.find_one({
        "_id": cat["_id"],
        "store_id": store["_id"]
    })

    return _store_category_response(
        "Category enabled." if new_status else "Category disabled.",
        "success",
        200,
        {
            "category": _store_category_payload(
                updated_category,
                store["_id"]
            )
        }
    )


@app.route('/store/categories/<cid>/delete', methods=['POST'], endpoint='store_category_delete')
@login_required(role='store')
def store_category_delete(cid):
    u, store = _get_current_store_or_redirect()

    if not store:
        if _store_category_ajax_request():
            return jsonify({
                "ok": False,
                "message": "Store not found.",
                "feedback_type": "danger"
            }), 404

        return redirect(url_for("store_dashboard"))

    cat = _get_store_category_by_id(store["_id"], cid)

    if not cat:
        return _store_category_response(
            "Category not found.",
            "danger",
            404
        )

    product_count = _get_category_product_count(
        store["_id"],
        cat.get("name")
    )

    if product_count > 0:
        mongo.store_categories.update_one(
            {"_id": cat["_id"]},
            {
                "$set": {
                    "is_active": 0,
                    "updated_at": datetime.utcnow().isoformat()
                }
            }
        )

        updated_category = mongo.store_categories.find_one({
            "_id": cat["_id"],
            "store_id": store["_id"]
        })

        return _store_category_response(
            "This category has products, so it was disabled instead of deleted.",
            "warning",
            200,
            {
                "deleted": False,
                "disabled": True,
                "category": _store_category_payload(
                    updated_category,
                    store["_id"]
                )
            }
        )

    mongo.store_categories.delete_one({"_id": cat["_id"]})

    return _store_category_response(
        "Category deleted.",
        "success",
        200,
        {
            "deleted": True,
            "disabled": False,
            "category_id": str(cat["_id"])
        }
    )


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


@app.route('/store/product-bundles', methods=['GET'], endpoint='store_product_bundles')
@login_required(role='store')
def store_product_bundles_page():
    u, store = _store_bundle_get_current_store()

    if not store:
        flash("Store not found.", "danger")
        return redirect(url_for("store_dashboard"))

    page_context = _store_bundle_page_context(store)

    return render_template(
        "store_product_bundles.html",
        user=u,
        store=store,
        **page_context
    )


@app.route('/store/product-bundles/new', methods=['POST'], endpoint='store_product_bundle_create')
@login_required(role='store')
def store_product_bundle_create():
    u, store = _store_bundle_get_current_store()

    if not store:
        flash("Store not found.", "danger")
        return redirect(url_for("store_dashboard"))

    product_ids = _store_bundle_product_ids_from_form(request.form)

    if len(product_ids) < 2:
        flash("Please select at least 2 products to create a bundle.", "warning")
        return redirect(url_for("store_product_bundles"))

    products = _store_bundle_products_for_store(store, product_ids)

    if len(products) != len(product_ids):
        flash("One or more selected products are invalid for this store.", "warning")
        return redirect(url_for("store_product_bundles"))

    image_path, image_error = _store_bundle_upload_image()

    if image_error:
        flash(image_error, "warning")
        return redirect(url_for("store_product_bundles"))

    quantities = _store_bundle_quantities_from_form(request.form, product_ids)
    bundle_doc = build_product_bundle_document(
        store,
        request.form,
        products,
        quantities_by_product_id=quantities,
        image_path=image_path or "",
        actor=u
    )

    if not bundle_doc.get("bundle_name"):
        flash("Bundle name is required.", "warning")
        return redirect(url_for("store_product_bundles"))

    if not bundle_doc.get("items") or len(bundle_doc.get("items")) < 2:
        flash("A bundle must contain at least 2 valid products.", "warning")
        return redirect(url_for("store_product_bundles"))

    mongo.product_bundles.insert_one(bundle_doc)

    flash("Product bundle created successfully.", "success")
    return redirect(url_for("store_product_bundles"))


@app.route('/store/product-bundles/<bundle_id>/edit', methods=['GET'], endpoint='store_product_bundle_edit')
@login_required(role='store')
def store_product_bundle_edit(bundle_id):
    u, store = _store_bundle_get_current_store()

    if not store:
        flash("Store not found.", "danger")
        return redirect(url_for("store_dashboard"))

    bid_obj, bundle = _store_bundle_find(store, bundle_id)

    if not bid_obj or not bundle:
        flash("Product bundle not found for your store.", "warning")
        return redirect(url_for("store_product_bundles"))

    page_context = _store_bundle_page_context(store, edit_bundle=bundle)

    return render_template(
        "store_product_bundles.html",
        user=u,
        store=store,
        **page_context
    )


@app.route('/store/product-bundles/<bundle_id>/edit', methods=['POST'], endpoint='store_product_bundle_update')
@login_required(role='store')
def store_product_bundle_update(bundle_id):
    u, store = _store_bundle_get_current_store()

    if not store:
        flash("Store not found.", "danger")
        return redirect(url_for("store_dashboard"))

    bid_obj, bundle = _store_bundle_find(store, bundle_id)

    if not bid_obj or not bundle:
        flash("Product bundle not found for your store.", "warning")
        return redirect(url_for("store_product_bundles"))

    product_ids = _store_bundle_product_ids_from_form(request.form)
    product_ids = normalize_bundle_product_ids(product_ids)

    if len(product_ids) < 2:
        flash("A bundle must contain at least 2 products.", "warning")
        return redirect(url_for("store_product_bundle_edit", bundle_id=bundle_id))

    products = _store_bundle_products_for_store(store, product_ids)

    if len(products) != len(product_ids):
        flash("One or more selected products are invalid for this store.", "warning")
        return redirect(url_for("store_product_bundle_edit", bundle_id=bundle_id))

    image_path, image_error = _store_bundle_upload_image()

    if image_error:
        flash(image_error, "warning")
        return redirect(url_for("store_product_bundle_edit", bundle_id=bundle_id))

    quantities = _store_bundle_quantities_from_form(request.form, product_ids)
    bundle_doc = build_product_bundle_document(
        store,
        request.form,
        products,
        quantities_by_product_id=quantities,
        existing_bundle=bundle,
        image_path=image_path,
        actor=u
    )

    if not bundle_doc.get("bundle_name"):
        flash("Bundle name is required.", "warning")
        return redirect(url_for("store_product_bundle_edit", bundle_id=bundle_id))

    if not bundle_doc.get("items") or len(bundle_doc.get("items")) < 2:
        flash("A bundle must contain at least 2 valid products.", "warning")
        return redirect(url_for("store_product_bundle_edit", bundle_id=bundle_id))

    mongo.product_bundles.update_one(
        {"_id": bid_obj},
        {"$set": bundle_doc}
    )

    flash("Product bundle updated successfully.", "success")
    return redirect(url_for("store_product_bundles"))


@app.route('/store/product-bundles/<bundle_id>/toggle', methods=['POST'], endpoint='store_product_bundle_toggle')
@login_required(role='store')
def store_product_bundle_toggle(bundle_id):
    u, store = _store_bundle_get_current_store()

    if not store:
        flash("Store not found.", "danger")
        return redirect(url_for("store_dashboard"))

    bid_obj, bundle = _store_bundle_find(store, bundle_id)

    if not bid_obj or not bundle:
        flash("Product bundle not found for your store.", "warning")
        return redirect(url_for("store_product_bundles"))

    current = int(bundle.get("is_active", 0) or 0)
    next_status = 0 if current == 1 else 1

    if next_status == 1:
        stock = calculate_bundle_stock(bundle.get("items") or [])
        if int(stock.get("max_bundle_stock") or 0) <= 0:
            flash("This bundle cannot be activated because one or more products are out of stock/inactive.", "warning")
            return redirect(url_for("store_product_bundles"))

    mongo.product_bundles.update_one(
        {"_id": bid_obj},
        {
            "$set": {
                "is_active": next_status,
                "updated_at": datetime.utcnow().isoformat(),
                "updated_by": str(u.get("_id") or u.get("id") or ""),
                "updated_by_name": u.get("name") or "Store User"
            }
        }
    )

    flash("Product bundle activated." if next_status else "Product bundle deactivated.", "success")
    return redirect(url_for("store_product_bundles"))


@app.route('/store/product-bundles/<bundle_id>/delete', methods=['POST'], endpoint='store_product_bundle_delete')
@login_required(role='store')
def store_product_bundle_delete(bundle_id):
    u, store = _store_bundle_get_current_store()

    if not store:
        flash("Store not found.", "danger")
        return redirect(url_for("store_dashboard"))

    bid_obj, bundle = _store_bundle_find(store, bundle_id)

    if not bid_obj or not bundle:
        flash("Product bundle not found for your store.", "warning")
        return redirect(url_for("store_product_bundles"))

    order_item_exists = mongo.order_items.find_one({
        "$or": [
            {"bundle_id": bid_obj},
            {"bundle_id": str(bid_obj)},
            {"bundle_id_str": str(bid_obj)}
        ]
    })

    if order_item_exists:
        mongo.product_bundles.update_one(
            {"_id": bid_obj},
            {
                "$set": {
                    "is_active": 0,
                    "is_deleted": 1,
                    "deleted_at": datetime.utcnow().isoformat(),
                    "updated_at": datetime.utcnow().isoformat(),
                    "deleted_by": str(u.get("_id") or u.get("id") or ""),
                    "deleted_by_name": u.get("name") or "Store User"
                }
            }
        )
        flash("This bundle has order history, so it was disabled instead of permanently deleted.", "warning")
    else:
        mongo.product_bundles.delete_one({"_id": bid_obj})
        flash("Product bundle deleted.", "success")

    return redirect(url_for("store_product_bundles"))

@app.route('/store/product/new', methods=['POST'])
@app.route('/store/products/new', methods=['POST'])
@login_required(role='store')
def store_product_new():
    u = current_user()

    store = mongo.stores.find_one({"user_id": u["id"]})
    if not store:
        flash("Store not found.", "danger")
        return redirect(url_for("store_dashboard"))

    sid = store["_id"]

    name = request.form.get('name', '').strip()

    pricing = build_unit_product_update_from_form(request.form)

    price_per_unit = pricing["price_per_unit"]
    original_price_per_unit = pricing["original_price_per_unit"]
    stock_quantity = pricing["stock_quantity"]

    category_id = (request.form.get("category_id") or "").strip()
    category = (request.form.get("category") or "").strip()
    sub_category = (request.form.get("sub_category") or "").strip()

    category_doc = None

    if category_id:
        category_doc = _get_store_category_by_id(sid, category_id, active_only=True)

    if not category_doc and category:
        category_doc = _get_store_category_by_name(sid, category, active_only=True)

    if not category_doc:
        flash("Please select a valid active category.", "warning")
        return redirect(url_for("store_add_product"))

    category = category_doc.get("name")
    category_id = str(category_doc["_id"])

    allowed_subs = category_doc.get("sub_categories") or []

    if not name:
        flash('Product name is required.', 'warning')
        return redirect(url_for('store_add_product'))

    if original_price_per_unit <= 0:
        flash('Price must be greater than 0.', 'warning')
        return redirect(url_for('store_add_product'))

    if price_per_unit <= 0:
        flash('Final selling price must be greater than 0.', 'warning')
        return redirect(url_for('store_add_product'))

    if stock_quantity < 0:
        flash('Stock cannot be negative.', 'warning')
        return redirect(url_for('store_add_product'))

    if allowed_subs:
        if sub_category not in allowed_subs:
            flash("Please select a valid sub-category.", "warning")
            return redirect(url_for("store_add_product"))
    else:
        sub_category = None

    image = request.files.get('image')
    image_path = None
    thumbnail_path = None

    if image and image.filename:
        if allowed_file(image.filename):
            fn = secure_filename(image.filename)
            save_as = datetime.utcnow().strftime("%Y%m%d%H%M%S_") + fn
            os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
            image.save(os.path.join(app.config["UPLOAD_FOLDER"], save_as))
            image_path = f"uploads/{save_as}"
            thumbnail_path = _store_generate_product_card_thumbnail(image_path)
        else:
            flash("Invalid image file type.", "warning")
            return redirect(url_for("store_add_product"))

    now = datetime.utcnow().isoformat()
    shipping_package = parse_product_shipping_package_from_form(request.form)

    mongo.products.insert_one({
        "store_id": sid,
        "store_name": store.get("store_name", ""),

        "name": name,

        "unit_type": pricing["unit_type"],
        "unit_label": pricing["unit_label"],

        "original_price_per_unit": pricing["original_price_per_unit"],
        "price_per_unit": pricing["price_per_unit"],
        "mrp_per_unit": pricing["mrp_per_unit"],
        "stock_quantity": pricing["stock_quantity"],

        "original_price_per_unit": original_price_per_unit,
        "price_per_unit": price_per_unit,
        "mrp_per_unit": pricing["mrp_per_unit"],

        "discount_enabled": pricing["discount_enabled"],
        "discount_type": pricing["discount_type"],
        "discount_value": pricing["discount_value"],
        "discount_amount_per_unit": pricing["discount_amount_per_unit"],
        "discount_percent": pricing["discount_percent"],

        "stock_quantity": stock_quantity,
        "quantity_min": pricing["quantity_min"],
        "quantity_step": pricing["quantity_step"],
        "quantity_message": pricing["quantity_message"],

        "shipping_weight_kg": shipping_package["shipping_weight_kg"],
        "shipping_length_cm": shipping_package["shipping_length_cm"],
        "shipping_breadth_cm": shipping_package["shipping_breadth_cm"],
        "shipping_height_cm": shipping_package["shipping_height_cm"],

        "category_id": category_id,
        "category": category,
        "sub_category": sub_category,

        "image_path": image_path,
        "thumbnail_path": thumbnail_path,
        "is_active": 1 if stock_quantity > 0 else 0,

        "created_at": now,
        "updated_at": now
    })

    flash("Product added successfully.", "success")
    return redirect(url_for("store_products"))

@app.route('/store/product/<pid>/toggle', methods=['POST'])
@login_required(role='store')
def store_product_toggle(pid):
    u = current_user()

    store = mongo.stores.find_one({"user_id": u["id"]})
    if not store:
        flash("Store not found.", "danger")
        return redirect(url_for("store_dashboard"))

    try:
        pid_obj = ObjectId(pid)
    except Exception:
        flash("Invalid product.", "danger")
        return redirect(url_for("store_dashboard"))

    product = mongo.products.find_one({
        "_id": pid_obj,
        "store_id": store["_id"]
    })

    if not product:
        flash("Product not found.", "danger")
        return redirect(url_for("store_dashboard"))

    current_active = int(product.get("is_active") or 0)
    new_active = 0 if current_active == 1 else 1

    mongo.products.update_one(
        {"_id": pid_obj},
        {
            "$set": {
                "is_active": new_active,
                "updated_at": datetime.utcnow().isoformat()
            }
        }
    )

    flash("Product status updated.", "success")
    return redirect(url_for("store_products"))

@app.route('/store/product/<pid>/delete', methods=['POST'])
@login_required(role='store')
def store_product_delete(pid):
    u = current_user()

    store = mongo.stores.find_one({"user_id": u["id"]})
    if not store:
        flash("Store not found.", "danger")
        return redirect(url_for("store_dashboard"))

    try:
        pid_obj = ObjectId(pid)
    except Exception:
        flash("Invalid product.", "danger")
        return redirect(url_for("store_dashboard"))

    product = mongo.products.find_one({
        "_id": pid_obj,
        "store_id": store["_id"]
    })

    if not product:
        flash("Product not found.", "danger")
        return redirect(url_for("store_dashboard"))

    order_item_exists = mongo.order_items.find_one({"product_id": pid_obj})

    if order_item_exists:
        mongo.products.update_one(
            {"_id": pid_obj},
            {
                "$set": {
                    "is_active": 0,
                    "deleted_at": datetime.utcnow().isoformat(),
                    "updated_at": datetime.utcnow().isoformat()
                }
            }
        )
        flash("Product has order history, so it was disabled instead of deleted.", "warning")
    else:
        mongo.products.delete_one({"_id": pid_obj})
        flash("Product deleted.", "success")

    return redirect(url_for("store_products"))

@app.route('/store/product/<pid>/stock/add', methods=['POST'], endpoint='store_product_add_stock')
@login_required(role='store')
def store_product_add_stock(pid):
    u = current_user()

    store = mongo.stores.find_one({"user_id": u["id"]})
    if not store:
        flash("Store not found.", "danger")
        return redirect(url_for("store_dashboard"))

    try:
        pid_obj = ObjectId(pid)
    except Exception:
        flash("Invalid product.", "danger")
        return redirect(url_for("store_dashboard"))

    product = mongo.products.find_one({
        "_id": pid_obj,
        "store_id": store["_id"]
    })

    if not product:
        flash("Product not found for your store.", "warning")
        return redirect(url_for("store_dashboard"))

    try:
        add_kg = float(request.form.get("add_kg", "0") or 0)
    except ValueError:
        add_kg = 0.0

    if add_kg <= 0:
        flash("Enter a positive stock amount.", "warning")
        return redirect(url_for("store_dashboard"))

    mongo.products.update_one(
        {"_id": pid_obj},
        {
            "$inc": {"stock_quantity": add_kg},
            "$set": {
                "is_active": 1,
                "updated_at": datetime.utcnow().isoformat()
            }
        }
    )

    flash(f"Added {add_kg:.2f} kg to stock.", "success")
    return redirect(url_for("store_dashboard"))

@app.route('/store/product/<pid>/edit', methods=['GET'], endpoint='store_product_edit')
@login_required(role='store')
def store_product_edit(pid):
    u = current_user()

    store = mongo.stores.find_one({"user_id": u["id"]})
    if not store:
        flash("Store not found.", "danger")
        return redirect(url_for("store_dashboard"))

    try:
        pid_obj = ObjectId(pid)
    except Exception:
        flash("Invalid product.", "danger")
        return redirect(url_for("store_dashboard"))

    product = mongo.products.find_one({
        "_id": pid_obj,
        "store_id": store["_id"]
    })

    if not product:
        flash("Product not found for your store.", "warning")
        return redirect(url_for("store_dashboard"))

    product["id"] = str(product["_id"])
    product["store_id"] = str(product.get("store_id")) if product.get("store_id") else ""
    hydrate_product_unit_fields(product)

    active_categories = _get_store_categories(store["_id"], active_only=True)

    return render_template(
        "store_product_edit.html",
        user=u,
        store=store,
        product=product,
        active_categories=active_categories,
        unit_options=UNIT_OPTIONS,
        unit_type_labels=UNIT_TYPE_LABELS
    )

@app.route('/store/product/<pid>/edit', methods=['POST'], endpoint='store_product_update')
@login_required(role='store')
def store_product_update(pid):
    u = current_user()

    store = mongo.stores.find_one({"user_id": u["id"]})
    if not store:
        flash("Store not found.", "danger")
        return redirect(url_for("store_dashboard"))

    try:
        pid_obj = ObjectId(pid)
    except Exception:
        flash("Invalid product.", "danger")
        return redirect(url_for("store_dashboard"))

    product = mongo.products.find_one({
        "_id": pid_obj,
        "store_id": store["_id"]
    })

    if not product:
        flash("Product not found for your store.", "warning")
        return redirect(url_for("store_dashboard"))

    name = (request.form.get("name") or "").strip()

    submitted_category_id = (request.form.get("category_id") or "").strip()
    submitted_category_name = (request.form.get("category") or "").strip()
    submitted_sub_category = (request.form.get("sub_category") or "").strip()

    current_category_id = str(product.get("category_id") or "").strip()
    current_category_name = (product.get("category") or "").strip()
    current_sub_category = (product.get("sub_category") or "").strip()

    category_doc = None

    if submitted_category_id:
        category_doc = _get_store_category_by_id(
            store["_id"],
            submitted_category_id,
            active_only=True
        )

    if not category_doc and submitted_category_name:
        category_doc = _get_store_category_by_name(
            store["_id"],
            submitted_category_name,
            active_only=True
        )

    category_was_changed = bool(
        submitted_category_id
        and submitted_category_id != current_category_id
    )

    # Preserve an unchanged existing category even if that category was
    # disabled later. Unrelated edits must still be saveable.
    if not category_doc and not category_was_changed:
        category_or_conditions = []

        if current_category_id:
            try:
                category_or_conditions.append({
                    "_id": ObjectId(current_category_id)
                })
            except Exception:
                category_or_conditions.append({
                    "_id": current_category_id
                })

        if current_category_name:
            category_or_conditions.append({
                "name": {
                    "$regex": f"^{re.escape(current_category_name)}$",
                    "$options": "i"
                }
            })

        if category_or_conditions:
            category_doc = mongo.store_categories.find_one({
                "store_id": store["_id"],
                "$or": category_or_conditions
            })

    if not category_doc:
        flash("Please select a valid category before saving.", "warning")
        return redirect(url_for("store_product_edit", pid=pid))

    category = (
        category_doc.get("name")
        or current_category_name
    ).strip()

    category_id = str(
        category_doc.get("_id")
        or current_category_id
    )

    allowed_subs = category_doc.get("sub_categories") or []
    sub_category = submitted_sub_category or current_sub_category

    if allowed_subs:
        if sub_category not in allowed_subs:
            flash("Please select a valid sub-category.", "warning")
            return redirect(url_for("store_product_edit", pid=pid))
    else:
        sub_category = None

    fallback_original_price = product_original_price_per_unit(product)

    try:
        pricing = build_unit_product_update_from_form(
            request.form,
            fallback_original_price=fallback_original_price
        )
    except Exception:
        app.logger.exception(
            "Failed to parse product update form for product %s",
            pid
        )
        flash(
            "The product values could not be processed. Please check the entered values.",
            "danger"
        )
        return redirect(url_for("store_product_edit", pid=pid))

    price = pricing["price_per_unit"]
    original_price = pricing["original_price_per_unit"]
    stock = pricing["stock_quantity"]

    if not name:
        flash("Product name is required.", "warning")
        return redirect(url_for("store_product_edit", pid=pid))

    if original_price < 0:
        flash("Enter a valid non-negative price.", "warning")
        return redirect(url_for("store_product_edit", pid=pid))

    if original_price <= 0:
        flash("Price must be greater than 0.", "warning")
        return redirect(url_for("store_product_edit", pid=pid))

    if price <= 0:
        flash("Final selling price must be greater than 0.", "warning")
        return redirect(url_for("store_product_edit", pid=pid))

    if stock < 0:
        flash("Enter a valid non-negative stock.", "warning")
        return redirect(url_for("store_product_edit", pid=pid))

    shipping_package = parse_product_shipping_package_from_form(
        request.form,
        product
    )

    update_data = {
        "name": name,
        "unit_type": pricing["unit_type"],
        "unit_label": pricing["unit_label"],
        "original_price_per_unit": original_price,
        "price_per_unit": price,
        "mrp_per_unit": pricing["mrp_per_unit"],
        "discount_enabled": pricing["discount_enabled"],
        "discount_type": pricing["discount_type"],
        "discount_value": pricing["discount_value"],
        "discount_amount_per_unit": pricing["discount_amount_per_unit"],
        "discount_percent": pricing["discount_percent"],
        "stock_quantity": stock,
        "quantity_min": pricing["quantity_min"],
        "quantity_step": pricing["quantity_step"],
        "quantity_message": pricing["quantity_message"],
        "shipping_weight_kg": shipping_package["shipping_weight_kg"],
        "shipping_length_cm": shipping_package["shipping_length_cm"],
        "shipping_breadth_cm": shipping_package["shipping_breadth_cm"],
        "shipping_height_cm": shipping_package["shipping_height_cm"],
        "category_id": category_id,
        "category": category,
        "sub_category": sub_category,
        "is_active": 1 if stock > 0 else int(product.get("is_active") or 0),
        "updated_at": datetime.utcnow().isoformat()
    }

    image = request.files.get("image")
    if image and image.filename:
        if not allowed_file(image.filename):
            flash("Invalid image file type.", "warning")
            return redirect(url_for("store_product_edit", pid=pid))

        fn = secure_filename(image.filename)
        save_as = datetime.utcnow().strftime("%Y%m%d%H%M%S_") + fn
        os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
        image.save(os.path.join(app.config["UPLOAD_FOLDER"], save_as))
        update_data["image_path"] = f"uploads/{save_as}"

        generated_thumbnail_path = _store_generate_product_card_thumbnail(
            update_data["image_path"]
        )

        if generated_thumbnail_path:
            update_data["thumbnail_path"] = generated_thumbnail_path

    try:
        update_result = mongo.products.update_one(
            {
                "_id": pid_obj,
                "store_id": store["_id"]
            },
            {
                "$set": update_data
            }
        )
    except Exception:
        app.logger.exception(
            "Failed to update product %s for store %s",
            pid,
            store.get("_id")
        )
        flash(
            "The product could not be saved because of a database error.",
            "danger"
        )
        return redirect(url_for("store_product_edit", pid=pid))

    if update_result.matched_count != 1:
        flash("The product could not be found while saving.", "danger")
        return redirect(url_for("store_products"))

    if update_result.modified_count == 0:
        flash("No product values were changed.", "info")
    else:
        flash("Product updated successfully.", "success")

    return redirect(url_for("store_product_edit", pid=pid))

@app.route('/store/transactions.csv')
@login_required(role='store')
def store_txn_csv():
    """
    Download transactions for this store as CSV.
    Supported presets via ?range=day|week|month.
    You can also pass explicit ?start=YYYY-MM-DD&end=YYYY-MM-DD.
    Only PAID transactions are included.
    """
    u = current_user()

    store = mongo.stores.find_one({"user_id": u["id"]})

    if not store:
        flash("Store not found.", "danger")
        return redirect(url_for("store_dashboard"))

    preset = (request.args.get("range") or "").lower()
    start_str = request.args.get("start")
    end_str = request.args.get("end")

    if start_str and end_str:
        try:
            start_date = date.fromisoformat(start_str)
            end_date = date.fromisoformat(end_str)
        except Exception:
            flash("Invalid start/end date. Use YYYY-MM-DD.", "warning")
            return redirect(url_for("store_dashboard"))
    else:
        today = datetime.utcnow().date()

        if preset == "week":
            start_date = today - timedelta(days=today.weekday())
            end_date = start_date + timedelta(days=7)
        elif preset == "month":
            start_date = date(today.year, today.month, 1)

            if today.month == 12:
                end_date = date(today.year + 1, 1, 1)
            else:
                end_date = date(today.year, today.month + 1, 1)
        else:
            start_date = today
            end_date = today + timedelta(days=1)

    start_iso = f"{start_date.isoformat()}T00:00:00"
    end_iso = f"{end_date.isoformat()}T00:00:00"

    txns = list(
        mongo.transactions.find({
            "status": "PAID",
            "created_at": {
                "$gte": start_iso,
                "$lt": end_iso
            }
        }).sort("created_at", -1)
    )

    csv_lines = [
        "txn_id,txn_created_at,order_id,items_total,delivery_fee,tip_amount,paid_amount,txn_status"
    ]

    for t in txns:
        order_id = t.get("order_id")
        order = None

        if order_id:
            order = mongo.orders.find_one({
                "_id": order_id,
                "store_id": store["_id"]
            })

        if not order:
            continue

        csv_lines.append(",".join([
            str(t.get("_id", "")),
            str(t.get("created_at", "")),
            str(order.get("_id", "")),
            str(float(order.get("total_amount") or 0)),
            str(float(order.get("delivery_fee") or 0)),
            str(float(order.get("tip_amount") or 0)),
            str(float(t.get("amount") or 0)),
            str(t.get("status", "")),
        ]))

    data = "\n".join(csv_lines).encode("utf-8")

    return send_file(
        io.BytesIO(data),
        mimetype="text/csv",
        as_attachment=True,
        download_name="store_transactions.csv"
    )


@app.route("/store/cancelled-orders")
@app.route("/store/cancelled-orders/<cancel_type>")
@login_required(role="store")
def store_cancelled_orders(cancel_type="customer"):
    u = current_user()

    store = mongo.stores.find_one({
        "user_id": u["id"]
    })

    if not store:
        flash("Store not found.", "danger")
        return redirect(url_for("store_dashboard"))

    cancel_type = (cancel_type or "customer").strip().lower()

    if cancel_type not in ["customer", "store"]:
        cancel_type = "customer"

    store_id_values = [
        store["_id"],
        str(store["_id"])
    ]

    base_query = {
        "$and": [
            {
                "store_id": {
                    "$in": store_id_values
                }
            },
            {
                "status": {
                    "$in": ["CANCELLED", "CANCELED"]
                }
            },
            _store_cancelled_source_query(cancel_type)
        ]
    }

    cancelled_orders = list(
        mongo.orders.find(base_query).sort("cancelled_at", -1)
    )

    prepared_orders = [
        _store_prepare_cancelled_order_row(order, store)
        for order in cancelled_orders
    ]

    customer_cancelled_count = mongo.orders.count_documents({
        "$and": [
            {
                "store_id": {
                    "$in": store_id_values
                }
            },
            {
                "status": {
                    "$in": ["CANCELLED", "CANCELED"]
                }
            },
            _store_cancelled_source_query("customer")
        ]
    })

    store_cancelled_count = mongo.orders.count_documents({
        "$and": [
            {
                "store_id": {
                    "$in": store_id_values
                }
            },
            {
                "status": {
                    "$in": ["CANCELLED", "CANCELED"]
                }
            },
            _store_cancelled_source_query("store")
        ]
    })

    total_cancelled_count = customer_cancelled_count + store_cancelled_count

    total_cancelled_value = round(
        sum(float(order.get("total_payable") or 0) for order in prepared_orders),
        2
    )

    online_refund_pending_count = sum(
        1
        for order in prepared_orders
        if order.get("refund_status") in [
            "READY_FOR_REFUND",
            "REFUND_PENDING",
            "NOT_STARTED",
            "PENDING"
        ]
    )

    cod_void_count = sum(
        1
        for order in prepared_orders
        if order.get("payment_method") in [
            "COD",
            "CASH_ON_DELIVERY",
            "COD_RIDER_COLLECTION"
        ]
        and order.get("payment_status") in [
            "VOID",
            "CANCELLED",
            "PENDING"
        ]
    )

    return render_template(
        "store_cancelled_orders.html",
        user=u,
        store=store,
        orders=prepared_orders,
        cancel_type=cancel_type,
        customer_cancelled_count=customer_cancelled_count,
        store_cancelled_count=store_cancelled_count,
        total_cancelled_count=total_cancelled_count,
        current_cancelled_count=len(prepared_orders),
        total_cancelled_value=total_cancelled_value,
        online_refund_pending_count=online_refund_pending_count,
        cod_void_count=cod_void_count
    )


@app.route('/store/order/<oid>/status', methods=['POST'])
@app.route('/store/orders/<oid>/status', methods=['POST'])
@login_required(role='store')
def store_order_status(oid):
    u = current_user()

    store = mongo.stores.find_one({"user_id": u["id"]})

    if not store:
        flash("Store not found.", "danger")
        return redirect(url_for("store_dashboard"))

    try:
        oid_obj = ObjectId(oid)
    except Exception:
        flash("Invalid order.", "danger")
        return redirect(url_for("store_orders"))

    order = mongo.orders.find_one({
        "_id": oid_obj,
        "$or": [
            {"store_id": store["_id"]},
            {"store_id": str(store["_id"])}
        ]
    })

    if not order:
        flash("Order not found.", "danger")
        return redirect(url_for("store_orders"))

    current_status = (order.get("status") or "").strip().upper()
    new_status = (request.form.get("status") or "").strip().upper()
    now = datetime.utcnow().isoformat()

    if current_status in {"CANCELLED", "CANCELED", "DELIVERED"}:
        flash("This order can no longer be updated.", "warning")
        return redirect(url_for("store_orders"))

    # Delivery workflow statuses must not be controlled by the normal order dropdown.
    delivery_locked_statuses = {
        "READY_FOR_PICKUP",
        "SHIPMENT_READY",
        "ASSIGNED_TO_DELIVERY",
        "ACCEPTED_BY_DELIVERY_MAN",
        "REACHED_STORE",
        "PICKED_UP",
        "OUT_FOR_DELIVERY",
        "DELIVERY_FAILED",
        "DELIVERED"
    }

    if current_status in delivery_locked_statuses:
        flash("This order is controlled by the delivery workflow. Use Delivery Control actions.", "warning")
        return redirect(url_for("store_orders"))

    allowed_statuses = {
        "PLACED",
        "CONFIRMED",
        "PREPARING",
        "PACKAGING",
        "CANCELLED",
    }

    if new_status not in allowed_statuses:
        flash("Invalid order status selected.", "warning")
        return redirect(url_for("store_orders"))

    update_data = {
        "status": new_status,
        "updated_at": now
    }

    unset_data = {}

    if new_status == "PREPARING":
        update_data["preparing_at"] = now

    if new_status == "PACKAGING":
        update_data["packaging_at"] = now

    if new_status == "CANCELLED":
        # Restore stock only once because already-cancelled orders are blocked above.
        order_items = list(mongo.order_items.find({
            "$or": [
                {"order_id": oid_obj},
                {"order_id": str(oid_obj)}
            ]
        }))

        for line in order_items:
            product_id = line.get("product_id")
            restore_qty = 0

            try:
                restore_qty = float(line.get("quantity") or line.get("cart_quantity") or 0)
            except Exception:
                restore_qty = 0

            if product_id and restore_qty > 0:
                product_query_values = [product_id]

                try:
                    if ObjectId.is_valid(str(product_id)):
                        product_query_values.append(ObjectId(str(product_id)))
                except Exception:
                    pass

                mongo.products.update_one(
                    {"_id": {"$in": product_query_values}},
                    {
                        "$inc": {
                            "stock_quantity": restore_qty
                        },
                        "$set": {
                            "is_active": 1,
                            "updated_at": now
                        }
                    }
                )

        payment_method = (order.get("payment_method") or "COD").strip().upper()
        payment_status = (order.get("payment_status") or "PENDING").strip().upper()

        is_cod_order = payment_method in {
            "COD",
            "CASH_ON_DELIVERY",
            "COD_RIDER_COLLECTION"
        }

        is_online_paid = (
            payment_method not in {
                "COD",
                "CASH_ON_DELIVERY",
                "COD_RIDER_COLLECTION"
            }
            and payment_status in {
                "PAID",
                "ONLINE_PAID",
                "SUCCESS"
            }
        )

        update_data.update({
            "status": "CANCELLED",
            "cancelled_at": now,
            "cancelled_by": "store",
            "cancelled_by_role": "store",
            "cancelled_by_id": str(u.get("_id") or u.get("id")),
            "cancelled_by_name": u.get("name") or "Store User",
            "cancel_reason": request.form.get("cancel_reason") or "Cancelled by store",
            "cancellation_reason": request.form.get("cancel_reason") or "Cancelled by store",

            # Make sure order does not remain in active delivery/store queue.
            "delivery_status": "CANCELLED",
            "delivery_fulfillment_status": "CANCELLED",
            "ready_for_pickup": False,
            "shipment_ready": False,
            "needs_reassignment": False,
            "delivery_cancelled_by_partner": False,

            # Internal delivery settlement should not remain active.
            "delivery_boy_earning": 0,
            "delivery_boy_payout_amount": 0,
            "delivery_boy_payout_status": "NOT_REQUIRED",
            "rider_cash_to_submit": 0,
            "expected_rider_cash_to_submit": 0,
            "rider_cash_settlement_status": "NOT_REQUIRED",
            "cod_collection_status": "NOT_REQUIRED",

            "updated_at": now
        })

        if is_online_paid:
            update_data.update({
                "refund_status": "READY_FOR_REFUND",
                "refund_reason": "STORE_CANCELLED_BEFORE_DELIVERY",
                "order_settlement_status": "REFUND_PENDING",
                "payment_collection_status": "PAID_REFUND_PENDING",
                "transaction_status": "REFUND_PENDING"
            })
        elif is_cod_order:
            update_data.update({
                "payment_status": "VOID",
                "refund_status": "NOT_REQUIRED",
                "refund_reason": "COD_CANCELLED_BEFORE_PAYMENT",
                "order_settlement_status": "CANCELLED_VOID",
                "payment_collection_status": "VOID",
                "transaction_status": "VOID",
                "platform_fee_status": "NOT_REQUIRED",
                "store_payout_status": "NOT_REQUIRED"
            })
        else:
            update_data.update({
                "refund_status": "NOT_REQUIRED",
                "order_settlement_status": "CANCELLED",
                "payment_collection_status": "CANCELLED",
                "transaction_status": "CANCELLED"
            })

        unset_data.update({
            "delivery_partner_id": "",
            "delivery_partner_name": "",
            "delivery_partner_phone": "",
            "delivery_assignment_source": "",
            "assigned_at": "",
            "delivery_assigned_at": "",
            "reached_store_at": "",
            "picked_up_at": "",
            "out_for_delivery_at": "",
            "shipment_ready_at": "",
            "ready_for_pickup_at": ""
        })

    update_payload = {
        "$set": update_data
    }

    if unset_data:
        update_payload["$unset"] = unset_data

    mongo.orders.update_one(
        {
            "_id": oid_obj,
            "$or": [
                {"store_id": store["_id"]},
                {"store_id": str(store["_id"])}
            ]
        },
        update_payload
    )

    if new_status == "CANCELLED":
        # Stop any active delivery notifications for this order.
        mongo.delivery_notifications.update_many(
            {
                "$or": [
                    {"order_id": oid_obj},
                    {"order_id": str(oid_obj)}
                ]
            },
            {
                "$set": {
                    "is_active": False,
                    "updated_at": now,
                    "closed_reason": "ORDER_CANCELLED_BY_STORE"
                }
            }
        )

        # Keep transactions aligned for reports.
        mongo.transactions.update_many(
            {
                "$or": [
                    {"order_id": oid_obj},
                    {"order_id": str(oid_obj)}
                ]
            },
            {
                "$set": {
                    "status": update_data.get("transaction_status", "CANCELLED"),
                    "payment_status": update_data.get("payment_status", payment_status),
                    "payment_collection_status": update_data.get("payment_collection_status", "CANCELLED"),
                    "order_settlement_status": update_data.get("order_settlement_status", "CANCELLED"),
                    "refund_status": update_data.get("refund_status", "NOT_REQUIRED"),
                    "updated_at": now
                }
            }
        )

    add_order_event(
        oid_obj,
        new_status,
        "Cancelled by store" if new_status == "CANCELLED" else "Updated by store",
        u
    )

    _create_store_notification(
        store,
        title="Order cancelled" if new_status == "CANCELLED" else "Order status updated",
        message=(
            f"Order #{str(order['_id'])[-6:]} was cancelled by store."
            if new_status == "CANCELLED"
            else f"Order #{str(order['_id'])[-6:]} status changed to {new_status}."
        ),
        notif_type="order",
        order=order,
        event_key=f"store-status-{str(order['_id'])}-{new_status}-{now}"
    )

    if new_status == "CANCELLED":
        flash("Order cancelled successfully and removed from active queue.", "success")
    else:
        flash("Order status updated successfully.", "success")

    return redirect(url_for("store_orders"))
