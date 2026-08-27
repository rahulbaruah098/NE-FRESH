"""Store products route section.

Route decorators, endpoint names, URL paths, HTTP methods and function bodies are
preserved from the Step 6 source. Shared legacy helpers are imported from
``routes.store.shared`` during this transitional decomposition.
"""

from routes.store.shared import *

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
