"""Admin stores route section.

Route decorators, endpoint names, URL paths, HTTP methods and function bodies are
preserved from the Step 6 source. Shared legacy helpers are imported from
``routes.admin.shared`` during this transitional decomposition.
"""

from routes.admin.shared import *

@app.route('/admin/create-store', methods=['GET', 'POST'])
@login_required(role='admin')
def admin_create_store():

    # =========================
    # CREATE STORE
    # =========================
    if request.method == 'POST':

        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').lower().strip()
        phone_raw = request.form.get('phone', '').strip()
        password = request.form.get('password', '')
        store_name = request.form.get('store_name', '').strip()
        address = request.form.get('address', '').strip()
        city = (request.form.get("city") or "").strip()
        state = (request.form.get("state") or "Assam").strip()
        pincode = _clean_pin(request.form.get("pincode") or "")

        lat_raw = request.form.get('latitude')
        lng_raw = request.form.get('longitude')

        latitude = None
        longitude = None

        is_online = _admin_bool_from_form("is_online", True)
        delivery_enabled = _admin_bool_from_form("delivery_enabled", False)

        delivery_mode = (request.form.get("delivery_mode") or "polygon").strip().lower()
        if delivery_mode not in ["polygon"]:
            delivery_mode = "polygon"

        delivery_zone_polygon = _admin_parse_delivery_zone_polygon(
            request.form.get("delivery_zone_polygon") or ""
        )

        delivery_base_fee = _admin_money_or_default(
            request.form.get("delivery_base_fee"),
            40
        )

               # =========================
        # PARSE STORE LOCATION
        # =========================
        latitude = _admin_float_or_none(lat_raw, -90, 90)
        longitude = _admin_float_or_none(lng_raw, -180, 180)

        # =========================
        # NORMALIZE PHONE
        # =========================
        phone = normalize_phone(phone_raw)

        # =========================
        # VALIDATION
        # =========================
        if not name or not email or not phone or not password or not store_name:
            flash("Please fill all required fields.", "warning")
            return redirect(url_for('admin_create_store'))
        
        if pincode and not is_serviceable_pincode(pincode):
            flash("Please enter a valid 6-digit store pincode.", "warning")
            return redirect(url_for('admin_create_store'))

        if state and not is_assam_state(state):
            flash("Store state must be Assam for delivery operations.", "warning")
            return redirect(url_for('admin_create_store'))

        if latitude is None or longitude is None:
            flash("Store pickup latitude and longitude are required.", "warning")
            return redirect(url_for('admin_create_store'))

        if delivery_enabled and delivery_mode == "polygon" and not delivery_zone_polygon:
            flash("Delivery zone polygon is required when delivery is enabled.", "warning")
            return redirect(url_for('admin_create_store'))

        if len(password) < 6:
            flash("Password must be at least 6 characters.", "warning")
            return redirect(url_for('admin_create_store'))

        # =========================
        # CHECK EXISTING USER
        # =========================
        existing = mongo.users.find_one({
            "$or": [
                {"email": email},
                {"phone": phone}
            ]
        })

        if existing:
            flash("Email or phone already exists. Use different details.", "warning")
            return redirect(url_for('admin_create_store'))

        # =========================
        # INSERT STORE USER
        # =========================
        created_user_id = None

        try:

            result = mongo.users.insert_one({
                "name": name,
                "email": email,
                "phone": phone,
                "password_hash": generate_password_hash(password),
                "role": "store",
                "phone_verified": 1,
                "is_active": 1,
                "created_at": datetime.utcnow().isoformat()
            })

            user_id = str(result.inserted_id)
            created_user_id = result.inserted_id

            # =========================
            # INSERT STORE
            # =========================
            now = datetime.utcnow().isoformat()

            mongo.stores.insert_one({
                "user_id": user_id,
                "store_name": store_name,

                "address": address,
                "city": city,
                "state": state,
                "pincode": pincode,

                "latitude": latitude,
                "longitude": longitude,

                # Admin/account status.
                "is_active": 1,

                # Store operational status.
                "is_online": 1 if is_online else 0,
                "is_open": 1 if is_online else 0,

                # Delivery/serviceability fields.
                "delivery_available": bool(delivery_enabled),
                "delivery_enabled": 1 if delivery_enabled else 0,
                "delivery_mode": delivery_mode,
                "delivery_zone_polygon": delivery_zone_polygon,
                "delivery_zone_configured": 1 if delivery_zone_polygon else 0,
                "delivery_base_fee": delivery_base_fee,

                "created_at": now,
                "updated_at": now
            })

        except DuplicateKeyError:

            if created_user_id:
                try:
                    mongo.users.delete_one({"_id": created_user_id})
                except Exception:
                    pass

            flash(
                "Email or phone already exists. Please use different details.",
                "danger"
            )

            return redirect(url_for('admin_create_store'))

        except Exception as e:

            if created_user_id:
                try:
                    mongo.users.delete_one({"_id": created_user_id})
                except Exception:
                    pass

            flash(f"Store creation failed: {e}", "danger")

            return redirect(url_for('admin_create_store'))

        flash("Store created successfully.", "success")

        return redirect(url_for('admin_create_store'))

    # =========================
    # DASHBOARD METRICS
    # =========================
    metrics = {
        "stores": mongo.stores.count_documents({}),
        "orders": mongo.orders.count_documents({}),
        "users": mongo.users.count_documents({"role": "customer"}),
        "products": mongo.products.count_documents({})
    }

    # =========================
    # RENDER PAGE
    # =========================
    return render_template(
        'admin_create_store.html',
        user=current_user(),
        metrics=metrics
    )


@app.route("/admin/stores")
@login_required(role="admin")
def admin_store_overview():
    stores, filters = _admin_store_overview_build_rows()

    total_stores = len(stores)
    active_stores = len([s for s in stores if int(s.get("is_active") or 0) == 1])
    inactive_stores = len([s for s in stores if int(s.get("is_active") or 0) != 1])
    if filters.get("range") != "all":
        new_stores = len([s for s in stores if s.get("created_in_range")])
    else:
        new_stores = len([s for s in stores if s.get("created_in_last_30_days")])

    top_selling_stores = sorted(
        stores,
        key=lambda x: (float(x.get("revenue") or 0), int(x.get("orders") or 0)),
        reverse=True
    )[:6]

    most_popular_stores = sorted(
        stores,
        key=lambda x: (int(x.get("orders") or 0), float(x.get("rating") or 0)),
        reverse=True
    )[:6]

    top_product_stores = sorted(
        stores,
        key=lambda x: (int(x.get("products") or 0), int(x.get("orders") or 0)),
        reverse=True
    )[:6]

    metrics = {
        "total_stores": total_stores,
        "active_stores": active_stores,
        "inactive_stores": inactive_stores,
        "new_stores": new_stores,
        "total_transactions": sum(int(s.get("orders") or 0) for s in stores),
        "commission_earned": round(sum(float(s.get("commission_earned") or 0) for s in stores), 2),
        "store_withdrawals": round(sum(float(s.get("store_withdrawals") or 0) for s in stores), 2),
    }

    return render_template(
        "admin_store_overview.html",
        user=current_user(),
        metrics=metrics,
        stores=stores,
        filters=filters,
        top_selling_stores=top_selling_stores,
        most_popular_stores=most_popular_stores,
        top_product_stores=top_product_stores,
        active_group="store",
        active_page="store_overview",
    )


@app.route("/admin/stores/list")
@login_required(role="admin")
def admin_store_list():
    all_stores, filtered_stores, filters, store_counts = _admin_store_list_rows()
    stores, pagination = _admin_store_list_paginate(filtered_stores, filters)

    return render_template(
        "admin_store_list.html",
        user=current_user(),
        stores=stores,
        all_store_count=len(all_stores),
        store_counts=store_counts,
        list_filters=filters,
        pagination=pagination,
        status_options=ADMIN_STORE_LIST_STATUS_OPTIONS,
        active_group="store",
        active_page="store_list",
    )


@app.route("/admin/stores/reviews")
@login_required(role="admin")
def admin_store_reviews():
    filters = _admin_store_review_filters()

    stores = [
        _admin_store_review_enrich(store)
        for store in _admin_store_rows()
    ]

    recommended_ready_stores = [
        store for store in stores
        if store.get("recommendation_ready")
    ]

    recommended_stores = _admin_store_review_sort(
        stores,
        {"sort_by": "score"}
    )

    filtered_stores = [
        store for store in stores
        if _admin_store_review_matches(store, filters)
    ]
    filtered_stores = _admin_store_review_sort(filtered_stores, filters)
    review_stores, pagination = _admin_store_list_paginate(filtered_stores, filters)

    review_counts = _admin_store_review_counts(stores)
    active_stores = len([store for store in stores if int(store.get("is_active") or 0) == 1])
    inactive_stores = len(stores) - active_stores

    review_metrics = {
        "total_stores": len(stores),
        "filtered_stores": len(filtered_stores),
        "recommended_stores": len(recommended_ready_stores),
        "active_stores": active_stores,
        "inactive_stores": inactive_stores,
        "total_products": sum(int(store.get("products") or 0) for store in stores),
        "total_orders": sum(int(store.get("orders") or 0) for store in stores),
        "avg_rating": _admin_store_review_average_rating(stores),
    }

    return render_template(
        "admin_store_reviews.html",
        user=current_user(),
        stores=stores,
        recommended_stores=recommended_stores,
        review_stores=review_stores,
        review_filters=filters,
        review_status_options=ADMIN_STORE_REVIEW_STATUS_OPTIONS,
        review_sort_options=ADMIN_STORE_REVIEW_SORT_OPTIONS,
        review_counts=review_counts,
        review_metrics=review_metrics,
        pagination=pagination,
        review_return_url=_admin_store_review_return_url(),
        active_group="store",
        active_page="store_reviews",
    )


@app.route("/admin/stores/export.csv")
@login_required(role="admin")
def admin_stores_export_csv():
    all_stores, stores, filters, store_counts = _admin_store_list_rows()

    rows = [
        [
            "SL",
            "Store Name",
            "Store ID",
            "Owner Name",
            "Owner Email",
            "Owner Phone",
            "City",
            "State",
            "Pincode",
            "Account Status",
            "Online Status",
            "Delivery Status",
            "Delivery Zone",
            "Orders",
            "Products",
            "Revenue",
            "Rating",
            "Created At",
        ]
    ]

    for idx, store in enumerate(stores, start=1):
        is_online = int(store.get("is_online", store.get("is_open", 1)) or 0) == 1
        delivery_on = int(store.get("delivery_enabled", 1 if store.get("delivery_available", True) else 0) or 0) == 1
        zone_ready = _admin_store_list_zone_ready(store)

        rows.append([
            idx,
            store.get("store_name", ""),
            store.get("id", ""),
            store.get("owner_name", ""),
            store.get("owner_email", ""),
            store.get("owner_phone", ""),
            store.get("city", ""),
            store.get("state", ""),
            store.get("pincode", ""),
            "Active" if int(store.get("is_active") or 0) == 1 else "Inactive",
            "Online" if is_online else "Offline",
            "Delivery On" if delivery_on else "Delivery Off",
            "Zone Ready" if zone_ready else "Zone Missing",
            store.get("orders", 0),
            store.get("products", 0),
            "%.2f" % float(store.get("revenue") or 0),
            store.get("rating", 0),
            store.get("created_at", ""),
        ])

    def csv_escape(value):
        value = "" if value is None else str(value)
        return '"' + value.replace('"', '""') + '"'

    csv_data = "\n".join(",".join(csv_escape(col) for col in row) for row in rows)

    suffix_parts = [
        (filters.get("status") or "all").replace(" ", "_"),
    ]

    if filters.get("q"):
        suffix_parts.append("searched")

    filename = "nelocals_stores_" + "_".join(suffix_parts) + ".csv"

    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@app.route("/admin/stores/overview/export.csv")
@login_required(role="admin")
def admin_store_overview_export_csv():
    stores, filters = _admin_store_overview_build_rows()
    suffix = (filters.get("range") or "all").replace(" ", "_")
    return _admin_store_overview_csv_response(stores, f"nelocals_store_overview_{suffix}.csv")


@app.route("/admin/stores/<store_id>/toggle", methods=["POST"])
@login_required(role="admin")
def admin_store_toggle(store_id):
    try:
        sid = ObjectId(store_id)
    except Exception:
        return _admin_store_json_or_redirect(False, "Invalid store.", "danger", 400)

    store = mongo.stores.find_one({"_id": sid})

    if not store:
        return _admin_store_json_or_redirect(False, "Store not found.", "warning", 404)

    current_status = int(store.get("is_active", 1) or 0)
    next_status = 0 if current_status == 1 else 1

    mongo.stores.update_one(
        {"_id": sid},
        {"$set": {"is_active": next_status}}
    )

    user_id = store.get("user_id")
    if user_id:
        try:
            mongo.users.update_one(
                {"_id": ObjectId(str(user_id))},
                {"$set": {"is_active": next_status}}
            )
        except Exception:
            pass

    return _admin_store_json_or_redirect(
        True,
        "Store status updated successfully.",
        "success",
        store_id=str(sid),
        field="is_active",
        value=next_status,
        store_counts=_admin_store_ajax_counts_payload(),
    )


@app.route("/admin/stores/<store_id>/online-toggle", methods=["POST"])
@login_required(role="admin")
def admin_store_online_toggle(store_id):
    try:
        sid = ObjectId(store_id)
    except Exception:
        return _admin_store_json_or_redirect(False, "Invalid store.", "danger", 400)

    store = mongo.stores.find_one({"_id": sid})

    if not store:
        return _admin_store_json_or_redirect(False, "Store not found.", "warning", 404)

    current_status = int(store.get("is_online", store.get("is_open", 1)) or 0)
    next_status = 0 if current_status == 1 else 1

    now = datetime.utcnow().isoformat()

    mongo.stores.update_one(
        {"_id": sid},
        {
            "$set": {
                "is_online": next_status,
                "is_open": next_status,
                "online_status_updated_at": now,
                "updated_at": now
            }
        }
    )

    return _admin_store_json_or_redirect(
        True,
        "Store is now online." if next_status else "Store is now offline.",
        "success",
        store_id=str(sid),
        field="is_online",
        value=next_status,
        store_counts=_admin_store_ajax_counts_payload(),
    )


@app.route("/admin/stores/<store_id>/delivery-toggle", methods=["POST"])
@login_required(role="admin")
def admin_store_delivery_toggle(store_id):
    try:
        sid = ObjectId(store_id)
    except Exception:
        return _admin_store_json_or_redirect(False, "Invalid store.", "danger", 400)

    store = mongo.stores.find_one({"_id": sid})

    if not store:
        return _admin_store_json_or_redirect(False, "Store not found.", "warning", 404)

    current_status = int(store.get("delivery_enabled", 1 if store.get("delivery_available", True) else 0) or 0)
    next_status = 0 if current_status == 1 else 1

    now = datetime.utcnow().isoformat()

    mongo.stores.update_one(
        {"_id": sid},
        {
            "$set": {
                "delivery_enabled": next_status,
                "delivery_available": bool(next_status),
                "delivery_status_updated_at": now,
                "updated_at": now
            }
        }
    )

    return _admin_store_json_or_redirect(
        True,
        "Store delivery is now enabled." if next_status else "Store delivery is now disabled.",
        "success",
        store_id=str(sid),
        field="delivery_enabled",
        value=next_status,
        store_counts=_admin_store_ajax_counts_payload(),
    )


@app.route("/admin/stores/<store_id>/edit", methods=["GET"], endpoint="admin_store_edit")
@login_required(role="admin")
def admin_store_edit(store_id):
    try:
        sid = ObjectId(store_id)
    except Exception:
        flash("Invalid store.", "danger")
        return redirect(url_for("admin_store_list"))

    store = mongo.stores.find_one({"_id": sid})

    if not store:
        flash("Store not found.", "warning")
        return redirect(url_for("admin_store_list"))

    return render_template(
        "admin_store_edit.html",
        user=current_user(),
        store=_admin_store_edit_row(store),
        active_group="store",
        active_page="store_list",
    )


@app.route("/admin/stores/<store_id>/update", methods=["POST"])
@login_required(role="admin")
def admin_store_update(store_id):
    try:
        sid = ObjectId(store_id)
    except Exception:
        flash("Invalid store.", "danger")
        return redirect(url_for("admin_store_list"))

    store = mongo.stores.find_one({"_id": sid})

    if not store:
        flash("Store not found.", "warning")
        return redirect(url_for("admin_store_list"))

    store_name = request.form.get("store_name", "").strip()
    address = request.form.get("address", "").strip()
    city = (request.form.get("city") or store.get("city") or "").strip()
    state = (request.form.get("state") or store.get("state") or "Assam").strip()
    pincode = _clean_pin(request.form.get("pincode") or store.get("pincode") or "")

    latitude_raw = request.form.get("latitude")
    longitude_raw = request.form.get("longitude")

    latitude = _admin_float_or_none(
        latitude_raw,
        -90,
        90
    )
    longitude = _admin_float_or_none(
        longitude_raw,
        -180,
        180
    )

    if latitude is None and str(latitude_raw or "").strip() == "":
        latitude = _admin_float_or_none(store.get("latitude"), -90, 90)

    if longitude is None and str(longitude_raw or "").strip() == "":
        longitude = _admin_float_or_none(store.get("longitude"), -180, 180)

    is_active = _admin_bool_from_form(
        "is_active",
        bool(int(store.get("is_active", 1) or 0))
    )

    is_online = _admin_bool_from_form(
        "is_online",
        bool(int(store.get("is_online", store.get("is_open", 1)) or 0))
    )

    delivery_enabled = _admin_bool_from_form(
        "delivery_enabled",
        bool(int(store.get("delivery_enabled", 1 if store.get("delivery_available", True) else 0) or 0))
    )

    delivery_mode = (request.form.get("delivery_mode") or store.get("delivery_mode") or "polygon").strip().lower()
    if delivery_mode not in ["polygon"]:
        delivery_mode = "polygon"

    raw_delivery_zone_polygon = request.form.get("delivery_zone_polygon")
    existing_delivery_zone_polygon = _admin_parse_delivery_zone_polygon(
        store.get("delivery_zone_polygon")
    )
    if not existing_delivery_zone_polygon:
        for zone_key in [
            "delivery_zone",
            "zone_polygon",
            "service_area_polygon",
            "delivery_area_polygon",
            "service_area",
            "zone",
        ]:
            existing_delivery_zone_polygon = _admin_parse_delivery_zone_polygon(store.get(zone_key))
            if existing_delivery_zone_polygon:
                break
    submitted_delivery_zone_polygon = _admin_parse_delivery_zone_polygon(
        raw_delivery_zone_polygon if raw_delivery_zone_polygon is not None else ""
    )

    if (
        delivery_enabled
        and not submitted_delivery_zone_polygon
        and existing_delivery_zone_polygon
    ):
        delivery_zone_polygon = existing_delivery_zone_polygon
    else:
        delivery_zone_polygon = submitted_delivery_zone_polygon

    delivery_base_fee = _admin_money_or_default(
        request.form.get("delivery_base_fee"),
        store.get("delivery_base_fee", 40)
    )

    owner_name = request.form.get("owner_name", "").strip()
    owner_email = request.form.get("owner_email", "").lower().strip()
    owner_phone = normalize_phone(request.form.get("owner_phone", "").strip())

    if not store_name:
        flash("Store name is required.", "warning")
        return redirect(url_for("admin_store_list"))
    
    if pincode and not is_serviceable_pincode(pincode):
        flash("Please enter a valid 6-digit store pincode.", "warning")
        return redirect(url_for("admin_store_list"))

    if state and not is_assam_state(state):
        flash("Store state must be Assam for delivery operations.", "warning")
        return redirect(url_for("admin_store_list"))

    if latitude is None or longitude is None:
        flash("Store pickup latitude and longitude are required.", "warning")
        return redirect(url_for("admin_store_list"))

    if delivery_enabled and delivery_mode == "polygon" and not delivery_zone_polygon:
        flash("Delivery zone polygon is required when delivery is enabled.", "warning")
        return redirect(url_for("admin_store_list"))

    mongo.stores.update_one(
        {"_id": sid},
        {
            "$set": {
                "store_name": store_name,

                "address": address,
                "city": city,
                "state": state,
                "pincode": pincode,

                "latitude": latitude,
                "longitude": longitude,

                "is_active": 1 if is_active else 0,
                "is_online": 1 if is_online else 0,
                "is_open": 1 if is_online else 0,

                "delivery_available": bool(delivery_enabled),
                "delivery_enabled": 1 if delivery_enabled else 0,
                "delivery_mode": delivery_mode,
                "delivery_zone_polygon": delivery_zone_polygon,
                "delivery_zone_configured": 1 if delivery_zone_polygon else 0,
                "delivery_base_fee": delivery_base_fee,

                "updated_at": datetime.utcnow().isoformat()
            }
        }
    )

    user_id = store.get("user_id")

    if user_id:
        update_user = {}
        update_user["is_active"] = 1 if is_active else 0

        if owner_name:
            update_user["name"] = owner_name

        if owner_email:
            update_user["email"] = owner_email

        if owner_phone:
            update_user["phone"] = owner_phone

        if update_user:
            try:
                mongo.users.update_one(
                    {"_id": ObjectId(str(user_id))},
                    {"$set": update_user}
                )
            except Exception:
                pass

    flash("Store updated successfully.", "success")
    return redirect(url_for("admin_store_list"))


@app.route("/admin/stores/<store_id>/delete", methods=["POST"])
@login_required(role="admin")
def admin_store_delete(store_id):
    try:
        sid = ObjectId(store_id)
    except Exception:
        return _admin_store_json_or_redirect(False, "Invalid store.", "danger", 400)

    store = mongo.stores.find_one({"_id": sid})

    if not store:
        return _admin_store_json_or_redirect(False, "Store not found.", "warning", 404)

    order_cnt = mongo.orders.count_documents({
        "$or": [
            {"store_id": sid},
            {"store_id": str(sid)}
        ]
    })

    user_id = store.get("user_id")

    if order_cnt > 0:
        mongo.stores.update_one(
            {"_id": sid},
            {"$set": {"is_active": 0}}
        )

        if user_id:
            try:
                mongo.users.update_one(
                    {"_id": ObjectId(str(user_id))},
                    {"$set": {"is_active": 0}}
                )
            except Exception:
                pass

        return _admin_store_json_or_redirect(
            True,
            "Store has orders, so it was disabled instead of deleted.",
            "warning",
            store_id=str(sid),
            mode="disabled",
            field="is_active",
            value=0,
            store_counts=_admin_store_ajax_counts_payload(),
        )

    mongo.products.delete_many({
        "$or": [
            {"store_id": sid},
            {"store_id": str(sid)}
        ]
    })

    mongo.stores.delete_one({"_id": sid})

    if user_id:
        try:
            mongo.users.delete_one({"_id": ObjectId(str(user_id))})
        except Exception:
            pass

    return _admin_store_json_or_redirect(
        True,
        "Store deleted successfully.",
        "success",
        store_id=str(sid),
        mode="deleted",
        store_counts=_admin_store_ajax_counts_payload(),
    )
