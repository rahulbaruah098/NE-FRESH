"""Orders routes extracted from the updated app.py.

Logic, decorators, endpoint names and route paths are intentionally preserved.
Only the file location changed.
"""

from app_core import *


@app.route('/orders/<oid>/cancel', methods=['POST'])
@login_required()
def order_cancel(oid):
    u = current_user()

    try:
        oid_obj = ObjectId(oid)
    except Exception:
        flash("Invalid order.", "danger")
        return redirect(url_for("orders"))

    order_doc = mongo.orders.find_one({
        "_id": oid_obj,
        "user_id": u["id"]
    })

    if not order_doc:
        flash("Order not found.", "danger")
        return redirect(url_for("orders"))

    if order_doc.get("status") not in CANCELLABLE_STATUSES:
        flash("This order can no longer be cancelled.", "warning")
        return redirect(url_for("order_track", oid=oid))

    order_items = list(mongo.order_items.find({"order_id": oid_obj}))

    for line in order_items:
        product_id = line.get("product_id")
        weight_kg = float(line.get("weight_kg") or 0)

        if product_id and weight_kg > 0:
            mongo.products.update_one(
                {"_id": product_id},
                {
                    "$inc": {"stock_kg": weight_kg},
                    "$set": {"is_active": 1}
                }
            )

    now = datetime.utcnow().isoformat()

    payment_status = order_doc.get("payment_status")
    new_payment_status = "REFUNDED" if payment_status == "PAID" else payment_status

    mongo.orders.update_one(
        {"_id": oid_obj},
        {
            "$set": {
                "status": "CANCELLED",
                "payment_status": new_payment_status,
                "delivery_partner_id": None,
                "cancelled_at": now
            }
        }
    )

    mongo.transactions.update_many(
        {"order_id": oid_obj},
        {
            "$set": {
                "status": "REFUNDED" if payment_status == "PAID" else "VOID",
                "updated_at": now
            }
        }
    )

    mongo.order_events.insert_one({
        "order_id": oid_obj,
        "status": "CANCELLED",
        "note": "Cancelled by customer",
        "created_at": now
    })

    flash("Order cancelled successfully.", "success")
    return redirect(url_for("orders"))

@app.route('/checkout', methods=['GET', 'POST'])
@login_required()
def checkout():
    u = current_user()
    cid = get_or_create_cart(u["id"])

    store_lat = None
    store_lng = None

    cart_items = list(mongo.cart_items.find({"cart_id": cid}))

    items = []

    for ci in cart_items:
        product = mongo.products.find_one({"_id": ci.get("product_id")})
        if not product:
            continue

        item = {
            "product_id": product["_id"],
            "product_id_str": str(product["_id"]),
            "weight_kg": float(ci.get("weight_kg") or 0),
            "price_per_kg": float(product.get("price_per_kg") or 0),
            "store_id": product.get("store_id"),
            "stock_kg": float(product.get("stock_kg") or 0),
            "is_active": int(product.get("is_active") or 0),
            "name": product.get("name", ""),
            "image_path": product.get("image_path", "")
        }

        items.append(item)

    store_ids = sorted(set([str(it["store_id"]) for it in items if it.get("store_id")]))
    cart_store_count = len(store_ids)

    if cart_store_count > 1:
        flash("Your cart contains items from multiple stores. Please clear the cart and order from one store at a time.", "danger")
        return redirect(url_for("cart_page"))

    addresses = list(
        mongo.addresses.find({"user_id": u["id"]}).sort([
            ("is_default", -1),
            ("created_at", -1)
        ])
    )

    for a in addresses:
        a["id"] = str(a["_id"])

    if items:
        store = mongo.stores.find_one({"_id": items[0]["store_id"]})
        if store:
            store_lat = store.get("latitude")
            store_lng = store.get("longitude")

    if request.method == "POST":
        if not items:
            flash("Your cart is empty.", "warning")
            return redirect(url_for("cart_page"))

        if cart_store_count > 1:
            flash("Your cart contains items from multiple stores. Please order from one store at a time.", "danger")
            return redirect(url_for("cart_page"))

        for it in items:
            if int(it["is_active"] or 0) != 1:
                flash("One or more items are sold out.", "danger")
                return redirect(url_for("cart_page"))

            if float(it["stock_kg"] or 0) <= 0:
                flash("One or more items are sold out.", "danger")
                return redirect(url_for("cart_page"))

            if float(it["weight_kg"] or 0) > float(it["stock_kg"] or 0):
                flash("One or more items have reduced stock. Please update your cart.", "danger")
                return redirect(url_for("cart_page"))

        addr_id = request.form.get("address_id")

        if not addr_id:
            flash("Please select a delivery address.", "warning")
            return redirect(url_for("checkout"))

        try:
            addr_obj_id = ObjectId(addr_id)
        except Exception:
            flash("Invalid address selected.", "danger")
            return redirect(url_for("checkout"))

        sel = mongo.addresses.find_one({
            "_id": addr_obj_id,
            "user_id": u["id"]
        })

        if not sel:
            flash("Invalid address selected.", "danger")
            return redirect(url_for("checkout"))

        sel_pin = (sel.get("pincode") or "").strip()

        if not is_serviceable_pincode(sel_pin):
            flash("Please enter a valid 6-digit pincode.", "danger")
            return redirect(url_for("checkout"))

        if not is_assam_state(sel.get("state")):
            flash("Delivery is currently available only within Assam.", "danger")
            return redirect(url_for("checkout"))

        items_total = sum([
            float(it["weight_kg"] or 0) * float(it["price_per_kg"] or 0)
            for it in items
        ])

        store_id = items[0]["store_id"]
        store = mongo.stores.find_one({"_id": store_id}) or {}

        store_lat = store.get("latitude")
        store_lng = store.get("longitude")

        addr_lat = sel.get("latitude") if sel.get("latitude") else session.get("location_lat")
        addr_lng = sel.get("longitude") if sel.get("longitude") else session.get("location_lng")

        km = haversine_km(store_lat, store_lng, addr_lat, addr_lng)

# Assam-wide delivery: no distance blocking.
# Delivery fee is calculated by distance if coordinates are available.
        delivery_fee = calculate_delivery_fee_by_distance(km)

        tip_amount = request.form.get("tip_amount", "0").strip()

        try:
            tip_amount = float(tip_amount or 0)
        except ValueError:
            tip_amount = 0.0

        if tip_amount < 0:
            tip_amount = 0.0

        if tip_amount > 10000:
            tip_amount = 10000.0

        tip_amount = round(tip_amount, 2)

        now = datetime.utcnow().isoformat()
        total_payable = items_total + float(delivery_fee) + float(tip_amount)

        order_items_docs = []

        for it in items:
            line_total = float(it["weight_kg"]) * float(it["price_per_kg"])

            order_items_docs.append({
                "product_id": it["product_id"],
                "product_name": it.get("name", ""),
                "weight_kg": float(it["weight_kg"]),
                "unit_price_per_kg": float(it["price_per_kg"]),
                "line_total": line_total,
                "image_path": it.get("image_path", "")
            })

        order_result = mongo.orders.insert_one({
            "user_id": u["id"],
            "customer_name": u.get("name"),
            "customer_phone": u.get("phone"),
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
            "created_at": now
        })

        oid = order_result.inserted_id

        for order_item in order_items_docs:
            order_item["order_id"] = oid
            mongo.order_items.insert_one(order_item)

            mongo.products.update_one(
                {"_id": order_item["product_id"]},
                {"$inc": {"stock_kg": -float(order_item["weight_kg"])}}
            )

            updated_product = mongo.products.find_one({"_id": order_item["product_id"]})
            if updated_product and float(updated_product.get("stock_kg") or 0) <= 0:
                mongo.products.update_one(
                    {"_id": order_item["product_id"]},
                    {"$set": {"stock_kg": 0}}
            )

        mongo.transactions.insert_one({
            "order_id": oid,
            "amount": float(total_payable),
            "payment_method": "COD",
            "status": "PENDING",
            "created_at": now
        })

        mongo.order_addresses.insert_one({
            "order_id": oid,
            "line1": sel.get("line1"),
            "line2": sel.get("line2"),
            "city": sel.get("city"),
            "state": sel.get("state"),
            "pincode": sel.get("pincode"),
            "latitude": sel.get("latitude"),
            "longitude": sel.get("longitude"),
            "created_at": now
        })

        mongo.order_events.insert_one({
            "order_id": oid,
            "status": "PLACED",
            "note": "",
            "created_at": now
        })

        mongo.cart_items.delete_many({"cart_id": cid})

        flash("Order placed! (COD)", "success")
        return redirect(url_for("orders"))

    total = sum([
        float(it["weight_kg"] or 0) * float(it["price_per_kg"] or 0)
        for it in items
    ])

    return render_template(
        "checkout.html",
        user=u,
        addresses=addresses,
        total=total,
        base_fee=BASE_DELIVERY_FEE_INR,
        slabs=DELIVERY_SURCHARGE_SLABS,
        max_km=None,
        delivery_mode=DELIVERY_MODE,
        delivery_message="Delivery is available across Assam. Delivery fee is calculated according to distance.",
        store_lat=store_lat,
        store_lng=store_lng,
        cart_store_count=cart_store_count,
    )

@app.route("/orders", endpoint="orders")
@login_required()
def my_orders():
    u = current_user()

    orders = list(
        mongo.orders.find({"user_id": u["id"]}).sort("created_at", -1)
    )

    for o in orders:
        o["id"] = str(o["_id"])
        o["store_name"] = o.get("store_name", "")
        o["total_amount"] = float(o.get("total_amount") or 0)
        o["delivery_fee"] = float(o.get("delivery_fee") or 0)
        o["tip_amount"] = float(o.get("tip_amount") or 0)

    return render_template("orders.html", orders=orders, user=u)

@app.route("/orders/<oid>")
@login_required()
def order_track(oid):
    u = current_user()

    data = get_order_full(
        oid,
        for_user_id=u["id"] if u["role"] == "customer" else None
    )

    if not data:
        flash("Order not found.", "danger")
        return redirect(url_for("orders"))

    return render_template("order_track.html", user=u, **data)

@app.route("/orders/<oid>/feedback", methods=["POST"])
@login_required()
def order_feedback(oid):
    u = current_user()

    try:
        oid_obj = ObjectId(oid)
    except Exception:
        flash("Invalid order.", "danger")
        return redirect(url_for("orders"))

    order_doc = mongo.orders.find_one({
        "_id": oid_obj,
        "user_id": u["id"]
    })

    if not order_doc:
        flash("Order not found.", "danger")
        return redirect(url_for("orders"))

    if order_doc.get("status") != "DELIVERED":
        flash("You can submit feedback only after delivery.", "warning")
        return redirect(url_for("order_track", oid=oid))

    if request.form.get("received_confirm") != "1":
        flash("Please confirm that you received your items.", "warning")
        return redirect(url_for("order_track", oid=oid))

    now = datetime.utcnow().isoformat()

    store_rating = _clamp_rating(request.form.get("store_rating"))
    store_comment = (request.form.get("store_comment") or "").strip() or None

    if store_rating:
        mongo.store_ratings.insert_one({
            "user_id": u["id"],
            "order_id": oid_obj,
            "store_id": order_doc.get("store_id"),
            "rating": store_rating,
            "comment": store_comment,
            "created_at": now
        })

    delivery_rating = _clamp_rating(request.form.get("delivery_rating"))
    delivery_comment = (request.form.get("delivery_comment") or "").strip() or None

    if order_doc.get("delivery_partner_id") and delivery_rating:
        mongo.delivery_ratings.insert_one({
            "user_id": u["id"],
            "order_id": oid_obj,
            "delivery_partner_id": order_doc.get("delivery_partner_id"),
            "rating": delivery_rating,
            "comment": delivery_comment,
            "created_at": now
        })

    order_items = list(mongo.order_items.find({"order_id": oid_obj}))

    for it in order_items:
        pid = it.get("product_id")
        if not pid:
            continue

        pid_str = str(pid)
        rating_value = _clamp_rating(request.form.get(f"product_rating_{pid_str}"))
        comment_value = (request.form.get(f"product_comment_{pid_str}") or "").strip() or None

        if rating_value:
            mongo.product_ratings.insert_one({
                "user_id": u["id"],
                "order_id": oid_obj,
                "product_id": pid,
                "product_name": it.get("product_name", ""),
                "rating": rating_value,
                "comment": comment_value,
                "created_at": now
            })

    title = (request.form.get("complaint_title") or "").strip()
    desc = (request.form.get("complaint_description") or "").strip()

    image = request.files.get("complaint_image")
    image_path = None

    if image and image.filename and allowed_file(image.filename):
        fn = secure_filename(image.filename)
        save_as = datetime.utcnow().strftime("%Y%m%d%H%M%S_") + fn
        os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
        image.save(os.path.join(app.config["UPLOAD_FOLDER"], save_as))
        image_path = f"uploads/{save_as}"

    if title or desc or image_path:
        message = f"{title}\n{desc}".strip()

        mongo.complaints.insert_one({
            "user_id": u["id"],
            "order_id": oid_obj,
            "target_type": "store",
            "target_id": order_doc.get("store_id"),
            "title": title or None,
            "message": message,
            "image_path": image_path,
            "status": "NEW",
            "created_at": now
        })

        if order_doc.get("delivery_partner_id"):
            mongo.complaints.insert_one({
                "user_id": u["id"],
                "order_id": oid_obj,
                "target_type": "delivery",
                "target_id": order_doc.get("delivery_partner_id"),
                "title": title or None,
                "message": message,
                "image_path": image_path,
                "status": "NEW",
                "created_at": now
            })

    flash("Thanks for your feedback!", "success")
    return redirect(url_for("order_track", oid=oid))

@app.route("/api/orders/<oid>/status", methods=["GET"], endpoint="api_order_status")
@login_required()
def api_order_status(oid):
    u = current_user()

    data = get_order_full(
        oid,
        for_user_id=u["id"] if u["role"] == "customer" else None
    )

    if not data:
        return jsonify({
            "ok": False,
            "error": "not found"
        }), 404

    o = data["order"]

    events = []
    for e in data.get("events", []):
        events.append({
            "id": str(e.get("_id")) if e.get("_id") else e.get("id"),
            "status": e.get("status"),
            "note": e.get("note", ""),
            "created_at": e.get("created_at")
        })

    return jsonify({
        "ok": True,
        "id": o.get("id"),
        "status": o.get("status"),
        "payment_status": o.get("payment_status"),
        "delivery_partner_name": o.get("delivery_partner_name"),
        "events": events
    })

@app.route('/api/orders', methods=['GET'])
@api_login_required
def api_orders_list(user_id):
    orders = list(
        mongo.orders.find({"user_id": str(user_id)}).sort("created_at", -1)
    )

    result = []

    for o in orders:
        result.append({
            "id": str(o["_id"]),
            "store_name": o.get("store_name", ""),
            "total_amount": float(o.get("total_amount") or 0),
            "delivery_fee": float(o.get("delivery_fee") or 0),
            "tip_amount": float(o.get("tip_amount") or 0),
            "total_payable": float(
                o.get("total_payable")
                or (
                    float(o.get("total_amount") or 0)
                    + float(o.get("delivery_fee") or 0)
                    + float(o.get("tip_amount") or 0)
                )
            ),
            "status": o.get("status", ""),
            "payment_status": o.get("payment_status", ""),
            "created_at": o.get("created_at", "")
        })

    return jsonify({
        "success": True,
        "orders": result
    })

@app.route('/api/orders/<oid>', methods=['GET'])
@api_login_required
def api_order_detail(user_id, oid):
    data = get_order_full(oid, for_user_id=str(user_id))

    if not data:
        return jsonify({
            "success": False,
            "error": "Order not found"
        }), 404

    o = data["order"]

    items = []

    for item in data.get("items", []):
        items.append({
            "product_id": str(item.get("product_id")) if item.get("product_id") else "",
            "name": item.get("name", ""),
            "weight_kg": float(item.get("weight_kg") or 0),
            "unit_price_per_kg": float(item.get("unit_price_per_kg") or item.get("price_per_kg") or 0),
            "line_total": float(item.get("line_total") or 0),
            "image_path": item.get("image_path", "")
        })

    address = data.get("address") or {}

    if address and address.get("_id"):
        address["id"] = str(address["_id"])
        address.pop("_id", None)

    events = []

    for e in data.get("events", []):
        events.append({
            "id": str(e.get("_id")) if e.get("_id") else e.get("id", ""),
            "status": e.get("status", ""),
            "note": e.get("note", ""),
            "created_at": e.get("created_at", "")
        })

    return jsonify({
        "success": True,
        "order": {
            "id": o.get("id") or str(o.get("_id")),
            "store_name": o.get("store_name", ""),
            "total_amount": float(o.get("total_amount") or 0),
            "delivery_fee": float(o.get("delivery_fee") or 0),
            "tip_amount": float(o.get("tip_amount") or 0),
            "total_payable": float(
                o.get("total_payable")
                or (
                    float(o.get("total_amount") or 0)
                    + float(o.get("delivery_fee") or 0)
                    + float(o.get("tip_amount") or 0)
                )
            ),
            "status": o.get("status", ""),
            "payment_status": o.get("payment_status", ""),
            "created_at": o.get("created_at", ""),
            "delivery_partner_id": str(o.get("delivery_partner_id")) if o.get("delivery_partner_id") else "",
            "delivery_partner_name": o.get("delivery_partner_name", ""),
            "items": items,
            "address": address,
            "events": events
        }
    })
