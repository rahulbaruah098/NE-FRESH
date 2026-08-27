"""Store reviews route section.

Route decorators, endpoint names, URL paths, HTTP methods and function bodies are
preserved from the Step 6 source. Shared legacy helpers are imported from
``routes.store.shared`` during this transitional decomposition.
"""

from routes.store.shared import *

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
