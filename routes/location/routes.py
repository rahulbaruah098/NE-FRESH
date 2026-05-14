"""Location routes extracted from the updated app.py.

Logic, decorators, endpoint names and route paths are intentionally preserved.
Only the file location changed.
"""

from app_core import *


@app.route("/api/service/pincodes")
def api_service_pincodes():
    return jsonify({
        "ok": True,
        "mode": "ASSAM_STATE_WIDE",
        "message": "Delivery is available across Assam.",
        "pincodes": []
    })

@app.route("/api/location/set", methods=["POST"])
def api_location_set():
    data = request.get_json(silent=True) or {}
    address = (data.get("address") or "").strip()
    pincode_raw = data.get("pincode")
    lat = data.get("lat")
    lng = data.get("lng")

    pincode = _clean_pin(pincode_raw)
    if not pincode:
        return jsonify({"ok": False, "error": "no pincode"}), 400

    # normalize coords
    try:
        lat_f = float(lat) if lat is not None and str(lat).strip() != "" else None
    except Exception:
        lat_f = None
    try:
        lng_f = float(lng) if lng is not None and str(lng).strip() != "" else None
    except Exception:
        lng_f = None

    serviceable = is_serviceable_pincode(pincode)

    # ✅ keep existing structure
    session["service_area"] = {
        "address": address or f"Pincode {pincode}",
        "pincode": pincode,
        "lat": lat_f,
        "lng": lng_f,
    }

    # ✅ add keys that your checkout() already uses
    session["location_pincode"] = pincode
    session["location_lat"] = lat_f
    session["location_lng"] = lng_f

    session.modified = True
    return jsonify({"ok": True, "serviceable": serviceable, "service_area": session["service_area"]})

@app.route("/api/location/clear", methods=["POST"])
def api_location_clear():
    session.pop("service_area", None)

    # ✅ also clear these
    session.pop("location_pincode", None)
    session.pop("location_lat", None)
    session.pop("location_lng", None)

    session.modified = True
    return jsonify({"ok": True})

@app.route("/detect-location", methods=["GET", "POST"])
def detect_location():
    if request.method == "POST":
        data = request.get_json(silent=True) or request.form or {}
        pincode = (data.get("pincode") or "").strip()
        address = (data.get("address") or "").strip()
        lat = data.get("lat")
        lng = data.get("lng")
    else:
        pincode = (request.args.get("pincode") or "").strip()
        address = (request.args.get("address") or "").strip()
        lat = request.args.get("lat")
        lng = request.args.get("lng")

    if not pincode:
        flash("Could not detect pincode.", "warning")
        return redirect(request.referrer or url_for("index"))

    session["service_area"] = {
        "address": address or f"Pincode {pincode}",
        "pincode": pincode,
        "lat": float(lat) if lat else None,
        "lng": float(lng) if lng else None,
    }
    session.modified = True
    if not is_serviceable_pincode(pincode):
        flash("Please enter a valid 6-digit pincode.", "warning")
    else:
        flash(f"Location set to {pincode}. Delivery is available across Assam.", "success")

    return redirect(request.referrer or url_for("index"))

@app.route('/api/orders/<oid>/rider_location', methods=['GET'])
@api_login_required
def api_order_rider_location(user_id, oid):
    try:
        oid_obj = ObjectId(oid)
    except Exception:
        return jsonify({
            "success": False,
            "error": "Invalid order id"
        }), 400

    order = mongo.orders.find_one({
        "_id": oid_obj,
        "user_id": str(user_id)
    })

    if not order:
        return jsonify({
            "success": False,
            "error": "Order not found"
        }), 404

    row = mongo.delivery_locations.find_one(
        {"order_id": oid_obj},
        sort=[("recorded_at", -1)]
    )

    if not row:
        return jsonify({
            "success": True,
            "has_location": False,
            "location": None
        })

    return jsonify({
        "success": True,
        "has_location": True,
        "location": {
            "latitude": row.get("latitude"),
            "longitude": row.get("longitude"),
            "updated_at": row.get("recorded_at")
        }
    })
