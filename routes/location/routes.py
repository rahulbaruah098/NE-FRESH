"""Location routes extracted from the updated app.py.

Logic, decorators, endpoint names and route paths are intentionally preserved.
Only the file location changed.
"""

from app_core import *

import urllib.parse
import urllib.request


@app.route("/api/service/pincodes")
def api_service_pincodes():
    return jsonify({
        "ok": True,
        "mode": "STORE_POLYGON_ZONE",
        "message": "Set your location first. Final delivery availability depends on the selected store delivery zone.",
        "pincodes": []
    })


def _float_or_none(value):
    try:
        if value is None or str(value).strip() == "":
            return None
        return float(value)
    except Exception:
        return None


def _locationiq_reverse_geocode(lat, lng):
    """
    Convert GPS latitude/longitude into address details using LocationIQ.

    Required .env:
        LOCATIONIQ_API_KEY=your_key_here
    """
    api_key = os.getenv("LOCATIONIQ_API_KEY", "").strip()

    if not api_key:
        raise RuntimeError("LOCATIONIQ_API_KEY is missing in .env")

    query = urllib.parse.urlencode({
        "key": api_key,
        "lat": str(lat),
        "lon": str(lng),
        "format": "json",
        "addressdetails": "1",
        "normalizeaddress": "1",
    })

    url = f"https://us1.locationiq.com/v1/reverse?{query}"

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "NE-Fresh/1.0",
            "Accept": "application/json",
        }
    )

    with urllib.request.urlopen(req, timeout=12) as response:
        raw = response.read().decode("utf-8", errors="replace")
        return json.loads(raw)
    
def _locationiq_search_pincode(pincode):
    """
    Convert Indian pincode into approximate address coordinates using LocationIQ.

    Required .env:
        LOCATIONIQ_API_KEY=your_key_here
    """
    api_key = os.getenv("LOCATIONIQ_API_KEY", "").strip()

    if not api_key:
        raise RuntimeError("LOCATIONIQ_API_KEY is missing in .env")

    query = urllib.parse.urlencode({
        "key": api_key,
        "q": f"{pincode}, Assam, India",
        "format": "json",
        "addressdetails": "1",
        "normalizeaddress": "1",
        "limit": "1",
        "countrycodes": "in",
    })

    url = f"https://us1.locationiq.com/v1/search?{query}"

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "NE-Fresh/1.0",
            "Accept": "application/json",
        }
    )

    with urllib.request.urlopen(req, timeout=12) as response:
        raw = response.read().decode("utf-8", errors="replace")
        return json.loads(raw)


@app.route("/api/location/reverse", methods=["POST"])
def api_location_reverse():
    """
    Frontend sends:
        { "lat": 26.1445, "lng": 91.7362 }

    Backend returns:
        address, pincode, city, state, lat, lng

    This route is used by checkout's "Use Current Location" button.
    """
    data = request.get_json(silent=True) or {}

    lat = _float_or_none(data.get("lat"))
    lng = _float_or_none(data.get("lng"))

    if lat is None or lng is None:
        return jsonify({
            "ok": False,
            "error": "Latitude and longitude are required."
        }), 400

    if lat < -90 or lat > 90 or lng < -180 or lng > 180:
        return jsonify({
            "ok": False,
            "error": "Invalid latitude or longitude."
        }), 400

    try:
        result = _locationiq_reverse_geocode(lat, lng)
    except Exception as exc:
        return jsonify({
            "ok": False,
            "error": "Could not detect address from current location.",
            "detail": str(exc)
        }), 502

    address_data = result.get("address") or {}

    pincode = _clean_pin(
        address_data.get("postcode")
        or address_data.get("postal_code")
        or ""
    )

    city = (
        address_data.get("city")
        or address_data.get("town")
        or address_data.get("village")
        or address_data.get("municipality")
        or address_data.get("county")
        or ""
    )

    state = address_data.get("state") or ""
    country = address_data.get("country") or ""
    display_address = result.get("display_name") or ""

    if not pincode:
        return jsonify({
            "ok": False,
            "error": "Pincode could not be detected from this GPS location. Please enter pincode manually.",
            "lat": lat,
            "lng": lng,
            "address": display_address,
            "city": city,
            "state": state,
            "country": country,
        }), 422

    serviceable = is_serviceable_pincode(pincode)
    assam = is_assam_state(state)

    return jsonify({
        "ok": True,
        "lat": lat,
        "lng": lng,
        "pincode": pincode,
        "address": display_address or f"Pincode {pincode}",
        "city": city,
        "state": state,
        "country": country,
        "serviceable": serviceable,
        "assam": assam,
        "message": "Location detected successfully."
    })

@app.route("/api/location/pincode/resolve", methods=["POST"])
def api_location_pincode_resolve():
    """
    Frontend sends:
        { "pincode": "781017" }

    Backend returns approximate:
        address, city, state, lat, lng, pincode

    This route is for:
        1. Navbar location modal
        2. Checkout pincode section

    It does NOT guarantee final store delivery.
    Final delivery is checked by /api/checkout/serviceability.
    """
    data = request.get_json(silent=True) or {}

    pincode = _clean_pin(data.get("pincode") or "")

    if not pincode or len(pincode) != 6:
        return jsonify({
            "ok": False,
            "error": "Please enter a valid 6-digit pincode."
        }), 400

    try:
        results = _locationiq_search_pincode(pincode)
    except Exception as exc:
        return jsonify({
            "ok": False,
            "error": "Could not resolve this pincode right now.",
            "detail": str(exc)
        }), 502

    if not isinstance(results, list) or not results:
        return jsonify({
            "ok": False,
            "error": "No location found for this pincode."
        }), 404

    result = results[0] or {}
    address_data = result.get("address") or {}

    lat = _float_or_none(result.get("lat"))
    lng = _float_or_none(result.get("lon"))

    city = (
        address_data.get("city")
        or address_data.get("town")
        or address_data.get("village")
        or address_data.get("municipality")
        or address_data.get("county")
        or ""
    )

    state = address_data.get("state") or ""
    country = address_data.get("country") or ""
    display_address = result.get("display_name") or f"Pincode {pincode}"

    detected_pin = _clean_pin(
        address_data.get("postcode")
        or address_data.get("postal_code")
        or pincode
    )

    if detected_pin and detected_pin != pincode:
        detected_pin = pincode

    assam = is_assam_state(state)

    return jsonify({
        "ok": True,
        "pincode": pincode,
        "lat": lat,
        "lng": lng,
        "address": display_address,
        "city": city,
        "state": state,
        "country": country,
        "assam": assam,
        "serviceable": bool(assam and is_serviceable_pincode(pincode)),
        "message": (
            "Location resolved. Store-wise delivery availability will be checked at checkout."
            if assam else
            "Location resolved, but this appears outside Assam."
        )
    })


@app.route("/api/location/set", methods=["POST"])
def api_location_set():
    data = request.get_json(silent=True) or {}

    address = (data.get("address") or "").strip()
    pincode_raw = data.get("pincode")
    lat = data.get("lat")
    lng = data.get("lng")
    city = (data.get("city") or "").strip()
    state = (data.get("state") or "").strip()
    source = (data.get("source") or "manual").strip()

    pincode = _clean_pin(pincode_raw)

    if not pincode:
        return jsonify({
            "ok": False,
            "error": "no pincode"
        }), 400

    lat_f = _float_or_none(lat)
    lng_f = _float_or_none(lng)

    serviceable = is_serviceable_pincode(pincode)

    session["service_area"] = {
        "address": address or f"Pincode {pincode}",
        "pincode": pincode,
        "lat": lat_f,
        "lng": lng_f,
        "city": city,
        "state": state,
        "source": source,
    }

    session["location_pincode"] = pincode
    session["location_lat"] = lat_f
    session["location_lng"] = lng_f
    session["location_address"] = address or f"Pincode {pincode}"
    session["location_city"] = city
    session["location_state"] = state
    session["location_source"] = source

    session.modified = True

    return jsonify({
        "ok": True,
        "serviceable": serviceable,
        "service_area": session["service_area"],
        "message": "Location saved. Store-wise delivery availability will be checked at checkout."
    })


@app.route("/api/location/clear", methods=["POST"])
def api_location_clear():
    session.pop("service_area", None)

    session.pop("location_pincode", None)
    session.pop("location_lat", None)
    session.pop("location_lng", None)
    session.pop("location_address", None)
    session.pop("location_city", None)
    session.pop("location_state", None)
    session.pop("location_source", None)

    session.modified = True

    return jsonify({
        "ok": True
    })


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

    clean_pincode = _clean_pin(pincode)
    lat_f = _float_or_none(lat)
    lng_f = _float_or_none(lng)

    session["service_area"] = {
        "address": address or f"Pincode {clean_pincode}",
        "pincode": clean_pincode,
        "lat": lat_f,
        "lng": lng_f,
    }

    session["location_pincode"] = clean_pincode
    session["location_lat"] = lat_f
    session["location_lng"] = lng_f
    session["location_address"] = address or f"Pincode {clean_pincode}"

    session.modified = True

    if not is_serviceable_pincode(clean_pincode):
        flash("Please enter a valid 6-digit pincode.", "warning")
    else:
        flash(f"Location set to {clean_pincode}. Store-wise delivery availability will be checked at checkout.", "success")

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