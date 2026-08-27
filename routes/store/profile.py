"""Store profile route section.

Route decorators, endpoint names, URL paths, HTTP methods and function bodies are
preserved from the Step 6 source. Shared legacy helpers are imported from
``routes.store.shared`` during this transitional decomposition.
"""

from routes.store.shared import *

@app.route('/store/profile', methods=['GET'], endpoint='store_profile')
@login_required(role='store')
def store_profile_page():
    u, store = _get_current_store_or_redirect()

    if not store:
        return redirect(url_for("store_dashboard"))

    owner = mongo.users.find_one({"_id": ObjectId(str(store.get("user_id")))}) if store.get("user_id") else u
    if not owner:
        owner = u

    store["id"] = str(store["_id"])

    page_context = _build_store_split_page_context(store)
    profile_context = _build_store_profile_context(store, owner)

    return render_template(
        "store_profile.html",
        user=u,
        store=store,
        store_owner=owner,
        **page_context,
        **profile_context
    )


@app.route('/store/profile/update', methods=['POST'], endpoint='store_profile_update')
@login_required(role='store')
def store_profile_update():
    u, store = _get_current_store_or_redirect()

    if not store:
        flash("Store not found.", "danger")
        return redirect(url_for("store_dashboard"))

    now = datetime.utcnow().isoformat()

    store_name = (request.form.get("store_name") or "").strip()
    owner_name = (request.form.get("owner_name") or "").strip()
    phone_raw = (request.form.get("phone") or "").strip()
    phone = normalize_phone(phone_raw)

    address = (request.form.get("address") or "").strip()
    banner = request.files.get("banner")
    logo = request.files.get("logo")
    image = request.files.get("image")
    profile_image = request.files.get("profile_image")
    city = (request.form.get("city") or "").strip()
    state = (request.form.get("state") or "Assam").strip()
    pincode = _clean_pin(request.form.get("pincode") or "")

    description = (request.form.get("description") or "").strip()
    profile_intro = (request.form.get("profile_intro") or "").strip()
    opening_time = (request.form.get("opening_time") or "").strip()
    closing_time = (request.form.get("closing_time") or "").strip()
    working_days = request.form.getlist("working_days")

    min_order_amount_raw = (request.form.get("min_order_amount") or "").strip()

       # Delivery enabled/off.
    # IMPORTANT:
    # If the new delivery_enabled field is not submitted by some form,
    # keep the existing DB value instead of silently turning delivery off.
    existing_delivery_enabled = bool(
        int(
            store.get(
                "delivery_enabled",
                1 if store.get("delivery_available", False) else 0
            ) or 0
        )
    )

    delivery_enabled = _store_bool_from_form(
        "delivery_enabled",
        existing_delivery_enabled
    )

    # Keep old field in sync with new field.
    delivery_available = bool(delivery_enabled)

    # Store operational status. Separate from is_active.
    is_online = _store_bool_from_form(
        "is_online",
        bool(int(store.get("is_online", store.get("is_open", 1)) or 0))
    )

    delivery_mode = (request.form.get("delivery_mode") or "polygon").strip().lower()
    if delivery_mode not in ["polygon"]:
        delivery_mode = "polygon"

    existing_delivery_zone_polygon = store.get("delivery_zone_polygon") or []

    if "delivery_zone_polygon" in request.form:
        delivery_zone_raw = (request.form.get("delivery_zone_polygon") or "").strip()
        delivery_zone_polygon = _parse_delivery_zone_polygon(delivery_zone_raw)

        # An intentional [] clears the zone. Any non-empty boundary payload that
        # cannot produce a valid 3+ point polygon is rejected instead of being
        # silently converted to an empty zone. This protects an existing Store
        # service area if client-side validation is bypassed.
        if delivery_zone_raw:
            try:
                submitted_zone = json.loads(delivery_zone_raw)
            except Exception:
                submitted_zone = None

            if submitted_zone is None or not isinstance(submitted_zone, list):
                flash("Delivery service area data is invalid. Please redraw the boundary and save again.", "warning")
                return redirect(url_for("store_profile"))

            if submitted_zone and not delivery_zone_polygon:
                flash("Delivery service area needs at least 3 valid boundary points.", "warning")
                return redirect(url_for("store_profile"))
    else:
        delivery_zone_polygon = existing_delivery_zone_polygon


    lat_raw = (request.form.get("latitude") or "").strip()
    lng_raw = (request.form.get("longitude") or "").strip()

    latitude = _store_float_or_none(lat_raw, -90, 90)
    longitude = _store_float_or_none(lng_raw, -180, 180)


    try:
        min_order_amount = float(min_order_amount_raw) if min_order_amount_raw else None
    except Exception:
        min_order_amount = None

    if not store_name:
        flash("Store name is required.", "warning")
        return redirect(url_for("store_profile"))

    if not owner_name:
        flash("Owner name is required.", "warning")
        return redirect(url_for("store_profile"))

    if not phone:
        flash("Phone number is required.", "warning")
        return redirect(url_for("store_profile"))

    if not address:
        flash("Store address is required.", "warning")
        return redirect(url_for("store_profile"))
    
    if pincode and not is_serviceable_pincode(pincode):
        flash("Please enter a valid 6-digit store pincode.", "warning")
        return redirect(url_for("store_profile"))

    if state and not is_assam_state(state):
        flash("Store state must be Assam for delivery operations.", "warning")
        return redirect(url_for("store_profile"))

    if delivery_enabled and delivery_mode == "polygon" and not delivery_zone_polygon:
        flash("Delivery zone polygon is required when delivery is enabled.", "warning")
        return redirect(url_for("store_profile"))
    
    update_data = {
        "store_name": store_name,
        "owner_name": owner_name,
        "phone": phone,

        "address": address,
        "city": city,
        "state": state,
        "pincode": pincode,

        "description": description,
        "profile_intro": profile_intro,

        "latitude": latitude,
        "longitude": longitude,

        "opening_time": opening_time,
        "closing_time": closing_time,
        "working_days": working_days,
        "min_order_amount": min_order_amount,

        # Backward compatibility with old field.
        "delivery_available": bool(delivery_enabled),

        # New delivery/serviceability fields.
        "is_online": 1 if is_online else 0,
        "is_open": 1 if is_online else 0,
        "delivery_enabled": 1 if delivery_enabled else 0,
        "delivery_mode": delivery_mode,
        "delivery_zone_polygon": delivery_zone_polygon,
        "delivery_zone_configured": 1 if delivery_zone_polygon else 0,

        "profile_updated_at": now,
        "updated_at": now
    }

    profile_image = request.files.get("profile_image")

    if profile_image and profile_image.filename:
        if not allowed_file(profile_image.filename):
            flash("Invalid store profile image file type.", "warning")
            return redirect(url_for("store_profile"))

        image_bytes = profile_image.read()

        if not image_bytes:
            flash("Please upload a valid store profile image.", "warning")
            return redirect(url_for("store_profile"))

        if len(image_bytes) > 4 * 1024 * 1024:
            flash("Store profile image must be 4 MB or smaller.", "warning")
            return redirect(url_for("store_profile"))

        safe_name = secure_filename(profile_image.filename)
        mime_type = profile_image.mimetype or "image/jpeg"

        image_doc = {
            "store_id": store["_id"],
            "store_id_str": str(store["_id"]),
            "filename": safe_name,
            "mime_type": mime_type,
            "data": Binary(image_bytes),
            "is_active": 1,
            "uploaded_by": str(u.get("_id") or u.get("id") or ""),
            "created_at": now,
            "updated_at": now
        }

        inserted_image = mongo.store_profile_images.insert_one(image_doc)

        mongo.store_profile_images.update_many(
            {
                "store_id": store["_id"],
                "_id": {"$ne": inserted_image.inserted_id}
            },
            {
                "$set": {
                    "is_active": 0,
                    "updated_at": now
                }
            }
        )

        update_data["profile_image_id"] = inserted_image.inserted_id
        update_data["profile_image_filename"] = safe_name
        update_data["profile_image_mime_type"] = mime_type
        update_data["profile_image_updated_at"] = now

    logo = request.files.get("logo")

    if logo and logo.filename:
        if not allowed_file(logo.filename):
            flash("Invalid logo/image file type.", "warning")
            return redirect(url_for("store_profile"))

        safe_name = secure_filename(logo.filename)
        stored_name = datetime.utcnow().strftime("%Y%m%d%H%M%S_") + safe_name
        folder = os.path.join(app.config["UPLOAD_FOLDER"], "store_profiles")
        os.makedirs(folder, exist_ok=True)

        logo.save(os.path.join(folder, stored_name))
        update_data["logo_path"] = f"uploads/store_profiles/{stored_name}"

        logo = request.files.get("logo")
        image = request.files.get("image")

        banner = request.files.get("banner")

    if banner and banner.filename:
        if not allowed_file(banner.filename):
            flash("Invalid banner image file type.", "warning")
            return redirect(url_for("store_profile"))

        fn = secure_filename(banner.filename)
        save_as = "store_banner_" + str(store["_id"]) + "_" + datetime.utcnow().strftime("%Y%m%d%H%M%S_") + fn
        os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
        banner.save(os.path.join(app.config["UPLOAD_FOLDER"], save_as))
        update_data["banner_path"] = f"uploads/{save_as}"

    mongo.stores.update_one(
        {"_id": store["_id"]},
        {"$set": update_data}
    )

    if store.get("user_id"):
        try:
            mongo.users.update_one(
                {"_id": ObjectId(str(store.get("user_id")))},
                {
                    "$set": {
                        "name": owner_name,
                        "phone": phone,
                        "updated_at": now
                    }
                }
            )
        except Exception:
            mongo.users.update_one(
                {"_id": store.get("user_id")},
                {
                    "$set": {
                        "name": owner_name,
                        "phone": phone,
                        "updated_at": now
                    }
                }
            )

    flash("Store profile updated successfully.", "success")
    return redirect(url_for("store_profile"))
