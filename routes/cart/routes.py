"""Cart routes extracted from the updated app.py.

Logic, decorators, endpoint names and route paths are intentionally preserved.
Only the file location changed.
"""

from app_core import *


@app.route('/cart')
@login_required()
def cart_page():
    u = current_user()
    cid = get_or_create_cart(u["id"])

    cart_items = list(mongo.cart_items.find({"cart_id": cid}).sort("created_at", -1))

    items = []

    for ci in cart_items:
        product = mongo.products.find_one({"_id": ci.get("product_id")})
        if not product:
            continue

        store = None
        if product.get("store_id"):
            store = mongo.stores.find_one({"_id": product.get("store_id")})

        hydrate_product_unit_fields(product)

        quantity = cart_item_quantity(ci)
        unit_type = ci.get("unit_type") or product.get("unit_type") or "WEIGHT"
        unit_label = ci.get("unit_label") or product.get("unit_label") or "kg"
        price_per_unit = float(
            ci.get("price_per_unit_snapshot")
            if ci.get("price_per_unit_snapshot") is not None
            else product.get("price_per_unit") or 0
        )
        stock_quantity = float(product.get("stock_quantity") or 0)
        line_total = quantity * price_per_unit

        item = {
            "cart_item_id": str(ci["_id"]),

            # New unit-aware fields.
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
            "name": product.get("name", ""),
            "image_path": product.get("image_path", ""),
            "is_active": int(product.get("is_active") or 0),
            "store_id": str(product.get("store_id")) if product.get("store_id") else "",
            "store_name": store.get("store_name") if store else "",
        }

        items.append(item)

    total = sum([
        float(row.get("line_total") or 0)
        for row in items
    ])

    return render_template('cart.html', items=items, total=total, user=u)

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

    if quantity > stock:
        return jsonify({
            'ok': False,
            'msg': f'Only {stock:.2f} {unit_label} stock is available. Please enter a quantity equal to or below available stock.'
        }), 409

    cid = get_or_create_cart(user_id)

    existing_items = list(mongo.cart_items.find({"cart_id": cid}))

    for item in existing_items:
        existing_product = mongo.products.find_one({"_id": item.get("product_id")})
        if existing_product and existing_product.get("store_id") != new_store_id:
            return jsonify({
                "ok": False,
                "code": "DIFF_STORE",
                "msg": "Your cart already has items from another store. Please clear the cart first to add from this store."
            }), 409

    existing_cart_item = mongo.cart_items.find_one({
        "cart_id": cid,
        "product_id": product_obj_id
    })

    now = datetime.utcnow().isoformat()
    line_total = float(quantity or 0) * float(price_per_unit or 0)

    cart_update_data = {
        "cart_quantity": quantity,
        "quantity": quantity,
        "unit_type": unit_type,
        "unit_label": unit_label,
        "price_per_unit_snapshot": price_per_unit,
        "line_total": line_total,

        "updated_at": now
    }

    if existing_cart_item:
        mongo.cart_items.update_one(
            {"_id": existing_cart_item["_id"]},
            {
                "$set": cart_update_data
            }
        )
    else:
        cart_update_data.update({
            "cart_id": cid,
            "product_id": product_obj_id,
            "created_at": now
        })

        mongo.cart_items.insert_one(cart_update_data)

    cart_count = mongo.cart_items.count_documents({"cart_id": cid})

    return jsonify({
        'ok': True,
        'msg': f'Added {quantity:g} {unit_label} to cart',
        'cart_count': cart_count
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
        product = mongo.products.find_one({"_id": ci.get("product_id")})

        if not product:
            continue

        hydrate_product_unit_fields(product)

        quantity = cart_item_quantity(ci)
        unit_type = ci.get("unit_type") or product.get("unit_type") or "WEIGHT"
        unit_label = ci.get("unit_label") or product.get("unit_label") or "kg"
        price_per_unit = float(
            ci.get("price_per_unit_snapshot")
            if ci.get("price_per_unit_snapshot") is not None
            else product.get("price_per_unit") or 0
        )
        stock_quantity = float(product.get("stock_quantity") or 0)
        line_total = quantity * price_per_unit

        items.append({
            'id': str(ci['_id']),
            'product_id': str(product['_id']),
            'name': product.get('name', ''),

            # New unit-aware fields.
            'cart_quantity': quantity,
            'quantity': quantity,
            'unit_type': unit_type,
            'unit_label': unit_label,
            'price_per_unit': price_per_unit,
            'stock_quantity': stock_quantity,
            'quantity_min': float(product.get("quantity_min") or 1),
            'quantity_step': float(product.get("quantity_step") or 1),
            'quantity_message': product.get("quantity_message") or f"Minimum {float(product.get('quantity_min') or 1):g} {unit_label}",
            'line_total': line_total,

            'image_path': product.get('image_path', ''),
            'store_id': str(product.get('store_id')) if product.get('store_id') else None,
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
