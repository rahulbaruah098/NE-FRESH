"""Products routes extracted from the updated app.py.

Logic, decorators, endpoint names and route paths are intentionally preserved.
Only the file location changed.
"""

from app_core import *


@app.route('/products')
def products():
    allow, pin = _session_pin_is_serviceable()

    if session.get("service_area") and not allow:
        flash("Please enter a valid 6-digit pincode.", "warning")
        products = []
    else:
        products = list(mongo.products.find({
        "is_active": 1
        }).sort("created_at", -1))

    for p in products:
        p["id"] = str(p["_id"])
        hydrate_product_unit_fields(p)

        # Prevent Jinja sort/groupby crash when MongoDB has null category fields
        p["category"] = (p.get("category") or "Uncategorized").strip()
        p["sub_category"] = (p.get("sub_category") or "").strip()

        ratings = list(mongo.product_ratings.find({
            "product_id": p["_id"]
        }))

        rating_count = len(ratings)
        total_rating = 0

        for r in ratings:
            try:
                total_rating += float(r.get("rating") or 0)
            except (TypeError, ValueError):
                pass

        if rating_count > 0:
            avg_rating = round(total_rating / rating_count, 1)
        else:
            avg_rating = 0

        p["avg_rating"] = avg_rating
        p["rating_count"] = rating_count

        store = None
        if p.get("store_id"):
            store = mongo.stores.find_one({"_id": p["store_id"]})

        p["store_name"] = store.get("store_name") if store else ""
        p["store_id"] = str(p.get("store_id")) if p.get("store_id") else ""

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

    return render_template(
        'products.html',
        products=products,
        user=current_user()
    )

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

    result = []

    for p in products:
        store = None

        if p.get("store_id"):
            store = mongo.stores.find_one({"_id": p.get("store_id")})

        ratings = list(mongo.product_ratings.find({
            "product_id": p["_id"]
        }))

        rating_count = len(ratings)

        avg_rating = round(
            sum(float(r.get("rating") or 0) for r in ratings) / rating_count,
            1
        ) if rating_count else 0

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

    reviews = []

    for r in ratings[:20]:
        customer = None

        if r.get("user_id"):
            try:
                customer = mongo.users.find_one({"_id": ObjectId(r.get("user_id"))})
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
