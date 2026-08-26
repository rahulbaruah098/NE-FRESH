"""Products routes extracted from the updated app.py.

Logic, decorators, endpoint names and route paths are intentionally preserved.
Only the file location changed.
"""

from app_core import *
import secrets


def _products_batch_rating_stats(product_ids):
    stats = {}

    if not product_ids:
        return stats

    ratings = mongo.product_ratings.find(
        {"product_id": {"$in": product_ids}},
        {"product_id": 1, "rating": 1}
    )

    for rating in ratings:
        key = str(rating.get("product_id") or "")
        row = stats.setdefault(key, {"count": 0, "total": 0.0})
        row["count"] += 1

        try:
            row["total"] += float(rating.get("rating") or 0)
        except (TypeError, ValueError):
            pass

    return stats


def _products_batch_store_documents(store_ids, include_object_id_fallback=False):
    query_values = []
    seen = set()

    def add_value(value):
        if value in (None, ""):
            return

        marker = (type(value).__name__, str(value))
        if marker in seen:
            return

        seen.add(marker)
        query_values.append(value)

    for store_id in store_ids:
        add_value(store_id)

        if include_object_id_fallback:
            try:
                if ObjectId.is_valid(str(store_id)):
                    add_value(ObjectId(str(store_id)))
            except Exception:
                pass

    stores = {}

    if query_values:
        for store in mongo.stores.find({"_id": {"$in": query_values}}):
            store_id = store.get("_id")
            stores[(type(store_id).__name__, str(store_id))] = store
            stores.setdefault(("text", str(store_id)), store)

    return stores


def _products_store_from_batch(store_map, store_id, allow_text_fallback=False):
    if store_id in (None, ""):
        return None

    store = store_map.get((type(store_id).__name__, str(store_id)))

    if not store and allow_text_fallback:
        store = store_map.get(("text", str(store_id)))

    return store


def _products_batch_store_rating_stats(stores):
    stats = {}
    store_docs = {}

    for store in stores.values():
        if not isinstance(store, dict) or not store.get("_id"):
            continue
        store_docs[str(store["_id"])] = store

    if not store_docs:
        return stats

    rating_store_values = []
    seen = set()

    for store in store_docs.values():
        for value in [store["_id"], str(store["_id"])]:
            marker = (type(value).__name__, str(value))
            if marker in seen:
                continue
            seen.add(marker)
            rating_store_values.append(value)

    ratings = mongo.store_ratings.find(
        {"store_id": {"$in": rating_store_values}},
        {"store_id": 1, "rating": 1}
    )

    for rating in ratings:
        key = str(rating.get("store_id") or "")
        row = stats.setdefault(key, {"count": 0, "total": 0.0})
        row["count"] += 1

        try:
            row["total"] += float(rating.get("rating") or 0)
        except (TypeError, ValueError):
            pass

    return stats


@app.route('/products')
def products():
    allow, pin = _session_pin_is_serviceable()

    if session.get("service_area") and not allow:
        flash("Please enter a valid 6-digit pincode.", "warning")
        products = []
        product_bundles = []
    else:
        products = list(mongo.products.find({
        "is_active": 1
        }).sort("created_at", -1))

        product_bundles = list(
            mongo.product_bundles.find({
                "$and": [
                    {
                        "$or": [
                            {"is_active": 1},
                            {"is_active": True}
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

    u = current_user()

    cart_lookup = {}
    bundle_cart_lookup = {}

    if u and u.get("role") == "customer":
        cid = get_or_create_cart(u["id"])

        cart_items = list(mongo.cart_items.find({
            "cart_id": cid
        }))

        for ci in cart_items:
            item_type = (ci.get("item_type") or "product").strip().lower()

            if item_type == "bundle" or ci.get("bundle_id"):
                bundle_id_value = ci.get("bundle_id") or ci.get("bundle_id_str")

                if bundle_id_value:
                    bundle_cart_lookup[str(bundle_id_value)] = {
                        "cart_item_id": str(ci.get("_id")),
                        "cart_quantity": cart_item_quantity(ci)
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

    product_ids = [p.get("_id") for p in products if p.get("_id") is not None]
    product_rating_stats = _products_batch_rating_stats(product_ids)

    product_store_ids = [
        p.get("store_id")
        for p in products
        if p.get("store_id") not in (None, "")
    ]
    product_store_map = _products_batch_store_documents(product_store_ids)
    product_store_rating_stats = _products_batch_store_rating_stats(product_store_map)

    for p in products:
        p["id"] = str(p["_id"])
        hydrate_product_unit_fields(p)

        cart_info = cart_lookup.get(str(p["_id"]))

        if cart_info:
            p["in_cart"] = True
            p["cart_item_id"] = cart_info.get("cart_item_id", "")
            p["cart_quantity"] = cart_info.get("cart_quantity", p.get("quantity_min") or 1)
        else:
            p["in_cart"] = False
            p["cart_item_id"] = ""
            p["cart_quantity"] = 0

        # Prevent Jinja sort/groupby crash when MongoDB has null category fields
        p["category"] = (p.get("category") or "Uncategorized").strip()
        p["sub_category"] = (p.get("sub_category") or "").strip()

        rating_row = product_rating_stats.get(str(p["_id"]), {})
        rating_count = int(rating_row.get("count") or 0)
        rating_total = float(rating_row.get("total") or 0)
        p["avg_rating"] = round(rating_total / rating_count, 1) if rating_count else 0
        p["rating_count"] = rating_count

        raw_store_id = p.get("store_id")
        store = _products_store_from_batch(product_store_map, raw_store_id)

        p["store_name"] = store.get("store_name") if store else ""
        p["store_id"] = str(raw_store_id) if raw_store_id else ""

        if store:
            p["store_address"] = store.get("address", "")
            p["store_logo_path"] = store.get("logo_path", "")
            p["store_banner_path"] = store.get("banner_path", "")
            p["store_profile_intro"] = (
                store.get("profile_intro")
                or store.get("description")
                or "Fresh groceries and daily essentials from this store."
            ).strip()
        else:
            p["store_address"] = ""
            p["store_logo_path"] = ""
            p["store_banner_path"] = ""
            p["store_profile_intro"] = "Fresh groceries and daily essentials from this store."

        store_rating_avg = 0
        store_rating_count = 0

        if store:
            store_rating_row = product_store_rating_stats.get(str(store["_id"]), {})
            store_rating_count = int(store_rating_row.get("count") or 0)
            store_rating_total = float(store_rating_row.get("total") or 0)

            if store_rating_count > 0:
                store_rating_avg = round(store_rating_total / store_rating_count, 2)

        p["store_avg_rating"] = store_rating_avg
        p["store_rating_count"] = store_rating_count

    live_product_bundles = []

    for b in product_bundles:
        b = build_live_product_bundle(
            b,
            notify_store=True,
            notification_context="public_products"
        ) or b

        if not is_product_bundle_customer_available(b):
            continue

        live_product_bundles.append(b)

    bundle_store_ids = [
        b.get("store_id")
        for b in live_product_bundles
        if b.get("store_id") not in (None, "")
    ]
    bundle_store_map = _products_batch_store_documents(
        bundle_store_ids,
        include_object_id_fallback=True
    )

    visible_product_bundles = []

    for b in live_product_bundles:
        b["id"] = str(b.get("_id"))

        bundle_cart_info = bundle_cart_lookup.get(str(b.get("_id")))

        if bundle_cart_info:
            b["in_cart"] = True
            b["cart_item_id"] = bundle_cart_info.get("cart_item_id", "")
            b["cart_quantity"] = bundle_cart_info.get("cart_quantity", 1)
        else:
            b["in_cart"] = False
            b["cart_item_id"] = ""
            b["cart_quantity"] = 0

        store_id = b.get("store_id")
        store = _products_store_from_batch(
            bundle_store_map,
            store_id,
            allow_text_fallback=True
        )

        b["store_name"] = b.get("store_name") or (store.get("store_name") if store else "")
        b["store_id"] = str(store_id) if store_id else b.get("store_id_str", "")

        visible_product_bundles.append(b)

    product_bundles = visible_product_bundles

    return render_template(
        'products.html',
        products=products,
        product_bundles=product_bundles,
        user=u
    )


def _suggestion_safe_object_id(value):
    try:
        if ObjectId.is_valid(str(value)):
            return ObjectId(str(value))
    except Exception:
        pass
    return None


def _suggestion_store_values(store_id):
    values = []
    seen = set()

    for value in [store_id, str(store_id or "")]:
        if value in (None, ""):
            continue

        key = str(value)
        if key not in seen:
            values.append(value)
            seen.add(key)

    obj_id = _suggestion_safe_object_id(store_id)
    if obj_id is not None and str(obj_id) not in seen:
        values.append(obj_id)

    return values


@app.route('/api/products/suggestions', methods=['GET'], endpoint='api_product_suggestions')
def api_product_suggestions():
    """
    Suggested products shown only after a successful Add to Cart.

    Rules:
    - same store only
    - active and in-stock products only
    - same sub-category first, then same category
    - exclude the product just added
    - for a bundle, exclude all child products already inside that bundle
    - exclude products already present in the customer's cart
    """
    item_type = (request.args.get("item_type") or "product").strip().lower()
    product_id_raw = (request.args.get("product_id") or "").strip()
    bundle_id_raw = (request.args.get("bundle_id") or "").strip()

    try:
        limit = int(request.args.get("limit") or 4)
    except (TypeError, ValueError):
        limit = 4

    limit = max(1, min(limit, 5))

    store_id = None
    preferred_category = ""
    preferred_sub_category = ""
    excluded_ids = set()

    if item_type == "bundle" or bundle_id_raw:
        bundle_obj_id = _suggestion_safe_object_id(bundle_id_raw)

        if not bundle_obj_id:
            return jsonify({"ok": False, "suggestions": [], "msg": "Invalid bundle"}), 400

        bundle = mongo.product_bundles.find_one({"_id": bundle_obj_id})

        if not bundle:
            return jsonify({"ok": False, "suggestions": [], "msg": "Product bundle not found"}), 404

        bundle = build_live_product_bundle(
            bundle,
            notify_store=False,
            notification_context="suggestions"
        ) or bundle

        store_id = bundle.get("store_id") or bundle.get("store_id_str")

        child_categories = []
        child_sub_categories = []

        for child in bundle.get("items") or []:
            child_product_id = child.get("product_id") or child.get("product_id_str")
            child_obj_id = _suggestion_safe_object_id(child_product_id)

            if child_obj_id:
                excluded_ids.add(child_obj_id)

                child_product = mongo.products.find_one({"_id": child_obj_id})
                if child_product:
                    category = (child_product.get("category") or "").strip()
                    sub_category = (child_product.get("sub_category") or "").strip()

                    if category:
                        child_categories.append(category)

                    if sub_category:
                        child_sub_categories.append(sub_category)

        if child_sub_categories:
            preferred_sub_category = child_sub_categories[0]

        if child_categories:
            preferred_category = child_categories[0]

    else:
        product_obj_id = _suggestion_safe_object_id(product_id_raw)

        if not product_obj_id:
            return jsonify({"ok": False, "suggestions": [], "msg": "Invalid product"}), 400

        source_product = mongo.products.find_one({"_id": product_obj_id})

        if not source_product:
            return jsonify({"ok": False, "suggestions": [], "msg": "Product not found"}), 404

        excluded_ids.add(product_obj_id)
        store_id = source_product.get("store_id") or source_product.get("store_id_str")
        preferred_category = (source_product.get("category") or "").strip()
        preferred_sub_category = (source_product.get("sub_category") or "").strip()

    if not store_id:
        return jsonify({"ok": True, "suggestions": []}), 200

    user = current_user()

    if user and user.get("role") == "customer":
        cid = get_or_create_cart(user["id"])

        for cart_item in mongo.cart_items.find({"cart_id": cid}, {"product_id": 1, "item_type": 1}):
            cart_item_type = (cart_item.get("item_type") or "product").strip().lower()

            if cart_item_type == "bundle":
                continue

            cart_product_id = _suggestion_safe_object_id(cart_item.get("product_id"))
            if cart_product_id:
                excluded_ids.add(cart_product_id)

    store_values = _suggestion_store_values(store_id)
    store_string_values = list({
        str(value)
        for value in store_values
        if value not in (None, "")
    })

    query = {
        "$and": [
            {
                "$or": [
                    {"store_id": {"$in": store_values}},
                    {"store_id_str": {"$in": store_string_values}}
                ]
            },
            {
                "$or": [
                    {"is_active": 1},
                    {"is_active": True}
                ]
            },
            {"stock_quantity": {"$gt": 0}}
        ]
    }

    if excluded_ids:
        query["$and"].append({"_id": {"$nin": list(excluded_ids)}})

    candidates = list(
        mongo.products.find(query).sort("created_at", -1).limit(40)
    )

    def suggestion_rank(product):
        category = (product.get("category") or "").strip()
        sub_category = (product.get("sub_category") or "").strip()

        same_sub = bool(
            preferred_sub_category
            and sub_category
            and sub_category.casefold() == preferred_sub_category.casefold()
        )
        same_category = bool(
            preferred_category
            and category
            and category.casefold() == preferred_category.casefold()
        )

        if same_sub:
            return 0

        if same_category:
            return 1

        return 2

    candidates.sort(key=suggestion_rank)

    suggestions = []

    for product in candidates[:limit]:
        hydrate_product_unit_fields(product)

        price = float(product.get("price_per_unit") or 0)
        original_price = float(product.get("original_price_per_unit") or price)
        quantity_min = float(product.get("quantity_min") or 1)
        quantity_step = float(product.get("quantity_step") or quantity_min or 1)
        stock_quantity = float(product.get("stock_quantity") or 0)

        suggestions.append({
            "id": str(product["_id"]),
            "name": product.get("name") or "Product",
            "image_path": product.get("image_path") or "",
            "category": (product.get("category") or "").strip(),
            "sub_category": (product.get("sub_category") or "").strip(),
            "store_id": str(product.get("store_id") or product.get("store_id_str") or ""),
            "store_name": product.get("store_name") or "",
            "unit_type": product.get("unit_type") or "WEIGHT",
            "unit_label": product.get("unit_label") or "kg",
            "quantity_min": quantity_min,
            "quantity_step": quantity_step,
            "stock_quantity": stock_quantity,
            "price_per_unit": price,
            "original_price_per_unit": original_price,
            "has_discount": bool(
                product.get("discount_enabled")
                and original_price > price
            )
        })

    return jsonify({
        "ok": True,
        "suggestions": suggestions
    }), 200


@app.route('/api/ratings/product/<pid>')
def api_product_rating(pid):
    try:
        pid_obj = ObjectId(pid)
    except Exception:
        return jsonify({
            "ok": False,
            "avg": 0,
            "count": 0
        }), 400

    ratings = list(mongo.product_ratings.find({
        "product_id": pid_obj
    }))

    count = len(ratings)

    if count > 0:
        avg = round(
            sum(float(r.get("rating") or 0) for r in ratings) / count,
            1
        )
    else:
        avg = 0

    return jsonify({
        "ok": True,
        "avg": avg,
        "count": count
    })


# =========================================================
# RECENTLY VIEWED PRODUCTS
# =========================================================

_RECENT_PRODUCT_VIEW_DAYS = 30


def _recent_product_viewer_key(user=None, create_guest=False):
    """
    Stable viewer identity for Recently Viewed Products.

    Signed-in customers:
      user:<user id>

    Guests:
      guest:<browser/session token>

    Staff accounts are intentionally not tracked because their product-detail
    activity is administrative rather than shopper browsing.
    """
    if user:
        role = str(user.get("role") or "").strip().lower()

        if role == "customer":
            user_id = str(user.get("id") or user.get("_id") or "").strip()

            if user_id:
                return f"user:{user_id}"

        return ""

    token = str(session.get("_recent_product_viewer_key") or "").strip()

    if not token and create_guest:
        token = secrets.token_urlsafe(18)
        session["_recent_product_viewer_key"] = token

    return f"guest:{token}" if token else ""


def _record_recent_product_view(product, user=None):
    """
    Record one shopper's most recent view of a product.

    Repeated views of the same product update the existing row instead of
    creating duplicates. The newest view therefore moves that product back to
    the front of the homepage Recently Viewed section.
    """
    if not product:
        return

    if int(product.get("is_active") or 0) != 1:
        return

    viewer_key = _recent_product_viewer_key(
        user=user,
        create_guest=(user is None)
    )

    if not viewer_key:
        return

    product_id = product.get("_id")

    if not product_id:
        return

    now_dt = datetime.utcnow()
    cutoff_dt = now_dt - timedelta(days=_RECENT_PRODUCT_VIEW_DAYS)

    store_id = product.get("store_id")

    mongo.product_view_events.update_one(
        {
            "viewer_key": viewer_key,
            "product_id": product_id
        },
        {
            "$setOnInsert": {
                "first_viewed_at": now_dt
            },
            "$set": {
                "product_id": product_id,
                "product_id_str": str(product_id),
                "store_id": store_id,
                "store_id_str": str(store_id) if store_id else "",
                "last_viewed_at": now_dt,
                "viewer_type": (
                    "customer"
                    if user and str(user.get("role") or "").lower() == "customer"
                    else "guest"
                )
            },
            "$inc": {
                "view_count": 1
            }
        },
        upsert=True
    )

    # Keep each viewer's history genuinely recent and bounded.
    mongo.product_view_events.delete_many({
        "viewer_key": viewer_key,
        "last_viewed_at": {"$lt": cutoff_dt}
    })


@app.route('/product/<pid>')
def product_detail(pid):
    try:
        product_obj_id = ObjectId(pid)
    except Exception:
        flash("Product not found.", "warning")
        return redirect(url_for('products'))

    p = mongo.products.find_one({"_id": product_obj_id})

    if not p:
        flash("Product not found.", "warning")
        return redirect(url_for('products'))

    p["id"] = str(p["_id"])
    hydrate_product_unit_fields(p)

    store = None
    if p.get("store_id"):
        store = mongo.stores.find_one({"_id": p["store_id"]})

    p["store_name"] = store.get("store_name") if store else ""
    p["store_id"] = str(p.get("store_id")) if p.get("store_id") else ""

    # Store profile fields for Products page → Stores tab
    if store:
        p["store_address"] = store.get("address", "")
        p["store_logo_path"] = store.get("logo_path", "")
        p["store_banner_path"] = store.get("banner_path", "")
        p["store_profile_intro"] = (
            store.get("profile_intro")
            or store.get("description")
            or "Fresh groceries and daily essentials from this store."
        ).strip()
    else:
        p["store_address"] = ""
        p["store_logo_path"] = ""
        p["store_banner_path"] = ""
        p["store_profile_intro"] = "Fresh groceries and daily essentials from this store."

    # Real store rating for Products page → Stores tab
    store_rating_avg = 0
    store_rating_count = 0

    if store:
        store_rating_query = {
            "$or": [
                {"store_id": store["_id"]},
                {"store_id": str(store["_id"])}
            ]
        }

        store_ratings = list(mongo.store_ratings.find(store_rating_query))
        store_rating_count = len(store_ratings)
        store_rating_total = 0

        for sr in store_ratings:
            try:
                store_rating_total += float(sr.get("rating") or 0)
            except (TypeError, ValueError):
                pass

        if store_rating_count > 0:
            store_rating_avg = round(store_rating_total / store_rating_count, 2)

    p["store_avg_rating"] = store_rating_avg
    p["store_rating_count"] = store_rating_count

    u = current_user()
    is_staff = bool(u and (u.get("role") in ("admin", "store")))

    if not is_staff and int(p.get("is_active") or 0) != 1:
        abort(404)

        cart_info = None

    # Shopper-facing Recent Views:
    # record only guests and customer accounts, never admin/store previews.
    if not u or u.get("role") == "customer":
        _record_recent_product_view(p, user=u)

    if u and u.get("role") == "customer":
        cid = get_or_create_cart(u["id"])

        cart_info = mongo.cart_items.find_one({
            "cart_id": cid,
            "product_id": product_obj_id
        })

        if cart_info:
            p["in_cart"] = True
            p["cart_item_id"] = str(cart_info.get("_id"))
            p["cart_quantity"] = cart_item_quantity(cart_info)
        else:
            p["in_cart"] = False
            p["cart_item_id"] = ""
            p["cart_quantity"] = 0
    else:
        p["in_cart"] = False
        p["cart_item_id"] = ""
        p["cart_quantity"] = 0

    ratings = list(mongo.product_ratings.find({
        "product_id": product_obj_id
    }).sort("created_at", -1))

    rating_count = len(ratings)

    if rating_count > 0:
        avg_rating = round(
            sum(float(r.get("rating") or 0) for r in ratings) / rating_count,
            1
        )
    else:
        avg_rating = 0

    rating_summary = {
        "avg": avg_rating,
        "count": rating_count
    }

    reviews = []

    for r in ratings:
        customer = None

        if r.get("user_id"):
            try:
                customer = mongo.users.find_one({"_id": ObjectId(r.get("user_id"))})
            except Exception:
                customer = None

        reviews.append({
            "rating": r.get("rating"),
            "comment": r.get("comment"),
            "created_at": r.get("created_at"),
            "customer_name": customer.get("name") if customer else "Customer"
        })

    selected_quantity_raw = (
        request.args.get("quantity")
        or p.get("cart_quantity")
        or p.get("quantity_min")
        or 1
    )

    try:
        selected_quantity = float(selected_quantity_raw)
    except (TypeError, ValueError):
        selected_quantity = float(p.get("quantity_min", 1) or 1)

    min_quantity = float(p.get("quantity_min", 0.25) or 0.25)

    if selected_quantity < min_quantity:
        selected_quantity = min_quantity

    return render_template(
        'product.html',
        user=u,
        product=p,
        rating=rating_summary,
        reviews=reviews,
        selected_quantity=selected_quantity
    )

@app.route("/products/<pid>/review", methods=["POST"], endpoint="submit_product_review")
@login_required()
def submit_product_review(pid):
    u = current_user()

    if not u:
        flash("Please log in first.", "warning")
        return redirect(url_for("login"))

    if u.get("role") != "customer":
        flash("Only customer accounts can submit product reviews.", "warning")
        return redirect(url_for("product_detail", pid=pid))

    try:
        pid_obj = ObjectId(pid)
    except Exception:
        flash("Invalid product.", "danger")
        return redirect(url_for("products"))

    product = mongo.products.find_one({"_id": pid_obj})

    if not product:
        flash("Product not found.", "danger")
        return redirect(url_for("products"))

    try:
        rating = float(request.form.get("rating") or 0)
    except (TypeError, ValueError):
        rating = 0

    review = (request.form.get("review") or "").strip()

    if rating < 1 or rating > 5:
        flash("Please select a valid rating between 1 and 5.", "warning")
        return redirect(url_for("product_detail", pid=pid))

    if len(review) > 800:
        flash("Review is too long. Please keep it within 800 characters.", "warning")
        return redirect(url_for("product_detail", pid=pid))

    now = datetime.utcnow().isoformat()

    existing_review = mongo.product_ratings.find_one({
        "product_id": pid_obj,
        "user_id": str(u["_id"])
    })

    review_doc = {
        "product_id": pid_obj,
        "product_name": product.get("name", ""),
        "store_id": product.get("store_id"),
        "store_name": product.get("store_name", ""),
        "user_id": str(u["_id"]),
        "reviewer_name": u.get("name", "Customer"),
        "rating": rating,
        "review": review,
        "comment": review,
        "is_active": 1,
        "updated_at": now
    }

    if existing_review:
        mongo.product_ratings.update_one(
            {"_id": existing_review["_id"]},
            {"$set": review_doc}
        )
        flash("Your product review has been updated.", "success")
    else:
        review_doc["created_at"] = now
        mongo.product_ratings.insert_one(review_doc)
        flash("Thank you! Your product review has been submitted.", "success")

    return redirect(url_for("product_detail", pid=pid))

@app.route('/rate/product/<int:pid>', methods=['POST'])
@login_required()
def rate_product_disabled(pid):
    flash('Please rate from the order page after your delivery is completed.', 'info')
    return redirect(request.referrer or url_for('orders'))

@app.route('/api/ratings/product/<int:pid>')
def api_ratings_product(pid):
    s = get_product_rating_summary(pid)
    return jsonify({"ok": True, "avg": s["avg"], "count": s["count"]})

@app.route('/api/ratings/store/<int:sid>')
def api_ratings_store(sid):
    s = get_store_rating_summary(sid)
    return jsonify({"ok": True, "avg": s["avg"], "count": s["count"]})

@app.route('/api/products', methods=['GET'])
def api_products_list():
    category = (request.args.get('category') or '').strip()
    sub_category = (request.args.get('sub_category') or '').strip()
    search = (request.args.get('search') or '').strip()

    allowed_categories = ['Fresh cuts', 'Ready to cook', 'Spices']
    fresh_cut_subs = ['Curry cuts', 'Boneless & Mince', 'Offals']

    mongo_filter = {
        "is_active": 1,
        "stock_quantity": {"$gt": 0}
    }

    if category:
        if category not in allowed_categories:
            return jsonify({'success': False, 'error': 'Invalid category'}), 400

        mongo_filter["category"] = category

        if sub_category:
            if category != 'Fresh cuts':
                return jsonify({'success': False, 'error': 'sub_category only valid for Fresh cuts'}), 400

            if sub_category not in fresh_cut_subs:
                return jsonify({'success': False, 'error': 'Invalid sub_category'}), 400

            mongo_filter["sub_category"] = sub_category

    if search:
        mongo_filter["$or"] = [
            {"name": {"$regex": search, "$options": "i"}},
            {"store_name": {"$regex": search, "$options": "i"}},
            {"category": {"$regex": search, "$options": "i"}},
            {"sub_category": {"$regex": search, "$options": "i"}},
        ]

    products = list(
        mongo.products.find(mongo_filter).sort("created_at", -1).limit(100)
    )

    product_ids = [p.get("_id") for p in products if p.get("_id") is not None]
    rating_stats = _products_batch_rating_stats(product_ids)
    store_map = _products_batch_store_documents([
        p.get("store_id")
        for p in products
        if p.get("store_id") not in (None, "")
    ])

    result = []

    for p in products:
        store = _products_store_from_batch(store_map, p.get("store_id"))
        rating_row = rating_stats.get(str(p["_id"]), {})
        rating_count = int(rating_row.get("count") or 0)
        rating_total = float(rating_row.get("total") or 0)
        avg_rating = round(rating_total / rating_count, 1) if rating_count else 0

        result.append({
            "id": str(p["_id"]),
            "name": p.get("name", ""),
            "price_per_unit": float(p.get("price_per_unit") or 0),
            "stock_quantity": float(p.get("stock_quantity") or 0),
            "unit_type": p.get("unit_type") or "WEIGHT",
            "unit_label": p.get("unit_label") or "kg",
            "image_path": p.get("image_path", ""),
            "store_name": store.get("store_name") if store else p.get("store_name", ""),
            "store_id": str(p.get("store_id")) if p.get("store_id") else "",
            "avg_rating": float(avg_rating),
            "rating_count": int(rating_count),
            "category": p.get("category", ""),
            "sub_category": p.get("sub_category", ""),
        })

    return jsonify({
        "success": True,
        "products": result
    })

@app.route('/api/products/<pid>', methods=['GET'])
def api_product_detail(pid):
    try:
        pid_obj = ObjectId(pid)
    except Exception:
        return jsonify({
            "success": False,
            "error": "Invalid product id"
        }), 400

    p = mongo.products.find_one({
        "_id": pid_obj,
        "is_active": 1,
        "stock_quantity": {"$gt": 0}
    })

    if not p:
        return jsonify({
            "success": False,
            "error": "Product not found"
        }), 404

    store = None

    if p.get("store_id"):
        store = mongo.stores.find_one({"_id": p.get("store_id")})

    ratings = list(
        mongo.product_ratings.find({
            "product_id": p["_id"]
        }).sort("created_at", -1)
    )

    rating_count = len(ratings)

    avg_rating = round(
        sum(float(r.get("rating") or 0) for r in ratings) / rating_count,
        1
    ) if rating_count else 0

    review_rows = ratings[:20]
    review_user_ids = []
    seen_review_users = set()

    for r in review_rows:
        raw_user_id = r.get("user_id")
        if not raw_user_id:
            continue

        try:
            user_id = ObjectId(str(raw_user_id))
        except Exception:
            continue

        key = str(user_id)
        if key in seen_review_users:
            continue

        seen_review_users.add(key)
        review_user_ids.append(user_id)

    review_users = {}

    if review_user_ids:
        for customer in mongo.users.find(
            {"_id": {"$in": review_user_ids}},
            {"name": 1}
        ):
            review_users[str(customer.get("_id"))] = customer

    reviews = []

    for r in review_rows:
        customer = None
        raw_user_id = r.get("user_id")

        if raw_user_id:
            try:
                customer = review_users.get(str(ObjectId(str(raw_user_id))))
            except Exception:
                customer = None

        reviews.append({
            "rating": r.get("rating"),
            "comment": r.get("comment"),
            "customer_name": customer.get("name") if customer else "Customer",
            "created_at": r.get("created_at")
        })

    return jsonify({
        "success": True,
        "product": {
            "id": str(p["_id"]),
            "name": p.get("name", ""),
            "price_per_unit": float(p.get("price_per_unit") or 0),
            "stock_quantity": float(p.get("stock_quantity") or 0),
            "unit_type": p.get("unit_type") or "WEIGHT",
            "unit_label": p.get("unit_label") or "kg",
            "image_path": p.get("image_path", ""),
            "store_name": store.get("store_name") if store else p.get("store_name", ""),
            "store_id": str(p.get("store_id")) if p.get("store_id") else "",
            "avg_rating": float(avg_rating),
            "rating_count": int(rating_count),
            "category": p.get("category", ""),
            "sub_category": p.get("sub_category", ""),
            "reviews": reviews
        }
    })
