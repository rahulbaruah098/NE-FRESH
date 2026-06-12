"""Store routes extracted from the updated app.py.

Logic, decorators, endpoint names and route paths are intentionally preserved.
Only the file location changed.
"""

from app_core import *


def _store_bool_from_form(name, default=False):
    value = request.form.get(name)

    if value is None:
        return bool(default)

    return str(value).strip().lower() in ["1", "true", "yes", "on"]


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
            "total_amount": float(o.get("total_amount") or 0),
            "delivery_fee": float(o.get("delivery_fee") or 0),
            "tip_amount": float(o.get("tip_amount") or 0),
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

        if user and user.get("role") == "customer":
            cid = get_or_create_cart(user["id"])

            cart_items = list(mongo.cart_items.find({
                "cart_id": cid
            }))

            for ci in cart_items:
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

    return render_template(
        "store_catalog.html",
        user=user,
        store=store,
        products=products,
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

@app.route('/store/dashboard')
@login_required(role='store')
def store_dashboard():
    u, store = _get_current_store_or_redirect()

    if not store:
        flash("Store not found.", "danger")
        return redirect(url_for("login"))

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

        auto_accept_orders = _store_bool_from_form(
            "auto_accept_orders",
            bool(int(store.get("auto_accept_orders", 0) or 0))
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

        preparation_time = _settings_int_or_default(
            request.form.get("preparation_time"),
            store.get("preparation_time", 30) or 30,
            0,
            300
        )

        delivery_base_fee = _store_money_or_default(
            request.form.get("delivery_base_fee"),
            store.get("delivery_base_fee", 40)
        )

        free_delivery_above = _store_money_or_default(
            request.form.get("free_delivery_above"),
            store.get("free_delivery_above", 0)
        )

        delivery_min_order_amount = _store_money_or_default(
            request.form.get("delivery_min_order_amount"),
            store.get("delivery_min_order_amount", 0)
        )

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
            "preparation_time": preparation_time,
            "allow_cod": 1 if allow_cod else 0,
            "allow_online_payment": 1 if allow_online_payment else 0,
            "auto_accept_orders": 1 if auto_accept_orders else 0,

            "delivery_enabled": 1 if delivery_enabled else 0,
            "delivery_available": bool(delivery_enabled),
            "delivery_base_fee": delivery_base_fee,
            "free_delivery_above": free_delivery_above,
            "delivery_min_order_amount": delivery_min_order_amount,
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

        row["total_amount"] = float(o.get("total_amount") or 0)
        row["delivery_fee"] = float(o.get("delivery_fee") or 0)
        row["tip_amount"] = float(o.get("tip_amount") or 0)
        row["total_payable"] = (
            float(o.get("total_amount") or 0)
            + float(o.get("delivery_fee") or 0)
            + float(o.get("tip_amount") or 0)
        )

        delivered.append(row)

    store_view = dict(store)
    store_view["id"] = str(store["_id"])

    return render_template(
        "store_delivered_orders.html",
        user=u,
        store=store_view,
        orders=delivered
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

    return render_template(
        "store_products.html",
        user=u,
        store=store,
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

@app.route('/store/orders', methods=['GET'], endpoint='store_orders')
@login_required(role='store')
def store_orders_page():
    u, store = _get_current_store_or_redirect()

    if not store:
        return redirect(url_for("store_dashboard"))

    page_context = _build_store_split_page_context(store) or {}

    available_delivery_people = _hydrate_store_delivery_people_for_template(store)

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

        row["total_amount"] = _safe_float(row.get("total_amount"))
        row["delivery_fee"] = _safe_float(row.get("delivery_fee"))
        row["tip_amount"] = _safe_float(row.get("tip_amount"))

        if row.get("total_payable") is None:
            row["total_payable"] = (
                row["total_amount"]
                + row["delivery_fee"]
                + row["tip_amount"]
            )
        else:
            row["total_payable"] = _safe_float(row.get("total_payable"))

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
            "$or": [
                {"store_id": store_id},
                {"store_id": store_id_str},
                {"store_name": store.get("store_name")},
                {"store_name": store.get("name")}
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
        if _order_belongs_to_store(order)
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

    print(
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

    allowed_ready_statuses = {
        "CONFIRMED",
        "PREPARING",
        "PACKAGING"
    }

    if status == "READY_FOR_PICKUP":
        flash("This order is already ready for pickup.", "info")
        return redirect(url_for("store_orders"))

    if status not in allowed_ready_statuses:
        flash("Only confirmed/preparing/packaging orders can be marked ready for pickup.", "warning")
        return redirect(url_for("store_orders"))

    now = datetime.utcnow().isoformat()

    result = mongo.orders.update_one(
        {
            "_id": oid_obj,
            "status": {"$in": list(allowed_ready_statuses)},
            "$or": [
                {"delivery_partner_id": {"$exists": False}},
                {"delivery_partner_id": None},
                {"delivery_partner_id": ""}
            ]
        },
        {
            "$set": {
                "status": "READY_FOR_PICKUP",
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
        "READY_FOR_PICKUP",
        "Marked ready for pickup by store.",
        u
    )

    _create_store_notification(
        store,
        title="Order ready for pickup",
        message=f"Order #{str(oid_obj)[-6:]} is ready for delivery pickup.",
        notif_type="delivery",
        order=order,
        event_key=f"ready-pickup-{str(oid_obj)}-{now}"
    )

    flash("Order marked ready for pickup.", "success")
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

    if status != "READY_FOR_PICKUP":
        flash("Please mark this order ready for pickup before assigning a delivery boy.", "warning")
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
    city = (request.form.get("city") or "").strip()
    state = (request.form.get("state") or "Assam").strip()
    pincode = _clean_pin(request.form.get("pincode") or "")

    description = (request.form.get("description") or "").strip()
    profile_intro = (request.form.get("profile_intro") or "").strip()
    opening_time = (request.form.get("opening_time") or "").strip()
    closing_time = (request.form.get("closing_time") or "").strip()
    working_days = request.form.getlist("working_days")

    preparation_time_raw = (request.form.get("preparation_time") or "").strip()
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

    delivery_base_fee = _store_money_or_default(
        request.form.get("delivery_base_fee"),
        store.get("delivery_base_fee", 40)
    )

    lat_raw = (request.form.get("latitude") or "").strip()
    lng_raw = (request.form.get("longitude") or "").strip()

    latitude = _store_float_or_none(lat_raw, -90, 90)
    longitude = _store_float_or_none(lng_raw, -180, 180)

    preparation_time = None
    min_order_amount = None

    try:
        preparation_time = int(float(preparation_time_raw)) if preparation_time_raw else None
    except Exception:
        preparation_time = None

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
        "preparation_time": preparation_time,
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
        "delivery_base_fee": delivery_base_fee,

        "profile_updated_at": now,
        "updated_at": now
    }

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

@app.route('/store/categories/new', methods=['POST'], endpoint='store_category_new')
@login_required(role='store')
def store_category_new():
    u, store = _get_current_store_or_redirect()

    if not store:
        return redirect(url_for("store_dashboard"))

    name = (request.form.get("name") or "").strip()
    sub_categories_raw = (request.form.get("sub_categories") or "").strip()

    if not name:
        flash("Category name is required.", "warning")
        return redirect(url_for("store_categories"))

    slug = _category_slug(name)

    if not slug:
        flash("Enter a valid category name.", "warning")
        return redirect(url_for("store_categories"))

    existing = mongo.store_categories.find_one({
        "store_id": store["_id"],
        "slug": slug
    })

    if existing:
        flash("This category already exists.", "warning")
        return redirect(url_for("store_categories"))

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
            flash("Only JPG, JPEG, PNG or WEBP images are allowed for category image.", "warning")
            return redirect(url_for("store_categories"))

        category_image_path = _save_store_category_image(
            category_image,
            store["_id"],
            slug
        )

    mongo.store_categories.insert_one({
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

    flash("Category added.", "success")
    return redirect(url_for("store_categories"))

@app.route('/store/categories/<cid>/update', methods=['POST'], endpoint='store_category_update')
@login_required(role='store')
def store_category_update(cid):
    u, store = _get_current_store_or_redirect()

    if not store:
        return redirect(url_for("store_dashboard"))

    cat = _get_store_category_by_id(store["_id"], cid)

    if not cat:
        flash("Category not found.", "danger")
        return redirect(url_for("store_categories"))

    old_name = cat.get("name", "")
    name = (request.form.get("name") or "").strip()
    sub_categories_raw = (request.form.get("sub_categories") or "").strip()

    if not name:
        flash("Category name is required.", "warning")
        return redirect(url_for("store_categories"))

    slug = _category_slug(name)

    duplicate = mongo.store_categories.find_one({
        "_id": {"$ne": cat["_id"]},
        "store_id": store["_id"],
        "slug": slug
    })

    if duplicate:
        flash("Another category with this name already exists.", "warning")
        return redirect(url_for("store_categories"))

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
            flash("Only JPG, JPEG, PNG or WEBP images are allowed for category image.", "warning")
            return redirect(url_for("store_categories"))

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

    flash("Category updated.", "success")
    return redirect(url_for("store_categories"))

@app.route('/store/categories/<cid>/toggle', methods=['POST'], endpoint='store_category_toggle')
@login_required(role='store')
def store_category_toggle(cid):
    u, store = _get_current_store_or_redirect()

    if not store:
        return redirect(url_for("store_dashboard"))

    cat = _get_store_category_by_id(store["_id"], cid)

    if not cat:
        flash("Category not found.", "danger")
        return redirect(url_for("store_categories"))

    new_status = 0 if int(cat.get("is_active") or 0) == 1 else 1

    mongo.store_categories.update_one(
        {"_id": cat["_id"]},
        {
            "$set": {
                "is_active": new_status,
                "updated_at": datetime.utcnow().isoformat()
            }
        }
    )

    flash("Category enabled." if new_status else "Category disabled.", "success")
    return redirect(url_for("store_categories"))

@app.route('/store/categories/<cid>/delete', methods=['POST'], endpoint='store_category_delete')
@login_required(role='store')
def store_category_delete(cid):
    u, store = _get_current_store_or_redirect()

    if not store:
        return redirect(url_for("store_dashboard"))

    cat = _get_store_category_by_id(store["_id"], cid)

    if not cat:
        flash("Category not found.", "danger")
        return redirect(url_for("store_categories"))

    product_count = _get_category_product_count(store["_id"], cat.get("name"))

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

        flash("This category has products, so it was disabled instead of deleted.", "warning")
        return redirect(url_for("store_categories"))

    mongo.store_categories.delete_one({"_id": cat["_id"]})

    flash("Category deleted.", "success")
    return redirect(url_for("store_categories"))

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

    if image and image.filename:
        if allowed_file(image.filename):
            fn = secure_filename(image.filename)
            save_as = datetime.utcnow().strftime("%Y%m%d%H%M%S_") + fn
            os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
            image.save(os.path.join(app.config["UPLOAD_FOLDER"], save_as))
            image_path = f"uploads/{save_as}"
        else:
            flash("Invalid image file type.", "warning")
            return redirect(url_for("store_add_product"))

    now = datetime.utcnow().isoformat()

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

        "category_id": category_id,
        "category": category,
        "sub_category": sub_category,

        "image_path": image_path,
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

    category_id = (request.form.get("category_id") or product.get("category_id") or "").strip()
    raw_category = (request.form.get("category") or product.get("category") or "").strip()
    sub_category = (request.form.get("sub_category") or product.get("sub_category") or "").strip()

    category_doc = None

    if category_id:
        category_doc = _get_store_category_by_id(store["_id"], category_id, active_only=True)

    if not category_doc and raw_category:
        category_doc = _get_store_category_by_name(store["_id"], raw_category, active_only=True)

    if not category_doc:
        flash("Please select a valid active category.", "warning")
        return redirect(url_for("store_product_edit", pid=pid))

    category = category_doc.get("name")
    category_id = str(category_doc["_id"])
    allowed_subs = category_doc.get("sub_categories") or []

    fallback_original_price = product_original_price_per_unit(product)

    pricing = build_unit_product_update_from_form(
        request.form,
        fallback_original_price=fallback_original_price
    )

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

    if allowed_subs:
        if not sub_category:
            sub_category = product.get("sub_category") or ""

        if sub_category not in allowed_subs:
            flash("Please select a valid sub-category.", "warning")
            return redirect(url_for("store_product_edit", pid=pid))
    else:
        sub_category = None

    update_data = {
        "name": name,

        "unit_type": pricing["unit_type"],
        "unit_label": pricing["unit_label"],

        "original_price_per_unit": pricing["original_price_per_unit"],
        "price_per_unit": pricing["price_per_unit"],
        "mrp_per_unit": pricing["mrp_per_unit"],
        "stock_quantity": pricing["stock_quantity"],

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

    mongo.products.update_one(
        {"_id": pid_obj},
        {"$set": update_data}
    )

    flash("Product updated.", "success")
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

    # Delivery workflow statuses must not be controlled by the normal order dropdown.
    delivery_locked_statuses = {
        "READY_FOR_PICKUP",
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
        "CANCELLED",
    }

    if new_status not in allowed_statuses:
        flash("Invalid order status selected.", "warning")
        return redirect(url_for("store_orders"))

    update_data = {
        "status": new_status,
        "updated_at": now
    }

    if new_status == "PREPARING":
        update_data["preparing_at"] = now

    if new_status == "CANCELLED":
        update_data["cancelled_at"] = now
        update_data["cancelled_by"] = "store"
        update_data["cancelled_by_id"] = str(u.get("_id") or u.get("id"))
        update_data["cancelled_by_name"] = u.get("name") or "Store User"

    mongo.orders.update_one(
        {
            "_id": oid_obj,
            "$or": [
                {"store_id": store["_id"]},
                {"store_id": str(store["_id"])}
            ]
        },
        {"$set": update_data}
    )

    add_order_event(
        oid_obj,
        new_status,
        "Updated by store",
        u
    )

    _create_store_notification(
        store,
        title="Order status updated",
        message=f"Order #{str(order['_id'])[-6:]} status changed to {new_status}.",
        notif_type="order",
        order=order,
        event_key=f"store-status-{str(order['_id'])}-{new_status}-{now}"
    )

    flash("Order status updated successfully.", "success")
    return redirect(url_for("store_orders"))
