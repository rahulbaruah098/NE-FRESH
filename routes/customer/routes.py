"""Customer routes extracted from the updated app.py.

Logic, decorators, endpoint names and route paths are intentionally preserved.
Only the file location changed.
"""

from app_core import *


@app.route("/profile", methods=["GET", "POST"])
@login_required()
def profile():
    u = current_user()

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        phone = request.form.get("phone", "").strip()

        update_data = {}

        if name:
            update_data["name"] = name

        if phone:
            update_data["phone"] = normalize_phone(phone)

        if update_data:
            mongo.users.update_one(
                {"_id": ObjectId(u["id"])},
                {"$set": update_data}
            )

        flash("Profile updated.", "success")
        return redirect(url_for("profile"))

    addrs = list(
        mongo.addresses.find({"user_id": u["id"]}).sort([
            ("is_default", -1),
            ("created_at", -1)
        ])
    )

    for a in addrs:
        a["id"] = str(a["_id"])

    return_to = (request.args.get("next") or "").strip()

    if return_to not in {"checkout"}:
        return_to = ""

    return render_template(
        "profile.html",
        user=u,
        addresses=addrs,
        return_to=return_to
    )

@app.route("/profile/address/new", methods=["POST"])
@login_required()
def address_new():
    u = current_user()

    line1 = request.form.get("line1", "").strip()
    line2 = request.form.get("line2", "").strip()
    city = request.form.get("city", "").strip()
    state = request.form.get("state", "").strip()
    pincode = request.form.get("pincode", "").strip()
    label = request.form.get("label", "").strip() or "Home"
    is_def = 1 if request.form.get("is_default") == "1" else 0

    return_to = (request.form.get("next") or "").strip()

    if return_to not in {"checkout"}:
        return_to = ""

    lat_raw = (request.form.get("latitude") or "").strip()
    lng_raw = (request.form.get("longitude") or "").strip()

    latitude = None
    longitude = None

    if lat_raw:
        try:
            latitude = float(lat_raw)
            if latitude < -90 or latitude > 90:
                latitude = None
        except Exception:
            latitude = None

    if lng_raw:
        try:
            longitude = float(lng_raw)
            if longitude < -180 or longitude > 180:
                longitude = None
        except Exception:
            longitude = None

    if not line1:
        flash("Address line 1 is required.", "warning")
        return redirect(url_for("profile"))
    
    if not is_serviceable_pincode(pincode):
        flash("Please enter a valid 6-digit pincode.", "warning")
        return redirect(url_for("profile"))

    if not is_assam_state(state):
        flash("Delivery is currently available only within Assam.", "warning")
        return redirect(url_for("profile"))

    if is_def:
        mongo.addresses.update_many(
            {"user_id": u["id"]},
            {"$set": {"is_default": 0}}
        )

    mongo.addresses.insert_one({
        "user_id": u["id"],
        "label": label,
        "line1": line1,
        "line2": line2,
        "city": city,
        "state": state,
        "pincode": pincode,
        "latitude": latitude,
        "longitude": longitude,
        "is_default": is_def,
        "created_at": datetime.utcnow().isoformat()
    })

    flash("Address saved.", "success")

    if return_to == "checkout":
        return redirect(url_for("checkout"))

    return redirect(url_for("profile"))

@app.route("/api/checkout/address/save", methods=["POST"])
@login_required()
def api_checkout_address_save():
    """
    Save a delivery address directly from checkout modal.

    Used by checkout flow:
    Use Current Location -> detect GPS/pincode -> user fills house/landmark
    -> save address without leaving checkout page.
    """
    u = current_user()
    data = request.get_json(silent=True) or {}

    line1 = (data.get("line1") or "").strip()
    line2 = (data.get("line2") or "").strip()
    city = (data.get("city") or "").strip()
    state = (data.get("state") or "").strip()
    pincode = _clean_pin(data.get("pincode") or "")
    label = (data.get("label") or "Home").strip() or "Home"

    lat_raw = data.get("latitude")
    lng_raw = data.get("longitude")

    def _safe_float(value, min_value=None, max_value=None):
        try:
            if value is None or str(value).strip() == "":
                return None

            n = float(value)

            if min_value is not None and n < min_value:
                return None

            if max_value is not None and n > max_value:
                return None

            return n
        except Exception:
            return None

    latitude = _safe_float(lat_raw, -90, 90)
    longitude = _safe_float(lng_raw, -180, 180)

    existing_count = mongo.addresses.count_documents({
        "user_id": u["id"]
    })

    is_default = 1 if data.get("is_default") in [1, "1", True, "true", "yes"] else 0

    # If this is the user's first address, force it as default.
    if existing_count == 0:
        is_default = 1

    if not line1:
        return jsonify({
            "ok": False,
            "error": "House / flat / building details are required."
        }), 400

    if not pincode or not is_serviceable_pincode(pincode):
        return jsonify({
            "ok": False,
            "error": "Please enter a valid 6-digit pincode."
        }), 400

    if not state:
        return jsonify({
            "ok": False,
            "error": "State is required."
        }), 400

    if not is_assam_state(state):
        return jsonify({
            "ok": False,
            "error": "Delivery is currently available only within Assam."
        }), 400

    if not city:
        return jsonify({
            "ok": False,
            "error": "City / town / area is required."
        }), 400

    if is_default:
        mongo.addresses.update_many(
            {"user_id": u["id"]},
            {"$set": {"is_default": 0}}
        )

    now = datetime.utcnow().isoformat()

    address_doc = {
        "user_id": u["id"],
        "label": label,
        "line1": line1,
        "line2": line2,
        "city": city,
        "state": state,
        "pincode": pincode,
        "latitude": latitude,
        "longitude": longitude,
        "is_default": is_default,
        "source": data.get("source") or "checkout",
        "created_at": now,
        "updated_at": now,
    }

    result = mongo.addresses.insert_one(address_doc)
    address_id = str(result.inserted_id)

    # Keep global/session location synced for checkout fee calculation.
    session["service_area"] = {
        "address": ", ".join([x for x in [line1, line2, city, state, pincode] if x]),
        "pincode": pincode,
        "lat": latitude,
        "lng": longitude,
        "city": city,
        "state": state,
        "source": "checkout_address",
    }

    session["location_pincode"] = pincode
    session["location_lat"] = latitude
    session["location_lng"] = longitude
    session["location_address"] = session["service_area"]["address"]
    session["location_city"] = city
    session["location_state"] = state
    session["location_source"] = "checkout_address"
    session.modified = True

    return jsonify({
        "ok": True,
        "message": "Address saved successfully.",
        "address": {
            "id": address_id,
            "label": label,
            "line1": line1,
            "line2": line2,
            "city": city,
            "state": state,
            "pincode": pincode,
            "latitude": latitude,
            "longitude": longitude,
            "is_default": is_default,
        }
    })

@app.route("/profile/address/<aid>/delete", methods=["POST"])
@login_required()
def address_delete(aid):
    u = current_user()

    try:
        aid_obj = ObjectId(aid)
    except Exception:
        flash("Invalid address.", "danger")
        return redirect(url_for("profile"))

    mongo.addresses.delete_one({
        "_id": aid_obj,
        "user_id": u["id"]
    })

    flash("Address deleted.", "info")
    return redirect(url_for("profile"))

@app.route("/profile/address/<aid>/default", methods=["POST"])
@login_required()
def address_set_default(aid):
    u = current_user()

    try:
        aid_obj = ObjectId(aid)
    except Exception:
        flash("Invalid address.", "danger")
        return redirect(url_for("profile"))

    mongo.addresses.update_many(
        {"user_id": u["id"]},
        {"$set": {"is_default": 0}}
    )

    mongo.addresses.update_one(
        {"_id": aid_obj, "user_id": u["id"]},
        {"$set": {"is_default": 1}}
    )

    flash("Default address updated.", "success")
    return redirect(url_for("profile"))

@app.route("/api/profile/address/detect", methods=["POST"])
@login_required()
def api_address_detect():
    u = current_user()
    data = request.get_json(silent=True) or {}

    lat = data.get("latitude")
    lng = data.get("longitude")

    if lat is None or lng is None:
        return jsonify({"ok": False, "msg": "No coordinates"}), 400

    result = mongo.addresses.insert_one({
        "user_id": u["id"],
        "label": "Detected",
        "line1": "(Detected location)",
        "line2": "",
        "city": "",
        "state": "",
        "pincode": "",
        "latitude": float(lat),
        "longitude": float(lng),
        "is_default": 0,
        "created_at": datetime.utcnow().isoformat()
    })

    return jsonify({
        "ok": True,
        "address_id": str(result.inserted_id)
    })

@app.route("/complaints", methods=["GET", "POST"], endpoint="customer_complaints")
@login_required()
def customer_complaints():
    u = current_user()

    if not u:
        flash("Please log in first.", "warning")
        return redirect(url_for("login"))

    if u.get("role") != "customer":
        flash("Only customer accounts can raise complaints.", "warning")
        return redirect(url_for("index"))

    stores = list(
        mongo.stores.find({
            "$or": [
                {"is_active": 1},
                {"is_active": True},
                {"is_active": {"$exists": False}}
            ]
        }).sort("store_name", 1)
    )

    for s in stores:
        s["id"] = str(s["_id"])
        s["store_name"] = s.get("store_name", "Store")

    if request.method == "POST":
        complaint_type = (request.form.get("complaint_type") or "").strip()
        subject = (request.form.get("subject") or "").strip()
        message = (request.form.get("message") or "").strip()
        order_id = (request.form.get("order_id") or "").strip()
        product_name = (request.form.get("product_name") or "").strip()
        store_id = (request.form.get("store_id") or "").strip()

        allowed_types = {
            "order",
            "product",
            "store",
            "delivery",
            "payment",
            "refund",
            "other"
        }

        if complaint_type not in allowed_types:
            flash("Please select a valid complaint type.", "warning")
            return redirect(url_for("customer_complaints"))

        store_obj_id = None
        store = None
        assigned_to = "admin"
        target_type = "admin"
        store_name = ""

        # Store selection is optional now.
        # If selected, complaint goes to the store and Admin can also view it.
        # If not selected, complaint goes directly to NE FRESH Admin.
        if store_id:
            try:
                store_obj_id = ObjectId(store_id)
            except Exception:
                flash("Invalid store selected.", "danger")
                return redirect(url_for("customer_complaints"))

            store = mongo.stores.find_one({"_id": store_obj_id})

            if not store:
                flash("Selected store was not found.", "danger")
                return redirect(url_for("customer_complaints"))

            assigned_to = "store"
            target_type = "store"
            store_name = store.get("store_name", "")

        if not subject:
            flash("Complaint subject is required.", "warning")
            return redirect(url_for("customer_complaints"))

        if not message:
            flash("Complaint details are required.", "warning")
            return redirect(url_for("customer_complaints"))

        if len(subject) > 160:
            flash("Subject is too long. Please keep it within 160 characters.", "warning")
            return redirect(url_for("customer_complaints"))

        if len(message) > 1200:
            flash("Complaint details are too long. Please keep it within 1200 characters.", "warning")
            return redirect(url_for("customer_complaints"))

        complaint_image_path = ""

        complaint_image = request.files.get("complaint_image")

        if complaint_image and complaint_image.filename:
            if not allowed_file(complaint_image.filename):
                flash("Only JPG, JPEG, PNG or WEBP images are allowed.", "warning")
                return redirect(url_for("customer_complaints"))

            original_name = secure_filename(complaint_image.filename)
            ext = original_name.rsplit(".", 1)[1].lower()
            stored_name = "complaint_" + datetime.utcnow().strftime("%Y%m%d%H%M%S") + "_" + secrets.token_hex(6) + "." + ext

            complaint_folder = os.path.join(app.config["UPLOAD_FOLDER"], "complaints")
            os.makedirs(complaint_folder, exist_ok=True)

            complaint_image.save(os.path.join(complaint_folder, stored_name))

            complaint_image_path = "uploads/complaints/" + stored_name

        now = datetime.utcnow().isoformat()

        mongo.customer_complaints.insert_one({
            "user_id": str(u["_id"]),
            "customer_name": u.get("name", "Customer"),
            "customer_email": u.get("email", ""),
            "customer_phone": u.get("phone", ""),

            "complaint_type": complaint_type,
            "subject": subject,
            "message": message,
            "order_id": order_id,
            "product_name": product_name,

            "complaint_image_path": complaint_image_path,
            "image_path": complaint_image_path,
            "attachment_type": "image" if complaint_image_path else "",

            "store_id": store_obj_id,
            "store_id_str": str(store_obj_id) if store_obj_id else "",
            "store_name": store_name,

            "assigned_to": assigned_to,
            "target_type": target_type,

            "status": "open",
            "progress_status": "received",
            "priority": "normal",

            "admin_reply": "",
            "store_reply": "",
            "store_progress_note": "",
            "admin_progress_note": "",

            "created_at": now,
            "updated_at": now,
            "is_active": 1
        })

        if assigned_to == "admin":
            flash("Your complaint has been submitted directly to NE FRESH Admin.", "success")
        else:
            flash("Your complaint has been submitted to the selected store.", "success")
        return redirect(url_for("customer_complaints"))

    complaints = list(
        mongo.customer_complaints.find({
            "user_id": str(u["_id"]),
            "$or": [
                {"is_active": 1},
                {"is_active": True},
                {"is_active": {"$exists": False}}
            ]
        }).sort("created_at", -1)
    )

    for c in complaints:
        c["id"] = str(c["_id"])
        c["status_label"] = str(c.get("status") or "open").replace("_", " ").title()
        c["progress_status_label"] = str(c.get("progress_status") or "received").replace("_", " ").title()
        c["complaint_image_path"] = c.get("complaint_image_path") or c.get("image_path") or ""

        created_at = c.get("created_at") or ""
        c["created_at_display"] = created_at

        try:
            if isinstance(created_at, str) and created_at:
                clean_dt = created_at.replace("Z", "")
                dt_obj = datetime.fromisoformat(clean_dt)
                c["created_at_display"] = dt_obj.strftime("%d %b %Y, %I:%M %p")
        except Exception:
            pass

    metrics = {
        "total": len(complaints),
        "open": sum(1 for c in complaints if c.get("status") == "open"),
        "in_progress": sum(1 for c in complaints if c.get("status") == "in_progress"),
        "resolved": sum(1 for c in complaints if c.get("status") == "resolved")
    }

    return render_template(
        "customer_complaints.html",
        user=u,
        stores=stores,
        complaints=complaints,
        metrics=metrics
    )

# Legacy duplicate /complaints POST route disabled to avoid conflicting with customer_complaints().
# The current complaint submission flow is handled by customer_complaints().
@app.route('/complaints/legacy-submit-disabled', methods=['POST'])
@login_required()
def complaints_create_legacy_disabled():
    u = current_user()
    target_type = (request.form.get('target_type','') or '').lower()
    target_id = int(request.form.get('target_id','0') or 0)
    message = (request.form.get('message','') or '').strip()
    order_id = request.form.get('order_id')
    order_id = int(order_id) if order_id else None
    title = (request.form.get('title') or '').strip() or None

    if target_type not in ('store','delivery','product') or not target_id or not message:
        flash('Please provide valid complaint details.','warning')
        return redirect(request.referrer or url_for('index'))

    image_path = None
    f = request.files.get('image')
    if f and f.filename:
        fn = secure_filename(f.filename)
        save_as = datetime.utcnow().strftime("%Y%m%d%H%M%S_") + fn
        os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
        f.save(os.path.join(app.config["UPLOAD_FOLDER"], save_as))
        image_path = f"uploads/{save_as}"

    try:
        file_complaint(u['id'], target_type, target_id, message, order_id, image_path=image_path, title=title)
        flash('Complaint submitted. We’ll review it shortly.','success')
    except Exception as e:
        flash(f'Could not submit complaint: {e}','danger')
    return redirect(request.referrer or url_for('index'))

@app.route('/api/user/profile', methods=['GET'])
@api_login_required
def api_user_profile(user_id):
    try:
        user_obj_id = ObjectId(str(user_id))
    except Exception:
        return jsonify({
            'success': False,
            'error': 'Invalid user id'
        }), 400

    u = mongo.users.find_one({"_id": user_obj_id})

    if not u:
        return jsonify({
            'success': False,
            'error': 'User not found'
        }), 404

    return jsonify({
        'success': True,
        'user': {
            'id': str(u['_id']),
            'name': u.get('name', ''),
            'email': u.get('email', ''),
            'phone': u.get('phone', ''),
            'role': u.get('role', '')
        }
    })

@app.route('/api/user/profile', methods=['PUT'])
@api_login_required
def api_user_profile_update(user_id):
    data = request.get_json(silent=True) or {}

    name = (data.get('name') or '').strip()
    phone = (data.get('phone') or '').strip()

    update_data = {}

    if name:
        update_data['name'] = name

    if phone:
        update_data['phone'] = normalize_phone(phone)

    if update_data:
        try:
            user_obj_id = ObjectId(str(user_id))
        except Exception:
            return jsonify({
                'success': False,
                'error': 'Invalid user id'
            }), 400

        mongo.users.update_one(
            {"_id": user_obj_id},
            {"$set": update_data}
        )

    return jsonify({'success': True})

@app.route("/api/addresses", methods=["POST"])
@api_login_required
def api_addresses_create(user_id):
    data = request.get_json(silent=True) or {}

    label = (data.get("label") or "Home").strip()
    line1 = (data.get("line1") or data.get("address_line_1") or "").strip()
    line2 = (data.get("line2") or data.get("address_line_2") or "").strip()
    city = (data.get("city") or "").strip()
    state = (data.get("state") or "").strip()
    pincode = (data.get("pincode") or "").strip()
    lat = data.get("latitude")
    lng = data.get("longitude")
    is_def = 1 if bool(data.get("is_default", True)) else 0

    if not line1:
        return jsonify({"success": False, "error": "Address line1 is required"}), 400

    if not pincode or len(pincode) != 6 or not pincode.isdigit():
        return jsonify({"success": False, "error": "Valid 6-digit pincode required"}), 400

    if not is_serviceable_pincode(pincode):
        return jsonify({'success': False, 'error': 'Invalid pincode'}), 400

    if not is_assam_state(state):
        return jsonify({'success': False, 'error': 'Delivery is currently available only within Assam'}), 400

    latitude = None
    longitude = None

    if lat is not None and str(lat).strip() != "":
        try:
            latitude = float(lat)
        except Exception:
            latitude = None

    if lng is not None and str(lng).strip() != "":
        try:
            longitude = float(lng)
        except Exception:
            longitude = None

    if is_def:
        mongo.addresses.update_many(
            {"user_id": str(user_id)},
            {"$set": {"is_default": 0}}
        )

    result = mongo.addresses.insert_one({
        "user_id": str(user_id),
        "label": label,
        "line1": line1,
        "line2": line2,
        "city": city,
        "state": state,
        "pincode": pincode,
        "latitude": latitude,
        "longitude": longitude,
        "is_default": is_def,
        "created_at": datetime.utcnow().isoformat()
    })

    return jsonify({
        "success": True,
        "address_id": str(result.inserted_id)
    }), 201

@app.route("/api/addresses", methods=["GET"])
@api_login_required
def api_addresses_list(user_id):
    rows = list(
        mongo.addresses.find({"user_id": str(user_id)}).sort([
            ("is_default", -1),
            ("created_at", -1)
        ])
    )

    return jsonify({
        "success": True,
        "addresses": [{
            "id": str(r["_id"]),
            "label": r.get("label", ""),
            "address_line_1": r.get("line1", ""),
            "address_line_2": r.get("line2", ""),
            "line1": r.get("line1", ""),
            "line2": r.get("line2", ""),
            "city": r.get("city", ""),
            "state": r.get("state", ""),
            "pincode": r.get("pincode", ""),
            "latitude": float(r["latitude"]) if r.get("latitude") is not None else None,
            "longitude": float(r["longitude"]) if r.get("longitude") is not None else None,
            "is_default": bool(r.get("is_default")),
            "created_at": r.get("created_at", ""),
        } for r in rows]
    }), 200

@app.route("/api/addresses/<address_id>", methods=["DELETE"])
@api_login_required
def api_delete_address(user_id, address_id):
    try:
        address_obj_id = ObjectId(str(address_id))
    except Exception:
        return jsonify({"success": False, "error": "Invalid address id"}), 400

    result = mongo.addresses.delete_one({
        "_id": address_obj_id,
        "user_id": str(user_id)
    })

    if result.deleted_count == 0:
        return jsonify({"success": False, "error": "Address not found"}), 404

    return jsonify({"success": True}), 200

@app.route("/api/addresses/<address_id>/delete", methods=["POST"])
@api_login_required
def api_addresses_delete_post(user_id, address_id):
    return api_delete_address(user_id, address_id)
