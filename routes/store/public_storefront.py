"""Store public storefront route section.

Route decorators, endpoint names, URL paths, HTTP methods and function bodies are
preserved from the Step 6 source. Shared legacy helpers are imported from
``routes.store.shared`` during this transitional decomposition.
"""

from routes.store.shared import *

@app.route("/api/store/<store_id>/location")
def api_store_location(store_id):
    try:
        store_obj_id = ObjectId(store_id)
    except Exception:
        return jsonify({
            "ok": False,
            "error": "Invalid store id"
        }), 400

    store = mongo.stores.find_one({"_id": store_obj_id})

    if not store:
        return jsonify({
            "ok": False,
            "error": "Store not found"
        }), 404

    if store.get("latitude") is None or store.get("longitude") is None:
        return jsonify({
            "ok": False,
            "error": "Store coordinates not available"
        }), 400

    return jsonify({
        "ok": True,
        "store_id": str(store["_id"]),
        "store_name": store.get("store_name", ""),
        "latitude": float(store.get("latitude")),
        "longitude": float(store.get("longitude"))
    })


@app.route('/api/store/orders/<oid>', methods=['GET'])
@login_required(role='store')
def api_store_order_detail(oid):
    u = current_user()

    store = mongo.stores.find_one({"user_id": u["id"]})
    if not store:
        return jsonify({
            "ok": False,
            "error": "store not found"
        }), 404

    try:
        oid_obj = ObjectId(oid)
    except Exception:
        return jsonify({
            "ok": False,
            "error": "invalid order id"
        }), 400

    o = mongo.orders.find_one({
        "_id": oid_obj,
        "store_id": store["_id"]
    })

    if not o:
        return jsonify({
            "ok": False,
            "error": "not found"
        }), 404

    customer = None
    if o.get("user_id"):
        try:
            customer = mongo.users.find_one({"_id": ObjectId(o.get("user_id"))})
        except Exception:
            customer = None

    addr = mongo.order_addresses.find_one({"order_id": oid_obj})

    return jsonify({
        "ok": True,
        "order": {
            "id": str(o["_id"]),
            "created_at": o.get("created_at"),
            "status": o.get("status"),
            "payment_status": o.get("payment_status"),
            "items_subtotal": float(o.get("items_subtotal") or o.get("total_amount") or 0),
            "total_amount": float(o.get("total_amount") or 0),
            "delivery_fee": float(o.get("delivery_fee") or 0),
            "platform_fee": float(o.get("platform_fee") or 0),
            "tip_amount": float(o.get("tip_amount") or 0),
            "total_payable": float(
                o.get("total_payable")
                or (
                    float(o.get("items_subtotal") or o.get("total_amount") or 0)
                    + float(o.get("delivery_fee") or 0)
                    + float(o.get("platform_fee") or 0)
                    + float(o.get("tip_amount") or 0)
                )
            ),
            "delivery_partner_name": o.get("delivery_partner_name") or "",
            "delivery_partner_phone": o.get("delivery_partner_phone") or "",
            "delivery_assignment_source": o.get("delivery_assignment_source") or "",
            "customer_name": customer.get("name") if customer else o.get("customer_name"),
            "customer_phone": customer.get("phone") if customer else o.get("customer_phone"),
            "addr_line1": addr.get("line1") if addr else "",
            "addr_line2": addr.get("line2") if addr else "",
            "addr_city": addr.get("city") if addr else "",
            "addr_state": addr.get("state") if addr else "",
            "addr_pincode": addr.get("pincode") if addr else "",
            "addr_lat": addr.get("latitude") if addr else None,
            "addr_lng": addr.get("longitude") if addr else None,
        }
    })


@app.route("/stores/<sid>")
def store_catalog(sid):
    user = current_user()

    try:
        sid_obj = ObjectId(sid)
    except Exception:
        flash("Store not found.", "warning")
        return redirect(url_for("products"))

    store = mongo.stores.find_one({"_id": sid_obj})

    if not store:
        flash("Store not found.", "warning")
        return redirect(url_for("products"))

    store["id"] = str(store["_id"])
    store["store_name"] = store.get("store_name", "Store")
    store["address"] = store.get("address", "")
    store["description"] = store.get("description", "")
    store["logo_path"] = store.get("logo_path", "")
    store["banner_path"] = store.get("banner_path", "")
    store["opening_time"] = store.get("opening_time", "")
    store["closing_time"] = store.get("closing_time", "")
    store["is_open"] = int(store.get("is_open", 1))
    store["is_active"] = int(store.get("is_active", 1))

    allow, pin = _session_pin_is_serviceable()

    products = []
    product_bundles = []
    categories = []
    category_counts = {}
    store_reviews = []
    store_avg_rating = 0
    store_rating_count = 0
    can_review_store = bool(user and user.get("role") == "customer")

    if session.get("service_area") and not allow:
        flash("Please enter a valid 6-digit pincode.", "warning")
    else:
        products = list(
            mongo.products.find({
                "$and": [
                    {
                        "$or": [
                            {"store_id": sid_obj},
                            {"store_id": str(sid_obj)}
                        ]
                    },
                    {
                        "is_active": 1
                    },
                    {
                        "stock_quantity": {"$gt": 0}
                    }
                ]
            }).sort("created_at", -1)
        )

        cart_lookup = {}
        bundle_cart_lookup = {}

        if user and user.get("role") == "customer":
            cid = get_or_create_cart(user["id"])

            cart_items = list(mongo.cart_items.find({
                "cart_id": cid
            }))

            for ci in cart_items:
                item_type = (ci.get("item_type") or "product").strip().lower()

                if item_type == "bundle":
                    bundle_id_value = ci.get("bundle_id") or ci.get("bundle_id_str")

                    if bundle_id_value:
                        bundle_cart_lookup[str(bundle_id_value)] = {
                            "cart_item_id": str(ci.get("_id")),
                            "cart_quantity": cart_item_quantity(ci),
                        }

                    continue

                product_id_value = ci.get("product_id")

                if product_id_value:
                    cart_lookup[str(product_id_value)] = {
                        "cart_item_id": str(ci.get("_id")),
                        "cart_quantity": cart_item_quantity(ci),
                        "unit_type": ci.get("unit_type"),
                        "unit_label": ci.get("unit_label")
                    }

        for p in products:
            p["id"] = str(p["_id"])
            p["name"] = (p.get("name") or "Product").strip()
            p["category"] = (p.get("category") or "Uncategorized").strip()
            p["sub_category"] = (p.get("sub_category") or "").strip()
            p["image_path"] = p.get("image_path", "")
            hydrate_product_unit_fields(p)
            p["store_id"] = str(sid_obj)
            p["store_name"] = store.get("store_name", "")

            cart_info = cart_lookup.get(str(p["_id"]))

            if cart_info:
                p["in_cart"] = True
                p["cart_item_id"] = cart_info.get("cart_item_id", "")
                p["cart_quantity"] = cart_info.get("cart_quantity", p.get("quantity_min") or 1)
            else:
                p["in_cart"] = False
                p["cart_item_id"] = ""
                p["cart_quantity"] = 0

            product_ratings = list(mongo.product_ratings.find({
                "product_id": p["_id"]
            }))

            product_rating_count = len(product_ratings)
            product_total_rating = 0

            for r in product_ratings:
                try:
                    product_total_rating += float(r.get("rating") or 0)
                except (TypeError, ValueError):
                    pass

            if product_rating_count > 0:
                p["avg_rating"] = round(product_total_rating / product_rating_count, 1)
            else:
                p["avg_rating"] = 0

            p["rating_count"] = product_rating_count

            cat = p["category"] or "Uncategorized"

            if cat not in category_counts:
                category_counts[cat] = 0

            category_counts[cat] += 1

        raw_bundles = list(
            mongo.product_bundles.find({
                "$and": [
                    {
                        "$or": [
                            {"store_id": sid_obj},
                            {"store_id": str(sid_obj)},
                            {"store_id_str": str(sid_obj)}
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
                        "is_active": 1
                    }
                ]
            }).sort("updated_at", -1)
        )

        for bundle in raw_bundles:
            bundle = build_live_product_bundle(
                bundle,
                notify_store=True,
                notification_context="store_catalog"
            ) or bundle

            if not is_product_bundle_customer_available(bundle):
                continue

            bundle["id"] = str(bundle.get("_id"))
            bundle["bundle_name"] = bundle.get("bundle_name") or "Product Bundle"
            bundle["description"] = bundle.get("description") or ""
            bundle["store_name"] = bundle.get("store_name") or store.get("store_name", "Store")
            bundle["image_path"] = bundle.get("image_path") or ""

            cart_info = bundle_cart_lookup.get(str(bundle.get("_id")))

            if cart_info:
                bundle["in_cart"] = True
                bundle["cart_item_id"] = cart_info.get("cart_item_id", "")
                bundle["cart_quantity"] = cart_info.get("cart_quantity", 1)
            else:
                bundle["in_cart"] = False
                bundle["cart_item_id"] = ""
                bundle["cart_quantity"] = 0

            product_bundles.append(bundle)

        categories = [
            {
                "name": name,
                "count": count
            }
            for name, count in sorted(category_counts.items())
        ]

    store_reviews = list(
        mongo.store_ratings.find({
            "$or": [
                {"store_id": sid_obj},
                {"store_id": str(sid_obj)}
            ]
        }).sort("created_at", -1).limit(20)
    )

    store_rating_count = len(store_reviews)
    store_total_rating = 0

    for r in store_reviews:
        r["id"] = str(r["_id"])

        try:
            store_total_rating += float(r.get("rating") or 0)
        except (TypeError, ValueError):
            pass

        if r.get("user_id"):
            reviewer = None

            try:
                reviewer = mongo.users.find_one({"_id": ObjectId(str(r.get("user_id")))})
            except Exception:
                reviewer = mongo.users.find_one({"_id": str(r.get("user_id"))})

            r["reviewer_name"] = reviewer.get("name", "Customer") if reviewer else r.get("reviewer_name", "Customer")
        else:
            r["reviewer_name"] = r.get("reviewer_name", "Customer")

    if store_rating_count > 0:
        store_avg_rating = round(store_total_rating / store_rating_count, 1)

    store["avg_rating"] = store_avg_rating
    store["rating_count"] = store_rating_count
    store["product_count"] = len(products)
    store["bundle_count"] = len(product_bundles)

    return render_template(
        "store_catalog.html",
        user=user,
        store=store,
        products=products,
        product_bundles=product_bundles,
        categories=categories,
        store_reviews=store_reviews,
        store_avg_rating=store_avg_rating,
        store_rating_count=store_rating_count,
        can_review_store=can_review_store
    )


@app.route("/stores/<sid>/review", methods=["POST"])
@login_required()
def submit_store_review(sid):
    u = current_user()

    if not u:
        flash("Please log in first.", "warning")
        return redirect(url_for("login"))

    if u.get("role") != "customer":
        flash("Only customer accounts can submit store reviews.", "warning")
        return redirect(url_for("store_catalog", sid=sid))

    try:
        sid_obj = ObjectId(sid)
    except Exception:
        flash("Invalid store.", "danger")
        return redirect(url_for("products"))

    store = mongo.stores.find_one({"_id": sid_obj})

    if not store:
        flash("Store not found.", "danger")
        return redirect(url_for("products"))

    try:
        rating = float(request.form.get("rating") or 0)
    except (TypeError, ValueError):
        rating = 0

    review = (request.form.get("review") or "").strip()

    if rating < 1 or rating > 5:
        flash("Please select a valid rating between 1 and 5.", "warning")
        return redirect(url_for("store_catalog", sid=sid))

    if len(review) > 800:
        flash("Review is too long. Please keep it within 800 characters.", "warning")
        return redirect(url_for("store_catalog", sid=sid))

    now = datetime.utcnow().isoformat()

    existing_review = mongo.store_ratings.find_one({
        "store_id": sid_obj,
        "user_id": str(u["_id"])
    })

    review_doc = {
        "store_id": sid_obj,
        "store_name": store.get("store_name", ""),
        "user_id": str(u["_id"]),
        "reviewer_name": u.get("name", "Customer"),
        "rating": rating,
        "review": review,
        "comment": review,
        "is_active": 1,
        "updated_at": now
    }

    if existing_review:
        mongo.store_ratings.update_one(
            {"_id": existing_review["_id"]},
            {"$set": review_doc}
        )
        flash("Your store review has been updated.", "success")
    else:
        review_doc["created_at"] = now
        mongo.store_ratings.insert_one(review_doc)
        flash("Thank you! Your store review has been submitted.", "success")

    return redirect(url_for("store_catalog", sid=sid))


@app.route('/rate/store/<int:sid>', methods=['POST'])
@login_required()
def rate_store_disabled(sid):
    flash('Please rate from the order page after your delivery is completed.', 'info')
    return redirect(request.referrer or url_for('orders'))


@app.route('/store/profile-image/<store_id>', methods=['GET'], endpoint='store_profile_image')
def store_profile_image(store_id):
    """Serve the active store profile picture stored in MongoDB."""
    try:
        store_obj_id = ObjectId(str(store_id))
    except Exception:
        return "Store image not found", 404

    store = mongo.stores.find_one({"_id": store_obj_id})

    if not store:
        return "Store image not found", 404

    image_doc = None
    profile_image_id = store.get("profile_image_id")

    if profile_image_id:
        try:
            profile_image_obj_id = ObjectId(str(profile_image_id))
        except Exception:
            profile_image_obj_id = profile_image_id

        image_doc = mongo.store_profile_images.find_one({
            "_id": profile_image_obj_id,
            "store_id": store_obj_id,
            "is_active": 1
        })

    if not image_doc:
        image_doc = mongo.store_profile_images.find_one(
            {
                "$or": [
                    {"store_id": store_obj_id},
                    {"store_id": str(store_obj_id)}
                ],
                "is_active": 1
            },
            sort=[("created_at", -1)]
        )

    if not image_doc or not image_doc.get("data"):
        return "Store image not found", 404

    image_data = image_doc.get("data")

    if not isinstance(image_data, (bytes, bytearray)):
        try:
            image_data = bytes(image_data)
        except Exception:
            return "Store image not found", 404

    response = send_file(
        io.BytesIO(image_data),
        mimetype=image_doc.get("mime_type") or "image/jpeg",
        as_attachment=False,
        download_name=image_doc.get("filename") or "store-profile-image"
    )
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return response
