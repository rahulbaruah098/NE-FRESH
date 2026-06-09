"""Public routes extracted from the updated app.py.

Logic, decorators, endpoint names and route paths are intentionally preserved.
Only the file location changed.
"""

from app_core import *


@app.route('/')
def index():
    user = current_user()
    allow, pin = _session_pin_is_serviceable()

    products = []
    latest_products = []
    new_products = []
    popular_products = []
    discount_products = []
    featured_products = []
    best_reviewed_products = []
    stores = []
    recommended_stores = []
    new_stores = []
    categories = []
    product_rating_map = {}
    store_rating_map = {}

    if session.get("service_area") and not allow:
        flash("Please enter a valid 6-digit pincode.", "warning")
    else:
        products = list(mongo.products.find({
            "is_active": 1
        }).sort("created_at", -1).limit(80))

        for p in products:
            _hydrate_home_product(p)

            product_rating_map[p["id"]] = {
                "avg": p.get("avg_rating", 0),
                "count": p.get("rating_count", 0)
            }

        # Latest fallback
        latest_products = products[:10]

        # New arrivals = products added within last 7 days
        new_products = [
            p for p in products
            if p.get("is_new_arrival")
        ]

        new_products = sorted(
            new_products,
            key=lambda x: _parse_home_dt(x.get("created_at")) or datetime.min,
            reverse=True
        )[:10]

        if not new_products:
            new_products = latest_products[:10]

        # Popular products = frequent sales/order_items first
        popular_products = sorted(
            products,
            key=lambda x: (
                int(x.get("sales_count") or 0),
                float(x.get("avg_rating") or 0),
                int(x.get("rating_count") or 0)
            ),
            reverse=True
        )[:10]

        if not popular_products:
            popular_products = latest_products[:10]

        # Discount products = real discount fields
        discount_products = [
            p for p in products
            if bool(p.get("discount_enabled"))
            and float(p.get("discount_amount_per_unit") or 0) > 0
        ]

        discount_products = sorted(
            discount_products,
            key=lambda x: (
                float(x.get("discount_percent") or 0),
               float(x.get("discount_amount_per_unit") or 0)
            ),
            reverse=True
        )[:10]

        # Best reviewed products
        best_reviewed_products = sorted(
            products,
            key=lambda x: (
                float(x.get("avg_rating") or 0),
                int(x.get("rating_count") or 0),
                int(x.get("sales_count") or 0)
            ),
            reverse=True
        )[:10]

        featured_products = popular_products[:10] if popular_products else latest_products[:10]

                # Real-time categories from store_categories collection
        category_map = {}

        store_categories = list(
            mongo.store_categories.find({
                "$or": [
                    {"is_active": 1},
                    {"is_active": True},
                    {"is_active": {"$exists": False}}
                ]
            }).sort("name", 1)
        )

        for cat in store_categories:
            cat_name = (cat.get("name") or "").strip()

            if not cat_name:
                continue

            cat_key = cat_name.lower()

            category_image_path = (
                cat.get("category_image_path")
                or cat.get("image_path")
                or cat.get("icon_path")
                or ""
            )

            if cat_key not in category_map:
                category_map[cat_key] = {
                    "id": str(cat.get("_id")),
                    "name": cat_name,
                    "count": 0,
                    "emoji": cat.get("emoji") or cat.get("icon") or "🛒",
                    "image_path": category_image_path,
                    "category_image_path": category_image_path,
                    "store_id": str(cat.get("store_id")) if cat.get("store_id") else "",
                    "sub_categories": cat.get("sub_categories") or []
                }
            else:
                if category_image_path and not category_map[cat_key].get("category_image_path"):
                    category_map[cat_key]["image_path"] = category_image_path
                    category_map[cat_key]["category_image_path"] = category_image_path

        # Count active products under each real-time category
        for p in products:
            cat_name = (p.get("category") or "Uncategorized").strip() or "Uncategorized"
            cat_key = cat_name.lower()

            if cat_key not in category_map:
                category_map[cat_key] = {
                    "id": "",
                    "name": cat_name,
                    "count": 0,
                    "emoji": "🛒",
                    "image_path": "",
                    "category_image_path": "",
                    "store_id": "",
                    "sub_categories": []
                }

            category_map[cat_key]["count"] += 1

        categories = sorted(
            list(category_map.values()),
            key=lambda x: x["name"].lower()
        )

        stores = list(mongo.stores.find({
            "is_active": 1
        }).sort("created_at", -1).limit(30))

        for s in stores:
            s["id"] = str(s["_id"])
            s["store_name"] = s.get("store_name", "Store")
            s["address"] = s.get("address", "")
            s["logo_path"] = s.get("logo_path", "")
            s["banner_path"] = s.get("banner_path", "")
            s["profile_intro"] = (
                s.get("profile_intro")
                or s.get("description")
                or "Fresh groceries and daily essentials from this store."
            ).strip()
            s["description"] = (s.get("description") or "").strip()
            s["is_open"] = int(s.get("is_open", 1))
            s["created_at"] = s.get("created_at", "")

            s["product_count"] = mongo.products.count_documents({
                "store_id": s["_id"],
                "is_active": 1
            })

            store_avg_rating, store_rating_count = _home_store_rating_summary(s["_id"])

            s["avg_rating"] = store_avg_rating
            s["rating_count"] = store_rating_count

            store_rating_map[s["id"]] = {
                "avg": store_avg_rating,
                "count": store_rating_count
            }

        recommended_stores = sorted(
            stores,
            key=lambda x: (
                float(x.get("avg_rating") or 0),
                int(x.get("rating_count") or 0),
                int(x.get("product_count") or 0)
            ),
            reverse=True
        )[:10]

        new_stores = stores[:10]

    return render_template(
        'index.html',
        user=user,
        products=products,
        latest_products=latest_products,
        new_products=new_products,
        popular_products=popular_products,
        discount_products=discount_products,
        featured_products=featured_products,
        best_reviewed_products=best_reviewed_products,
        categories=categories,
        stores=stores,
        recommended_stores=recommended_stores,
        new_stores=new_stores,
        product_rating_map=product_rating_map,
        store_rating_map=store_rating_map
    )


def _public_notification_priority(value):
    priority = (value or "medium").strip().lower()

    if priority not in ["high", "medium", "low"]:
        priority = "medium"

    return priority


def _public_notification_priority_rank(priority):
    priority = _public_notification_priority(priority)

    if priority == "high":
        return 1

    if priority == "medium":
        return 2

    return 3


@app.route("/api/homepage/notifications", methods=["GET"], endpoint="api_homepage_notifications")
def api_homepage_notifications():
    notifications = list(
        mongo.homepage_notifications.find({
            "is_active": 1,
            "show_ticker": 1,
            "$or": [
                {"display_location": "homepage"},
                {"display_location": "all"},
                {"display_location": {"$exists": False}}
            ]
        }).sort([
            ("priority_rank", 1),
            ("created_at", -1)
        ]).limit(20)
    )

    items = []

    for n in notifications:
        priority = _public_notification_priority(n.get("priority"))

        items.append({
            "id": str(n.get("_id")),
            "title": n.get("title", ""),
            "message": n.get("message", ""),
            "priority": priority,
            "priority_rank": _public_notification_priority_rank(priority),
            "link_url": n.get("link_url", ""),
            "show_popup": int(n.get("show_popup", 0) or 0),
            "created_at": n.get("created_at", "")
        })

    return jsonify({
        "ok": True,
        "count": len(items),
        "notifications": items
    })

@app.route('/legal/privacy')
def legal_privacy():
    return render_template('legal/privacy.html', user=current_user())

@app.route('/legal/security')
def legal_security():
    return render_template('legal/security.html', user=current_user())

@app.route('/legal/terms')
def legal_terms():
    return render_template('legal/terms.html', user=current_user())

@app.route('/help')
def legal_help():
    return render_template('legal/help.html', user=current_user())

@app.route('/report-fraud')
def legal_report_fraud():
    return render_template('legal/report_fraud.html', user=current_user())

@app.route('/about')
def about():
    """
    About Us page for Chhimphei Women Poultry Producer Company Limited
    """
    company_info = {
        "name": "Chhimphei Women Poultry Producer Company Limited",
        "year": 2018,
        "location": "Melriat, Aizawl, Mizoram",
        "fssai": "21825102002418",
        "phone": "8132831406",
        "website": "chhimphei.com",
        "supported_by": "Mizoram State Rural Livelihood Mission (MzSRLM)",
    }

    u = current_user()
    cart_count = 0

    if u:
        cid = get_or_create_cart(u["id"])
        cart_count = mongo.cart_items.count_documents({"cart_id": cid})

    return render_template(
        "about.html",
        info=company_info,
        user=u,
        cart_count=cart_count
    )

@app.route("/search")
def search():
    q = (request.args.get("q", "") or "").strip()
    user = current_user()

    products = []
    stores = []

    if q:
        products = list(
            mongo.products.find({
                "is_active": 1,
                "stock_quantity": {"$gt": 0},
                "$or": [
                    {"name": {"$regex": q, "$options": "i"}},
                    {"category": {"$regex": q, "$options": "i"}},
                    {"sub_category": {"$regex": q, "$options": "i"}},
                    {"store_name": {"$regex": q, "$options": "i"}},
                ]
            }).sort("created_at", -1).limit(50)
        )

        for p in products:
            p["id"] = str(p["_id"])

            store = None
            if p.get("store_id"):
                store = mongo.stores.find_one({"_id": p["store_id"]})

            p["store_name"] = store.get("store_name") if store else p.get("store_name", "")
            p["store_id"] = str(p.get("store_id")) if p.get("store_id") else ""

        stores = list(
            mongo.stores.find({
                "$or": [
                    {"store_name": {"$regex": q, "$options": "i"}},
                    {"address": {"$regex": q, "$options": "i"}},
                ]
            }).sort("store_name", 1).limit(30)
        )

        for s in stores:
            s["id"] = str(s["_id"])
            s["product_count"] = mongo.products.count_documents({
                "store_id": s["_id"],
                "is_active": 1,
                "stock_quantity": {"$gt": 0}
            })

    return render_template("search.html", user=user, q=q, products=products, stores=stores)

@app.route('/newsletter/subscribe', methods=['POST'])
def newsletter_subscribe():
    email = request.form.get('email', '').strip().lower()

    if not email or '@' not in email:
        flash('Please enter a valid email.', 'danger')
        return redirect(request.referrer or url_for('index'))

    existing = mongo.newsletter_subscribers.find_one({"email": email})

    if existing:
        flash('You are already subscribed.', 'info')
        return redirect(request.referrer or url_for('index'))

    mongo.newsletter_subscribers.insert_one({
        "email": email,
        "created_at": datetime.utcnow().isoformat()
    })

    flash('Subscribed to newsletter!', 'success')
    return redirect(request.referrer or url_for('index'))

@app.route('/uploads/<path:fn>')
def uploaded_file(fn):
    if '..' in fn or fn.startswith('/'):
        return abort(404)
    full = os.path.join(app.config['UPLOAD_FOLDER'], fn)
    if not os.path.isfile(full):
        return abort(404)
    return send_file(full)

@app.route('/__routes')
def __routes():
    return "<pre>" + "\n".join(
        f"{r.endpoint:30} {r.methods} {r}"
        for r in app.url_map.iter_rules()
    ) + "</pre>"

@app.route("/contact", methods=["GET", "POST"])
def contact():
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        email = (request.form.get("email") or "").strip()
        phone = (request.form.get("phone") or "").strip() or None
        subject = (request.form.get("subject") or "").strip()
        message = (request.form.get("message") or "").strip()

        if not name or not email or not subject or not message:
            flash("Please fill all required fields.", "warning")
            return redirect(url_for("contact"))

        mongo.contact_messages.insert_one({
            "name": name,
            "email": email,
            "phone": phone,
            "subject": subject,
            "message": message,
            "status": "NEW",
            "created_at": datetime.utcnow().isoformat()
        })

        flash("Message sent! We will contact you soon.", "success")
        return redirect(url_for("contact"))

    return render_template("contact.html", user=current_user())
