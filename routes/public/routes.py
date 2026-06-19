"""Public routes extracted from the updated app.py.

Logic, decorators, endpoint names and route paths are intentionally preserved.
Only the file location changed.
"""

from app_core import *

import re
import requests

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
    cart_lookup = {}

    if session.get("service_area") and not allow:
        flash("Please enter a valid 6-digit pincode.", "warning")
    else:
        products = list(mongo.products.find({
            "is_active": 1
        }).sort("created_at", -1).limit(80))

        # Homepage cart lookup for customer users
        # This lets homepage product cards show:
        # Added to Cart / Plus / Minus / Remove
        if user and user.get("role") == "customer":
            cid = get_or_create_cart(user["id"])

            cart_items = list(mongo.cart_items.find({
                "cart_id": cid
            }))

            for ci in cart_items:
                product_id = ci.get("product_id")

                if product_id:
                    cart_lookup[str(product_id)] = {
                        "cart_item_id": str(ci["_id"]),
                        "cart_quantity": cart_item_quantity(ci)
                    }

        for p in products:
            hydrate_product_unit_fields(p)
            _hydrate_home_product(p)

            product_id = str(p.get("_id"))

            cart_row = cart_lookup.get(product_id)

            p["in_cart"] = bool(cart_row)
            p["cart_item_id"] = cart_row.get("cart_item_id") if cart_row else ""
            p["cart_quantity"] = cart_row.get("cart_quantity") if cart_row else 0

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

        # Real-time homepage categories from store_categories collection
        # Disabled categories must NOT appear again through products.
        category_map = {}
        active_category_names = set()
        active_category_store_keys = set()
        active_global_category_names = set()
        disabled_category_names = set()
        disabled_category_store_keys = set()

        all_store_categories = list(
            mongo.store_categories.find({}).sort("name", 1)
        )

        for cat in all_store_categories:
            cat_name = (cat.get("name") or "").strip()

            if not cat_name:
                continue

            cat_key = cat_name.lower()
            store_id_str = str(cat.get("store_id")) if cat.get("store_id") else ""

            raw_active = cat.get("is_active", None)

            is_active_category = raw_active in [1, True, "1", "true", "True"]

            if not is_active_category:
                disabled_category_names.add(cat_key)

                if store_id_str:
                    disabled_category_store_keys.add((store_id_str, cat_key))

                continue

            active_category_names.add(cat_key)

            if store_id_str:
                active_category_store_keys.add((store_id_str, cat_key))
            else:
                active_global_category_names.add(cat_key)

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
                    "store_id": store_id_str,
                    "sub_categories": cat.get("sub_categories") or [],
                    "is_active": 1
                }
            else:
                if category_image_path and not category_map[cat_key].get("category_image_path"):
                    category_map[cat_key]["image_path"] = category_image_path
                    category_map[cat_key]["category_image_path"] = category_image_path

        # Count only products whose category is still active.
        # Important: Do NOT recreate disabled categories from products.
        for p in products:
            cat_name = (p.get("category") or "").strip()

            if not cat_name:
                continue

            cat_key = cat_name.lower()
            product_store_id = str(p.get("store_id")) if p.get("store_id") else ""

            if cat_key in disabled_category_names:
                continue

            if product_store_id and (product_store_id, cat_key) in disabled_category_store_keys:
                continue

            category_is_active_for_product = False

            if product_store_id and (product_store_id, cat_key) in active_category_store_keys:
                category_is_active_for_product = True
            elif cat_key in active_global_category_names:
                category_is_active_for_product = True
            elif not product_store_id and cat_key in active_category_names:
                category_is_active_for_product = True

            if not category_is_active_for_product:
                continue

            if cat_key in category_map:
                category_map[cat_key]["count"] += 1

        categories = sorted(
            [
                c for c in category_map.values()
                if int(c.get("count") or 0) > 0
            ],
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

NEWSLETTER_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]{2,}$")
BREVO_CONTACTS_API_URL = "https://api.brevo.com/v3/contacts"


def _newsletter_now():
    return datetime.utcnow().isoformat()


def _get_brevo_newsletter_config():
    api_key = (os.getenv("BREVO_API_KEY") or "").strip()
    list_id_raw = (os.getenv("BREVO_NEWSLETTER_LIST_ID") or "").strip()

    if not api_key:
        return None, None, "BREVO_API_KEY is missing in .env"

    if not list_id_raw:
        return None, None, "BREVO_NEWSLETTER_LIST_ID is missing in .env"

    try:
        list_id = int(list_id_raw)
    except ValueError:
        return None, None, "BREVO_NEWSLETTER_LIST_ID must be a number"

    return api_key, list_id, None


def _sync_newsletter_email_to_brevo(email):
    api_key, list_id, config_error = _get_brevo_newsletter_config()

    if config_error:
        return {
            "ok": False,
            "status_code": 500,
            "message": config_error,
            "brevo_response": None
        }

    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "api-key": api_key
    }

    payload = {
        "email": email,
        "listIds": [list_id],
        "updateEnabled": True
    }

    try:
        response = requests.post(
            BREVO_CONTACTS_API_URL,
            headers=headers,
            json=payload,
            timeout=15
        )

        try:
            brevo_response = response.json()
        except Exception:
            brevo_response = {
                "raw": response.text
            }

        if response.status_code in [200, 201, 204]:
            return {
                "ok": True,
                "status_code": response.status_code,
                "message": "Synced with Brevo.",
                "brevo_response": brevo_response
            }

        return {
            "ok": False,
            "status_code": response.status_code,
            "message": "Brevo rejected the newsletter subscription.",
            "brevo_response": brevo_response
        }

    except requests.RequestException as e:
        return {
            "ok": False,
            "status_code": 502,
            "message": str(e),
            "brevo_response": None
        }


@app.route('/newsletter/subscribe', methods=['POST'])
def newsletter_subscribe():
    data = request.get_json(silent=True) or {}

    email = (
        data.get("email")
        or request.form.get("email")
        or ""
    )

    email = str(email).strip().lower()

    wants_json = (
        request.is_json
        or "application/json" in str(request.headers.get("Accept", ""))
    )

    def fail_response(message, status_code=400):
        if wants_json:
            return jsonify({
                "ok": False,
                "message": message
            }), status_code

        flash(message, "danger")
        return redirect(request.referrer or url_for("index"))

    def success_response(message, status_code=200):
        if wants_json:
            return jsonify({
                "ok": True,
                "message": message
            }), status_code

        flash(message, "success")
        return redirect(request.referrer or url_for("index"))

    if not email:
        return fail_response("Please enter your email address.", 400)

    if not NEWSLETTER_EMAIL_RE.match(email):
        return fail_response("Please enter a valid email address.", 400)

    now = _newsletter_now()

    try:
        mongo.newsletter_subscribers.create_index(
            "email",
            unique=True
        )

        existing = mongo.newsletter_subscribers.find_one({
            "email": email
        })

        if existing and existing.get("brevo_synced") is True:
            mongo.newsletter_subscribers.update_one(
                {
                    "email": email
                },
                {
                    "$set": {
                        "is_active": True,
                        "source": "footer",
                        "updated_at": now,
                        "last_subscribed_at": now
                    }
                }
            )

            return success_response("You are already subscribed.", 200)

        mongo.newsletter_subscribers.update_one(
            {
                "email": email
            },
            {
                "$set": {
                    "email": email,
                    "source": "footer",
                    "is_active": True,
                    "brevo_synced": False,
                    "brevo_status": "pending",
                    "updated_at": now,
                    "last_subscribed_at": now
                },
                "$setOnInsert": {
                    "created_at": now
                }
            },
            upsert=True
        )

        brevo_result = _sync_newsletter_email_to_brevo(email)

        if not brevo_result.get("ok"):
            mongo.newsletter_subscribers.update_one(
                {
                    "email": email
                },
                {
                    "$set": {
                        "brevo_synced": False,
                        "brevo_status": "failed",
                        "brevo_status_code": brevo_result.get("status_code"),
                        "brevo_error": brevo_result.get("message"),
                        "brevo_response": brevo_result.get("brevo_response"),
                        "updated_at": now
                    }
                }
            )

            print("[NEWSLETTER BREVO ERROR]", brevo_result)

            return fail_response(
                "Could not subscribe right now. Please try again.",
                502
            )

        mongo.newsletter_subscribers.update_one(
            {
                "email": email
            },
            {
                "$set": {
                    "brevo_synced": True,
                    "brevo_status": "synced",
                    "brevo_status_code": brevo_result.get("status_code"),
                    "brevo_response": brevo_result.get("brevo_response"),
                    "updated_at": now
                },
                "$unset": {
                    "brevo_error": ""
                }
            }
        )

        return success_response(
            "Subscribed! You’ll receive fresh updates soon.",
            201
        )

    except Exception as e:
        print("[NEWSLETTER ERROR]", str(e))

        return fail_response(
            "Something went wrong. Please try again.",
            500
        )

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
