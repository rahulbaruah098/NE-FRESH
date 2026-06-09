"""Api routes extracted from the updated app.py.

Logic, decorators, endpoint names and route paths are intentionally preserved.
Only the file location changed.
"""

from app_core import *


@app.route('/api/alerts/store', methods=['GET'])
@login_required(role='store')
def api_alerts_store():
    u = current_user()

    store = mongo.stores.find_one({"user_id": u["id"]})
    if not store:
        return jsonify({
            "ok": True,
            "new": [],
            "next_last_id": ""
        })

    last_id = (request.args.get("last_id") or "").strip()

    store_id_values = [store["_id"], str(store["_id"])]

    base_filter = {
        "store_id": {"$in": store_id_values}
    }

    # First poll: initialize only. Do not notify old orders.
    if not last_id:
        latest_order = mongo.orders.find_one(
            base_filter,
            sort=[("_id", -1)]
        )

        return jsonify({
            "ok": True,
            "new": [],
            "next_last_id": str(latest_order["_id"]) if latest_order else ""
        })

    try:
        last_obj_id = ObjectId(last_id)
    except Exception:
        # Invalid browser last_id: reset safely without popup.
        latest_order = mongo.orders.find_one(
            base_filter,
            sort=[("_id", -1)]
        )

        return jsonify({
            "ok": True,
            "new": [],
            "next_last_id": str(latest_order["_id"]) if latest_order else ""
        })

    query_filter = {
        "$and": [
            base_filter,
            {"_id": {"$gt": last_obj_id}}
        ]
    }

    rows = list(
        mongo.orders.find(query_filter).sort("_id", 1)
    )

    new_items = []
    next_last_id = last_id

    for o in rows:
        oid = str(o["_id"])
        next_last_id = oid

        total_payable = (
            float(o.get("total_amount") or 0)
            + float(o.get("delivery_fee") or 0)
            + float(o.get("tip_amount") or 0)
        )

        _create_store_notification(
            store,
            title="New order received",
            message=f"Order #{oid[-6:]} received. Payable amount ₹ {total_payable:.2f}.",
            notif_type="new_order",
            order=o,
            event_key=f"new-order-{oid}"
        )

        new_items.append({
            "order_id": oid,
            "total_payable": total_payable,
            "created_at": o.get("created_at", "")
        })

    return jsonify({
        "ok": True,
        "new": new_items,
        "next_last_id": next_last_id
    })

@app.route('/api/alerts/delivery', methods=['GET'])
@login_required(role='delivery')
def api_alerts_delivery():
    u = current_user()

    availability = _get_delivery_availability(u["id"])

    if not availability.get("active"):
        return jsonify({
            "ok": True,
            "active": False,
            "new": [],
            "next_last_id": ""
        })

    last_id = (request.args.get("last_id") or "").strip()

    base_filter = {
        "$and": [
            {
                "$or": [
                    {"delivery_partner_id": None},
                    {"delivery_partner_id": ""},
                    {"delivery_partner_id": {"$exists": False}}
                ]
            },
            {
            "status": {"$in": DELIVERY_ACTIONABLE_STATUSES}
            }
        ]
    }

    # First poll after active mode should initialize latest id only.
    # No offline backlog popup.
    if not last_id:
        latest_order = mongo.orders.find_one(
            base_filter,
            sort=[("_id", -1)]
        )

        return jsonify({
            "ok": True,
            "active": True,
            "new": [],
            "next_last_id": str(latest_order["_id"]) if latest_order else ""
        })

    try:
        last_obj_id = ObjectId(last_id)
    except Exception:
        latest_order = mongo.orders.find_one(
            base_filter,
            sort=[("_id", -1)]
        )

        return jsonify({
            "ok": True,
            "active": True,
            "new": [],
            "next_last_id": str(latest_order["_id"]) if latest_order else ""
        })

    query_filter = {
        "$and": [
            base_filter,
            {"_id": {"$gt": last_obj_id}}
        ]
    }

    rows = list(
        mongo.orders.find(query_filter).sort("_id", 1)
    )

    new_items = []
    next_last_id = last_id

    for o in rows:
        # Skip if order is no longer unassigned/actionable by the time poll reads it.
        if o.get("delivery_partner_id"):
            continue

        if o.get("status") not in DELIVERY_ACTIONABLE_STATUSES:
            continue

        distance_km = _driver_distance_to_store_km(o, availability)

        # If distance is available, only show nearby orders.
        if distance_km is not None and distance_km > DELIVERY_ACCEPT_RADIUS_KM:
            continue

        oid = str(o["_id"])
        next_last_id = oid

        total_payable = (
            float(o.get("total_amount") or 0)
            + float(o.get("delivery_fee") or 0)
            + float(o.get("tip_amount") or 0)
        )

        new_items.append({
            "order_id": oid,
            "created_at": o.get("created_at"),
            "total_payable": total_payable,
            "distance_km": distance_km
        })

    return jsonify({
        "ok": True,
        "active": True,
        "new": new_items,
        "next_last_id": next_last_id
    })

@app.route("/api/search/suggest")
def api_search_suggest():
    q = (request.args.get("q", "") or "").strip()

    if not q:
        return jsonify({
            "ok": True,
            "products": [],
            "stores": []
        })

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
        }).sort("created_at", -1).limit(8)
    )

    product_results = []

    for p in products:
        store_name = p.get("store_name", "")

        if p.get("store_id"):
            store = mongo.stores.find_one({"_id": p["store_id"]})
            if store:
                store_name = store.get("store_name", "")

        product_results.append({
            "id": str(p["_id"]),
            "name": p.get("name", ""),
            "store_name": store_name
        })

    stores = list(
        mongo.stores.find({
            "store_name": {"$regex": q, "$options": "i"}
        }).sort("store_name", 1).limit(6)
    )

    store_results = []

    for s in stores:
        store_results.append({
            "id": str(s["_id"]),
            "store_name": s.get("store_name", "")
        })

    return jsonify({
        "ok": True,
        "products": product_results,
        "stores": store_results
    })

@app.route("/api/auth/web-session", methods=["POST"])
@api_login_required
def api_create_web_session():
    ...
    ...
    return jsonify({
            'success': False,
            'error': f'Internal server error: {str(e)}'
        }), 500

@app.route('/api/categories', methods=['GET'])
def api_categories_list():
    # Since you don't have categories in your schema, return product types or empty
    return jsonify({
        'success': True,
        'categories': [
            {'id': 1, 'name': 'Fresh Chicken', 'slug': 'fresh-chicken'},
            {'id': 2, 'name': 'Processed', 'slug': 'processed'},
        ]
    })

@app.route('/api/checkout', methods=['POST'])
@api_login_required
def api_checkout(user_id):
    data = request.get_json(silent=True) or {}

    payment_method = (data.get("payment_method") or "COD").upper()
    tip_amount_raw = data.get("tip_amount", 0)
    address_id = data.get("address_id")

    try:
        tip_amount = float(tip_amount_raw or 0)
    except Exception:
        tip_amount = 0.0

    if tip_amount < 0:
        tip_amount = 0.0

    if tip_amount > 10000:
        tip_amount = 10000.0

    tip_amount = round(tip_amount, 2)

    cid = get_or_create_cart(user_id)

    cart_items = list(mongo.cart_items.find({"cart_id": cid}))

    if not cart_items:
        return jsonify({
            "success": False,
            "error": "Cart is empty"
        }), 400

    items = []

    for ci in cart_items:
        product = mongo.products.find_one({"_id": ci.get("product_id")})

        if not product:
            return jsonify({
                "success": False,
                "error": "One product no longer exists"
            }), 400

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

        if int(product.get("is_active") or 0) != 1 or stock_quantity <= 0:
            return jsonify({
                "success": False,
                "error": f"{product.get('name', 'Product')} is sold out"
            }), 400

        if quantity > stock_quantity:
            return jsonify({
                "success": False,
                "error": f"{product.get('name', 'Product')} has only {stock_quantity:.2f} {unit_label} available"
            }), 400

        items.append({
            "product_id": product["_id"],
            "product_name": product.get("name", ""),
            "quantity": quantity,
            "cart_quantity": quantity,
            "unit_type": unit_type,
            "unit_label": unit_label,
            "price_per_unit": price_per_unit,
            "unit_price": price_per_unit,
            "line_total": quantity * price_per_unit,
            "image_path": product.get("image_path", ""),
            "store_id": product.get("store_id")
        })

    store_ids = sorted(set([str(i["store_id"]) for i in items if i.get("store_id")]))

    if len(store_ids) != 1:
        return jsonify({
            "success": False,
            "error": "Please order from one store at a time"
        }), 400

    store_id = items[0]["store_id"]

    store = mongo.stores.find_one({"_id": store_id})

    if not store:
        return jsonify({
            "success": False,
            "error": "Store not found"
        }), 400

    address = None

    if address_id:
        try:
            address_obj_id = ObjectId(str(address_id))
            address = mongo.addresses.find_one({
                "_id": address_obj_id,
                "user_id": str(user_id)
            })
        except Exception:
            address = None

    if not address:
        address = mongo.addresses.find_one(
            {"user_id": str(user_id), "is_default": 1}
        )

    if not address:
        return jsonify({
            "success": False,
            "error": "Please add/select a delivery address"
        }), 400

    pincode = (address.get("pincode") or "").strip()

    if not is_serviceable_pincode(pincode):
        return jsonify({
            "success": False,
            "error": "Invalid pincode"
        }), 400

    if not is_assam_state(address.get("state")):
        return jsonify({
            "success": False,
            "error": "Delivery is currently available only within Assam"
        }), 400

    items_total = sum(float(i["line_total"] or 0) for i in items)

    store_lat = store.get("latitude")
    store_lng = store.get("longitude")
    addr_lat = address.get("latitude")
    addr_lng = address.get("longitude")

    km = haversine_km(store_lat, store_lng, addr_lat, addr_lng)

    # Assam-wide delivery: no distance blocking.
    # Keep base delivery fee for all Assam addresses.
    delivery_fee = BASE_DELIVERY_FEE_INR

    total_payable = float(items_total) + float(delivery_fee) + float(tip_amount)
    now = datetime.utcnow().isoformat()

    order_result = mongo.orders.insert_one({
        "user_id": str(user_id),
        "customer_name": "",
        "customer_phone": "",
        "store_id": store_id,
        "store_name": store.get("store_name", ""),
        "total_amount": float(items_total),
        "status": "PLACED",
        "payment_status": "PENDING",
        "delivery_partner_id": None,
        "delivery_fee": float(delivery_fee),
        "distance_km": float(km) if km is not None else None,
        "tip_amount": float(tip_amount),
        "total_payable": float(total_payable),
        "payment_method": payment_method,
        "created_at": now
    })

    order_id = order_result.inserted_id

    for item in items:
        order_item_doc = {
            "order_id": order_id,
            "product_id": item["product_id"],
            "product_name": item["product_name"],
            "quantity": float(item.get("quantity") or item.get("cart_quantity") or 0),
            "cart_quantity": float(item.get("quantity") or item.get("cart_quantity") or 0),
            "unit_type": item.get("unit_type") or "WEIGHT",
            "unit_label": item.get("unit_label") or "kg",
            "price_per_unit": float(item.get("price_per_unit") or item.get("unit_price") or 0),
            "unit_price": float(item.get("price_per_unit") or item.get("unit_price") or 0),
            "line_total": float(item.get("line_total") or 0),
            "image_path": item.get("image_path", "")
        }

        mongo.order_items.insert_one(order_item_doc)

        deduct_qty = float(order_item_doc.get("quantity") or 0)

        mongo.products.update_one(
            {"_id": item["product_id"]},
            {"$inc": {"stock_quantity": -deduct_qty}}
        )

        updated_product = mongo.products.find_one({"_id": item["product_id"]})

        if updated_product and float(updated_product.get("stock_quantity") or 0) <= 0:
            mongo.products.update_one(
                {"_id": item["product_id"]},
                {"$set": {"stock_quantity": 0, "is_active": 0}}
            )

    mongo.transactions.insert_one({
        "order_id": order_id,
        "amount": float(total_payable),
        "payment_method": payment_method,
        "status": "PENDING",
        "created_at": now
    })

    mongo.order_addresses.insert_one({
        "order_id": order_id,
        "line1": address.get("line1"),
        "line2": address.get("line2"),
        "city": address.get("city"),
        "state": address.get("state"),
        "pincode": address.get("pincode"),
        "latitude": address.get("latitude"),
        "longitude": address.get("longitude"),
        "created_at": now
    })

    mongo.order_events.insert_one({
        "order_id": order_id,
        "status": "PLACED",
        "note": "Order placed from API",
        "created_at": now
    })

    mongo.cart_items.delete_many({"cart_id": cid})

    return jsonify({
        "success": True,
        "order_id": str(order_id),
        "message": "Order placed successfully",
        "total_amount": float(items_total),
        "delivery_fee": float(delivery_fee),
        "tip_amount": float(tip_amount),
        "total_payable": float(total_payable)
    }), 201
