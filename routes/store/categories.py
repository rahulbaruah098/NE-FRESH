"""Store categories route section.

Route decorators, endpoint names, URL paths, HTTP methods and function bodies are
preserved from the Step 6 source. Shared legacy helpers are imported from
``routes.store.shared`` during this transitional decomposition.
"""

from routes.store.shared import *

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
