"""Cart routes extracted from the updated app.py.

Logic, decorators, endpoint names and route paths are intentionally preserved.
Only the file location changed.
"""

from app_core import *


# =========================================================
# CART PRODUCT/BUNDLE HELPERS
# =========================================================
def _cart_money_float(value, default=0.0):
    try:
        if value is None or str(value).strip() == "":
            return float(default)
        return float(value)
    except Exception:
        return float(default)


def _cart_quantity_int(value, default=1):
    try:
        qty = int(float(value or default))
    except Exception:
        qty = int(default)

    if qty < 1:
        qty = 1

    return qty


def _cart_safe_object_id(value):
    try:
        if ObjectId.is_valid(str(value)):
            return ObjectId(str(value))
    except Exception:
        pass

    return None


def _cart_bundle_store_id(bundle):
    if not bundle:
        return None

    return bundle.get("store_id") or bundle.get("store_id_str")


def _cart_existing_item_store_id(item):
    if not item:
        return None

    if item.get("store_id"):
        return item.get("store_id")

    item_type = (item.get("item_type") or "product").strip().lower()

    if item_type == "bundle" or item.get("bundle_id"):
        bundle_id = item.get("bundle_id") or item.get("bundle_id_str")
        bundle_obj_id = _cart_safe_object_id(bundle_id)
        bundle = None

        if bundle_obj_id:
            bundle = mongo.product_bundles.find_one({"_id": bundle_obj_id})

        if not bundle and bundle_id:
            bundle = mongo.product_bundles.find_one({"bundle_id_str": str(bundle_id)})

        return _cart_bundle_store_id(bundle)

    product_id = item.get("product_id")
    product = None

    if product_id:
        product = mongo.products.find_one({"_id": product_id})

    if not product and product_id:
        product_obj_id = _cart_safe_object_id(product_id)
        if product_obj_id:
            product = mongo.products.find_one({"_id": product_obj_id})

    return product.get("store_id") if product else None


def _cart_same_store_check(cart_id, new_store_id):
    existing_items = list(mongo.cart_items.find({"cart_id": cart_id}))

    for item in existing_items:
        existing_store_id = _cart_existing_item_store_id(item)

        if existing_store_id and new_store_id and str(existing_store_id) != str(new_store_id):
            return False

    return True


def _cart_refresh_bundle_for_cart(bundle, notify_store=False):
    """
    Builds a live bundle copy from current product records.
    This keeps cart validation correct if product stock/price changed after bundle creation.
    """
    return build_live_product_bundle(
        bundle,
        notify_store=notify_store,
        notification_context="cart_bundle"
    )


def _cart_hydrate_product_item(ci):
    product = mongo.products.find_one({"_id": ci.get("product_id")})

    if not product and ci.get("product_id"):
        product_obj_id = _cart_safe_object_id(ci.get("product_id"))
        if product_obj_id:
            product = mongo.products.find_one({"_id": product_obj_id})

    if not product:
        return None

    store = None
    if product.get("store_id"):
        store = mongo.stores.find_one({"_id": product.get("store_id")})

    hydrate_product_unit_fields(product)

    quantity = cart_item_quantity(ci)
    unit_type = ci.get("unit_type") or product.get("unit_type") or "WEIGHT"
    unit_label = ci.get("unit_label") or product.get("unit_label") or "kg"
    price_per_unit = _cart_money_float(
        ci.get("price_per_unit_snapshot")
        if ci.get("price_per_unit_snapshot") is not None
        else product.get("price_per_unit"),
        0
    )
    stock_quantity = _cart_money_float(product.get("stock_quantity"), 0)
    line_total = quantity * price_per_unit

    return {
        "item_type": "product",
        "is_bundle": False,
        "cart_item_id": str(ci["_id"]),
        "id": str(ci["_id"]),
        "cart_quantity": quantity,
        "quantity": quantity,
        "unit_type": unit_type,
        "unit_label": unit_label,
        "price_per_unit": price_per_unit,
        "stock_quantity": stock_quantity,
        "quantity_min": float(product.get("quantity_min") or 1),
        "quantity_step": float(product.get("quantity_step") or 1),
        "quantity_message": product.get("quantity_message") or f"Minimum {float(product.get('quantity_min') or 1):g} {unit_label}",
        "line_total": line_total,
        "product_id": str(product["_id"]),
        "bundle_id": "",
        "name": product.get("name", ""),
        "image_path": product.get("image_path", ""),
        "is_active": int(product.get("is_active") or 0),
        "store_id": str(product.get("store_id")) if product.get("store_id") else "",
        "store_name": store.get("store_name") if store else "",
    }


def _cart_hydrate_bundle_item(ci):
    bundle_id = ci.get("bundle_id") or ci.get("bundle_id_str")
    bundle_obj_id = _cart_safe_object_id(bundle_id)
    bundle = None

    if bundle_obj_id:
        bundle = mongo.product_bundles.find_one({"_id": bundle_obj_id})

    if not bundle and bundle_id:
        bundle = mongo.product_bundles.find_one({"bundle_id_str": str(bundle_id)})

    quantity = _cart_quantity_int(ci.get("cart_quantity") or ci.get("quantity"), 1)
    price_per_unit = _cart_money_float(
        ci.get("bundle_price_snapshot")
        if ci.get("bundle_price_snapshot") is not None
        else ci.get("price_per_unit_snapshot"),
        0
    )
    line_total = _cart_money_float(ci.get("line_total"), price_per_unit * quantity)

    bundle_items = ci.get("bundle_items_snapshot") or []
    stock_quantity = _cart_money_float(ci.get("max_bundle_stock_snapshot"), 0)
    active = int(ci.get("is_active_snapshot", 1) or 0)
    image_path = ci.get("image_path") or ""
    store_name = ci.get("store_name") or ""
    store_id = ci.get("store_id") or ""

    if bundle:
        live_bundle = _cart_refresh_bundle_for_cart(bundle, notify_store=True) or bundle
        bundle_items = live_bundle.get("items") or bundle_items
        stock_quantity = _cart_money_float(live_bundle.get("max_bundle_stock"), stock_quantity)
        active = int(live_bundle.get("is_active", active) or 0)
        image_path = live_bundle.get("image_path") or image_path
        store_name = live_bundle.get("store_name") or store_name
        store_id = live_bundle.get("store_id_str") or str(live_bundle.get("store_id") or store_id)

        live_price = _cart_money_float(live_bundle.get("bundle_price"), 0)
        if live_price > 0:
            price_per_unit = live_price
            line_total = price_per_unit * quantity

        live_savings = _cart_money_float(live_bundle.get("savings_amount"), 0)
    else:
        live_savings = _cart_money_float(ci.get("bundle_savings_snapshot"), 0)

    return {
        "item_type": "bundle",
        "is_bundle": True,
        "cart_item_id": str(ci["_id"]),
        "id": str(ci["_id"]),
        "cart_quantity": quantity,
        "quantity": quantity,
        "unit_type": "COUNT",
        "unit_label": "bundle",
        "price_per_unit": price_per_unit,
        "stock_quantity": stock_quantity,
        "quantity_min": 1,
        "quantity_step": 1,
        "quantity_message": "Minimum 1 bundle",
        "line_total": line_total,
        "product_id": "",
        "bundle_id": str(bundle_id or ""),
        "name": ci.get("bundle_name_snapshot") or (bundle.get("bundle_name") if bundle else "Product Bundle"),
        "bundle_name": ci.get("bundle_name_snapshot") or (bundle.get("bundle_name") if bundle else "Product Bundle"),
        "bundle_items": bundle_items,
        "bundle_savings": live_savings,
        "image_path": image_path,
        "is_active": active,
        "store_id": str(store_id) if store_id else "",
        "store_name": store_name,
    }


def _cart_hydrate_item(ci):
    item_type = (ci.get("item_type") or "product").strip().lower()

    if item_type == "bundle" or ci.get("bundle_id"):
        return _cart_hydrate_bundle_item(ci)

    return _cart_hydrate_product_item(ci)


@app.route('/cart')
@login_required()
def cart_page():
    u = current_user()
    cid = get_or_create_cart(u["id"])

    cart_items = list(mongo.cart_items.find({"cart_id": cid}).sort("created_at", -1))

    items = []

    for ci in cart_items:
        item = _cart_hydrate_item(ci)
        if item:
            items.append(item)

    total = sum([
        float(row.get("line_total") or 0)
        for row in items
    ])

    return render_template('cart.html', items=items, total=total, user=u)

@app.route('/api/cart/add-bundle', methods=['POST'])
@app.route('/api/cart/add', methods=['POST'])
@api_login_required
def api_cart_add(user_id):
    data = request.get_json(silent=True) or {}

    user_doc = None

    try:
        user_doc = mongo.users.find_one({"_id": ObjectId(user_id)})
    except Exception:
        user_doc = mongo.users.find_one({"_id": user_id})

    if not user_doc:
        return jsonify({
            "ok": False,
            "msg": "Please log in first."
        }), 401

    if user_doc.get("role") != "customer":
        return jsonify({
            "ok": False,
            "msg": "Only customer accounts can add products to cart."
        }), 403

    item_type = (data.get("item_type") or request.form.get("item_type") or "").strip().lower()
    bundle_id_raw = data.get("bundle_id") or request.form.get("bundle_id")

    # ---------------------------------------------------------
    # Bundle add-to-cart branch.
    # Existing bundle rows are incremented, then revalidated
    # against live child-product stock.
    # ---------------------------------------------------------
    if item_type == "bundle" or bundle_id_raw:
        bundle_obj_id = _cart_safe_object_id(bundle_id_raw)

        if not bundle_obj_id:
            return jsonify({'ok': False, 'msg': 'Invalid bundle'}), 400

        bundle = mongo.product_bundles.find_one({"_id": bundle_obj_id})

        if not bundle:
            return jsonify({'ok': False, 'msg': 'Product bundle not found'}), 404

        bundle = _cart_refresh_bundle_for_cart(bundle, notify_store=True) or bundle

        quantity_raw = (
            data.get("quantity")
            or request.form.get("quantity")
            or data.get("cart_quantity")
            or request.form.get("cart_quantity")
            or 1
        )
        quantity = _cart_quantity_int(quantity_raw, 1)

        cid = get_or_create_cart(user_id)
        new_store_id = _cart_bundle_store_id(bundle)

        if not _cart_same_store_check(cid, new_store_id):
            return jsonify({
                "ok": False,
                "code": "DIFF_STORE",
                "msg": "Your cart already has items from another store. Please clear the cart first to add from this store."
            }), 409

        existing_cart_item = mongo.cart_items.find_one({
            "cart_id": cid,
            "item_type": "bundle",
            "$or": [
                {"bundle_id": bundle_obj_id},
                {"bundle_id": str(bundle_obj_id)},
                {"bundle_id_str": str(bundle_obj_id)}
            ]
        })

        final_quantity = quantity

        if existing_cart_item:
            existing_quantity = _cart_quantity_int(
                existing_cart_item.get("cart_quantity")
                or existing_cart_item.get("quantity")
                or 1,
                1
            )
            final_quantity = existing_quantity + quantity

        ok, bundle_error = validate_product_bundle_for_cart(
            bundle,
            quantity=final_quantity
        )

        if not ok:
            return jsonify({'ok': False, 'msg': bundle_error}), 409

        now = datetime.utcnow().isoformat()
        cart_update_data = build_bundle_cart_snapshot(
            bundle,
            quantity=final_quantity
        )
        cart_update_data.update({
            "store_id": bundle.get("store_id"),
            "store_id_str": str(bundle.get("store_id") or bundle.get("store_id_str") or ""),
            "store_name": bundle.get("store_name") or "",
            "image_path": bundle.get("image_path") or "",
            "max_bundle_stock_snapshot": int(bundle.get("max_bundle_stock") or 0),
            "is_active_snapshot": int(bundle.get("is_active", 1) or 0),
            "updated_at": now
        })

        if existing_cart_item:
            mongo.cart_items.update_one(
                {"_id": existing_cart_item["_id"]},
                {"$set": cart_update_data}
            )
            cart_item_id = str(existing_cart_item["_id"])
        else:
            cart_update_data.update({
                "cart_id": cid,
                "created_at": now
            })
            inserted = mongo.cart_items.insert_one(cart_update_data)
            cart_item_id = str(inserted.inserted_id)

        cart_count = mongo.cart_items.count_documents({"cart_id": cid})

        return jsonify({
            'ok': True,
            'msg': f'Added {quantity:g} bundle to cart',
            'cart_count': cart_count,
            'cart_item_id': cart_item_id,
            'item_type': 'bundle',
            'bundle_id': str(bundle_obj_id),
            'bundle_name': bundle.get('bundle_name') or 'Product Bundle',
            'cart_quantity': final_quantity,
            'unit_label': 'bundle',
            'line_total': cart_update_data.get('line_total', 0)
        })

    # ---------------------------------------------------------
    # Existing normal product add-to-cart branch.
    # Existing product rows are incremented, then the combined
    # quantity is validated against live stock.
    # ---------------------------------------------------------
    product_id_raw = data.get("product_id") or request.form.get("product_id")

    try:
        product_obj_id = ObjectId(product_id_raw)
    except Exception:
        return jsonify({'ok': False, 'msg': 'Invalid product'}), 400

    product = mongo.products.find_one({"_id": product_obj_id})

    if not product:
        return jsonify({'ok': False, 'msg': 'Product not found'}), 404

    hydrate_product_unit_fields(product)

    unit_type = product.get("unit_type") or "WEIGHT"
    unit_label = product.get("unit_label") or "kg"

    quantity_raw = (
        data.get("quantity")
        or request.form.get("quantity")
        or data.get("cart_quantity")
        or request.form.get("cart_quantity")
        or 1
    )

    quantity, quantity_error = normalize_quantity_by_unit(
        quantity_raw,
        unit_type,
        unit_label
    )

    if quantity_error:
        return jsonify({'ok': False, 'msg': quantity_error}), 400

    try:
        quantity_min = float(product.get("quantity_min") or product.get("min_order_quantity") or 0)
    except (TypeError, ValueError):
        quantity_min = 0.0

    if quantity_min <= 0:
        rules = unit_quantity_rules(unit_type, unit_label)
        quantity_min = float(rules.get("min") or 1)

    if unit_type == "COUNT":
        quantity_min = int(round(quantity_min))

        if quantity_min < 1:
            quantity_min = 1

    if quantity < quantity_min:
        return jsonify({
            "ok": False,
            "code": "MIN_QTY",
            "msg": f"Minimum order quantity for this product is {quantity_min:g} {unit_label}."
        }), 400

    stock = float(product.get("stock_quantity") or 0)
    price_per_unit = float(product.get("price_per_unit") or 0)
    active = int(product.get("is_active") or 0)
    new_store_id = product.get("store_id")

    if active != 1 or stock <= 0:
        return jsonify({'ok': False, 'msg': 'This item is sold out'}), 409

    cid = get_or_create_cart(user_id)

    if not _cart_same_store_check(cid, new_store_id):
        return jsonify({
            "ok": False,
            "code": "DIFF_STORE",
            "msg": "Your cart already has items from another store. Please clear the cart first to add from this store."
        }), 409

    existing_cart_item = mongo.cart_items.find_one({
        "cart_id": cid,
        "product_id": product_obj_id,
        "$or": [
            {"item_type": {"$exists": False}},
            {"item_type": "product"}
        ]
    })

    final_quantity = quantity

    if existing_cart_item:
        existing_quantity = cart_item_quantity(existing_cart_item)

        combined_quantity, combined_error = normalize_quantity_by_unit(
            float(existing_quantity or 0) + float(quantity or 0),
            unit_type,
            unit_label
        )

        if combined_error:
            return jsonify({'ok': False, 'msg': combined_error}), 400

        final_quantity = combined_quantity

    if final_quantity > stock:
        return jsonify({
            'ok': False,
            'msg': f'Only {stock:.2f} {unit_label} stock is available. Please enter a quantity equal to or below available stock.'
        }), 409

    now = datetime.utcnow().isoformat()
    line_total = float(final_quantity or 0) * float(price_per_unit or 0)

    cart_update_data = {
        "item_type": "product",
        "cart_quantity": final_quantity,
        "quantity": final_quantity,
        "unit_type": unit_type,
        "unit_label": unit_label,
        "price_per_unit_snapshot": price_per_unit,
        "line_total": line_total,
        "store_id": product.get("store_id"),
        "store_id_str": str(product.get("store_id") or ""),
        "updated_at": now
    }

    if existing_cart_item:
        mongo.cart_items.update_one(
            {"_id": existing_cart_item["_id"]},
            {
                "$set": cart_update_data
            }
        )

        cart_item_id = str(existing_cart_item["_id"])
    else:
        cart_update_data.update({
            "cart_id": cid,
            "product_id": product_obj_id,
            "created_at": now
        })

        inserted = mongo.cart_items.insert_one(cart_update_data)
        cart_item_id = str(inserted.inserted_id)

    cart_count = mongo.cart_items.count_documents({"cart_id": cid})

    return jsonify({
        'ok': True,
        'msg': f'Added {quantity:g} {unit_label} to cart',
        'cart_count': cart_count,
        'cart_item_id': cart_item_id,
        'item_type': 'product',
        'product_id': str(product_obj_id),
        'cart_quantity': final_quantity,
        'unit_label': unit_label
    })


@app.route('/api/cart/update', methods=['POST'])
@api_login_required
def api_cart_update(user_id):
    data = request.get_json(silent=True) or {}
    item_id_raw = data.get("item_id") or request.form.get("item_id")
    quantity_raw = (
        data.get("quantity")
        or request.form.get("quantity")
        or data.get("cart_quantity")
        or request.form.get("cart_quantity")
    )

    item_obj_id = _cart_safe_object_id(item_id_raw)

    if not item_obj_id:
        return jsonify({'ok': False, 'msg': 'Invalid cart item'}), 400

    cid = get_or_create_cart(user_id)

    cart_item = mongo.cart_items.find_one({
        "_id": item_obj_id,
        "cart_id": cid
    })

    if not cart_item:
        return jsonify({'ok': False, 'msg': 'Cart item not found'}), 404

    item_type = (cart_item.get("item_type") or "product").strip().lower()
    now = datetime.utcnow().isoformat()

    # ---------------------------------------------------------
    # Bundle quantity update.
    # ---------------------------------------------------------
    if item_type == "bundle" or cart_item.get("bundle_id"):
        try:
            quantity_number = float(quantity_raw)
        except (TypeError, ValueError):
            return jsonify({'ok': False, 'msg': 'Please enter a valid bundle quantity.'}), 400

        if quantity_number < 1 or not quantity_number.is_integer():
            return jsonify({
                'ok': False,
                'msg': 'Bundle quantity must be a whole number of 1 or more.'
            }), 400

        quantity = int(quantity_number)

        bundle_id = cart_item.get("bundle_id") or cart_item.get("bundle_id_str")
        bundle_obj_id = _cart_safe_object_id(bundle_id)
        bundle = None

        if bundle_obj_id:
            bundle = mongo.product_bundles.find_one({"_id": bundle_obj_id})

        if not bundle and bundle_id:
            bundle = mongo.product_bundles.find_one({
                "bundle_id_str": str(bundle_id)
            })

        if not bundle:
            return jsonify({'ok': False, 'msg': 'Product bundle not found'}), 404

        bundle = _cart_refresh_bundle_for_cart(bundle, notify_store=True) or bundle

        ok, bundle_error = validate_product_bundle_for_cart(
            bundle,
            quantity=quantity
        )

        if not ok:
            return jsonify({'ok': False, 'msg': bundle_error}), 409

        cart_update_data = build_bundle_cart_snapshot(
            bundle,
            quantity=quantity
        )
        cart_update_data.update({
            "store_id": bundle.get("store_id"),
            "store_id_str": str(bundle.get("store_id") or bundle.get("store_id_str") or ""),
            "store_name": bundle.get("store_name") or "",
            "image_path": bundle.get("image_path") or "",
            "max_bundle_stock_snapshot": int(bundle.get("max_bundle_stock") or 0),
            "is_active_snapshot": int(bundle.get("is_active", 1) or 0),
            "updated_at": now
        })

        mongo.cart_items.update_one(
            {"_id": cart_item["_id"], "cart_id": cid},
            {"$set": cart_update_data}
        )

        return jsonify({
            "ok": True,
            "cart_item_id": str(cart_item["_id"]),
            "item_type": "bundle",
            "bundle_id": str(bundle.get("_id") or bundle_id or ""),
            "cart_quantity": quantity,
            "unit_label": "bundle",
            "stock_quantity": int(bundle.get("max_bundle_stock") or 0),
            "line_total": cart_update_data.get("line_total", 0)
        })

    # ---------------------------------------------------------
    # Product quantity update.
    # ---------------------------------------------------------
    product_id = cart_item.get("product_id")
    product_obj_id = _cart_safe_object_id(product_id)

    if not product_obj_id:
        return jsonify({'ok': False, 'msg': 'Invalid product'}), 400

    product = mongo.products.find_one({"_id": product_obj_id})

    if not product:
        return jsonify({'ok': False, 'msg': 'Product not found'}), 404

    hydrate_product_unit_fields(product)

    unit_type = product.get("unit_type") or "WEIGHT"
    unit_label = product.get("unit_label") or "kg"

    quantity, quantity_error = normalize_quantity_by_unit(
        quantity_raw,
        unit_type,
        unit_label
    )

    if quantity_error:
        return jsonify({'ok': False, 'msg': quantity_error}), 400

    try:
        quantity_min = float(product.get("quantity_min") or product.get("min_order_quantity") or 0)
    except (TypeError, ValueError):
        quantity_min = 0.0

    if quantity_min <= 0:
        rules = unit_quantity_rules(unit_type, unit_label)
        quantity_min = float(rules.get("min") or 1)

    if unit_type == "COUNT":
        quantity_min = int(round(quantity_min))

        if quantity_min < 1:
            quantity_min = 1

    if quantity < quantity_min:
        return jsonify({
            "ok": False,
            "code": "MIN_QTY",
            "msg": f"Minimum order quantity for this product is {quantity_min:g} {unit_label}."
        }), 400

    stock = float(product.get("stock_quantity") or 0)
    price_per_unit = float(product.get("price_per_unit") or 0)
    active = int(product.get("is_active") or 0)

    if active != 1 or stock <= 0:
        return jsonify({'ok': False, 'msg': 'This item is sold out'}), 409

    if quantity > stock:
        return jsonify({
            'ok': False,
            'msg': f'Only {stock:.2f} {unit_label} stock is available. Please enter a quantity equal to or below available stock.'
        }), 409

    line_total = float(quantity or 0) * float(price_per_unit or 0)

    mongo.cart_items.update_one(
        {"_id": cart_item["_id"], "cart_id": cid},
        {
            "$set": {
                "item_type": "product",
                "cart_quantity": quantity,
                "quantity": quantity,
                "unit_type": unit_type,
                "unit_label": unit_label,
                "price_per_unit_snapshot": price_per_unit,
                "line_total": line_total,
                "store_id": product.get("store_id"),
                "store_id_str": str(product.get("store_id") or ""),
                "updated_at": now
            }
        }
    )

    return jsonify({
        "ok": True,
        "cart_item_id": str(cart_item["_id"]),
        "item_type": "product",
        "product_id": str(product_obj_id),
        "cart_quantity": quantity,
        "unit_label": unit_label,
        "stock_quantity": stock,
        "line_total": line_total
    })


@app.route('/api/cart/remove', methods=['POST'])
@api_login_required
def api_cart_remove(user_id):
    data = request.get_json(silent=True) or {}
    item_id = data.get('item_id') or request.form.get('item_id')

    try:
        item_obj_id = ObjectId(item_id)
    except Exception:
        return jsonify({'ok': False, 'msg': 'Invalid item'}), 400

    cid = get_or_create_cart(user_id)

    mongo.cart_items.delete_one({
        "_id": item_obj_id,
        "cart_id": cid
    })

    cart_count = mongo.cart_items.count_documents({"cart_id": cid})

    return jsonify({
        'ok': True,
        'cart_count': cart_count
    })

@app.route('/api/cart', methods=['GET'])
@api_login_required
def api_cart_get(user_id):
    cid = get_or_create_cart(user_id)

    cart_items = list(
        mongo.cart_items.find({"cart_id": cid}).sort("created_at", -1)
    )

    items = []

    for ci in cart_items:
        item = _cart_hydrate_item(ci)

        if not item:
            continue

        items.append({
            'id': item.get('id') or item.get('cart_item_id'),
            'cart_item_id': item.get('cart_item_id'),
            'item_type': item.get('item_type', 'product'),
            'is_bundle': bool(item.get('is_bundle')),
            'product_id': item.get('product_id') or '',
            'bundle_id': item.get('bundle_id') or '',
            'name': item.get('name', ''),
            'bundle_name': item.get('bundle_name') or item.get('name', ''),
            'bundle_items': item.get('bundle_items') or [],
            'bundle_savings': item.get('bundle_savings') or 0,
            'cart_quantity': item.get('cart_quantity') or 0,
            'quantity': item.get('quantity') or 0,
            'unit_type': item.get('unit_type') or 'COUNT',
            'unit_label': item.get('unit_label') or 'unit',
            'price_per_unit': item.get('price_per_unit') or 0,
            'stock_quantity': item.get('stock_quantity') or 0,
            'quantity_min': item.get('quantity_min') or 1,
            'quantity_step': item.get('quantity_step') or 1,
            'quantity_message': item.get('quantity_message') or '',
            'line_total': item.get('line_total') or 0,
            'image_path': item.get('image_path') or '',
            'store_id': item.get('store_id') or None,
            'store_name': item.get('store_name') or '',
        })

    total = sum([
        float(item.get('line_total') or 0)
        for item in items
    ])

    return jsonify({
        'success': True,
        'items': items,
        'total': float(total)
    }), 200

@app.route('/api/cart/clear', methods=['POST'])
@api_login_required
def api_cart_clear(user_id):
    cid = get_or_create_cart(user_id)

    mongo.cart_items.delete_many({"cart_id": cid})

    return jsonify({
        'success': True,
        'cart_count': 0
    })
