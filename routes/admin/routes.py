"""Admin routes extracted from the updated app.py.

Logic, decorators, endpoint names and route paths are intentionally preserved.
Only the file location changed.
"""

from app_core import *


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
    # Quick links
    # -------------------------
    quick_links = [
        {"label": "Pending Approvals", "endpoint": "admin_approvals"},
        {"label": "Manage Users", "endpoint": "admin_users"},
        {"label": "Complaints", "endpoint": "admin_complaints"},
        {"label": "Create Store", "endpoint": "admin_create_store"},
        {"label": "Create Delivery Partner", "endpoint": "admin_create_delivery"},
        {"label": "Export Transactions CSV", "endpoint": "admin_transactions_csv"},
    ]

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
    complaints = list_recent_complaints(limit=200)
    return render_template('admin_complaints.html', user=current_user(), complaints=complaints)

@app.route('/admin/complaints/<int:cid>/status', methods=['POST'])
@login_required(role='admin')
def admin_complaint_set_status(cid):
    status = request.form.get('status','OPEN')
    try:
        update_complaint_status(cid, status)
        flash('Complaint status updated.','success')
    except Exception as e:
        flash(f'Failed to update: {e}','danger')
    return redirect(url_for('admin_complaints'))

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

@app.route("/admin/contact-messages")
@login_required(role="admin")
def admin_contact_messages():
    messages = list(
        mongo.contact_messages.find({}).sort("created_at", -1)
    )

    for m in messages:
        m["id"] = str(m["_id"])

    return render_template(
        "admin_contact_messages.html",
        user=current_user(),
        messages=messages
    )

@app.route(
    "/admin/contact-messages/<int:mid>/status",
    methods=["POST"],
    endpoint="admin_contact_message_status"
)


@app.route(
    "/admin/contact-messages/<mid>/status",
    methods=["POST"],
    endpoint="admin_contact_message_status"
)
@login_required(role="admin")
def admin_contact_message_status(mid):
    status = (request.form.get("status") or "NEW").upper()

    if status not in ("NEW", "READ", "RESOLVED"):
        status = "NEW"

    try:
        mid_obj = ObjectId(mid)
    except Exception:
        flash("Invalid message.", "danger")
        return redirect(url_for("admin_contact_messages"))

    mongo.contact_messages.update_one(
        {"_id": mid_obj},
        {
            "$set": {
                "status": status,
                "updated_at": datetime.utcnow().isoformat()
            }
        }
    )

    flash("Message status updated.", "success")
    return redirect(url_for("admin_contact_messages"))
