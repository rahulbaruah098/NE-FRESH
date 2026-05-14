import os
import io
import re
import math
from io import BytesIO
import secrets
from datetime import datetime, timedelta
from random import randint
import csv, zipfile, json
from datetime import date,datetime
import time
from flask import Flask, render_template, request,Response, redirect, url_for, session, flash, jsonify, send_file, abort
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from functools import wraps
from flask import make_response
from collections import defaultdict

# MongoDB imports
from bson import ObjectId
from pymongo.errors import DuplicateKeyError
from mongo_db import mongo, ensure_mongo_indexes

# ---- Env + Twilio
from dotenv import load_dotenv
load_dotenv()  # reads .env


app = Flask(__name__)
app.secret_key = os.getenv("APP_SECRET_KEY", "dev-only-change-this")  # set real secret in production

# WebView Session Configuration
from datetime import timedelta
app.config['SESSION_COOKIE_HTTPONLY'] = False  # Allow WebView to use cookies
app.config['SESSION_COOKIE_SECURE'] = False    # Set to True in production with HTTPS
app.config['SESSION_COOKIE_SAMESITE'] = None   # Allow cross-origin cookies for mobile
app.config['SESSION_COOKIE_NAME'] = 'session'  # Consistent cookie name
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=7)


# Your existing CORS setup should cover this, but verify:
CORS(app, resources={
    r"/api/*": {
        "origins": "*",
        "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization"],
        "supports_credentials": True
    }
})

print("[RUNNING]", __file__)


def _parse_since_to_sqlite(since_raw: str):
    """
    Returns a string in 'YYYY-MM-DD HH:MM:SS' that SQLite datetime() can safely compare.
    Accepts ISO ('T' separator) and sqlite style (' ' separator).
    """
    if not since_raw:
        dt = datetime.utcnow() - timedelta(minutes=2)
        return dt.strftime("%Y-%m-%d %H:%M:%S")

    s = str(since_raw).strip()

    # Remove trailing 'Z'
    if s.endswith("Z"):
        s = s[:-1]

    dt = None

    # Try ISO first (supports microseconds)
    try:
        dt = datetime.fromisoformat(s)
    except Exception:
        dt = None

    # Try sqlite style
    if dt is None:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                dt = datetime.strptime(s, fmt)
                break
            except Exception:
                continue

    if dt is None:
        dt = datetime.utcnow() - timedelta(minutes=2)

    return dt.strftime("%Y-%m-%d %H:%M:%S")
# ---------------------------
# CONTEXT (globals to templates)
# ---------------------------
# ---------------------------
# CONTEXT (globals to templates)
# ---------------------------
@app.context_processor
def inject_globals():
    return {
        "datetime": datetime,
        "service_area": session.get("service_area")
    }




@app.context_processor
def inject_cart_count():
    try:
        u = current_user()
        if not u:
            return dict(cart_count=0)

        cid = get_or_create_cart(u["id"])
        cart_count = mongo.cart_items.count_documents({"cart_id": cid})

        return dict(cart_count=cart_count)
    except Exception:
        return dict(cart_count=0)


# ---- Footer links site-wide ----
FOOTER_LINKS = [
    {"label": "Privacy", "endpoint": "legal_privacy"},
    {"label": "Security", "endpoint": "legal_security"},
    {"label": "Terms of Service", "endpoint": "legal_terms"},
    {"label": "Help & Support", "endpoint": "legal_help"},
    {"label": "Report a Fraud", "endpoint": "legal_report_fraud"},
]
@app.context_processor
def inject_footer_links():
    return {"FOOTER_LINKS": FOOTER_LINKS}

# ----------------------
# ----------------------
# DELIVERY CONFIG
# ----------------------
BASE_DELIVERY_FEE_INR = 40

# Assam-wide delivery enabled:
# no fixed pincode list and no max-distance blocking.
# Delivery fee is calculated by distance slabs.
DELIVERY_SURCHARGE_SLABS = [
    (0, 2, 0),       # 0 - 2 km: ₹40
    (2, 5, 15),      # 2 - 5 km: ₹55
    (5, 10, 30),     # 5 - 10 km: ₹70
    (10, 20, 50),    # 10 - 20 km: ₹90
    (20, 50, 80),    # 20 - 50 km: ₹120
    (50, 9999, 120), # 50+ km: ₹160
]

MAX_DELIVERY_KM = None
DELIVERY_MODE = "ASSAM_STATE_WIDE_DISTANCE_FEE"

def haversine_km(lat1, lon1, lat2, lon2):
    if None in (lat1, lon1, lat2, lon2):
        return None
    R = 6371.0
    phi1 = math.radians(float(lat1))
    phi2 = math.radians(float(lat2))
    dphi = math.radians(float(lat2) - float(lat1))
    dlmb = math.radians(float(lon2) - float(lon1))
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


# ----------------------
# DELIVERY PARTNER LIVE MODE
# ----------------------
DELIVERY_ACTIONABLE_STATUSES = ["PLACED", "CONFIRMED", "PREPARING"]
DELIVERY_ASSIGNED_ACTIVE_STATUSES = ["ASSIGNED_TO_DELIVERY", "OUT_FOR_DELIVERY"]

# Only drivers within this radius from the store pickup point can accept.
# If store coordinates are missing, distance check is skipped.
DELIVERY_ACCEPT_RADIUS_KM = 15.0


def _delivery_now():
    return datetime.utcnow().isoformat()


def _get_delivery_availability(user_id):
    return mongo.delivery_availability.find_one({"user_id": str(user_id)}) or {}


def _is_delivery_active(user_id):
    row = _get_delivery_availability(user_id)
    return bool(row.get("active"))


def _get_float_or_none(value):
    try:
        if value is None or str(value).strip() == "":
            return None
        return float(value)
    except Exception:
        return None

# =========================================================
# PRODUCT DISCOUNT HELPERS
# =========================================================
def _safe_float(value, default=0.0):
    try:
        if value is None or str(value).strip() == "":
            return float(default)
        return float(value)
    except Exception:
        return float(default)

# =========================================================
# PRODUCT DISCOUNT HELPERS
# =========================================================
def _safe_float(value, default=0.0):
    try:
        if value is None or str(value).strip() == "":
            return float(default)
        return float(value)
    except Exception:
        return float(default)


def _calculate_product_pricing_from_form(request_form, fallback_original_price=0):
    """
    Product pricing rules:
    - original_price_per_kg = store-entered base price
    - price_per_kg = final customer selling price after discount
    - discount can be disabled, percent-based, or fixed-amount based
    """

    original_price = _safe_float(
        request_form.get("original_price_per_kg") or request_form.get("price_per_kg"),
        fallback_original_price
    )

    if original_price < 0:
        original_price = -1

    discount_enabled_raw = (
        request_form.get("discount_enabled")
        or request_form.get("is_discount_enabled")
        or ""
    )

    discount_enabled = str(discount_enabled_raw).strip().lower() in {
        "1",
        "true",
        "on",
        "yes",
        "enabled"
    }

    discount_type = (request_form.get("discount_type") or "percent").strip().lower()

    if discount_type not in {"percent", "amount"}:
        discount_type = "percent"

    discount_value = _safe_float(request_form.get("discount_value"), 0)

    if discount_value < 0:
        discount_value = 0

    discount_amount = 0.0
    discount_percent = 0.0
    final_price = original_price

    if discount_enabled and original_price > 0 and discount_value > 0:
        if discount_type == "percent":
            if discount_value > 100:
                discount_value = 100

            discount_percent = discount_value
            discount_amount = original_price * (discount_percent / 100)
            final_price = original_price - discount_amount

        elif discount_type == "amount":
            if discount_value > original_price:
                discount_value = original_price

            discount_amount = discount_value
            final_price = original_price - discount_amount
            discount_percent = (discount_amount / original_price * 100) if original_price else 0

    if final_price < 0:
        final_price = 0

    return {
        "original_price_per_kg": round(original_price, 2),
        "price_per_kg": round(final_price, 2),
        "discount_enabled": bool(discount_enabled and discount_amount > 0),
        "discount_type": discount_type,
        "discount_value": round(discount_value, 2),
        "discount_amount_per_kg": round(discount_amount, 2),
        "discount_percent": round(discount_percent, 2)
    }

def _calculate_product_pricing_from_form(request_form, fallback_original_price=0):
    """
    Product pricing rules:
    - original_price_per_kg = store-entered base price
    - price_per_kg = final customer selling price after discount
    - discount can be disabled, percent-based, or fixed-amount based
    """

    original_price = _safe_float(
        request_form.get("original_price_per_kg") or request_form.get("price_per_kg"),
        fallback_original_price
    )

    if original_price < 0:
        original_price = -1

    discount_enabled_raw = (
        request_form.get("discount_enabled")
        or request_form.get("is_discount_enabled")
        or ""
    )

    discount_enabled = str(discount_enabled_raw).strip().lower() in {
        "1",
        "true",
        "on",
        "yes",
        "enabled"
    }

    discount_type = (request_form.get("discount_type") or "percent").strip().lower()

    if discount_type not in {"percent", "amount"}:
        discount_type = "percent"

    discount_value = _safe_float(request_form.get("discount_value"), 0)

    if discount_value < 0:
        discount_value = 0

    discount_amount = 0.0
    discount_percent = 0.0
    final_price = original_price

    if discount_enabled and original_price > 0 and discount_value > 0:
        if discount_type == "percent":
            if discount_value > 100:
                discount_value = 100

            discount_percent = discount_value
            discount_amount = original_price * (discount_percent / 100)
            final_price = original_price - discount_amount

        elif discount_type == "amount":
            if discount_value > original_price:
                discount_value = original_price

            discount_amount = discount_value
            final_price = original_price - discount_amount
            discount_percent = (discount_amount / original_price * 100) if original_price else 0

    if final_price < 0:
        final_price = 0

    return {
        "original_price_per_kg": round(original_price, 2),
        "price_per_kg": round(final_price, 2),
        "discount_enabled": bool(discount_enabled and discount_amount > 0),
        "discount_type": discount_type,
        "discount_value": round(discount_value, 2),
        "discount_amount_per_kg": round(discount_amount, 2),
        "discount_percent": round(discount_percent, 2)
    }


def _driver_distance_to_store_km(order_doc, availability_doc):
    driver_lat = _get_float_or_none(availability_doc.get("latitude"))
    driver_lng = _get_float_or_none(availability_doc.get("longitude"))

    if driver_lat is None or driver_lng is None:
        return None

    store = None
    if order_doc.get("store_id"):
        store = mongo.stores.find_one({"_id": order_doc.get("store_id")})

    if not store:
        return None

    store_lat = _get_float_or_none(store.get("latitude"))
    store_lng = _get_float_or_none(store.get("longitude"))

    if store_lat is None or store_lng is None:
        return None

    return haversine_km(driver_lat, driver_lng, store_lat, store_lng)


def _hydrate_delivery_order(o):
    store = mongo.stores.find_one({"_id": o.get("store_id")}) if o.get("store_id") else None

    customer = None
    if o.get("user_id"):
        try:
            customer = mongo.users.find_one({"_id": ObjectId(o.get("user_id"))})
        except Exception:
            customer = None

    addr = mongo.order_addresses.find_one({"order_id": o["_id"]})

    o["id"] = str(o["_id"])
    o["store_name"] = store.get("store_name") if store else o.get("store_name", "")
    o["customer_name"] = customer.get("name") if customer else o.get("customer_name", "")
    o["customer_phone"] = customer.get("phone") if customer else o.get("customer_phone", "")

    o["addr_line1"] = addr.get("line1") if addr else ""
    o["addr_line2"] = addr.get("line2") if addr else ""
    o["addr_city"] = addr.get("city") if addr else ""
    o["addr_state"] = addr.get("state") if addr else ""
    o["addr_pincode"] = addr.get("pincode") if addr else ""
    o["addr_lat"] = addr.get("latitude") if addr else None
    o["addr_lng"] = addr.get("longitude") if addr else None

    o["total_amount"] = float(o.get("total_amount") or 0)
    o["delivery_fee"] = float(o.get("delivery_fee") or 0)
    o["tip_amount"] = float(o.get("tip_amount") or 0)
    o["total_payable"] = (
        float(o.get("total_amount") or 0)
        + float(o.get("delivery_fee") or 0)
        + float(o.get("tip_amount") or 0)
    )

    return o


def calculate_delivery_fee_by_distance(km):
    """
    Assam-wide delivery:
    - No distance blocking.
    - If distance is unavailable, charge base fee.
    - If distance is available, add slab surcharge.
    """
    if km is None:
        return float(BASE_DELIVERY_FEE_INR)

    try:
        km = float(km)
    except Exception:
        return float(BASE_DELIVERY_FEE_INR)

    surcharge = 0

    for low, high, fee in DELIVERY_SURCHARGE_SLABS:
        if km >= low and km < high:
            surcharge = fee
            break

    return float(BASE_DELIVERY_FEE_INR + surcharge)

def _ensure_contact_messages_status_column():
    # MongoDB does not need table/column migration.
    return

# ======================
# ASSAM-WIDE DELIVERY
# ======================

def _seed_pincodes_if_empty():
    # Kept as a safe no-op because app startup still calls it.
    # Old fixed pincode seeding is disabled.
    return


def _clean_pin(pin) -> str:
    """Keep digits only and trim spaces."""
    if pin is None:
        return ""
    s = str(pin).strip()
    return "".join(ch for ch in s if ch.isdigit())


def _clean_state(state) -> str:
    return (state or "").strip().lower()


def is_assam_state(state) -> bool:
    return _clean_state(state) in {
        "assam",
        "as"
    }



def _norm_status(value):
    return (str(value).strip().upper() if value is not None else "")


def _norm_role(value):
    return (str(value).strip().lower() if value is not None else "")



def get_serviceable_pincodes():
    # Fixed pincode list removed.
    # Delivery is now controlled by Assam state check.
    return []


def is_serviceable_pincode(pin: str) -> bool:
    # Old fixed pincode matching removed.
    # Keep only basic Indian pincode validation.
    clean_pin = _clean_pin(pin)
    return bool(clean_pin and len(clean_pin) == 6 and clean_pin.isdigit())



@app.route("/api/service/pincodes")
def api_service_pincodes():
    return jsonify({
        "ok": True,
        "mode": "ASSAM_STATE_WIDE",
        "message": "Delivery is available across Assam.",
        "pincodes": []
    })

# Store location data in session; front-end JS should call this after getting geolocation & pincode
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

# Backend Update for app.py
# Add this endpoint after the /api/service/pincodes endpoint (around line 197)

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


# /detect-location?lat=..&lng=..&pincode=..&address=..
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

# ----------------------
# AUTH HELPERS
# ----------------------
def current_user():
    uid = session.get("user_id")
    if not uid:
        return None

    try:
        user = mongo.users.find_one({"_id": ObjectId(uid)})
    except Exception:
        return None

    if user:
        user["id"] = str(user["_id"])

    return user

def login_required(role=None):
    def deco(fn):
        @wraps(fn)
        def wrap(*a, **kw):
            u = current_user()
            if not u:
                flash("Please log in first.", "warning")
                return redirect(url_for("login"))
            if role and u["role"] != role:
                flash("Access denied.", "danger")
                return redirect(url_for("index"))
            return fn(*a, **kw)
        return wrap
    return deco

# Helper: JWT-like session token (simplified)
def generate_session_token(user_id):
    """Generate a simple session token"""
    import hashlib
    from datetime import datetime
    raw = f"{user_id}:{datetime.utcnow().isoformat()}:{app.secret_key}"
    return hashlib.sha256(raw.encode()).hexdigest()

def verify_api_token():
    """Verify API token from Authorization header using MongoDB."""
    auth_header = request.headers.get('Authorization', '')

    if not auth_header.startswith('Bearer '):
        return None

    token = auth_header.replace('Bearer ', '').strip()

    if not token:
        return None

    session_doc = mongo.api_sessions.find_one({
        "token": token,
        "expires_at": {"$gt": datetime.utcnow().isoformat()}
    })

    if not session_doc:
        return None

    return str(session_doc.get("user_id"))

from functools import wraps
from flask import jsonify

def api_login_required(_func=None):
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            # 1) normal web session (cookie login)
            u = current_user()
            if u:
                return f(user_id=u["id"], *args, **kwargs)

            # 2) token auth (mobile app)
            user_id = verify_api_token()
            if not user_id:
                return jsonify({"ok": False, "msg": "Unauthorized"}), 401

            return f(user_id=user_id, *args, **kwargs)
        return wrapped

    # supports @api_login_required and @api_login_required()
    if _func is None:
        return decorator
    return decorator(_func)



# Create API sessions table
def _ensure_api_sessions_table():
    # MongoDB collection is created automatically on first insert.
    return

with app.app_context():
    ensure_mongo_indexes()
    _seed_pincodes_if_empty()


# ----------------------
# MISC UTILS
# ----------------------
app.config["UPLOAD_FOLDER"] = os.path.join(os.path.dirname(__file__), "uploads")
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024
os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

ALLOWED_EXTS = {"jpg","jpeg","png","webp"}
def allowed_file(filename): 
    return "." in filename and filename.rsplit(".",1)[1].lower() in ALLOWED_EXTS

def normalize_phone(phone):
    phone = (phone or "").strip()
    if not phone:
        return ""

    # Keep leading + if present, remove spaces/dashes/brackets
    has_plus = phone.startswith("+")
    digits = "".join(ch for ch in phone if ch.isdigit())

    if not digits:
        return ""

    if has_plus:
        return "+" + digits

    # Default India format for 10-digit numbers
    if len(digits) == 10:
        return "+91" + digits

    return digits

def _row_get(row, key, default=0):
    try:
        v = row[key]
        return default if v is None else v
    except Exception:
        return default

def order_total_payable(order_row):
    return float(_row_get(order_row, 'total_amount', 0)) + \
           float(_row_get(order_row, 'delivery_fee', 0)) + \
           float(_row_get(order_row, 'tip_amount', 0))

def ensure_admin_seed_password():
    admin = mongo.users.find_one({"email": "admin@chhimphei.local"})

    if not admin:
        mongo.users.insert_one({
            "name": "Administrator",
            "email": "admin@chhimphei.local",
            "phone": "+911234567890",
            "password_hash": generate_password_hash("admin123"),
            "role": "admin",
            "phone_verified": 1,
            "is_active": 1,
            "created_at": datetime.utcnow().isoformat()
        })
        return

    if admin.get("password_hash") == "!!set_in_app!!":
        mongo.users.update_one(
            {"_id": admin["_id"]},
            {"$set": {"password_hash": generate_password_hash("admin123")}}
        )

def send_sms(phone: str, message: str) -> bool:
    print(f"[DEV SMS] to={phone} :: {message}")
    return True

with app.app_context():
    ensure_admin_seed_password()

# =========================================================
# CUSTOMER CANCEL ORDER
# =========================================================
CANCELLABLE_STATUSES = {"PLACED", "CONFIRMED","PREPARING"}
def is_cancellable(status: str) -> bool:
    return status and status.upper() in CANCELLABLE_STATUSES

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

# ----------------------
# PUBLIC PAGES (with pincode gating)
# ----------------------
def _session_pin_is_serviceable():
    sa = session.get("service_area")
    pin = (sa.get("pincode").strip() if sa and sa.get("pincode") else "")
    return is_serviceable_pincode(pin), pin


def _parse_home_dt(value):
    try:
        if isinstance(value, datetime):
            return value

        if not value:
            return None

        value = str(value).replace("Z", "").strip()
        return datetime.fromisoformat(value)
    except Exception:
        return None


def _home_product_sales_count(product_id):
    try:
        product_obj_id = product_id if isinstance(product_id, ObjectId) else ObjectId(str(product_id))
    except Exception:
        product_obj_id = product_id

    return mongo.order_items.count_documents({
        "$or": [
            {"product_id": product_obj_id},
            {"product_id": str(product_obj_id)}
        ]
    })


def _home_product_rating_summary(product_id):
    ratings = list(mongo.product_ratings.find({
        "$or": [
            {"product_id": product_id},
            {"product_id": str(product_id)}
        ]
    }))

    rating_count = len(ratings)
    total_rating = 0

    for r in ratings:
        try:
            total_rating += float(r.get("rating") or 0)
        except (TypeError, ValueError):
            pass

    avg_rating = round(total_rating / rating_count, 1) if rating_count else 0

    return avg_rating, rating_count


def _home_store_rating_summary(store_id):
    ratings = list(mongo.store_ratings.find({
        "$or": [
            {"store_id": store_id},
            {"store_id": str(store_id)}
        ]
    }))

    rating_count = len(ratings)
    total_rating = 0

    for r in ratings:
        try:
            total_rating += float(r.get("rating") or 0)
        except (TypeError, ValueError):
            pass

    avg_rating = round(total_rating / rating_count, 1) if rating_count else 0

    return avg_rating, rating_count


def _hydrate_home_product(p):
    p["id"] = str(p["_id"])
    p["category"] = (p.get("category") or "Uncategorized").strip()
    p["sub_category"] = (p.get("sub_category") or "").strip()
    p["name"] = (p.get("name") or "Product").strip()
    p["image_path"] = p.get("image_path", "")
    p["price_per_kg"] = float(p.get("price_per_kg") or 0)
    p["original_price_per_kg"] = float(
        p.get("original_price_per_kg")
        or p.get("mrp_per_kg")
        or p.get("old_price")
        or p.get("price_per_kg")
        or 0
    )
    p["discount_enabled"] = bool(p.get("discount_enabled"))
    p["discount_percent"] = float(p.get("discount_percent") or 0)
    p["discount_amount_per_kg"] = float(p.get("discount_amount_per_kg") or 0)
    p["stock_kg"] = float(p.get("stock_kg") or 0)

    avg_rating, rating_count = _home_product_rating_summary(p["_id"])
    p["avg_rating"] = avg_rating
    p["rating_count"] = rating_count
    p["sales_count"] = _home_product_sales_count(p["_id"])

    created_dt = _parse_home_dt(p.get("created_at"))
    p["created_dt"] = created_dt
    p["is_new_arrival"] = bool(created_dt and created_dt >= (datetime.utcnow() - timedelta(days=7)))

    store = None
    if p.get("store_id"):
        store = mongo.stores.find_one({"_id": p["store_id"]})

    p["store_name"] = store.get("store_name") if store else p.get("store_name", "")
    p["store_id"] = str(p.get("store_id")) if p.get("store_id") else ""

    return p


@app.route('/')
def index():
    user = current_user()
    allow, pin = _session_pin_is_serviceable()

    products = []
    latest_products = []
    new_products = []
    popular_products = []
    discount_products = []
    featured_products = []
    best_reviewed_products = []
    stores = []
    recommended_stores = []
    new_stores = []
    categories = []
    product_rating_map = {}
    store_rating_map = {}

    if session.get("service_area") and not allow:
        flash("Please enter a valid 6-digit pincode.", "warning")
    else:
        products = list(mongo.products.find({
            "is_active": 1
        }).sort("created_at", -1).limit(80))

        for p in products:
            _hydrate_home_product(p)

            product_rating_map[p["id"]] = {
                "avg": p.get("avg_rating", 0),
                "count": p.get("rating_count", 0)
            }

        # Latest fallback
        latest_products = products[:10]

        # New arrivals = products added within last 7 days
        new_products = [
            p for p in products
            if p.get("is_new_arrival")
        ]

        new_products = sorted(
            new_products,
            key=lambda x: _parse_home_dt(x.get("created_at")) or datetime.min,
            reverse=True
        )[:10]

        if not new_products:
            new_products = latest_products[:10]

        # Popular products = frequent sales/order_items first
        popular_products = sorted(
            products,
            key=lambda x: (
                int(x.get("sales_count") or 0),
                float(x.get("avg_rating") or 0),
                int(x.get("rating_count") or 0)
            ),
            reverse=True
        )[:10]

        if not popular_products:
            popular_products = latest_products[:10]

        # Discount products = real discount fields
        discount_products = [
            p for p in products
            if bool(p.get("discount_enabled"))
            and float(p.get("discount_amount_per_kg") or 0) > 0
        ]

        discount_products = sorted(
            discount_products,
            key=lambda x: (
                float(x.get("discount_percent") or 0),
                float(x.get("discount_amount_per_kg") or 0)
            ),
            reverse=True
        )[:10]

        # Best reviewed products
        best_reviewed_products = sorted(
            products,
            key=lambda x: (
                float(x.get("avg_rating") or 0),
                int(x.get("rating_count") or 0),
                int(x.get("sales_count") or 0)
            ),
            reverse=True
        )[:10]

        featured_products = popular_products[:10] if popular_products else latest_products[:10]

                # Real-time categories from store_categories collection
        category_map = {}

        store_categories = list(
            mongo.store_categories.find({
                "$or": [
                    {"is_active": 1},
                    {"is_active": True},
                    {"is_active": {"$exists": False}}
                ]
            }).sort("name", 1)
        )

        for cat in store_categories:
            cat_name = (cat.get("name") or "").strip()

            if not cat_name:
                continue

            cat_key = cat_name.lower()

            category_image_path = (
                cat.get("category_image_path")
                or cat.get("image_path")
                or cat.get("icon_path")
                or ""
            )

            if cat_key not in category_map:
                category_map[cat_key] = {
                    "id": str(cat.get("_id")),
                    "name": cat_name,
                    "count": 0,
                    "emoji": cat.get("emoji") or cat.get("icon") or "🛒",
                    "image_path": category_image_path,
                    "category_image_path": category_image_path,
                    "store_id": str(cat.get("store_id")) if cat.get("store_id") else "",
                    "sub_categories": cat.get("sub_categories") or []
                }
            else:
                if category_image_path and not category_map[cat_key].get("category_image_path"):
                    category_map[cat_key]["image_path"] = category_image_path
                    category_map[cat_key]["category_image_path"] = category_image_path

        # Count active products under each real-time category
        for p in products:
            cat_name = (p.get("category") or "Uncategorized").strip() or "Uncategorized"
            cat_key = cat_name.lower()

            if cat_key not in category_map:
                category_map[cat_key] = {
                    "id": "",
                    "name": cat_name,
                    "count": 0,
                    "emoji": "🛒",
                    "image_path": "",
                    "category_image_path": "",
                    "store_id": "",
                    "sub_categories": []
                }

            category_map[cat_key]["count"] += 1

        categories = sorted(
            list(category_map.values()),
            key=lambda x: x["name"].lower()
        )

        stores = list(mongo.stores.find({
            "is_active": 1
        }).sort("created_at", -1).limit(30))

        for s in stores:
            s["id"] = str(s["_id"])
            s["store_name"] = s.get("store_name", "Store")
            s["address"] = s.get("address", "")
            s["logo_path"] = s.get("logo_path", "")
            s["banner_path"] = s.get("banner_path", "")
            s["profile_intro"] = (
                s.get("profile_intro")
                or s.get("description")
                or "Fresh groceries and daily essentials from this store."
            ).strip()
            s["description"] = (s.get("description") or "").strip()
            s["is_open"] = int(s.get("is_open", 1))
            s["created_at"] = s.get("created_at", "")

            s["product_count"] = mongo.products.count_documents({
                "store_id": s["_id"],
                "is_active": 1
            })

            store_avg_rating, store_rating_count = _home_store_rating_summary(s["_id"])

            s["avg_rating"] = store_avg_rating
            s["rating_count"] = store_rating_count

            store_rating_map[s["id"]] = {
                "avg": store_avg_rating,
                "count": store_rating_count
            }

        recommended_stores = sorted(
            stores,
            key=lambda x: (
                float(x.get("avg_rating") or 0),
                int(x.get("rating_count") or 0),
                int(x.get("product_count") or 0)
            ),
            reverse=True
        )[:10]

        new_stores = stores[:10]

    return render_template(
        'index.html',
        user=user,
        products=products,
        latest_products=latest_products,
        new_products=new_products,
        popular_products=popular_products,
        discount_products=discount_products,
        featured_products=featured_products,
        best_reviewed_products=best_reviewed_products,
        categories=categories,
        stores=stores,
        recommended_stores=recommended_stores,
        new_stores=new_stores,
        product_rating_map=product_rating_map,
        store_rating_map=store_rating_map
    )

# ----------------------
# LEGAL & HELP PAGES
# ----------------------
@app.route('/legal/privacy')
def legal_privacy():
    return render_template('legal/privacy.html', user=current_user())

@app.route('/legal/security')
def legal_security():
    return render_template('legal/security.html', user=current_user())

@app.route('/legal/terms')
def legal_terms():
    return render_template('legal/terms.html', user=current_user())

@app.route('/help')
def legal_help():
    return render_template('legal/help.html', user=current_user())

@app.route('/report-fraud')
def legal_report_fraud():
    return render_template('legal/report_fraud.html', user=current_user())

# ----------------------
# AUTH
# ----------------------
@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email','').lower().strip()
        password = request.form.get('password','')

        u = mongo.users.find_one({"email": email})

        if not u:
            flash('Invalid credentials.', 'danger')
            return redirect(url_for('login'))

        if not u.get('is_active') and u.get('role') != 'customer':
            flash('Your account awaits admin approval.', 'warning')
            return redirect(url_for('login'))

        if check_password_hash(u.get('password_hash', ''), password):
            session['user_id'] = str(u['_id'])
            flash('Welcome back!', 'success')

            if u.get('role') == 'admin':
                return redirect(url_for('admin_dashboard'))
            elif u.get('role') == 'store':
                return redirect(url_for('store_dashboard'))
            elif u.get('role') == 'delivery':
                return redirect(url_for('delivery_dashboard'))
            else:
                return redirect(url_for('index'))

        flash('Invalid credentials.', 'danger')

    return render_template('login.html')

# ---------- Forgot Password ----------
@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        identifier = request.form.get('identifier', '').strip().lower()

        u = mongo.users.find_one({
            "$or": [
                {"email": identifier},
                {"phone": identifier}
            ]
        })

        if u:
            token = secrets.token_urlsafe(32)
            now = datetime.utcnow().isoformat()
            expires_at = (datetime.utcnow() + timedelta(minutes=30)).isoformat()

            mongo.password_reset_tokens.insert_one({
                "user_id": str(u["_id"]),
                "token": token,
                "expires_at": expires_at,
                "consumed": 0,
                "created_at": now
            })

            reset_link = url_for('reset_password', token=token, _external=True)
            print(f"[DEV RESET LINK] Send this to the user: {reset_link}")

            if u.get('phone'):
                try:
                    send_sms(u['phone'], f"Reset your password: {reset_link}")
                except Exception:
                    pass

        flash("If the account exists, a reset link has been sent.", "info")
        return redirect(url_for('login'))

    return render_template('forgot_password.html')


@app.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    row = mongo.password_reset_tokens.find_one({
        "token": token,
        "consumed": 0
    })

    if not row:
        flash("Invalid or expired reset link.", "danger")
        return redirect(url_for('forgot_password'))

    try:
        if datetime.fromisoformat(row.get("expires_at")) < datetime.utcnow():
            flash("Invalid or expired reset link.", "danger")
            return redirect(url_for('forgot_password'))
    except Exception:
        flash("Invalid or expired reset link.", "danger")
        return redirect(url_for('forgot_password'))

    if request.method == 'POST':
        new_pw = request.form.get('password', '')
        confirm = request.form.get('confirm', '')

        if not new_pw or len(new_pw) < 6:
            flash("Password must be at least 6 characters.", "warning")
            return redirect(url_for('reset_password', token=token))

        if new_pw != confirm:
            flash("Passwords do not match.", "warning")
            return redirect(url_for('reset_password', token=token))

        pwd_hash = generate_password_hash(new_pw)

        try:
            user_obj_id = ObjectId(row.get("user_id"))
        except Exception:
            flash("Invalid or expired reset link.", "danger")
            return redirect(url_for('forgot_password'))

        mongo.users.update_one(
            {"_id": user_obj_id},
            {"$set": {"password_hash": pwd_hash}}
        )

        mongo.password_reset_tokens.update_one(
            {"_id": row["_id"]},
            {"$set": {"consumed": 1, "consumed_at": datetime.utcnow().isoformat()}}
        )

        flash("Your password has been reset. Please log in.", "success")
        return redirect(url_for('login'))

    return render_template('reset_password.html')

# ---------- Register + OTP ----------
@app.route('/register', methods=['GET','POST'])
def register():
    if request.method == 'POST':
        name = (request.form.get('name','') or '').strip()
        email = (request.form.get('email','') or '').lower().strip()
        phone = (request.form.get('phone','') or '').strip()
        password = request.form.get('password','') or ''

        if not name or not email or not password:
            flash('Please fill all required fields.', 'warning')
            return redirect(url_for('register'))

        if len(password) < 6:
            flash('Password must be at least 6 characters.', 'warning')
            return redirect(url_for('register'))

        if phone:
            phone = normalize_phone(phone)

        try:
            result = mongo.users.insert_one({
                "name": name,
                "email": email,
                "phone": phone,
                "password_hash": generate_password_hash(password),
                "role": "customer",
                "phone_verified": 1,
                "is_active": 1,
                "created_at": datetime.utcnow().isoformat()
            })
        except DuplicateKeyError:
            flash('Email or phone already registered.', 'danger')
            return redirect(url_for('register'))

        session['user_id'] = str(result.inserted_id)
        flash('Account created! You are logged in.', 'success')
        return redirect(url_for('index'))

    return render_template('register.html')







@app.route('/logout')
def logout():
    session.clear()
    flash('Logged out.','info')
    return redirect(url_for('index'))

# ----------------------
# CUSTOMER PROFILE + ADDRESSES
# ----------------------

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

    return render_template("profile.html", user=u, addresses=addrs)


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
    return redirect(url_for("profile"))


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



# =========================================================
# CUSTOMER COMPLAINTS
# =========================================================
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

        if not store_id:
            flash("Please select the store related to this complaint.", "warning")
            return redirect(url_for("customer_complaints"))

        try:
            store_obj_id = ObjectId(store_id)
        except Exception:
            flash("Invalid store selected.", "danger")
            return redirect(url_for("customer_complaints"))

        store = mongo.stores.find_one({"_id": store_obj_id})

        if not store:
            flash("Selected store was not found.", "danger")
            return redirect(url_for("customer_complaints"))

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
            "store_id_str": str(store_obj_id),
            "store_name": store.get("store_name", ""),

            "assigned_to": "store",
            "target_type": "store",

            "status": "open",
            "progress_status": "received",
            "priority": "normal",

            "admin_reply": "",
            "store_reply": "",
            "store_progress_note": "",

            "created_at": now,
            "updated_at": now,
            "is_active": 1
        })

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


# ----------------------
# CATALOG + CART
# ----------------------
@app.route('/products')
def products():
    allow, pin = _session_pin_is_serviceable()

    if session.get("service_area") and not allow:
        flash("Please enter a valid 6-digit pincode.", "warning")
        products = []
    else:
        products = list(mongo.products.find({
        "is_active": 1
        }).sort("created_at", -1))

    for p in products:
        p["id"] = str(p["_id"])

        # Prevent Jinja sort/groupby crash when MongoDB has null category fields
        p["category"] = (p.get("category") or "Uncategorized").strip()
        p["sub_category"] = (p.get("sub_category") or "").strip()

        ratings = list(mongo.product_ratings.find({
            "product_id": p["_id"]
        }))

        rating_count = len(ratings)
        total_rating = 0

        for r in ratings:
            try:
                total_rating += float(r.get("rating") or 0)
            except (TypeError, ValueError):
                pass

        if rating_count > 0:
            avg_rating = round(total_rating / rating_count, 1)
        else:
            avg_rating = 0

        p["avg_rating"] = avg_rating
        p["rating_count"] = rating_count

        store = None
        if p.get("store_id"):
            store = mongo.stores.find_one({"_id": p["store_id"]})

        p["store_name"] = store.get("store_name") if store else ""
        p["store_id"] = str(p.get("store_id")) if p.get("store_id") else ""

        if store:
            p["store_address"] = store.get("address", "")
            p["store_logo_path"] = store.get("logo_path", "")
            p["store_banner_path"] = store.get("banner_path", "")
            p["store_profile_intro"] = (
                store.get("profile_intro")
                or store.get("description")
                or "Fresh groceries and daily essentials from this store."
            ).strip()
        else:
            p["store_address"] = ""
            p["store_logo_path"] = ""
            p["store_banner_path"] = ""
            p["store_profile_intro"] = "Fresh groceries and daily essentials from this store."

        store_rating_avg = 0
        store_rating_count = 0

        if store:
            store_rating_query = {
                "$or": [
                    {"store_id": store["_id"]},
                    {"store_id": str(store["_id"])}
                ]
            }

            store_ratings = list(mongo.store_ratings.find(store_rating_query))
            store_rating_count = len(store_ratings)
            store_rating_total = 0

            for sr in store_ratings:
                try:
                    store_rating_total += float(sr.get("rating") or 0)
                except (TypeError, ValueError):
                    pass

            if store_rating_count > 0:
                store_rating_avg = round(store_rating_total / store_rating_count, 2)

        p["store_avg_rating"] = store_rating_avg
        p["store_rating_count"] = store_rating_count

    return render_template(
        'products.html',
        products=products,
        user=current_user()
    )


def get_or_create_cart(uid):
    existing = mongo.carts.find_one({"user_id": str(uid)})

    if existing:
        return existing["_id"]

    result = mongo.carts.insert_one({
        "user_id": str(uid),
        "created_at": datetime.utcnow().isoformat(),
        "updated_at": datetime.utcnow().isoformat()
    })

    return result.inserted_id


@app.route('/api/ratings/product/<pid>')
def api_product_rating(pid):
    try:
        pid_obj = ObjectId(pid)
    except Exception:
        return jsonify({
            "ok": False,
            "avg": 0,
            "count": 0
        }), 400

    ratings = list(mongo.product_ratings.find({
        "product_id": pid_obj
    }))

    count = len(ratings)

    if count > 0:
        avg = round(
            sum(float(r.get("rating") or 0) for r in ratings) / count,
            1
        )
    else:
        avg = 0

    return jsonify({
        "ok": True,
        "avg": avg,
        "count": count
    })

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

        item = {
            "cart_item_id": str(ci["_id"]),
            "weight_kg": float(ci.get("weight_kg") or 0),
            "product_id": str(product["_id"]),
            "name": product.get("name", ""),
            "price_per_kg": float(product.get("price_per_kg") or 0),
            "image_path": product.get("image_path", ""),
            "stock_kg": float(product.get("stock_kg") or 0),
            "is_active": int(product.get("is_active") or 0),
            "store_id": str(product.get("store_id")) if product.get("store_id") else "",
            "store_name": store.get("store_name") if store else "",
        }

        items.append(item)

    total = sum([
        float(row["weight_kg"] or 0) * float(row["price_per_kg"] or 0)
        for row in items
    ])

    return render_template('cart.html', items=items, total=total, user=u)


# ==================== CART API (WEB + APP TOKEN) ====================

def _get_api_or_web_user():
    try:
        uid = verify_api_token()
        if uid:
            return {"id": str(uid)}
    except Exception:
        pass

    try:
        u = current_user()
        if u and u.get("id"):
            return {"id": str(u["id"])}
    except Exception:
        pass

    return None


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

    try:
        weight_kg = float(data.get("weight_kg") or request.form.get('weight_kg', '1') or 1)
    except (TypeError, ValueError):
        return jsonify({'ok': False, 'msg': 'Invalid weight'}), 400

    if weight_kg < 0.25:
        return jsonify({'ok': False, 'msg': 'Minimum 0.25 kg'}), 400

    weight_kg = round(round(weight_kg * 4) / 4, 2)

    product = mongo.products.find_one({"_id": product_obj_id})

    if not product:
        return jsonify({'ok': False, 'msg': 'Product not found'}), 404

    stock = float(product.get("stock_kg") or 0)
    active = int(product.get("is_active") or 0)
    new_store_id = product.get("store_id")

    if active != 1 or stock <= 0:
        return jsonify({'ok': False, 'msg': 'This item is sold out'}), 409

    if weight_kg > stock:
        return jsonify({
            'ok': False,
            'msg': f'Only {stock:.2f} kg stock is available. Please enter a quantity equal to or below available stock.'
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

    if existing_cart_item:
        mongo.cart_items.update_one(
            {"_id": existing_cart_item["_id"]},
            {
                "$set": {
                    "weight_kg": weight_kg,
                    "updated_at": now
                }
            }
        )
    else:
        mongo.cart_items.insert_one({
            "cart_id": cid,
            "product_id": product_obj_id,
            "weight_kg": weight_kg,
            "created_at": now,
            "updated_at": now
        })

    cart_count = mongo.cart_items.count_documents({"cart_id": cid})

    return jsonify({
        'ok': True,
        'msg': 'Added to cart',
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




# ----------------------
# CHECKOUT + ORDERS
# ----------------------
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


# Orders list
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

# ---------- Order tracking ----------
def get_order_full(oid, for_user_id=None):
    try:
        oid_obj = ObjectId(oid)
    except Exception:
        return None

    query_filter = {"_id": oid_obj}

    if for_user_id is not None:
        query_filter["user_id"] = str(for_user_id)

    order = mongo.orders.find_one(query_filter)

    if not order:
        return None

    order["id"] = str(order["_id"])

    items = list(mongo.order_items.find({"order_id": oid_obj}))

    for item in items:
        item["id"] = str(item["_id"])
        item["product_id"] = str(item.get("product_id"))
        item["name"] = item.get("product_name", "")
        item["price_per_kg"] = item.get("unit_price_per_kg", 0)

    addr = mongo.order_addresses.find_one({"order_id": oid_obj})

    if addr:
        addr["id"] = str(addr["_id"])

    events = list(mongo.order_events.find({"order_id": oid_obj}).sort("created_at", 1))

    for e in events:
        e["id"] = str(e["_id"])

    return {
        "order": order,
        "items": items,
        "address": addr,
        "events": events,
    }

@app.route('/about')
def about():
    """
    About Us page for Chhimphei Women Poultry Producer Company Limited
    """
    company_info = {
        "name": "Chhimphei Women Poultry Producer Company Limited",
        "year": 2018,
        "location": "Melriat, Aizawl, Mizoram",
        "fssai": "21825102002418",
        "phone": "8132831406",
        "website": "chhimphei.com",
        "supported_by": "Mizoram State Rural Livelihood Mission (MzSRLM)",
    }

    u = current_user()
    cart_count = 0

    if u:
        cid = get_or_create_cart(u["id"])
        cart_count = mongo.cart_items.count_documents({"cart_id": cid})

    return render_template(
        "about.html",
        info=company_info,
        user=u,
        cart_count=cart_count
    )


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

# ---------- Feedback ----------
def _clamp_rating(val):
    try:
        v = int(val)
    except (TypeError, ValueError):
        return None
    if v < 1 or v > 5:
        return None
    return v

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

# ----------------------
# DELIVERY
# ----------------------
@app.route('/delivery')
@login_required(role='delivery')
def delivery_dashboard():
    u = current_user()

    availability = _get_delivery_availability(u["id"])
    delivery_active = bool(availability.get("active"))
    active_since = availability.get("active_since")

    orders = []

    # Driver OFF = show no order data.
    if delivery_active and active_since:
        raw_orders = list(
            mongo.orders.find({
                "$or": [
                    {
                        "delivery_partner_id": u["id"],
                        "status": {"$in": DELIVERY_ASSIGNED_ACTIVE_STATUSES}
                    },
                    {
                        "$and": [
                            {
                                "$or": [
                                    {"delivery_partner_id": None},
                                    {"delivery_partner_id": {"$exists": False}}
                                ]
                            },
                            {"created_at": {"$gte": active_since}},
                            {"status": {"$in": DELIVERY_ACTIONABLE_STATUSES}}
                        ]
                    }
                ]
            }).sort("created_at", -1)
        )

        for o in raw_orders:
            o = _hydrate_delivery_order(o)
            distance_km = _driver_distance_to_store_km(o, availability)
            o["driver_store_distance_km"] = distance_km
            orders.append(o)

        # Nearby first, unknown distance last.
        orders.sort(
            key=lambda x: (
                0 if x.get("delivery_partner_id") == u["id"] else 1,
                999999 if x.get("driver_store_distance_km") is None else x.get("driver_store_distance_km")
            )
        )

    return render_template(
        'delivery_dashboard.html',
        user=u,
        orders=orders,
        delivery_active=delivery_active,
        delivery_availability=availability,
        delivery_accept_radius_km=DELIVERY_ACCEPT_RADIUS_KM
    )


@app.route('/api/delivery/availability', methods=['POST'])
@login_required(role='delivery')
def api_delivery_availability():
    u = current_user()
    data = request.get_json(silent=True) or {}

    active = bool(data.get("active"))
    now = _delivery_now()

    if active:
        lat = _get_float_or_none(data.get("latitude"))
        lng = _get_float_or_none(data.get("longitude"))

        if lat is None or lng is None:
            return jsonify({
                "ok": False,
                "error": "GPS location is required to go active."
            }), 400

        mongo.delivery_availability.update_one(
            {"user_id": u["id"]},
            {
                "$set": {
                    "user_id": u["id"],
                    "active": True,
                    "active_since": now,
                    "latitude": lat,
                    "longitude": lng,
                    "updated_at": now
                }
            },
            upsert=True
        )

        return jsonify({
            "ok": True,
            "active": True,
            "active_since": now
        })

    mongo.delivery_availability.update_one(
        {"user_id": u["id"]},
        {
            "$set": {
                "user_id": u["id"],
                "active": False,
                "offline_at": now,
                "updated_at": now
            }
        },
        upsert=True
    )

    return jsonify({
        "ok": True,
        "active": False
    })

@app.route('/delivery/order/<oid>/assign', methods=['POST'])
@login_required(role='delivery')
def delivery_assign(oid):
    u = current_user()

    availability = _get_delivery_availability(u["id"])

    if not availability.get("active"):
        flash("Please go active before accepting delivery orders.", "warning")
        return redirect(url_for("delivery_dashboard"))

    try:
        oid_obj = ObjectId(oid)
    except Exception:
        flash("Invalid order.", "danger")
        return redirect(url_for("delivery_dashboard"))

    order = mongo.orders.find_one({"_id": oid_obj})

    if not order:
        flash("Order not found.", "danger")
        return redirect(url_for("delivery_dashboard"))

    if order.get("status") not in DELIVERY_ACTIONABLE_STATUSES:
        flash("This order is no longer available for delivery.", "warning")
        return redirect(url_for("delivery_dashboard"))

    existing_partner = order.get("delivery_partner_id")

    if existing_partner:
        if existing_partner == u["id"]:
            flash("This order is already assigned to you.", "info")
        else:
            flash("This order is already assigned to another delivery partner.", "warning")
        return redirect(url_for("delivery_dashboard"))

    distance_km = _driver_distance_to_store_km(order, availability)

    if distance_km is not None and distance_km > DELIVERY_ACCEPT_RADIUS_KM:
        flash(
            f"This order is too far from your current location ({distance_km:.1f} km).",
            "warning"
        )
        return redirect(url_for("delivery_dashboard"))

    now = _delivery_now()

    # Atomic acceptance:
    # Only one delivery partner can win this update.
    result = mongo.orders.update_one(
        {
            "_id": oid_obj,
            "$or": [
                {"delivery_partner_id": None},
                {"delivery_partner_id": {"$exists": False}}
            ],
            "status": {"$in": DELIVERY_ACTIONABLE_STATUSES}
        },
        {
            "$set": {
                "delivery_partner_id": u["id"],
                "status": "ASSIGNED_TO_DELIVERY",
                "assigned_at": now,
                "assignment_distance_km": distance_km,
                "updated_at": now
            }
        }
    )

    if result.modified_count != 1:
        flash("This order was just accepted by another delivery partner or is no longer available.", "warning")
        return redirect(url_for("delivery_dashboard"))

    mongo.order_events.insert_one({
        "order_id": oid_obj,
        "status": "ASSIGNED_TO_DELIVERY",
        "note": "Assigned to delivery partner",
        "created_at": now
    })

    flash("Order assigned to you.", "success")
    return redirect(url_for("delivery_dashboard"))


@app.route('/delivery/order/<oid>/status', methods=['POST'])
@login_required(role='delivery')
def delivery_status(oid):
    u = current_user()

    try:
        oid_obj = ObjectId(oid)
    except Exception:
        flash("Invalid order.", "danger")
        return redirect(url_for("delivery_dashboard"))

    order = mongo.orders.find_one({
        "_id": oid_obj,
        "delivery_partner_id": u["id"]
    })

    if not order:
        flash("Order not found or not assigned to you.", "danger")
        return redirect(url_for("delivery_dashboard"))

    new_status = request.form.get('status', 'OUT_FOR_DELIVERY').upper()
    now = datetime.utcnow().isoformat()

    if new_status == 'DELIVERED':
        cod_received = request.form.get('cod_received')

        if cod_received != '1':
            flash('Please confirm that payment (COD) has been received before marking Delivered.', 'warning')
            return redirect(url_for('delivery_dashboard'))

        mongo.orders.update_one(
            {"_id": oid_obj},
            {
                "$set": {
                    "status": "DELIVERED",
                    "payment_status": "PAID",
                    "updated_at": now,
                    "delivered_at": now
                }
            }
        )

        mongo.transactions.update_many(
            {"order_id": oid_obj},
            {
                "$set": {
                    "status": "PAID",
                    "updated_at": now
                }
            }
        )

        mongo.order_events.insert_one({
            "order_id": oid_obj,
            "status": "DELIVERED",
            "note": "COD received",
            "created_at": now
        })

        flash('Delivery completed and payment confirmed.', 'success')
        return redirect(url_for('delivery_dashboard'))

    mongo.orders.update_one(
        {"_id": oid_obj},
        {
            "$set": {
                "status": new_status,
                "updated_at": now
            }
        }
    )

    mongo.order_events.insert_one({
        "order_id": oid_obj,
        "status": new_status,
        "note": "",
        "created_at": now
    })

    flash('Delivery status updated.', 'success')
    return redirect(url_for('delivery_dashboard'))

# ----------------------
# DELIVERY API — Customer polls rider location
# ----------------------
@app.route('/delivery/api/location', methods=['POST'])
@login_required(role='delivery')
def delivery_update_location():
    u = current_user()
    data = request.get_json(silent=True) or {}

    lat_raw = data.get("latitude")
    lng_raw = data.get("longitude")

    # Accept frontend aliases also
    if lat_raw is None:
        lat_raw = data.get("lat")

    if lng_raw is None:
        lng_raw = data.get("lng")

    try:
        lat = float(lat_raw)
        lng = float(lng_raw)
    except (TypeError, ValueError):
        return jsonify({
            "ok": False,
            "error": "latitude/longitude required",
            "received": data
        }), 400

    oid = data.get("order_id")
    heading = data.get("heading")
    speed = data.get("speed")

    oid_obj = None

    if oid:
        try:
            oid_obj = ObjectId(str(oid))
        except Exception:
            # Do not fail live sharing only because frontend sent old/integer order id.
            # Save general delivery location without order_id.
            oid_obj = None

        if oid_obj:
            order = mongo.orders.find_one({
                "_id": oid_obj,
                "delivery_partner_id": u["id"]
            })

            if not order:
                return jsonify({
                    "ok": False,
                    "error": "order not found or not assigned to you"
                }), 404

    mongo.delivery_locations.insert_one({
        "delivery_partner_id": u["id"],
        "order_id": oid_obj,
        "latitude": lat,
        "longitude": lng,
        "heading": heading,
        "speed": speed,
        "recorded_at": datetime.utcnow().isoformat()
    })

    return jsonify({
        "ok": True,
        "latitude": lat,
        "longitude": lng,
        "order_id": str(oid_obj) if oid_obj else None
    })

# --- Product detail with ratings ---
@app.route('/product/<pid>')
def product_detail(pid):
    try:
        product_obj_id = ObjectId(pid)
    except Exception:
        flash("Product not found.", "warning")
        return redirect(url_for('products'))

    p = mongo.products.find_one({"_id": product_obj_id})

    if not p:
        flash("Product not found.", "warning")
        return redirect(url_for('products'))

    p["id"] = str(p["_id"])

    store = None
    if p.get("store_id"):
        store = mongo.stores.find_one({"_id": p["store_id"]})

    p["store_name"] = store.get("store_name") if store else ""
    p["store_id"] = str(p.get("store_id")) if p.get("store_id") else ""

    # Store profile fields for Products page → Stores tab
    if store:
        p["store_address"] = store.get("address", "")
        p["store_logo_path"] = store.get("logo_path", "")
        p["store_banner_path"] = store.get("banner_path", "")
        p["store_profile_intro"] = (
            store.get("profile_intro")
            or store.get("description")
            or "Fresh groceries and daily essentials from this store."
        ).strip()
    else:
        p["store_address"] = ""
        p["store_logo_path"] = ""
        p["store_banner_path"] = ""
        p["store_profile_intro"] = "Fresh groceries and daily essentials from this store."

    # Real store rating for Products page → Stores tab
    store_rating_avg = 0
    store_rating_count = 0

    if store:
        store_rating_query = {
            "$or": [
                {"store_id": store["_id"]},
                {"store_id": str(store["_id"])}
            ]
        }

        store_ratings = list(mongo.store_ratings.find(store_rating_query))
        store_rating_count = len(store_ratings)
        store_rating_total = 0

        for sr in store_ratings:
            try:
                store_rating_total += float(sr.get("rating") or 0)
            except (TypeError, ValueError):
                pass

        if store_rating_count > 0:
            store_rating_avg = round(store_rating_total / store_rating_count, 2)

    p["store_avg_rating"] = store_rating_avg
    p["store_rating_count"] = store_rating_count

    u = current_user()
    is_staff = bool(u and (u.get("role") in ("admin", "store")))

    if not is_staff and int(p.get("is_active") or 0) != 1:
        abort(404)

    ratings = list(mongo.product_ratings.find({
        "product_id": product_obj_id
    }).sort("created_at", -1))

    rating_count = len(ratings)

    if rating_count > 0:
        avg_rating = round(
            sum(float(r.get("rating") or 0) for r in ratings) / rating_count,
            1
        )
    else:
        avg_rating = 0

    rating_summary = {
        "avg": avg_rating,
        "count": rating_count
    }

    reviews = []

    for r in ratings:
        customer = None

        if r.get("user_id"):
            try:
                customer = mongo.users.find_one({"_id": ObjectId(r.get("user_id"))})
            except Exception:
                customer = None

        reviews.append({
            "rating": r.get("rating"),
            "comment": r.get("comment"),
            "created_at": r.get("created_at"),
            "customer_name": customer.get("name") if customer else "Customer"
        })

    selected_weight_kg = request.args.get("weight_kg", "1.00")

    try:
        selected_weight_kg = float(selected_weight_kg)
    except (TypeError, ValueError):
        selected_weight_kg = 1.00

    if selected_weight_kg < 0.25:
        selected_weight_kg = 0.25

    return render_template(
        'product.html',
        user=u,
        product=p,
        rating=rating_summary,
        reviews=reviews,
        selected_weight_kg=selected_weight_kg
    )

# ======================
# CUSTOMER PRODUCT REVIEW
# ======================
@app.route("/products/<pid>/review", methods=["POST"], endpoint="submit_product_review")
@login_required()
def submit_product_review(pid):
    u = current_user()

    if not u:
        flash("Please log in first.", "warning")
        return redirect(url_for("login"))

    if u.get("role") != "customer":
        flash("Only customer accounts can submit product reviews.", "warning")
        return redirect(url_for("product_detail", pid=pid))

    try:
        pid_obj = ObjectId(pid)
    except Exception:
        flash("Invalid product.", "danger")
        return redirect(url_for("products"))

    product = mongo.products.find_one({"_id": pid_obj})

    if not product:
        flash("Product not found.", "danger")
        return redirect(url_for("products"))

    try:
        rating = float(request.form.get("rating") or 0)
    except (TypeError, ValueError):
        rating = 0

    review = (request.form.get("review") or "").strip()

    if rating < 1 or rating > 5:
        flash("Please select a valid rating between 1 and 5.", "warning")
        return redirect(url_for("product_detail", pid=pid))

    if len(review) > 800:
        flash("Review is too long. Please keep it within 800 characters.", "warning")
        return redirect(url_for("product_detail", pid=pid))

    now = datetime.utcnow().isoformat()

    existing_review = mongo.product_ratings.find_one({
        "product_id": pid_obj,
        "user_id": str(u["_id"])
    })

    review_doc = {
        "product_id": pid_obj,
        "product_name": product.get("name", ""),
        "store_id": product.get("store_id"),
        "store_name": product.get("store_name", ""),
        "user_id": str(u["_id"]),
        "reviewer_name": u.get("name", "Customer"),
        "rating": rating,
        "review": review,
        "comment": review,
        "is_active": 1,
        "updated_at": now
    }

    if existing_review:
        mongo.product_ratings.update_one(
            {"_id": existing_review["_id"]},
            {"$set": review_doc}
        )
        flash("Your product review has been updated.", "success")
    else:
        review_doc["created_at"] = now
        mongo.product_ratings.insert_one(review_doc)
        flash("Thank you! Your product review has been submitted.", "success")

    return redirect(url_for("product_detail", pid=pid))



@app.route('/api/delivery/orders/<oid>/location', methods=['GET'])
@login_required()
def delivery_api_get_latest(oid):
    try:
        oid_obj = ObjectId(oid)
    except Exception:
        return jsonify({"ok": False, "error": "invalid order id"}), 400

    row = mongo.delivery_locations.find_one(
        {"order_id": oid_obj},
        sort=[("recorded_at", -1)]
    )

    if not row:
        return jsonify({
            "ok": True,
            "has_location": False
        })

    return jsonify({
        "ok": True,
        "has_location": True,
        "data": {
            "latitude": row.get("latitude"),
            "longitude": row.get("longitude"),
            "updated_at": row.get("recorded_at")
        }
    })

# ----------- ALERTS -----------
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

    active_since = availability.get("active_since") or _delivery_now()
    last_id = (request.args.get("last_id") or "").strip()

    base_filter = {
        "$and": [
            {
                "$or": [
                    {"delivery_partner_id": None},
                    {"delivery_partner_id": {"$exists": False}}
                ]
            },
            {"status": {"$in": DELIVERY_ACTIONABLE_STATUSES}},
            {"created_at": {"$gte": active_since}}
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
            "total_amount": float(o.get("total_amount") or 0),
            "delivery_fee": float(o.get("delivery_fee") or 0),
            "tip_amount": float(o.get("tip_amount") or 0),
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

# ======================
# UNIVERSAL SEARCH
# ======================
@app.route("/search")
def search():
    q = (request.args.get("q", "") or "").strip()
    user = current_user()

    products = []
    stores = []

    if q:
        products = list(
            mongo.products.find({
                "is_active": 1,
                "stock_kg": {"$gt": 0},
                "$or": [
                    {"name": {"$regex": q, "$options": "i"}},
                    {"category": {"$regex": q, "$options": "i"}},
                    {"sub_category": {"$regex": q, "$options": "i"}},
                    {"store_name": {"$regex": q, "$options": "i"}},
                ]
            }).sort("created_at", -1).limit(50)
        )

        for p in products:
            p["id"] = str(p["_id"])

            store = None
            if p.get("store_id"):
                store = mongo.stores.find_one({"_id": p["store_id"]})

            p["store_name"] = store.get("store_name") if store else p.get("store_name", "")
            p["store_id"] = str(p.get("store_id")) if p.get("store_id") else ""

        stores = list(
            mongo.stores.find({
                "$or": [
                    {"store_name": {"$regex": q, "$options": "i"}},
                    {"address": {"$regex": q, "$options": "i"}},
                ]
            }).sort("store_name", 1).limit(30)
        )

        for s in stores:
            s["id"] = str(s["_id"])
            s["product_count"] = mongo.products.count_documents({
                "store_id": s["_id"],
                "is_active": 1,
                "stock_kg": {"$gt": 0}
            })

    return render_template("search.html", user=user, q=q, products=products, stores=stores)

# ======================
# STORE CATALOG PAGE (also gated)
# ======================
# ======================
# STORE CATALOG PAGE / PUBLIC STORE PROFILE
# ======================
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
                "$or": [
                    {"store_id": sid_obj},
                    {"store_id": str(sid_obj)}
                ],
                "is_active": 1,
                "stock_kg": {"$gt": 0}
            }).sort("created_at", -1)
        )

        for p in products:
            p["id"] = str(p["_id"])
            p["name"] = (p.get("name") or "Product").strip()
            p["category"] = (p.get("category") or "Uncategorized").strip()
            p["sub_category"] = (p.get("sub_category") or "").strip()
            p["image_path"] = p.get("image_path", "")
            p["price_per_kg"] = float(p.get("price_per_kg") or 0)
            p["mrp_per_kg"] = float(p.get("mrp_per_kg") or p.get("old_price") or 0)
            p["stock_kg"] = float(p.get("stock_kg") or 0)
            p["store_id"] = str(sid_obj)
            p["store_name"] = store.get("store_name", "")

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

    return render_template(
        "store_catalog.html",
        user=user,
        store=store,
        products=products,
        categories=categories,
        store_reviews=store_reviews,
        store_avg_rating=store_avg_rating,
        store_rating_count=store_rating_count,
        can_review_store=can_review_store
    )

# ======================
# CUSTOMER STORE REVIEW
# ======================
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
            "stock_kg": {"$gt": 0},
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
# ----------------------
# Ratings routes — disabled (from feedback only)
# ----------------------
@app.route('/rate/product/<int:pid>', methods=['POST'])
@login_required()
def rate_product_disabled(pid):
    flash('Please rate from the order page after your delivery is completed.', 'info')
    return redirect(request.referrer or url_for('orders'))

@app.route('/rate/store/<int:sid>', methods=['POST'])
@login_required()
def rate_store_disabled(sid):
    flash('Please rate from the order page after your delivery is completed.', 'info')
    return redirect(request.referrer or url_for('orders'))

@app.route('/api/ratings/product/<int:pid>')
def api_ratings_product(pid):
    s = get_product_rating_summary(pid)
    return jsonify({"ok": True, "avg": s["avg"], "count": s["count"]})

@app.route('/api/ratings/store/<int:sid>')
def api_ratings_store(sid):
    s = get_store_rating_summary(sid)
    return jsonify({"ok": True, "avg": s["avg"], "count": s["count"]})

# ----------------------
# Complaints
# ----------------------
@app.route('/complaints', methods=['POST'])
@login_required()
def complaints_create():
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

# ----------------------
# ADMIN
# ----------------------
def table_has_columns(table, columns):
    # MongoDB collections do not have fixed columns.
    # Keep this helper for old dashboard compatibility.
    return True

def _csv_from_rows(rows):
    if not rows:
        return [], []
    dict_rows = [dict(r) if not isinstance(r, dict) else r for r in rows]
    keys = set()
    for r in dict_rows:
        keys.update(r.keys())
    fieldnames = sorted(keys)
    return fieldnames, dict_rows

def _zip_add_csv(zf, name, rows):
    fieldnames, dict_rows = _csv_from_rows(rows)
    buf = io.StringIO()
    if fieldnames:
        w = csv.DictWriter(buf, fieldnames=fieldnames)
        w.writeheader()
        for r in dict_rows:
            row = {k: r.get(k) for k in fieldnames}
            w.writerow(row)
    zf.writestr(name, buf.getvalue())

def _zip_add_json(zf, name, obj):
    zf.writestr(name, json.dumps(obj, indent=2, default=str))


def _to_object_id(value):
    if isinstance(value, ObjectId):
        return value
    try:
        return ObjectId(str(value))
    except Exception:
        return None


def _find_by_any_id(collection_name, value):
    """
    Find a document by ObjectId or string _id.
    Works even if your older data stored mixed id types.
    """
    if value is None:
        return None

    collection = mongo[collection_name]
    oid = _to_object_id(value)

    queries = []
    if oid is not None:
        queries.append({"_id": oid})
    queries.append({"_id": str(value)})

    for q in queries:
        doc = collection.find_one(q)
        if doc:
            return doc

    return None


def _order_total(order_doc):
    return (
        float(order_doc.get("total_amount") or 0)
        + float(order_doc.get("delivery_fee") or 0)
        + float(order_doc.get("tip_amount") or 0)
    )


def _parse_dt(value):
    if not value:
        return None

    s = str(value).strip()
    if not s:
        return None

    if s.endswith("Z"):
        s = s[:-1]

    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
    ):
        try:
            return datetime.strptime(s, fmt)
        except Exception:
            pass

    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def _month_index_from_created_at(value):
    dt = _parse_dt(value)
    if not dt:
        return None
    return dt.month - 1  # 0..11

def _safe_oid(value):
    if isinstance(value, ObjectId):
        return value
    try:
        return ObjectId(str(value))
    except Exception:
        return None



def _find_doc(collection_name, value):
    """
    Find a document by ObjectId or string _id.
    Handles mixed legacy data types safely.
    """
    if value is None:
        return None

    collection = mongo[collection_name]
    candidates = []

    oid = _safe_oid(value)
    if oid is not None:
        candidates.append({"_id": oid})

    candidates.append({"_id": str(value)})

    for query in candidates:
        doc = collection.find_one(query)
        if doc:
            return doc

    return None



def _rating_summary(collection_name, target_field, lookup_collection, lookup_name_field, image_fields=None, limit=5):
    image_fields = image_fields or []
    buckets = defaultdict(lambda: {"sum": 0.0, "count": 0})

    for row in mongo[collection_name].find({}):
        target_id = row.get(target_field)
        if target_id is None:
            continue

        key = str(target_id)
        try:
            rating = float(row.get("rating") or 0)
        except Exception:
            rating = 0.0

        buckets[key]["sum"] += rating
        buckets[key]["count"] += 1

    result = []
    for key, agg in buckets.items():
        ref = _find_doc(lookup_collection, key)
        if not ref:
            continue

        item = {
            "id": key,
            lookup_name_field: ref.get(lookup_name_field, "") or "",
            "avg_rating": round(agg["sum"] / agg["count"], 2) if agg["count"] else 0,
            "rating_count": agg["count"],
        }

        for field in image_fields:
            if ref.get(field):
                item["image_url"] = ref.get(field)
                break

        result.append(item)

    result.sort(key=lambda x: (x["avg_rating"], x["rating_count"]), reverse=True)
    return result[:limit]





def _top_selling_items(limit=6):
    buckets = defaultdict(lambda: {"sold": 0, "sold_kg": 0.0, "revenue": 0.0})

    for row in mongo.order_items.find({}):
        pid = row.get("product_id")
        if pid is None:
            continue

        key = str(pid)
        buckets[key]["sold"] += 1
        buckets[key]["sold_kg"] += float(row.get("weight_kg") or 0)
        buckets[key]["revenue"] += float(row.get("line_total") or 0)

    result = []
    for key, agg in buckets.items():
        product = _find_doc("products", key)
        if not product:
            continue

        result.append({
            "product_id": key,
            "product_name": product.get("name", "") or product.get("product_name", "") or "Product",
            "sold": agg["sold"],
            "sold_kg": round(agg["sold_kg"], 2),
            "revenue": round(agg["revenue"], 2),
            "image_url": product.get("image_path") or product.get("image_url") or "",
        })

    result.sort(key=lambda x: (x["sold"], x["revenue"]), reverse=True)
    return result[:limit]



def _top_stores_by_orders(limit=6):
    """
    Returns popular stores based on order count and delivered revenue.
    """
    buckets = defaultdict(lambda: {"orders": 0, "delivered_orders": 0, "revenue": 0.0})

    for order in mongo.orders.find({}):
        sid = order.get("store_id")
        if sid is None:
            continue

        key = str(sid)

        buckets[key]["orders"] += 1

        if (order.get("status") or "").upper() == "DELIVERED":
            buckets[key]["delivered_orders"] += 1
            buckets[key]["revenue"] += _order_total(order)

    out = []

    for key, agg in buckets.items():
        store = _find_by_any_id("stores", key)
        if not store:
            continue

        out.append({
            "store_id": key,
            "store_name": store.get("store_name", ""),
            "orders": agg["orders"],
            "likes": agg["orders"],  # keeps your current template working
            "delivered_orders": agg["delivered_orders"],
            "revenue": round(agg["revenue"], 2),
            "subtitle": f'{agg["orders"]} orders',
            "image_url": store.get("image_url") or store.get("logo") or "",
        })

    out.sort(key=lambda x: (x["orders"], x["revenue"]), reverse=True)
    return out[:limit]


def _top_customers(limit=6):
    buckets = defaultdict(lambda: {"orders": 0, "spent": 0.0})

    for order in mongo.orders.find({}):
        uid = order.get("user_id")
        if uid is None:
            continue

        key = str(uid)
        buckets[key]["orders"] += 1

        if _norm_status(order.get("status")) == "DELIVERED":
            buckets[key]["spent"] += _order_total(order)

    result = []
    for key, agg in buckets.items():
        user = _find_doc("users", key)
        if not user or _norm_role(user.get("role")) != "customer":
            continue

        result.append({
            "user_id": key,
            "name": user.get("name", "") or "",
            "phone": user.get("phone", "") or "",
            "orders": agg["orders"],
            "spent": round(agg["spent"], 2),
        })

    result.sort(key=lambda x: (x["orders"], x["spent"]), reverse=True)
    return result[:limit]



def _top_deliverymen(limit=6):
    buckets = defaultdict(lambda: {"orders": 0, "delivered_orders": 0})

    for order in mongo.orders.find({"delivery_partner_id": {"$exists": True, "$ne": None}}):
        did = order.get("delivery_partner_id")
        if did is None:
            continue

        key = str(did)
        buckets[key]["orders"] += 1

        if _norm_status(order.get("status")) == "DELIVERED":
            buckets[key]["delivered_orders"] += 1

    result = []
    for key, agg in buckets.items():
        user = _find_doc("users", key)
        if not user or _norm_role(user.get("role")) != "delivery":
            continue

        result.append({
            "user_id": key,
            "name": user.get("name", "") or "",
            "phone": user.get("phone", "") or "",
            "orders": agg["orders"],
            "completed_orders": agg["delivered_orders"],
        })

    result.sort(key=lambda x: (x["orders"], x["completed_orders"]), reverse=True)
    return result[:limit]




def _store_complaint_summary(limit=5):
    store_map = defaultdict(int)

    complaints = list(mongo.complaints.find({
        "$or": [
            {"target_type": "store"},
            {"store_id": {"$exists": True}}
        ]
    }))

    for c in complaints:
        sid = c.get("store_id") or c.get("target_id")
        if not sid:
            continue
        store_map[str(sid)] += 1

    out = []

    for sid_str, cnt in store_map.items():
        store = _find_by_any_id("stores", sid_str)
        out.append({
            "store_id": sid_str,
            "store_name": store.get("store_name", "") if store else "",
            "cnt": cnt
        })

    out.sort(key=lambda x: x["cnt"], reverse=True)
    return out[:limit]


def _delivery_complaint_summary(limit=5):
    delivery_map = defaultdict(int)

    complaints = list(mongo.complaints.find({
        "$or": [
            {"target_type": "delivery"},
            {"delivery_partner_id": {"$exists": True}}
        ]
    }))

    for c in complaints:
        did = c.get("delivery_partner_id") or c.get("target_id")
        if not did:
            continue
        delivery_map[str(did)] += 1

    out = []

    for did_str, cnt in delivery_map.items():
        user = _find_by_any_id("users", did_str)
        out.append({
            "delivery_id": did_str,
            "user_id": did_str,
            "delivery_name": user.get("name", "") if user else "",
            "name": user.get("name", "") if user else "",
            "phone": user.get("phone", "") if user else "",
            "cnt": cnt
        })

    out.sort(key=lambda x: x["cnt"], reverse=True)
    return out[:limit]


def _store_rankings_by_orders(limit=6):
    """
    Popular stores by order count.
    """
    buckets = defaultdict(lambda: {"orders": 0, "delivered_orders": 0, "revenue": 0.0})

    for order in mongo.orders.find({}):
        sid = order.get("store_id")
        if sid is None:
            continue

        key = str(sid)
        buckets[key]["orders"] += 1

        if _norm_status(order.get("status")) == "DELIVERED":
            buckets[key]["delivered_orders"] += 1
            buckets[key]["revenue"] += _order_total(order)

    result = []
    for key, agg in buckets.items():
        store = _find_doc("stores", key)
        if not store:
            continue

        result.append({
            "store_id": key,
            "store_name": store.get("store_name", "") or "",
            "orders": agg["orders"],
            "revenue": round(agg["revenue"], 2),
            "likes": agg["orders"],  # keeps older UI compatibility
            "subtitle": f'{agg["orders"]} orders',
            "image_url": store.get("image_url") or store.get("logo") or "",
        })

    result.sort(key=lambda x: (x["orders"], x["revenue"]), reverse=True)
    return result[:limit]




def _store_rankings_by_revenue(limit=6):
    """
    Revenue-first store ranking for the selling-stores section.
    """
    buckets = defaultdict(lambda: {"orders": 0, "revenue": 0.0})

    for order in mongo.orders.find({}):
        sid = order.get("store_id")
        if sid is None:
            continue

        key = str(sid)
        buckets[key]["orders"] += 1

        if _norm_status(order.get("status")) == "DELIVERED":
            buckets[key]["revenue"] += _order_total(order)

    result = []
    for key, agg in buckets.items():
        store = _find_doc("stores", key)
        if not store:
            continue

        result.append({
            "store_id": key,
            "store_name": store.get("store_name", "") or "",
            "orders": agg["orders"],
            "revenue": round(agg["revenue"], 2),
            "image_url": store.get("image_url") or store.get("logo") or "",
        })

    result.sort(key=lambda x: (x["revenue"], x["orders"]), reverse=True)
    return result[:limit]



def _dashboard_monthly_sales():
    """
    Delivered revenue by month for the current year.
    """
    labels = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    values = [0.0] * 12
    current_year = datetime.utcnow().year

    for order in mongo.orders.find({"status": {"$regex": "^DELIVERED$", "$options": "i"}}):
        dt = _parse_dt(order.get("created_at"))
        if not dt or dt.year != current_year:
            continue

        idx = dt.month - 1
        if 0 <= idx < 12:
            values[idx] += _order_total(order)

    return labels, [round(v, 2) for v in values]


def _top_store_complaints(limit=5):
    store_map = defaultdict(int)

    complaints = list(mongo.complaints.find({
        "$or": [
            {"target_type": "store"},
            {"store_id": {"$exists": True}},
        ]
    }))

    for c in complaints:
        sid = c.get("store_id") or c.get("target_id")
        if not sid:
            continue
        store_map[str(sid)] += 1

    result = []
    for sid_str, cnt in store_map.items():
        store = _find_doc("stores", sid_str)
        result.append({
            "store_id": sid_str,
            "store_name": store.get("store_name", "") if store else f"ID {sid_str}",
            "cnt": cnt,
        })

    result.sort(key=lambda x: x["cnt"], reverse=True)
    return result[:limit]



def _top_delivery_complaints(limit=5):
    delivery_map = defaultdict(int)

    complaints = list(mongo.complaints.find({
        "$or": [
            {"target_type": "delivery"},
            {"delivery_partner_id": {"$exists": True}},
        ]
    }))

    for c in complaints:
        did = c.get("delivery_partner_id") or c.get("target_id")
        if not did:
            continue
        delivery_map[str(did)] += 1

    result = []
    for did_str, cnt in delivery_map.items():
        user = _find_doc("users", did_str)
        result.append({
            "delivery_id": did_str,
            "user_id": did_str,
            "delivery_name": user.get("name", "") if user else f"ID {did_str}",
            "name": user.get("name", "") if user else f"ID {did_str}",
            "phone": user.get("phone", "") if user else "",
            "cnt": cnt,
        })

    result.sort(key=lambda x: x["cnt"], reverse=True)
    return result[:limit]



@app.route("/admin/dashboard")
@login_required(role="admin")
def admin_dashboard():
    # -------------------------
    # Load source collections once
    # -------------------------
    orders = list(mongo.orders.find({}))
    transactions = list(mongo.transactions.find({}))

    # -------------------------
    # Role-based user counts
    # -------------------------
    users_total = mongo.users.count_documents({})
    customers_total = mongo.users.count_documents({
        "role": {"$regex": "^customer$", "$options": "i"}
    })
    delivery_people_total = mongo.users.count_documents({
        "role": {"$regex": "^delivery$", "$options": "i"}
    })
    active_delivery_people_total = mongo.users.count_documents({
        "role": {"$regex": "^delivery$", "$options": "i"},
        "is_active": 1
    })

    stores_total = mongo.stores.count_documents({})
    products_total = mongo.products.count_documents({})
    orders_total = mongo.orders.count_documents({})

    # -------------------------
    # Normalize order status buckets
    # -------------------------
    status_counts = defaultdict(int)
    for order in orders:
        status_counts[_norm_status(order.get("status"))] += 1

    # Support mixed spellings already present in legacy data
    cancelled_orders_total = status_counts["CANCELLED"] + status_counts["CANCELED"]
    delivered_orders_total = status_counts["DELIVERED"]
    out_for_delivery_total = status_counts["OUT_FOR_DELIVERY"]
    assigned_orders_total = status_counts["ASSIGNED_TO_DELIVERY"] + status_counts["ACCEPTED_BY_DELIVERY_MAN"]
    preparing_orders_total = status_counts["PREPARING"] + status_counts["PACKAGING"]
    placed_orders_total = status_counts["PLACED"] + status_counts["CONFIRMED"]
    unassigned_orders_total = placed_orders_total

    # -------------------------
    # Payment / transaction buckets
    # -------------------------
    txn_status_counts = defaultdict(int)
    for txn in transactions:
        txn_status_counts[_norm_status(txn.get("status"))] += 1

    refunded_orders_total = txn_status_counts["REFUNDED"]
    failed_payments_total = txn_status_counts["FAILED"] + txn_status_counts["PAYMENT_FAILED"]
    pending_payments_total = txn_status_counts["PENDING"]
    paid_txn_total = txn_status_counts["PAID"]

    # -------------------------
    # GMV / earnings
    # -------------------------
    gmv = 0.0
    delivered_order_docs = []
    for order in orders:
        if _norm_status(order.get("status")) == "DELIVERED":
            delivered_order_docs.append(order)
            gmv += _order_total(order)

    total_earnings_from_paid_txn = sum(float(t.get("amount") or 0) for t in transactions if _norm_status(t.get("status")) == "PAID")
    total_earnings = total_earnings_from_paid_txn if total_earnings_from_paid_txn > 0 else gmv

    # -------------------------
    # Stores performance (revenue-first)
    # -------------------------
    by_store = []
    for store in mongo.stores.find({}).sort("store_name", 1):
        sid = store["_id"]
        store_orders = [o for o in orders if str(o.get("store_id")) == str(sid)]

        order_count = len(store_orders)
        revenue = 0.0
        delivered_count = 0

        for o in store_orders:
            if _norm_status(o.get("status")) == "DELIVERED":
                delivered_count += 1
                revenue += _order_total(o)

        by_store.append({
            "store_id": str(sid),
            "store_name": store.get("store_name", "") or "",
            "orders": order_count,
            "delivered_orders": delivered_count,
            "revenue": round(revenue, 2),
            "image_url": store.get("image_url") or store.get("logo") or "",
        })

    by_store.sort(key=lambda x: (x["revenue"], x["orders"]), reverse=True)

    # -------------------------
    # Rankings & summaries
    # -------------------------
    top_store_complaints = _top_store_complaints(limit=5)
    top_delivery_complaints = _top_delivery_complaints(limit=5)

    top_rated_stores = _rating_summary(
        collection_name="store_ratings",
        target_field="store_id",
        lookup_collection="stores",
        lookup_name_field="store_name",
        image_fields=["image_url", "logo"],
        limit=6,
    )

    top_rated_products = _rating_summary(
        collection_name="product_ratings",
        target_field="product_id",
        lookup_collection="products",
        lookup_name_field="name",
        image_fields=["image_path", "image_url"],
        limit=6,
    )

    top_rated_deliverymen = _rating_summary(
        collection_name="delivery_ratings",
        target_field="delivery_partner_id",
        lookup_collection="users",
        lookup_name_field="name",
        image_fields=[],
        limit=6,
    )

    top_selling_items = _top_selling_items(limit=6)
    most_popular_stores = _store_rankings_by_orders(limit=6)
    top_selling_store_tiles = _store_rankings_by_revenue(limit=6)
    top_customers = _top_customers(limit=6)
    top_deliverymen = _top_deliverymen(limit=6)

    # -------------------------
    # Chart data
    # -------------------------
    sales_labels, sales_values = _dashboard_monthly_sales()

    # -------------------------
    # Quick links
    # -------------------------
    quick_links = [
        {"label": "Pending Approvals", "endpoint": "admin_approvals"},
        {"label": "Manage Users", "endpoint": "admin_users"},
        {"label": "Complaints", "endpoint": "admin_complaints"},
        {"label": "Create Store", "endpoint": "admin_create_store"},
        {"label": "Create Delivery Partner", "endpoint": "admin_create_delivery"},
        {"label": "Export Transactions CSV", "endpoint": "admin_transactions_csv"},
    ]

    metrics = {
        "users": users_total,
        "customers": customers_total,
        "stores": stores_total,
        "products": products_total,
        "orders": orders_total,
        "gmv": round(gmv, 2),
        "total_earnings": round(total_earnings, 2),
        "delivery_people": delivery_people_total,
        "active_delivery_people": active_delivery_people_total,
        "unassigned_orders": unassigned_orders_total,
        "accepted_by_delivery": status_counts["ACCEPTED_BY_DELIVERY_MAN"],
        "packaging_orders": status_counts["PACKAGING"] + status_counts["PREPARING"],
        "out_for_delivery": out_for_delivery_total,
        "delivered_orders": delivered_orders_total,
        "cancelled_orders": cancelled_orders_total,
        "refunded_orders": refunded_orders_total,
        "failed_payments": failed_payments_total,
        "pending_payments": pending_payments_total,
        "paid_transactions": paid_txn_total,
    }

    return render_template(
        "admin_dashboard.html",
        user=current_user(),
        metrics=metrics,
        by_store=by_store,
        top_store_complaints=top_store_complaints,
        top_delivery_complaints=top_delivery_complaints,
        top_rated_stores=top_rated_stores,
        top_rated_products=top_rated_products,
        top_rated_deliverymen=top_rated_deliverymen,
        top_selling_items=top_selling_items,
        most_popular_stores=most_popular_stores,
        top_selling_store_tiles=top_selling_store_tiles,
        top_customers=top_customers,
        top_deliverymen=top_deliverymen,
        sales_labels=sales_labels,
        sales_values=sales_values,
        quick_links=quick_links,
        complaints_window_label="(all time)",
    )



@app.route('/admin/approvals')
@login_required(role='admin')
def admin_approvals():
    flash('Approval feature under development.', 'info')
    return redirect(url_for('admin_dashboard'))

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

        lat_raw = request.form.get('latitude')
        lng_raw = request.form.get('longitude')

        latitude = None
        longitude = None

        # =========================
        # PARSE LATITUDE
        # =========================
        try:
            latitude = float(lat_raw) if lat_raw and str(lat_raw).strip() else None
        except Exception:
            latitude = None

        # =========================
        # PARSE LONGITUDE
        # =========================
        try:
            longitude = float(lng_raw) if lng_raw and str(lng_raw).strip() else None
        except Exception:
            longitude = None

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

            # =========================
            # INSERT STORE
            # =========================
            mongo.stores.insert_one({
                "user_id": user_id,
                "store_name": store_name,
                "address": address,
                "latitude": latitude,
                "longitude": longitude,
                "is_active": 1,
                "created_at": datetime.utcnow().isoformat()
            })

        except DuplicateKeyError:

            flash(
                "Email or phone already exists. Please use different details.",
                "danger"
            )

            return redirect(url_for('admin_create_store'))

        except Exception as e:

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
    
# ============================================================
# ADMIN STORE MANAGEMENT
# Store / Store Overview / Store List / Store Reviews
# ============================================================

def _store_owner_for(store_doc):
    """
    Safely fetch store owner from users collection.
    Store stores user_id as string in admin_create_store().
    """
    user_id = store_doc.get("user_id")

    if not user_id:
        return None

    try:
        return mongo.users.find_one({"_id": ObjectId(str(user_id))})
    except Exception:
        return mongo.users.find_one({"_id": user_id})


def _store_order_docs(store_id):
    """
    Return orders for a store.
    Supports store_id stored as ObjectId or string.
    """
    store_id_str = str(store_id)

    return list(mongo.orders.find({
        "$or": [
            {"store_id": store_id},
            {"store_id": store_id_str}
        ]
    }))


def _store_product_count(store_id):
    """
    Return product count for a store.
    Supports store_id stored as ObjectId or string.
    """
    store_id_str = str(store_id)

    return mongo.products.count_documents({
        "$or": [
            {"store_id": store_id},
            {"store_id": store_id_str}
        ]
    })


def _store_rating_summary(store_id):
    """
    Return average rating for a store from store_ratings collection.
    Safe if collection is empty/missing records.
    """
    store_id_str = str(store_id)

    ratings = list(mongo.store_ratings.find({
        "$or": [
            {"store_id": store_id},
            {"store_id": store_id_str}
        ]
    }))

    if not ratings:
        return {
            "avg": 0,
            "count": 0
        }

    total = 0.0
    count = 0

    for rating in ratings:
        try:
            total += float(rating.get("rating") or rating.get("stars") or 0)
            count += 1
        except Exception:
            continue

    if count <= 0:
        return {
            "avg": 0,
            "count": 0
        }

    return {
        "avg": round(total / count, 1),
        "count": count
    }


def _admin_store_rows():
    """
    Build reusable store rows for overview, list, and reviews pages.
    """
    rows = []

    for store in mongo.stores.find({}).sort("created_at", -1):
        sid = store.get("_id")
        sid_str = str(sid)

        owner = _store_owner_for(store) or {}
        orders = _store_order_docs(sid)

        delivered_orders = [
            o for o in orders
            if _norm_status(o.get("status")) == "DELIVERED"
        ]

        revenue = sum(_order_total(o) for o in delivered_orders)
        rating = _store_rating_summary(sid)
        product_count = _store_product_count(sid)

        rows.append({
            "id": sid_str,
            "store_id": sid_str,
            "store_name": store.get("store_name") or store.get("name") or "Store",
            "address": store.get("address") or "",
            "image_url": store.get("image_url") or store.get("logo") or "",
            "is_active": int(store.get("is_active", 1) or 0),
            "created_at": store.get("created_at") or "",
            "owner_id": str(owner.get("_id")) if owner.get("_id") else "",
            "owner_name": owner.get("name") or "Owner",
            "owner_email": owner.get("email") or "",
            "owner_phone": owner.get("phone") or "",
            "orders": len(orders),
            "delivered_orders": len(delivered_orders),
            "products": product_count,
            "revenue": round(revenue, 2),
            "rating": rating["avg"],
            "rating_count": rating["count"],
        })

    return rows


@app.route("/admin/stores")
@login_required(role="admin")
def admin_store_overview():
    stores = _admin_store_rows()

    total_stores = len(stores)
    active_stores = len([s for s in stores if s["is_active"] == 1])
    inactive_stores = len([s for s in stores if s["is_active"] != 1])

    total_transactions = mongo.transactions.count_documents({})
    commission_earned = 0.0
    total_store_withdrawals = 0.0

    for txn in mongo.transactions.find({}):
        amount = float(txn.get("amount") or 0)

        if _norm_status(txn.get("status")) == "PAID":
            commission_earned += float(txn.get("commission_amount") or 0)

        txn_type = _norm_status(txn.get("type") or txn.get("transaction_type"))
        if txn_type in ["STORE_WITHDRAWAL", "WITHDRAWAL"]:
            total_store_withdrawals += amount

        if commission_earned <= 0:
        # fallback commission estimate if commission_amount is not stored
            commission_earned = sum(float(s.get("revenue") or 0) for s in stores)

    top_selling_stores = sorted(
        stores,
        key=lambda x: (x["revenue"], x["orders"]),
        reverse=True
    )[:6]

    most_popular_stores = sorted(
        stores,
        key=lambda x: (x["orders"], x["rating"]),
        reverse=True
    )[:6]

    top_product_stores = sorted(
        stores,
        key=lambda x: (x["products"], x["orders"]),
        reverse=True
    )[:6]

    metrics = {
        "total_stores": total_stores,
        "active_stores": active_stores,
        "inactive_stores": inactive_stores,
        "new_stores": mongo.stores.count_documents({}),
        "total_transactions": total_transactions,
        "commission_earned": round(commission_earned, 2),
        "store_withdrawals": round(total_store_withdrawals, 2),
    }

    return render_template(
        "admin_store_overview.html",
        user=current_user(),
        metrics=metrics,
        stores=stores,
        top_selling_stores=top_selling_stores,
        most_popular_stores=most_popular_stores,
        top_product_stores=top_product_stores,
        active_group="store",
        active_page="store_overview",
    )


@app.route("/admin/stores/list")
@login_required(role="admin")
def admin_store_list():
    stores = _admin_store_rows()

    return render_template(
        "admin_store_list.html",
        user=current_user(),
        stores=stores,
        active_group="store",
        active_page="store_list",
    )


@app.route("/admin/stores/reviews")
@login_required(role="admin")
def admin_store_reviews():
    stores = _admin_store_rows()

    recommended_stores = sorted(
        stores,
        key=lambda x: (x["rating"], x["orders"], x["products"]),
        reverse=True
    )

    return render_template(
        "admin_store_reviews.html",
        user=current_user(),
        stores=stores,
        recommended_stores=recommended_stores,
        active_group="store",
        active_page="store_reviews",
    )


@app.route("/admin/stores/export.csv")
@login_required(role="admin")
def admin_stores_export_csv():
    stores = _admin_store_rows()

    rows = [
        ["SL", "Store Name", "Store ID", "Owner Name", "Owner Email", "Owner Phone", "Status", "Created At"]
    ]

    for idx, store in enumerate(stores, start=1):
        rows.append([
            idx,
            store.get("store_name", ""),
            store.get("id", ""),
            store.get("owner_name", ""),
            store.get("owner_email", ""),
            store.get("owner_phone", ""),
            "Active" if store.get("is_active") == 1 else "Inactive",
            store.get("created_at", ""),
        ])

    def csv_escape(value):
        value = "" if value is None else str(value)
        return '"' + value.replace('"', '""') + '"'

    csv_data = "\n".join(",".join(csv_escape(col) for col in row) for row in rows)

    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=nefresh_stores.csv"}
    )


@app.route("/admin/stores/<store_id>/toggle", methods=["POST"])
@login_required(role="admin")
def admin_store_toggle(store_id):
    try:
        sid = ObjectId(store_id)
    except Exception:
        flash("Invalid store.", "danger")
        return redirect(url_for("admin_store_list"))

    store = mongo.stores.find_one({"_id": sid})

    if not store:
        flash("Store not found.", "warning")
        return redirect(url_for("admin_store_list"))

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

    flash("Store status updated successfully.", "success")
    return redirect(url_for("admin_store_list"))


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

    owner_name = request.form.get("owner_name", "").strip()
    owner_email = request.form.get("owner_email", "").lower().strip()
    owner_phone = normalize_phone(request.form.get("owner_phone", "").strip())

    if not store_name:
        flash("Store name is required.", "warning")
        return redirect(url_for("admin_store_list"))

    mongo.stores.update_one(
        {"_id": sid},
        {
            "$set": {
                "store_name": store_name,
                "address": address,
                "updated_at": datetime.utcnow().isoformat()
            }
        }
    )

    user_id = store.get("user_id")

    if user_id:
        update_user = {}

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
        flash("Invalid store.", "danger")
        return redirect(url_for("admin_store_list"))

    store = mongo.stores.find_one({"_id": sid})

    if not store:
        flash("Store not found.", "warning")
        return redirect(url_for("admin_store_list"))

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

        flash("Store has orders, so it was disabled instead of deleted.", "warning")
        return redirect(url_for("admin_store_list"))

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

    flash("Store deleted successfully.", "success")
    return redirect(url_for("admin_store_list"))





# =========================================================
# ADMIN DELIVERY MANAGEMENT HELPERS + ROUTES
# =========================================================

def _ad_now():
    return datetime.utcnow()


def _ad_iso_now():
    return datetime.utcnow().isoformat()


def _ad_safe_float(value, default=0.0):
    try:
        if value is None or str(value).strip() == "":
            return default
        return float(value)
    except Exception:
        return default


def _ad_safe_int(value, default=0):
    try:
        if value is None or str(value).strip() == "":
            return default
        return int(value)
    except Exception:
        return default


def _ad_money(value):
    return round(_ad_safe_float(value, 0), 2)


def _ad_object_id(value):
    try:
        return ObjectId(str(value))
    except Exception:
        return None


def _ad_norm_status(value):
    return str(value or "").strip().upper()


def _ad_is_active(doc):
    return 1 if doc and doc.get("is_active") in [1, True, "1", "true", "True", "yes", "Yes"] else 0


def _ad_parse_date(value):
    if not value:
        return None

    if isinstance(value, datetime):
        return value

    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)

    try:
        clean = str(value).replace("Z", "").strip()
        return datetime.fromisoformat(clean)
    except Exception:
        return None


def _ad_date_display(value):
    dt = _ad_parse_date(value)

    if not dt:
        return value or ""

    try:
        return dt.strftime("%d %b %Y")
    except Exception:
        return value or ""


def _ad_datetime_display(value):
    dt = _ad_parse_date(value)

    if not dt:
        return value or ""

    try:
        return dt.strftime("%d %b %Y, %I:%M %p")
    except Exception:
        return value or ""


def _ad_created_in_last_days(value, days=30):
    dt = _ad_parse_date(value)

    if not dt:
        return False

    return dt >= (_ad_now() - timedelta(days=days))


def _ad_mask_phone(phone):
    phone = str(phone or "").strip()

    if not phone:
        return ""

    digits = "".join(ch for ch in phone if ch.isdigit())

    if len(digits) <= 4:
        return phone

    return "+" + "*" * max(4, len(digits) - 4) + digits[-4:]


def _ad_mask_email(email):
    email = str(email or "").strip()

    if not email or "@" not in email:
        return email

    name, domain = email.split("@", 1)

    if len(name) <= 2:
        return name[:1] + "*****@" + domain

    return name[:1] + "*****" + name[-1:] + "@" + domain


def _ad_order_total(order_doc):
    if not order_doc:
        return 0.0

    if order_doc.get("total_payable") is not None:
        return _ad_safe_float(order_doc.get("total_payable"))

    total_amount = _ad_safe_float(order_doc.get("total_amount"))
    delivery_fee = _ad_safe_float(order_doc.get("delivery_fee"))
    tip_amount = _ad_safe_float(order_doc.get("tip_amount"))

    return total_amount + delivery_fee + tip_amount


def _ad_is_delivered(order_doc):
    status = _ad_norm_status(order_doc.get("status"))
    return status in ["DELIVERED", "COMPLETED", "ORDER_DELIVERED"]


def _ad_delivery_order_query(user_id):
    uid = str(user_id)
    uid_obj = _ad_object_id(uid)

    query_items = [
        {"delivery_partner_id": uid}
    ]

    if uid_obj:
        query_items.append({"delivery_partner_id": uid_obj})

    return {"$or": query_items}


def _ad_delivery_orders(user_id):
    return list(
        mongo.orders.find(_ad_delivery_order_query(user_id)).sort("created_at", -1)
    )


def _ad_delivery_active_order_query(user_id):
    uid = str(user_id)
    uid_obj = _ad_object_id(uid)

    query_items = [
        {
            "delivery_partner_id": uid,
            "status": {
                "$in": [
                    "ASSIGNED_TO_DELIVERY",
                    "OUT_FOR_DELIVERY",
                    "ACCEPTED_BY_DELIVERY_MAN",
                    "PICKED_UP"
                ]
            }
        }
    ]

    if uid_obj:
        query_items.append({
            "delivery_partner_id": uid_obj,
            "status": {
                "$in": [
                    "ASSIGNED_TO_DELIVERY",
                    "OUT_FOR_DELIVERY",
                    "ACCEPTED_BY_DELIVERY_MAN",
                    "PICKED_UP"
                ]
            }
        })

    return {"$or": query_items}


def _ad_delivery_assigned_orders(user_id):
    return mongo.orders.count_documents(_ad_delivery_active_order_query(user_id))


def _ad_delivery_availability(user_id):
    uid = str(user_id)

    row = mongo.delivery_availability.find_one({"user_id": uid})

    if not row:
        uid_obj = _ad_object_id(uid)
        if uid_obj:
            row = mongo.delivery_availability.find_one({"user_id": uid_obj})

    if not row:
        row = mongo.delivery_availability.find_one({"delivery_partner_id": uid})

    return row or {}


def _ad_delivery_is_online(user_id):
    row = _ad_delivery_availability(user_id)
    return 1 if row.get("active") in [1, True, "1", "true", "True"] else 0


def _ad_rating_summary_for_delivery(user_id):
    uid = str(user_id)
    uid_obj = _ad_object_id(uid)

    query_items = [
        {"delivery_partner_id": uid}
    ]

    if uid_obj:
        query_items.append({"delivery_partner_id": uid_obj})

    rows = list(mongo.delivery_ratings.find({"$or": query_items}))

    count = len(rows)
    total = 0.0

    for row in rows:
        total += _ad_safe_float(row.get("rating"))

    avg = round(total / count, 1) if count else 0

    return {
        "avg": avg,
        "count": count
    }


def _ad_delivery_user_base_row(user_doc):
    uid = str(user_doc.get("_id"))

    availability = _ad_delivery_availability(uid)
    orders = _ad_delivery_orders(uid)
    delivered_orders = [o for o in orders if _ad_is_delivered(o)]
    rating = _ad_rating_summary_for_delivery(uid)

    is_online = 1 if availability.get("active") in [1, True, "1", "true", "True"] else 0

    return {
        "id": uid,
        "name": user_doc.get("name") or "Delivery Partner",
        "email": user_doc.get("email") or "",
        "phone": user_doc.get("phone") or "",
        "email_masked": _ad_mask_email(user_doc.get("email") or ""),
        "phone_masked": _ad_mask_phone(user_doc.get("phone") or ""),
        "role": user_doc.get("role") or "delivery",
        "is_active": _ad_is_active(user_doc),
        "phone_verified": 1 if user_doc.get("phone_verified") in [1, True, "1", "true", "True"] else 0,
        "created_at": user_doc.get("created_at") or "",
        "created_at_display": _ad_date_display(user_doc.get("created_at")),
        "created_at_full": _ad_datetime_display(user_doc.get("created_at")),

        "zone": availability.get("zone") or availability.get("area") or "Main Zone",
        "latitude": availability.get("latitude"),
        "longitude": availability.get("longitude"),
        "is_online": is_online,
        "availability_status": "Online" if is_online else "Offline",
        "active_since": availability.get("active_since") or "",
        "offline_at": availability.get("offline_at") or "",

        "total_orders": len(orders),
        "total_completed_orders": len(delivered_orders),
        "currently_assigned_orders": _ad_delivery_assigned_orders(uid),
        "delivered_amount": _ad_money(sum(_ad_order_total(o) for o in delivered_orders)),
        "rating": rating["avg"],
        "rating_count": rating["count"],
    }


def _ad_delivery_rows():
    delivery_users = list(
        mongo.users.find({"role": "delivery"}).sort("created_at", -1)
    )

    rows = []

    for user_doc in delivery_users:
        rows.append(_ad_delivery_user_base_row(user_doc))

    return rows


def _ad_filter_delivery_rows(rows, search="", status="", availability=""):
    search = (search or "").strip().lower()
    status = (status or "").strip().lower()
    availability = (availability or "").strip().lower()

    filtered = rows

    if status == "active":
        filtered = [r for r in filtered if r.get("is_active")]
    elif status in ["inactive", "disabled", "blocked"]:
        filtered = [r for r in filtered if not r.get("is_active")]

    if availability == "online":
        filtered = [r for r in filtered if r.get("is_online")]
    elif availability == "offline":
        filtered = [r for r in filtered if not r.get("is_online")]

    if search:
        clean = []

        for row in filtered:
            haystack = " ".join([
                str(row.get("name") or ""),
                str(row.get("email") or ""),
                str(row.get("phone") or ""),
                str(row.get("zone") or ""),
            ]).lower()

            if search in haystack:
                clean.append(row)

        filtered = clean

    return filtered


def _ad_delivery_metrics(rows=None):
    if rows is None:
        rows = _ad_delivery_rows()

    active = [r for r in rows if r.get("is_active")]
    inactive = [r for r in rows if not r.get("is_active")]
    online = [r for r in rows if r.get("is_online")]
    offline = [r for r in rows if not r.get("is_online")]
    new_joined = [r for r in rows if _ad_created_in_last_days(r.get("created_at"), 30)]

    return {
        "total": len(rows),
        "active": len(active),
        "inactive": len(inactive),
        "blocked": len(inactive),
        "online": len(online),
        "offline": len(offline),
        "new_joined": len(new_joined),
        "completed_orders": sum(_ad_safe_int(r.get("total_completed_orders")) for r in rows),
        "assigned_orders": sum(_ad_safe_int(r.get("currently_assigned_orders")) for r in rows),
        "review_count": sum(_ad_safe_int(r.get("rating_count")) for r in rows),
        "delivered_amount": _ad_money(sum(_ad_safe_float(r.get("delivered_amount")) for r in rows)),
    }


def _ad_top_deliverymen(limit=6):
    rows = _ad_delivery_rows()

    rows = sorted(
        rows,
        key=lambda row: (
            _ad_safe_int(row.get("total_completed_orders")),
            _ad_safe_float(row.get("rating")),
            _ad_safe_int(row.get("currently_assigned_orders"))
        ),
        reverse=True
    )

    return rows[:limit]


def _ad_delivery_review_rows():
    ratings = list(
        mongo.delivery_ratings.find({}).sort("created_at", -1)
    )

    rows = []

    for rating_doc in ratings:
        delivery_partner_id = rating_doc.get("delivery_partner_id")
        delivery_user = None

        if delivery_partner_id:
            delivery_user = mongo.users.find_one({"_id": _ad_object_id(delivery_partner_id)})
            if not delivery_user:
                delivery_user = mongo.users.find_one({"_id": delivery_partner_id})

        order_doc = None
        order_id = rating_doc.get("order_id")

        if order_id:
            order_doc = mongo.orders.find_one({"_id": order_id})

            if not order_doc:
                order_obj = _ad_object_id(order_id)
                if order_obj:
                    order_doc = mongo.orders.find_one({"_id": order_obj})

        customer_user = None
        customer_id = rating_doc.get("user_id") or (order_doc.get("user_id") if order_doc else "")

        if customer_id:
            customer_user = mongo.users.find_one({"_id": _ad_object_id(customer_id)})
            if not customer_user:
                customer_user = mongo.users.find_one({"_id": customer_id})

        rows.append({
            "id": str(rating_doc.get("_id")),
            "order_id": str(order_id or ""),
            "order_ref": str(order_doc.get("_id"))[-6:] if order_doc else str(order_id or "")[-6:],
            "delivery_partner_id": str(delivery_partner_id or ""),
            "delivery_name": delivery_user.get("name") if delivery_user else rating_doc.get("delivery_name") or "Delivery Partner",
            "delivery_phone": delivery_user.get("phone") if delivery_user else "",
            "delivery_phone_masked": _ad_mask_phone(delivery_user.get("phone") if delivery_user else ""),
            "customer_name": customer_user.get("name") if customer_user else rating_doc.get("customer_name") or "Customer",
            "rating": _ad_safe_float(rating_doc.get("rating")),
            "review": rating_doc.get("comment") or rating_doc.get("review") or rating_doc.get("message") or "",
            "created_at": rating_doc.get("created_at") or "",
            "created_at_display": _ad_datetime_display(rating_doc.get("created_at")),
            "raw": rating_doc,
        })

    return rows


def _ad_filter_review_rows(rows, delivery_id="", sort_by="", search=""):
    delivery_id = (delivery_id or "").strip()
    sort_by = (sort_by or "").strip()
    search = (search or "").strip().lower()

    filtered = rows

    if delivery_id:
        filtered = [
            r for r in filtered
            if str(r.get("delivery_partner_id")) == str(delivery_id)
        ]

    if search:
        clean = []

        for row in filtered:
            haystack = " ".join([
                str(row.get("order_id") or ""),
                str(row.get("order_ref") or ""),
                str(row.get("delivery_name") or ""),
                str(row.get("customer_name") or ""),
                str(row.get("review") or ""),
            ]).lower()

            if search in haystack:
                clean.append(row)

        filtered = clean

    if sort_by == "rating_high":
        filtered = sorted(filtered, key=lambda r: _ad_safe_float(r.get("rating")), reverse=True)
    elif sort_by == "rating_low":
        filtered = sorted(filtered, key=lambda r: _ad_safe_float(r.get("rating")))
    else:
        filtered = sorted(
            filtered,
            key=lambda r: _ad_parse_date(r.get("created_at")) or datetime.min,
            reverse=True
        )

    return filtered


def _ad_delivery_review_metrics(rows=None):
    if rows is None:
        rows = _ad_delivery_review_rows()

    total = len(rows)
    avg = round(sum(_ad_safe_float(r.get("rating")) for r in rows) / total, 1) if total else 0

    five_star = sum(1 for r in rows if _ad_safe_float(r.get("rating")) >= 5)
    positive = sum(1 for r in rows if _ad_safe_float(r.get("rating")) >= 4)

    return {
        "total": total,
        "avg_rating": avg,
        "five_star": five_star,
        "positive": positive,
    }


def _ad_delivery_csv_response(rows, filename):
    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "SL",
        "User ID",
        "Name",
        "Email",
        "Phone",
        "Zone",
        "Completed Orders",
        "Assigned Orders",
        "Availability",
        "Account Status",
        "Rating",
        "Reviews",
        "Created At"
    ])

    for idx, row in enumerate(rows, start=1):
        writer.writerow([
            idx,
            row.get("id", ""),
            row.get("name", ""),
            row.get("email", ""),
            row.get("phone", ""),
            row.get("zone", ""),
            row.get("total_completed_orders", 0),
            row.get("currently_assigned_orders", 0),
            row.get("availability_status", ""),
            "Active" if row.get("is_active") else "Disabled",
            row.get("rating", 0),
            row.get("rating_count", 0),
            row.get("created_at", "")
        ])

    data = output.getvalue().encode("utf-8")

    return send_file(
        io.BytesIO(data),
        mimetype="text/csv",
        as_attachment=True,
        download_name=filename
    )


def _ad_delivery_reviews_csv_response(rows, filename):
    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "SL",
        "Review ID",
        "Order ID",
        "Deliveryman",
        "Customer",
        "Rating",
        "Review",
        "Created At"
    ])

    for idx, row in enumerate(rows, start=1):
        writer.writerow([
            idx,
            row.get("id", ""),
            row.get("order_id", ""),
            row.get("delivery_name", ""),
            row.get("customer_name", ""),
            row.get("rating", 0),
            row.get("review", ""),
            row.get("created_at", "")
        ])

    data = output.getvalue().encode("utf-8")

    return send_file(
        io.BytesIO(data),
        mimetype="text/csv",
        as_attachment=True,
        download_name=filename
    )


@app.route('/admin/delivery')
@login_required(role='admin')
def admin_delivery_overview():
    delivery_rows = _ad_delivery_rows()
    metrics = _ad_delivery_metrics(delivery_rows)
    top_deliverymen = _ad_top_deliverymen(limit=6)

    active_deliverymen = [
        row for row in delivery_rows
        if row.get("is_active") and row.get("is_online")
    ]

    recent_deliverymen = delivery_rows[:8]

    return render_template(
        "admin_delivery_overview.html",
        user=current_user(),
        active_group="delivery",
        active_page="delivery_overview",
        metrics=metrics,
        delivery_rows=delivery_rows,
        active_deliverymen=active_deliverymen,
        top_deliverymen=top_deliverymen,
        recent_deliverymen=recent_deliverymen,
    )


@app.route('/admin/create-delivery', methods=['GET', 'POST'])
@login_required(role='admin')
def admin_create_delivery():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').lower().strip()
        phone_raw = request.form.get('phone', '').strip()
        password = request.form.get('password', '')

        phone = normalize_phone(phone_raw)

        if not name or not email or not phone or not password:
            flash("Please fill all required fields.", "error")
            return redirect(url_for('admin_create_delivery'))

        if len(password) < 6:
            flash("Password must be at least 6 characters.", "error")
            return redirect(url_for('admin_create_delivery'))

        existing = mongo.users.find_one({
            "$or": [
                {"email": email},
                {"phone": phone}
            ]
        })

        if existing:
            flash("Email or phone already exists. Use different details.", "error")
            return redirect(url_for('admin_create_delivery'))

        try:
            result = mongo.users.insert_one({
                "name": name,
                "email": email,
                "phone": phone,
                "password_hash": generate_password_hash(password),
                "role": "delivery",
                "phone_verified": 1,
                "is_active": 1,
                "created_at": datetime.utcnow().isoformat()
            })

            mongo.delivery_availability.update_one(
                {"user_id": str(result.inserted_id)},
                {
                    "$set": {
                        "user_id": str(result.inserted_id),
                        "active": False,
                        "zone": "Main Zone",
                        "created_at": datetime.utcnow().isoformat(),
                        "updated_at": datetime.utcnow().isoformat()
                    }
                },
                upsert=True
            )

        except DuplicateKeyError:
            flash("This email or phone is already registered. Please use different details.", "error")
            return redirect(url_for('admin_create_delivery'))
        except Exception as e:
            flash(f"Failed to create delivery partner: {str(e)}", "error")
            return redirect(url_for('admin_create_delivery'))

        flash("Delivery partner created.", "success")
        return redirect(url_for('admin_create_delivery'))

    return render_template(
        'admin_create_delivery.html',
        user=current_user(),
        active_group="delivery",
        active_page="create_delivery_person"
    )


@app.route('/admin/delivery/list')
@login_required(role='admin')
def admin_delivery_list():
    search = request.args.get("search", "").strip()
    status = request.args.get("status", "").strip()
    availability = request.args.get("availability", "").strip()

    rows = _ad_delivery_rows()
    rows = _ad_filter_delivery_rows(
        rows,
        search=search,
        status=status,
        availability=availability
    )

    metrics = _ad_delivery_metrics(rows)

    return render_template(
        "admin_delivery_list.html",
        user=current_user(),
        active_group="delivery",
        active_page="delivery_list",
        delivery_users=rows,
        deliverymen=rows,
        metrics=metrics,
        search=search,
        status=status,
        availability=availability,
    )


@app.route('/admin/delivery/reviews')
@login_required(role='admin')
def admin_delivery_reviews():
    delivery_id = request.args.get("delivery_id", "").strip()
    sort_by = request.args.get("sort_by", "").strip()
    search = request.args.get("search", "").strip()

    delivery_options = _ad_delivery_rows()

    rows = _ad_delivery_review_rows()
    rows = _ad_filter_review_rows(
        rows,
        delivery_id=delivery_id,
        sort_by=sort_by,
        search=search
    )

    metrics = _ad_delivery_review_metrics(rows)

    return render_template(
        "admin_delivery_reviews.html",
        user=current_user(),
        active_group="delivery",
        active_page="delivery_reviews",
        reviews=rows,
        delivery_reviews=rows,
        delivery_options=delivery_options,
        metrics=metrics,
        delivery_id=delivery_id,
        sort_by=sort_by,
        search=search,
    )


@app.route('/admin/delivery/export.csv')
@login_required(role='admin')
def admin_delivery_export_csv():
    search = request.args.get("search", "").strip()
    status = request.args.get("status", "").strip()
    availability = request.args.get("availability", "").strip()

    rows = _ad_delivery_rows()
    rows = _ad_filter_delivery_rows(
        rows,
        search=search,
        status=status,
        availability=availability
    )

    return _ad_delivery_csv_response(rows, "delivery_users.csv")


@app.route('/admin/delivery/reviews/export.csv')
@login_required(role='admin')
def admin_delivery_reviews_export_csv():
    delivery_id = request.args.get("delivery_id", "").strip()
    sort_by = request.args.get("sort_by", "").strip()
    search = request.args.get("search", "").strip()

    rows = _ad_delivery_review_rows()
    rows = _ad_filter_review_rows(
        rows,
        delivery_id=delivery_id,
        sort_by=sort_by,
        search=search
    )

    return _ad_delivery_reviews_csv_response(rows, "delivery_reviews.csv")




# ---- Enable/Disable/Delete/Export per-user ----
@app.route('/admin/users/<uid>/enable', methods=['POST'])
@login_required(role='admin')
def admin_user_enable(uid):
    try:
        uid_obj = ObjectId(uid)
    except Exception:
        flash("Invalid user.", "danger")
        return redirect(request.referrer or url_for("admin_users"))

    result = mongo.users.update_one(
        {"_id": uid_obj},
        {"$set": {"is_active": 1}}
    )

    if result.matched_count == 0:
        flash("User not found.", "warning")
    else:
        flash("User activated.", "success")

    return redirect(request.referrer or url_for("admin_users"))

@app.route('/admin/transactions.csv')
@login_required(role='admin')
def admin_transactions_csv():
    transactions = list(
        mongo.transactions.find({}).sort("created_at", -1)
    )

    csv_lines = ['txn_id,created_at,order_id,total_amount,amount,status']

    for t in transactions:
        order_id = t.get("order_id")
        order = None

        if order_id:
            order = mongo.orders.find_one({"_id": order_id})

        txn_id = str(t.get("_id", ""))
        created_at = t.get("created_at", "")
        order_id_str = str(order_id) if order_id else ""
        total_amount = float(order.get("total_amount") or 0) if order else 0
        amount = float(t.get("amount") or 0)
        status = t.get("status", "")

        csv_lines.append(
            f"{txn_id},{created_at},{order_id_str},{total_amount},{amount},{status}"
        )

    data = "\n".join(csv_lines).encode("utf-8")

    return send_file(
        io.BytesIO(data),
        mimetype="text/csv",
        as_attachment=True,
        download_name="transactions.csv"
    )


@app.route('/admin/users/<uid>/transactions.csv')
@login_required(role='admin')
def admin_user_transactions_csv(uid):
    uid_str = str(uid)

    user_orders = list(mongo.orders.find({
        "$or": [
            {"user_id": uid_str},
            {"delivery_partner_id": uid_str}
        ]
    }))

    try:
        uid_obj = ObjectId(uid_str)
    except Exception:
        uid_obj = None

    if uid_obj:
        store = mongo.stores.find_one({"user_id": uid_str})
        if store:
            store_orders = list(mongo.orders.find({"store_id": store["_id"]}))
            user_orders.extend(store_orders)

    seen_order_ids = set()
    order_ids = []

    for order in user_orders:
        oid = order["_id"]
        if str(oid) not in seen_order_ids:
            seen_order_ids.add(str(oid))
            order_ids.append(oid)

    csv_lines = ["txn_id,created_at,order_id,amount,status"]

    if order_ids:
        rows = list(mongo.transactions.find({
            "order_id": {"$in": order_ids}
        }).sort("created_at", -1))

        for r in rows:
            csv_lines.append(
                f"{str(r.get('_id'))},"
                f"{r.get('created_at', '')},"
                f"{str(r.get('order_id', ''))},"
                f"{r.get('amount', 0)},"
                f"{r.get('status', '')}"
            )

    data = "\n".join(csv_lines).encode("utf-8")

    return send_file(
        io.BytesIO(data),
        mimetype="text/csv",
        as_attachment=True,
        download_name=f"user_{uid_str}_transactions.csv"
    )


@app.route('/admin/users/<uid>/export', methods=['GET'])
@login_required(role='admin')
def admin_user_export(uid):
    u = get_user_by_id(uid)
    if not u:
        flash('User not found.','warning')
        return redirect(url_for('admin_users'))
    try:
        data = render_export_to_csv_zip_bytes(uid)
    except Exception as e:
        flash(f'Failed to prepare export: {e}', 'danger')
        return redirect(url_for('admin_users'))
    fn = f"user_{uid}_export_{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}.zip"
    return send_file(BytesIO(data), mimetype='application/zip', as_attachment=True, download_name=fn)


@app.route('/admin/users/<uid>/export.zip', methods=['GET'])
@login_required(role='admin')
def admin_user_export_zip(uid):
    return admin_user_export(uid)


@app.route('/admin/users/<uid>/delete-hard', methods=['POST'])
@login_required(role='admin')
def admin_user_delete_hard(uid):
    ok, reason = can_delete_user_hard(uid)
    if not ok:
        flash(f'Cannot hard-delete: {reason}. The account should remain or be disabled.', 'warning')
        return redirect(request.referrer or url_for('admin_users'))
    try:
        if hard_delete_user(uid):
            flash('User hard-deleted.','success')
        else:
            flash('Hard delete failed.','danger')
    except Exception as e:
        flash(f'Hard delete failed: {e}','danger')
    return redirect(request.referrer or url_for('admin_users'))


@app.route('/admin/complaints')
@login_required(role='admin')
def admin_complaints():
    complaints = list_recent_complaints(limit=200)
    return render_template('admin_complaints.html', user=current_user(), complaints=complaints)

@app.route('/admin/complaints/<int:cid>/status', methods=['POST'])
@login_required(role='admin')
def admin_complaint_set_status(cid):
    status = request.form.get('status','OPEN')
    try:
        update_complaint_status(cid, status)
        flash('Complaint status updated.','success')
    except Exception as e:
        flash(f'Failed to update: {e}','danger')
    return redirect(url_for('admin_complaints'))

# =========================================================
# ADMIN USER MANAGEMENT HELPERS + ROUTES
# =========================================================

def _au_now():
    return datetime.utcnow()


def _au_iso_now():
    return datetime.utcnow().isoformat()


def _au_safe_float(value, default=0.0):
    try:
        if value is None or str(value).strip() == "":
            return default
        return float(value)
    except Exception:
        return default


def _au_safe_int(value, default=0):
    try:
        if value is None or str(value).strip() == "":
            return default
        return int(value)
    except Exception:
        return default


def _au_money(value):
    return round(_au_safe_float(value, 0), 2)


def _au_object_id(value):
    try:
        return ObjectId(str(value))
    except Exception:
        return None


def _au_is_active(doc):
    return 1 if doc and doc.get("is_active") in [1, True, "1", "true", "True", "yes", "Yes"] else 0


def _au_parse_date(value):
    if not value:
        return None

    if isinstance(value, datetime):
        return value

    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)

    try:
        clean = str(value).replace("Z", "").strip()
        return datetime.fromisoformat(clean)
    except Exception:
        return None


def _au_date_display(value):
    dt = _au_parse_date(value)

    if not dt:
        return value or ""

    try:
        return dt.strftime("%d %b %Y")
    except Exception:
        return value or ""


def _au_datetime_display(value):
    dt = _au_parse_date(value)

    if not dt:
        return value or ""

    try:
        return dt.strftime("%d %b %Y, %I:%M %p")
    except Exception:
        return value or ""


def _au_month_key(value):
    dt = _au_parse_date(value)
    if not dt:
        return None
    return dt.strftime("%b")


def _au_created_in_last_days(value, days=30):
    dt = _au_parse_date(value)
    if not dt:
        return False
    return dt >= (_au_now() - timedelta(days=days))


def _au_mask_email(email):
    email = (email or "").strip()

    if not email or "@" not in email:
        return email

    name, domain = email.split("@", 1)

    if len(name) <= 2:
        return name[0:1] + "*****@" + domain

    return name[0:1] + "*****" + name[-1:] + "@" + domain


def _au_mask_phone(phone):
    phone = (phone or "").strip()

    if not phone:
        return ""

    digits = "".join(ch for ch in phone if ch.isdigit())

    if len(digits) <= 4:
        return phone

    return "+" + "*" * max(4, len(digits) - 4) + digits[-4:]


def _au_user_base_row(user_doc):
    user_doc = user_doc or {}

    uid = str(user_doc.get("_id", ""))

    return {
        "id": uid,
        "uid": uid,
        "name": user_doc.get("name") or "User",
        "email": user_doc.get("email") or "",
        "phone": user_doc.get("phone") or "",
        "email_masked": _au_mask_email(user_doc.get("email") or ""),
        "phone_masked": _au_mask_phone(user_doc.get("phone") or ""),
        "role": user_doc.get("role") or "customer",
        "is_active": _au_is_active(user_doc),
        "phone_verified": 1 if user_doc.get("phone_verified") in [1, True, "1", "true", "True"] else 0,
        "created_at": user_doc.get("created_at") or "",
        "created_at_display": _au_date_display(user_doc.get("created_at")),
        "created_at_full": _au_datetime_display(user_doc.get("created_at")),
        "raw": user_doc,
    }


def _au_find_store_for_user(user_id):
    uid = str(user_id)

    store = mongo.stores.find_one({"user_id": uid})

    if store:
        return store

    uid_obj = _au_object_id(uid)
    if uid_obj:
        store = mongo.stores.find_one({"user_id": uid_obj})

    return store


def _au_store_order_query(store_id):
    if not store_id:
        return {"_id": {"$exists": False}}

    return {
        "$or": [
            {"store_id": store_id},
            {"store_id": str(store_id)}
        ]
    }


def _au_user_order_query(user_id):
    uid = str(user_id)

    return {
        "$or": [
            {"user_id": uid},
            {"customer_id": uid},
            {"delivery_partner_id": uid}
        ]
    }


def _au_order_total(order_doc):
    if not order_doc:
        return 0.0

    if order_doc.get("total_payable") is not None:
        return _au_safe_float(order_doc.get("total_payable"))

    total_amount = _au_safe_float(order_doc.get("total_amount"))
    delivery_fee = _au_safe_float(order_doc.get("delivery_fee"))
    tip_amount = _au_safe_float(order_doc.get("tip_amount"))

    return total_amount + delivery_fee + tip_amount


def _au_order_is_delivered(order_doc):
    status = str(order_doc.get("status") or "").upper()
    return status in ["DELIVERED", "COMPLETED", "ORDER_DELIVERED"]


def _au_user_orders(user_doc):
    if not user_doc:
        return []

    uid = str(user_doc.get("_id"))
    role = user_doc.get("role")

    orders = list(mongo.orders.find(_au_user_order_query(uid)).sort("created_at", -1))

    if role == "store":
        store = _au_find_store_for_user(uid)

        if store:
            store_orders = list(
                mongo.orders.find(_au_store_order_query(store["_id"])).sort("created_at", -1)
            )
            orders.extend(store_orders)

    seen = set()
    clean_orders = []

    for order in orders:
        oid = str(order.get("_id"))

        if oid in seen:
            continue

        seen.add(oid)
        clean_orders.append(order)

    return clean_orders


def _au_user_order_summary(user_doc):
    orders = _au_user_orders(user_doc)

    total_orders = len(orders)
    delivered_orders = sum(1 for order in orders if _au_order_is_delivered(order))
    total_amount = sum(_au_order_total(order) for order in orders)
    delivered_amount = sum(_au_order_total(order) for order in orders if _au_order_is_delivered(order))

    return {
        "orders": total_orders,
        "delivered_orders": delivered_orders,
        "total_amount": _au_money(total_amount),
        "delivered_amount": _au_money(delivered_amount),
    }


def _au_rating_summary(collection_name, filter_query):
    try:
        rows = list(getattr(mongo, collection_name).find(filter_query))
    except Exception:
        rows = []

    count = len(rows)
    total = 0.0

    for row in rows:
        total += _au_safe_float(row.get("rating"))

    avg = round(total / count, 1) if count else 0

    return {
        "avg": avg,
        "count": count
    }


def _au_store_rating_summary(store_id):
    if not store_id:
        return {"avg": 0, "count": 0}

    return _au_rating_summary(
        "store_ratings",
        {
            "$or": [
                {"store_id": store_id},
                {"store_id": str(store_id)}
            ]
        }
    )


def _au_delivery_rating_summary(user_id):
    uid = str(user_id)

    return _au_rating_summary(
        "delivery_ratings",
        {
            "$or": [
                {"delivery_partner_id": uid},
                {"delivery_partner_id": _au_object_id(uid)}
            ]
        }
    )


def _au_product_count_for_store(store_id):
    if not store_id:
        return 0

    return mongo.products.count_documents(
        {
            "$or": [
                {"store_id": store_id},
                {"store_id": str(store_id)}
            ]
        }
    )


def _au_active_products_for_store(store_id):
    if not store_id:
        return 0

    return mongo.products.count_documents(
        {
            "$and": [
                {
                    "$or": [
                        {"store_id": store_id},
                        {"store_id": str(store_id)}
                    ]
                },
                {
                    "$or": [
                        {"is_active": 1},
                        {"is_active": True},
                        {"is_active": {"$exists": False}}
                    ]
                }
            ]
        }
    )


def _au_delivery_availability(user_id):
    uid = str(user_id)

    row = mongo.delivery_availability.find_one({"user_id": uid})

    if not row:
        row = mongo.delivery_availability.find_one({"delivery_partner_id": uid})

    if not row:
        row = {}

    return row


def _au_delivery_assigned_orders(user_id):
    uid = str(user_id)

    return mongo.orders.count_documents({
        "delivery_partner_id": uid,
        "status": {
            "$in": [
                "ASSIGNED_TO_DELIVERY",
                "OUT_FOR_DELIVERY",
                "ACCEPTED_BY_DELIVERY_MAN",
                "PICKED_UP"
            ]
        }
    })


def _au_role_users(role):
    return list(mongo.users.find({"role": role}).sort("created_at", -1))


def _au_all_users():
    return list(mongo.users.find({}).sort("created_at", -1))


def _au_customer_rows():
    rows = []

    for user_doc in _au_role_users("customer"):
        base = _au_user_base_row(user_doc)
        summary = _au_user_order_summary(user_doc)

        base.update({
            "total_order": summary["orders"],
            "total_order_amount": summary["total_amount"],
            "joining_date": base["created_at_display"],
            "joining_date_raw": base["created_at"],
        })

        rows.append(base)

    return rows


def _au_delivery_user_rows():
    rows = []

    for user_doc in _au_role_users("delivery"):
        base = _au_user_base_row(user_doc)
        summary = _au_user_order_summary(user_doc)
        rating = _au_delivery_rating_summary(base["id"])
        availability = _au_delivery_availability(base["id"])

        is_online = 1 if availability.get("active") in [1, True, "1", "true", "True"] else 0

        base.update({
            "total_completed_orders": summary["delivered_orders"],
            "total_orders": summary["orders"],
            "rating": rating["avg"],
            "rating_count": rating["count"],
            "currently_assigned_orders": _au_delivery_assigned_orders(base["id"]),
            "availability_status": "Online" if is_online else "Offline",
            "is_online": is_online,
            "zone": availability.get("zone") or availability.get("area") or "Default Zone",
            "latitude": availability.get("latitude"),
            "longitude": availability.get("longitude"),
        })

        rows.append(base)

    return rows


def _au_store_user_rows():
    rows = []

    for user_doc in _au_role_users("store"):
        base = _au_user_base_row(user_doc)
        store = _au_find_store_for_user(base["id"])

        store_id = store.get("_id") if store else None
        store_orders = list(mongo.orders.find(_au_store_order_query(store_id)).sort("created_at", -1)) if store_id else []

        total_orders = len(store_orders)
        delivered_orders = sum(1 for order in store_orders if _au_order_is_delivered(order))
        revenue = sum(_au_order_total(order) for order in store_orders if _au_order_is_delivered(order))
        rating = _au_store_rating_summary(store_id)

        base.update({
            "store_id": str(store_id) if store_id else "",
            "store_name": store.get("store_name") if store else base["name"],
            "address": store.get("address") if store else "",
            "store_is_active": _au_is_active(store) if store else base["is_active"],
            "products": _au_product_count_for_store(store_id),
            "active_products": _au_active_products_for_store(store_id),
            "orders": total_orders,
            "delivered_orders": delivered_orders,
            "revenue": _au_money(revenue),
            "rating": rating["avg"],
            "rating_count": rating["count"],
        })

        rows.append(base)

    return rows


def _au_user_overview_data():
    users = _au_all_users()

    customers = [u for u in users if u.get("role") == "customer"]
    delivery_users = [u for u in users if u.get("role") == "delivery"]
    store_users = [u for u in users if u.get("role") == "store"]
    admins = [u for u in users if u.get("role") == "admin"]

    active_customers = [u for u in customers if _au_is_active(u)]
    inactive_customers = [u for u in customers if not _au_is_active(u)]
    new_customers = [u for u in customers if _au_created_in_last_days(u.get("created_at"), 30)]

    active_delivery = [u for u in delivery_users if _au_is_active(u)]
    inactive_delivery = [u for u in delivery_users if not _au_is_active(u)]
    new_delivery = [u for u in delivery_users if _au_created_in_last_days(u.get("created_at"), 30)]

    active_stores = [u for u in store_users if _au_is_active(u)]
    inactive_stores = [u for u in store_users if not _au_is_active(u)]
    new_stores = [u for u in store_users if _au_created_in_last_days(u.get("created_at"), 30)]

    current_year = datetime.utcnow().year
    month_labels = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    customer_growth = {m: 0 for m in month_labels}

    for user_doc in customers:
        created = _au_parse_date(user_doc.get("created_at"))

        if created and created.year == current_year:
            customer_growth[created.strftime("%b")] += 1

    rating_rows = list(mongo.store_ratings.find({}))
    rating_count = len(rating_rows)

    positive = 0
    good = 0
    neutral = 0
    negative = 0

    for row in rating_rows:
        rating_value = _au_safe_float(row.get("rating"))

        if rating_value >= 4:
            positive += 1
        elif rating_value >= 3:
            good += 1
        elif rating_value >= 2:
            neutral += 1
        else:
            negative += 1

    def pct(count):
        if rating_count <= 0:
            return 0
        return round((count / rating_count) * 100)

    delivery_rows = _au_delivery_user_rows()
    top_deliverymen = sorted(
        delivery_rows,
        key=lambda row: (
            _au_safe_int(row.get("total_completed_orders")),
            _au_safe_float(row.get("rating"))
        ),
        reverse=True
    )[:6]

    store_rows = _au_store_user_rows()
    top_store_users = sorted(
        store_rows,
        key=lambda row: (
            _au_safe_float(row.get("revenue")),
            _au_safe_int(row.get("orders"))
        ),
        reverse=True
    )[:6]

    recent_users = []

    for user_doc in users[:10]:
        base = _au_user_base_row(user_doc)
        recent_users.append(base)

    metrics = {
        "total_users": len(users),
        "total_admins": len(admins),

        "total_customers": len(customers),
        "active_customers": len(active_customers),
        "new_customers": len(new_customers),
        "blocked_customers": len(inactive_customers),

        "total_delivery_users": len(delivery_users),
        "active_delivery_users": len(active_delivery),
        "new_delivery_users": len(new_delivery),
        "inactive_delivery_users": len(inactive_delivery),
        "blocked_delivery_users": len(inactive_delivery),

        "total_store_users": len(store_users),
        "active_store_users": len(active_stores),
        "new_store_users": len(new_stores),
        "inactive_store_users": len(inactive_stores),

        "review_received": rating_count,
        "positive_pct": pct(positive),
        "good_pct": pct(good),
        "neutral_pct": pct(neutral),
        "negative_pct": pct(negative),
    }

    return {
        "metrics": metrics,
        "month_labels": month_labels,
        "customer_growth_values": [customer_growth[m] for m in month_labels],
        "top_deliverymen": top_deliverymen,
        "top_store_users": top_store_users,
        "recent_users": recent_users,
        "current_year": current_year,
    }


def _au_filter_rows_by_search(rows, search):
    search = (search or "").strip().lower()

    if not search:
        return rows

    filtered = []

    for row in rows:
        haystack = " ".join([
            str(row.get("name") or ""),
            str(row.get("email") or ""),
            str(row.get("phone") or ""),
            str(row.get("store_name") or ""),
            str(row.get("address") or ""),
            str(row.get("role") or ""),
        ]).lower()

        if search in haystack:
            filtered.append(row)

    return filtered


def _au_filter_rows_by_status(rows, status):
    status = (status or "").strip().lower()

    if status not in ["active", "inactive", "disabled"]:
        return rows

    if status == "active":
        return [row for row in rows if _au_safe_int(row.get("is_active")) == 1]

    return [row for row in rows if _au_safe_int(row.get("is_active")) == 0]


def _au_export_users_csv_response(rows, filename):
    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "SL",
        "User ID",
        "Name",
        "Email",
        "Phone",
        "Role",
        "Status",
        "Created At",
        "Total Orders",
        "Total Amount"
    ])

    for idx, row in enumerate(rows, start=1):
        writer.writerow([
            idx,
            row.get("id", ""),
            row.get("name", ""),
            row.get("email", ""),
            row.get("phone", ""),
            row.get("role", ""),
            "Active" if row.get("is_active") else "Disabled",
            row.get("created_at", ""),
            row.get("total_order") or row.get("orders") or row.get("total_orders") or 0,
            row.get("total_order_amount") or row.get("revenue") or 0,
        ])

    data = output.getvalue().encode("utf-8")

    return send_file(
        io.BytesIO(data),
        mimetype="text/csv",
        as_attachment=True,
        download_name=filename
    )


@app.route('/admin/users')
@login_required(role='admin')
def admin_users():
    data = _au_user_overview_data()

    return render_template(
        "admin_users_overview.html",
        user=current_user(),
        active_group="users",
        active_page="users_overview",
        metrics=data["metrics"],
        month_labels=data["month_labels"],
        customer_growth_values=data["customer_growth_values"],
        top_deliverymen=data["top_deliverymen"],
        top_store_users=data["top_store_users"],
        recent_users=data["recent_users"],
        current_year=data["current_year"],
    )


@app.route('/admin/users/store-users')
@login_required(role='admin')
def admin_store_users():
    search = request.args.get("search", "").strip()
    status = request.args.get("status", "").strip()

    rows = _au_store_user_rows()
    rows = _au_filter_rows_by_status(rows, status)
    rows = _au_filter_rows_by_search(rows, search)

    metrics = {
        "total": len(rows),
        "active": sum(1 for row in rows if row.get("is_active")),
        "disabled": sum(1 for row in rows if not row.get("is_active")),
        "total_orders": sum(_au_safe_int(row.get("orders")) for row in rows),
        "total_revenue": _au_money(sum(_au_safe_float(row.get("revenue")) for row in rows)),
        "total_products": sum(_au_safe_int(row.get("products")) for row in rows),
    }

    return render_template(
        "admin_store_users.html",
        user=current_user(),
        active_group="users",
        active_page="store_users",
        store_users=rows,
        users=rows,
        metrics=metrics,
        search=search,
        status=status,
    )


@app.route('/admin/users/delivery-users')
@login_required(role='admin')
def admin_delivery_users():
    search = request.args.get("search", "").strip()
    status = request.args.get("status", "").strip()
    availability = request.args.get("availability", "").strip().lower()

    rows = _au_delivery_user_rows()
    rows = _au_filter_rows_by_status(rows, status)
    rows = _au_filter_rows_by_search(rows, search)

    if availability == "online":
        rows = [row for row in rows if row.get("is_online")]
    elif availability == "offline":
        rows = [row for row in rows if not row.get("is_online")]

    metrics = {
        "total": len(rows),
        "active": sum(1 for row in rows if row.get("is_active")),
        "disabled": sum(1 for row in rows if not row.get("is_active")),
        "online": sum(1 for row in rows if row.get("is_online")),
        "offline": sum(1 for row in rows if not row.get("is_online")),
        "completed_orders": sum(_au_safe_int(row.get("total_completed_orders")) for row in rows),
        "assigned_orders": sum(_au_safe_int(row.get("currently_assigned_orders")) for row in rows),
    }

    return render_template(
        "admin_delivery_users.html",
        user=current_user(),
        active_group="users",
        active_page="delivery_users",
        delivery_users=rows,
        users=rows,
        metrics=metrics,
        search=search,
        status=status,
        availability=availability,
    )


@app.route('/admin/users/customers')
@login_required(role='admin')
def admin_customers():
    search = request.args.get("search", "").strip()
    status = request.args.get("status", "").strip()
    sort_by = request.args.get("sort_by", "").strip()
    limit_raw = request.args.get("limit", "").strip()

    rows = _au_customer_rows()
    rows = _au_filter_rows_by_status(rows, status)
    rows = _au_filter_rows_by_search(rows, search)

    if sort_by == "orders_desc":
        rows = sorted(rows, key=lambda row: _au_safe_int(row.get("total_order")), reverse=True)
    elif sort_by == "amount_desc":
        rows = sorted(rows, key=lambda row: _au_safe_float(row.get("total_order_amount")), reverse=True)
    elif sort_by == "joining_new":
        rows = sorted(rows, key=lambda row: _au_parse_date(row.get("created_at")) or datetime.min, reverse=True)
    elif sort_by == "joining_old":
        rows = sorted(rows, key=lambda row: _au_parse_date(row.get("created_at")) or datetime.min)

    if limit_raw:
        try:
            limit = int(limit_raw)
            if limit > 0:
                rows = rows[:limit]
        except Exception:
            pass

    metrics = {
        "total": len(rows),
        "active": sum(1 for row in rows if row.get("is_active")),
        "disabled": sum(1 for row in rows if not row.get("is_active")),
        "total_orders": sum(_au_safe_int(row.get("total_order")) for row in rows),
        "total_amount": _au_money(sum(_au_safe_float(row.get("total_order_amount")) for row in rows)),
    }

    return render_template(
        "admin_customers.html",
        user=current_user(),
        active_group="users",
        active_page="customers",
        customers=rows,
        users=rows,
        metrics=metrics,
        search=search,
        status=status,
        sort_by=sort_by,
        limit=limit_raw,
    )


@app.route('/admin/users/export.csv')
@login_required(role='admin')
def admin_users_export_csv():
    role = (request.args.get("role") or "").strip().lower()

    if role == "store":
        rows = _au_store_user_rows()
        filename = "store_users.csv"
    elif role == "delivery":
        rows = _au_delivery_user_rows()
        filename = "delivery_users.csv"
    elif role == "customer":
        rows = _au_customer_rows()
        filename = "customers.csv"
    else:
        rows = [_au_user_base_row(user_doc) for user_doc in _au_all_users()]
        filename = "users.csv"

    return _au_export_users_csv_response(rows, filename)



@app.route('/admin/users/<uid>/disable', methods=['POST'])
@login_required(role='admin')
def admin_user_disable(uid):
    try:
        uid_obj = ObjectId(uid)
    except Exception:
        flash("Invalid user.", "danger")
        return redirect(request.referrer or url_for("admin_users"))

    mongo.users.update_one(
        {"_id": uid_obj},
        {"$set": {"is_active": 0}}
    )

    flash("User disabled.", "info")
    return redirect(request.referrer or url_for("admin_users"))


@app.route('/admin/users/<uid>/delete', methods=['POST'])
@login_required(role='admin')
def admin_user_delete(uid):
    try:
        uid_obj = ObjectId(uid)
    except Exception:
        flash("Invalid user.", "danger")
        return redirect(request.referrer or url_for("admin_users"))

    udoc = mongo.users.find_one({"_id": uid_obj})

    if not udoc:
        flash("User not found.", "warning")
        return redirect(request.referrer or url_for("admin_users"))

    role = udoc.get("role")

    if role == "admin":
        flash("Refused to delete admin via UI.", "danger")
        return redirect(request.referrer or url_for("admin_users"))

    uid_str = str(uid_obj)

    if role == "store":
        store = mongo.stores.find_one({"user_id": uid_str})
        sid = store["_id"] if store else None

        order_cnt = mongo.orders.count_documents({"store_id": sid}) if sid else 0

        if order_cnt > 0:
            mongo.users.update_one({"_id": uid_obj}, {"$set": {"is_active": 0}})
            flash("Store has orders; user disabled instead of hard delete.", "warning")
            return redirect(request.referrer or url_for("admin_users"))

        if sid:
            mongo.products.delete_many({"store_id": sid})
            mongo.stores.delete_one({"_id": sid})

        mongo.users.delete_one({"_id": uid_obj})
        flash("Store user removed.", "success")
        return redirect(request.referrer or url_for("admin_users"))

    if role == "customer":
        order_cnt = mongo.orders.count_documents({"user_id": uid_str})

        if order_cnt > 0:
            mongo.users.update_one({"_id": uid_obj}, {"$set": {"is_active": 0}})
            flash("Customer has orders; user disabled instead of hard delete.", "warning")
            return redirect(request.referrer or url_for("admin_users"))

        mongo.addresses.delete_many({"user_id": uid_str})
        mongo.users.delete_one({"_id": uid_obj})
        flash("Customer removed.", "success")
        return redirect(request.referrer or url_for("admin_users"))

    if role == "delivery":
        order_cnt = mongo.orders.count_documents({"delivery_partner_id": uid_str})

        if order_cnt > 0:
            mongo.users.update_one({"_id": uid_obj}, {"$set": {"is_active": 0}})
            flash("Delivery partner has order history; user disabled.", "warning")
            return redirect(request.referrer or url_for("admin_users"))

        mongo.users.delete_one({"_id": uid_obj})
        flash("Delivery partner removed.", "success")
        return redirect(request.referrer or url_for("admin_users"))

    mongo.users.delete_one({"_id": uid_obj})
    flash("User removed.", "success")
    return redirect(request.referrer or url_for("admin_users"))

# ----------------------
# STORE
# ----------------------
@app.route('/store/dashboard')
@login_required(role='store')
def store_dashboard():
    u, store = _get_current_store_or_redirect()

    if not store:
        flash("Store not found.", "danger")
        return redirect(url_for("login"))

    page_context = _build_store_split_page_context(store)

    return render_template(
        "store_dashboard.html",
        user=u,
        store=store,
        **page_context
    )


@app.route('/store/delivered-orders')
@login_required(role='store')
def store_delivered_orders():
    """Show all delivered orders for this store."""
    u = current_user()

    store = mongo.stores.find_one({"user_id": u["id"]})

    if not store:
        flash("Store not found.", "danger")
        return redirect(url_for("store_dashboard"))

    delivered_docs = list(
        mongo.orders.find({
            "store_id": store["_id"],
            "status": "DELIVERED"
        }).sort("created_at", -1)
    )

    delivered = []

    for o in delivered_docs:
        customer = None

        if o.get("user_id"):
            try:
                customer = mongo.users.find_one({"_id": ObjectId(o.get("user_id"))})
            except Exception:
                customer = None

        addr = mongo.order_addresses.find_one({"order_id": o["_id"]})

        row = dict(o)
        row["id"] = str(o["_id"])
        row["customer_name"] = customer.get("name") if customer else o.get("customer_name", "")
        row["customer_phone"] = customer.get("phone") if customer else o.get("customer_phone", "")

        row["addr_line1"] = addr.get("line1") if addr else ""
        row["addr_line2"] = addr.get("line2") if addr else ""
        row["addr_city"] = addr.get("city") if addr else ""
        row["addr_state"] = addr.get("state") if addr else ""
        row["addr_pincode"] = addr.get("pincode") if addr else ""
        row["addr_lat"] = addr.get("latitude") if addr else None
        row["addr_lng"] = addr.get("longitude") if addr else None

        row["total_amount"] = float(o.get("total_amount") or 0)
        row["delivery_fee"] = float(o.get("delivery_fee") or 0)
        row["tip_amount"] = float(o.get("tip_amount") or 0)
        row["total_payable"] = (
            float(o.get("total_amount") or 0)
            + float(o.get("delivery_fee") or 0)
            + float(o.get("tip_amount") or 0)
        )

        delivered.append(row)

    store_view = dict(store)
    store_view["id"] = str(store["_id"])

    return render_template(
        "store_delivered_orders.html",
        user=u,
        store=store_view,
        orders=delivered
    )

# =========================================================
# STORE SPLIT PAGE HELPERS
# =========================================================

def _get_current_store_or_redirect():
    u = current_user()
    store = mongo.stores.find_one({"user_id": u["id"]})

    if not store:
        flash("Store not found.", "danger")
        return u, None

    store_view = dict(store)
    store_view["id"] = str(store["_id"])

    return u, store_view


def _get_store_products(store_id):
    products = list(
        mongo.products.find({"store_id": store_id}).sort("created_at", -1)
    )

    for p in products:
        p["id"] = str(p["_id"])
        p["store_id"] = str(p.get("store_id")) if p.get("store_id") else ""

    return products


def _get_store_orders(store_id):
    orders = list(
        mongo.orders.find({"store_id": store_id}).sort("created_at", -1)
    )

    hydrated = []

    for o in orders:
        row = dict(o)
        row["id"] = str(o["_id"])

        customer = None
        if o.get("user_id"):
            try:
                customer = mongo.users.find_one({"_id": ObjectId(o.get("user_id"))})
            except Exception:
                customer = None

        addr = mongo.order_addresses.find_one({"order_id": o["_id"]})

        row["customer_name"] = customer.get("name") if customer else o.get("customer_name", "")
        row["customer_phone"] = customer.get("phone") if customer else o.get("customer_phone", "")

        row["addr_line1"] = addr.get("line1") if addr else ""
        row["addr_line2"] = addr.get("line2") if addr else ""
        row["addr_city"] = addr.get("city") if addr else ""
        row["addr_state"] = addr.get("state") if addr else ""
        row["addr_pincode"] = addr.get("pincode") if addr else ""
        row["addr_lat"] = addr.get("latitude") if addr else None
        row["addr_lng"] = addr.get("longitude") if addr else None

        row["total_amount"] = float(o.get("total_amount") or 0)
        row["delivery_fee"] = float(o.get("delivery_fee") or 0)
        row["tip_amount"] = float(o.get("tip_amount") or 0)
        row["total_payable"] = (
            float(o.get("total_amount") or 0)
            + float(o.get("delivery_fee") or 0)
            + float(o.get("tip_amount") or 0)
        )

        hydrated.append(row)

    return hydrated



def _save_store_category_image(file_obj, store_id, category_id_prefix="category"):
    if not file_obj or not file_obj.filename:
        return ""

    if not allowed_file(file_obj.filename):
        return ""

    original_name = secure_filename(file_obj.filename)
    ext = original_name.rsplit(".", 1)[1].lower()

    stored_name = (
        "store_category_"
        + str(store_id)
        + "_"
        + str(category_id_prefix)
        + "_"
        + datetime.utcnow().strftime("%Y%m%d%H%M%S_")
        + secrets.token_hex(6)
        + "."
        + ext
    )

    category_folder = os.path.join(app.config["UPLOAD_FOLDER"], "store_categories")
    os.makedirs(category_folder, exist_ok=True)

    file_obj.save(os.path.join(category_folder, stored_name))

    return "uploads/store_categories/" + stored_name


# =========================================================
# STORE CATEGORY HELPERS
# =========================================================

DEFAULT_STORE_CATEGORIES = [
    {
        "name": "Fresh cuts",
        "sub_categories": ["Curry cuts", "Boneless & Mince", "Offals"],
    },
    {
        "name": "Ready to cook",
        "sub_categories": [],
    },
    {
        "name": "Spices",
        "sub_categories": [],
    },
]


def _category_slug(name):
    name = (name or "").strip().lower()
    name = re.sub(r"[^a-z0-9]+", "-", name)
    return name.strip("-")


def _ensure_store_categories(store_id):
    existing_count = mongo.store_categories.count_documents({
        "store_id": store_id
    })

    if existing_count > 0:
        return

    now = datetime.utcnow().isoformat()

    docs = []
    for cat in DEFAULT_STORE_CATEGORIES:
        docs.append({
            "store_id": store_id,
            "name": cat["name"],
            "slug": _category_slug(cat["name"]),
            "sub_categories": cat.get("sub_categories", []),
            "image_path": "",
            "category_image_path": "",
            "emoji": "🛒",
            "is_active": 1,
            "is_default": 1,
            "created_at": now,
            "updated_at": now,
})

    if docs:
        mongo.store_categories.insert_many(docs)


def _get_store_categories(store_id, active_only=False):
    _ensure_store_categories(store_id)

    query = {"store_id": store_id}

    if active_only:
        query["is_active"] = 1

    categories = list(
        mongo.store_categories.find(query).sort([
            ("is_active", -1),
            ("name", 1)
        ])
    )

    for cat in categories:
        cat["id"] = str(cat["_id"])

    return categories


def _get_store_category_by_id(store_id, category_id, active_only=False):
    try:
        category_obj_id = ObjectId(category_id)
    except Exception:
        return None

    query = {
        "_id": category_obj_id,
        "store_id": store_id
    }

    if active_only:
        query["is_active"] = 1

    cat = mongo.store_categories.find_one(query)

    if cat:
        cat["id"] = str(cat["_id"])

    return cat


def _get_store_category_by_name(store_id, name, active_only=False):
    slug = _category_slug(name)

    query = {
        "store_id": store_id,
        "slug": slug
    }

    if active_only:
        query["is_active"] = 1

    cat = mongo.store_categories.find_one(query)

    if cat:
        cat["id"] = str(cat["_id"])

    return cat


def _get_category_product_count(store_id, category_name):
    return mongo.products.count_documents({
        "store_id": store_id,
        "category": category_name
    })

# =========================================================
# STORE SPLIT PAGES
# =========================================================

def _build_store_split_page_context(store):
    sid = store["_id"]

    products = _get_store_products(sid)
    orders = _get_store_orders(sid)

    delivered_orders = [
        o for o in orders
        if (o.get("status") or "").upper() == "DELIVERED"
    ]

    delivered_order_ids = [
        o.get("_id") for o in delivered_orders
        if o.get("_id")
    ]

    paid_transactions = []

    if delivered_order_ids:
        paid_transactions = list(mongo.transactions.find({
            "order_id": {"$in": delivered_order_ids},
            "status": "PAID"
        }))

    gmv_total = sum(
        float(o.get("total_amount") or 0)
        + float(o.get("delivery_fee") or 0)
        + float(o.get("tip_amount") or 0)
        for o in delivered_orders
    )

    paid_total = sum(
    float(t.get("amount") or 0)
    for t in paid_transactions
)

    if not paid_transactions and delivered_orders:
        paid_total = sum(
        float(o.get("total_amount") or 0)
        + float(o.get("delivery_fee") or 0)
        + float(o.get("tip_amount") or 0)
        for o in delivered_orders
    )

    unique_customers = len(set([
        str(o.get("user_id"))
        for o in orders
        if o.get("user_id")
    ]))

    accepted_by_delivery = sum(
        1 for o in orders
        if (o.get("status") or "").upper() in {
            "ACCEPTED_BY_DELIVERY_MAN",
            "ASSIGNED_TO_DELIVERY"
        }
    )

    ready_for_pickup_orders = sum(
        1 for o in orders
        if (o.get("status") or "").upper() in {
            "READY_FOR_PICKUP",
            "PACKAGING",
            "PREPARING"
        }
    )

    delivery_people_total = 0
    active_delivery_people_total = 0
    available_delivery_people = []

    try:
        delivery_people_total = mongo.users.count_documents({
            "role": "delivery",
            "is_active": 1
        })
        active_delivery_people_total = delivery_people_total
    except Exception:
        delivery_people_total = 0
        active_delivery_people_total = 0

    metrics = {
        "total_orders": len(orders),
        "gmv_total": float(gmv_total),
        "paid_total": float(paid_total),
        "txn_count": len(paid_transactions) if paid_transactions else len(delivered_orders),
        "unique_customers": unique_customers,
        "delivery_people": delivery_people_total,
        "active_delivery_people": active_delivery_people_total,
        "accepted_by_delivery": accepted_by_delivery,
        "delivery_accepted": accepted_by_delivery,
        "ready_for_pickup_orders": ready_for_pickup_orders,
        "delivered_orders": len(delivered_orders),
    }

    return {
        "products": products,
        "orders": orders,
        "metrics": metrics,
        "available_delivery_people": available_delivery_people,
        "delivered_orders_total": len(delivered_orders),
        "categories": _get_store_categories(sid, active_only=False),
        "active_categories": _get_store_categories(sid, active_only=True),
    }

@app.route('/store/products/new', methods=['GET'], endpoint='store_add_product')
@login_required(role='store')
def store_add_product_page():
    u, store = _get_current_store_or_redirect()

    if not store:
        return redirect(url_for("store_dashboard"))

    page_context = _build_store_split_page_context(store)

    return render_template(
        "store_add_product.html",
        user=u,
        store=store,
        **page_context
    )

@app.route('/store/products', methods=['GET'], endpoint='store_products')
@login_required(role='store')
def store_products_page():
    u, store = _get_current_store_or_redirect()

    if not store:
        return redirect(url_for("store_dashboard"))

    page_context = _build_store_split_page_context(store)

    return render_template(
        "store_products.html",
        user=u,
        store=store,
        **page_context
    )


@app.route('/store/orders', methods=['GET'], endpoint='store_orders')
@login_required(role='store')
def store_orders_page():
    u, store = _get_current_store_or_redirect()

    if not store:
        return redirect(url_for("store_dashboard"))

    page_context = _build_store_split_page_context(store)

    return render_template(
        "store_orders.html",
        user=u,
        store=store,
        **page_context
    )



@app.route('/store/inventory', methods=['GET'], endpoint='store_inventory')
@login_required(role='store')
def store_inventory_page():
    u, store = _get_current_store_or_redirect()

    if not store:
        return redirect(url_for("store_dashboard"))

    page_context = _build_store_split_page_context(store)

    return render_template(
        "store_inventory.html",
        user=u,
        store=store,
        **page_context
    )


@app.route('/store/categories', methods=['GET'], endpoint='store_categories')
@login_required(role='store')
def store_categories_page():
    u, store = _get_current_store_or_redirect()

    if not store:
        return redirect(url_for("store_dashboard"))

    page_context = _build_store_split_page_context(store)

    return render_template(
        "store_categories.html",
        user=u,
        store=store,
        **page_context
    )


# =========================================================
# STORE REVIEWS
# =========================================================
@app.route('/store/reviews', methods=['GET'], endpoint='store_reviews')
@login_required(role='store')
def store_reviews_page():
    u, store = _get_current_store_or_redirect()

    if not store:
        return redirect(url_for("store_dashboard"))

    page_context = _build_store_split_page_context(store) or {}

    store_id = store["_id"]
    store_id_str = str(store_id)

    reviews = list(
        mongo.store_ratings.find({
            "$and": [
                {
                    "$or": [
                        {"store_id": store_id},
                        {"store_id": store_id_str}
                    ]
                },
                {
                    "$or": [
                        {"is_active": 1},
                        {"is_active": True},
                        {"is_active": {"$exists": False}}
                    ]
                }
            ]
        }).sort("created_at", -1)
    )

    total_reviews = len(reviews)
    total_rating = 0.0

    rating_breakdown = {
        5: 0,
        4: 0,
        3: 0,
        2: 0,
        1: 0
    }

    positive_reviews = 0
    low_reviews = 0

    for r in reviews:
        r["id"] = str(r["_id"])

        try:
            rating_value = float(r.get("rating") or r.get("stars") or 0)
        except (TypeError, ValueError):
            rating_value = 0.0

        if rating_value < 0:
            rating_value = 0.0

        if rating_value > 5:
            rating_value = 5.0

        r["rating"] = rating_value
        total_rating += rating_value

        rating_bucket = int(round(rating_value))
        if rating_bucket < 1 and rating_value > 0:
            rating_bucket = 1
        if rating_bucket > 5:
            rating_bucket = 5

        if rating_bucket in rating_breakdown:
            rating_breakdown[rating_bucket] += 1

        if rating_value >= 4:
            positive_reviews += 1

        if rating_value > 0 and rating_value <= 2:
            low_reviews += 1

        reviewer = None

        if r.get("user_id"):
            try:
                reviewer = mongo.users.find_one({"_id": ObjectId(str(r.get("user_id")))})
            except Exception:
                reviewer = mongo.users.find_one({"_id": str(r.get("user_id"))})

        if reviewer:
            r["reviewer_name"] = reviewer.get("name", "Customer")
            r["reviewer_email"] = reviewer.get("email", "")
            r["reviewer_phone"] = reviewer.get("phone", "")
        else:
            r["reviewer_name"] = r.get("reviewer_name", "Customer")
            r["reviewer_email"] = r.get("reviewer_email", "")
            r["reviewer_phone"] = r.get("reviewer_phone", "")

        r["review_text"] = r.get("review") or r.get("comment") or ""

        created_at = r.get("created_at") or r.get("updated_at") or ""
        r["created_at_display"] = created_at

        try:
            if isinstance(created_at, str) and created_at:
                clean_dt = created_at.replace("Z", "")
                dt_obj = datetime.fromisoformat(clean_dt)
                r["created_at_display"] = dt_obj.strftime("%d %b %Y, %I:%M %p")
        except Exception:
            pass

    avg_rating = round(total_rating / total_reviews, 1) if total_reviews else 0

    review_metrics = {
    "total_reviews": total_reviews,
    "avg_rating": avg_rating,
    "positive_reviews": sum(1 for r in reviews if float(r.get("rating") or 0) >= 4),
    "low_reviews": sum(1 for r in reviews if float(r.get("rating") or 0) > 0 and float(r.get("rating") or 0) <= 2)
    }

    return render_template(
    "store_reviews.html",
    user=u,
    store=store,
    reviews=reviews,
    recent_reviews=reviews[:6],
    rating_breakdown=rating_breakdown,
    review_metrics=review_metrics,
    **page_context
    )



# =========================================================
# STORE PRODUCT REVIEWS
# =========================================================
@app.route('/store/product-reviews', methods=['GET'], endpoint='store_product_reviews')
@login_required(role='store')
def store_product_reviews_page():
    u, store = _get_current_store_or_redirect()

    if not store:
        return redirect(url_for("store_dashboard"))

    page_context = _build_store_split_page_context(store) or {}

    store_id = store["_id"]
    store_id_str = str(store_id)

    store_products = list(mongo.products.find({
        "$or": [
            {"store_id": store_id},
            {"store_id": store_id_str}
        ]
    }))

    product_map = {}
    product_ids = []

    for p in store_products:
        pid = p["_id"]
        pid_str = str(pid)

        product_ids.append(pid)
        product_ids.append(pid_str)

        product_map[pid_str] = {
            "id": pid_str,
            "name": p.get("name", "Product"),
            "image_path": p.get("image_path", ""),
            "category": p.get("category", ""),
            "stock_kg": float(p.get("stock_kg") or 0),
            "price_per_kg": float(p.get("price_per_kg") or 0)
        }

    reviews = []

    if product_ids:
        reviews = list(
            mongo.product_ratings.find({
                "$and": [
                    {
                        "$or": [
                            {"product_id": {"$in": product_ids}},
                            {"store_id": store_id},
                            {"store_id": store_id_str}
                        ]
                    },
                    {
                        "$or": [
                            {"is_active": 1},
                            {"is_active": True},
                            {"is_active": {"$exists": False}}
                        ]
                    }
                ]
            }).sort("created_at", -1)
        )

    total_reviews = len(reviews)
    total_rating = 0.0
    positive_reviews = 0
    low_reviews = 0

    rating_breakdown = {
        5: 0,
        4: 0,
        3: 0,
        2: 0,
        1: 0
    }

    product_review_counts = {}

    for r in reviews:
        r["id"] = str(r["_id"])

        try:
            rating_value = float(r.get("rating") or r.get("stars") or 0)
        except (TypeError, ValueError):
            rating_value = 0.0

        if rating_value < 0:
            rating_value = 0.0

        if rating_value > 5:
            rating_value = 5.0

        r["rating"] = rating_value
        total_rating += rating_value

        rating_bucket = int(round(rating_value))
        if rating_bucket < 1 and rating_value > 0:
            rating_bucket = 1
        if rating_bucket > 5:
            rating_bucket = 5

        if rating_bucket in rating_breakdown:
            rating_breakdown[rating_bucket] += 1

        if rating_value >= 4:
            positive_reviews += 1

        if rating_value > 0 and rating_value <= 2:
            low_reviews += 1

        pid_raw = r.get("product_id")
        pid_str = str(pid_raw) if pid_raw else ""

        product_data = product_map.get(pid_str)

        if product_data:
            r["product_name"] = product_data.get("name", "Product")
            r["product_image_path"] = product_data.get("image_path", "")
            r["product_category"] = product_data.get("category", "")
        else:
            r["product_name"] = r.get("product_name", "Product")
            r["product_image_path"] = ""
            r["product_category"] = ""

        if pid_str:
            product_review_counts[pid_str] = product_review_counts.get(pid_str, 0) + 1

        reviewer = None

        if r.get("user_id"):
            try:
                reviewer = mongo.users.find_one({"_id": ObjectId(str(r.get("user_id")))})
            except Exception:
                reviewer = mongo.users.find_one({"_id": str(r.get("user_id"))})

        if reviewer:
            r["reviewer_name"] = reviewer.get("name", "Customer")
            r["reviewer_email"] = reviewer.get("email", "")
            r["reviewer_phone"] = reviewer.get("phone", "")
        else:
            r["reviewer_name"] = r.get("reviewer_name", "Customer")
            r["reviewer_email"] = r.get("reviewer_email", "")
            r["reviewer_phone"] = r.get("reviewer_phone", "")

        r["review_text"] = r.get("review") or r.get("comment") or ""

        created_at = r.get("created_at") or r.get("updated_at") or ""
        r["created_at_display"] = created_at

        try:
            if isinstance(created_at, str) and created_at:
                clean_dt = created_at.replace("Z", "")
                dt_obj = datetime.fromisoformat(clean_dt)
                r["created_at_display"] = dt_obj.strftime("%d %b %Y, %I:%M %p")
        except Exception:
            pass

    avg_rating = round(total_rating / total_reviews, 1) if total_reviews else 0

    product_review_metrics = {
        "total_reviews": total_reviews,
        "avg_rating": avg_rating,
        "positive_reviews": positive_reviews,
        "low_reviews": low_reviews,
        "reviewed_products": len(product_review_counts)
    }

    return render_template(
        "store_product_reviews.html",
        user=u,
        store=store,
        reviews=reviews,
        rating_breakdown=rating_breakdown,
        product_review_metrics=product_review_metrics,
        **page_context
    )


# =========================================================
# STORE COMPLAINTS
# =========================================================
@app.route('/store/complaints', methods=['GET'], endpoint='store_complaints')
@login_required(role='store')
def store_complaints_page():
    u, store = _get_current_store_or_redirect()

    if not store:
        return redirect(url_for("store_dashboard"))

    page_context = _build_store_split_page_context(store) or {}

    store_id = store["_id"]
    store_id_str = str(store_id)

    complaints = list(
        mongo.customer_complaints.find({
            "$and": [
                {
                    "$or": [
                        {"store_id": store_id},
                        {"store_id": store_id_str},
                        {"store_id_str": store_id_str}
                    ]
                },
                {
                    "$or": [
                        {"is_active": 1},
                        {"is_active": True},
                        {"is_active": {"$exists": False}}
                    ]
                }
            ]
        }).sort("created_at", -1)
    )

    for c in complaints:
        c["id"] = str(c["_id"])
        c["complaint_image_path"] = c.get("complaint_image_path") or c.get("image_path") or ""

        status = str(c.get("status") or "open").strip().lower()
        progress_status = str(c.get("progress_status") or "received").strip().lower()

        c["status"] = status
        c["progress_status"] = progress_status
        c["status_label"] = status.replace("_", " ").title()
        c["progress_status_label"] = progress_status.replace("_", " ").title()

        created_at = c.get("created_at") or ""
        updated_at = c.get("updated_at") or ""

        c["created_at_display"] = created_at
        c["updated_at_display"] = updated_at

        try:
            if isinstance(created_at, str) and created_at:
                clean_dt = created_at.replace("Z", "")
                dt_obj = datetime.fromisoformat(clean_dt)
                c["created_at_display"] = dt_obj.strftime("%d %b %Y, %I:%M %p")
        except Exception:
            pass

        try:
            if isinstance(updated_at, str) and updated_at:
                clean_dt = updated_at.replace("Z", "")
                dt_obj = datetime.fromisoformat(clean_dt)
                c["updated_at_display"] = dt_obj.strftime("%d %b %Y, %I:%M %p")
        except Exception:
            pass

    complaint_metrics = {
        "total": len(complaints),
        "open": sum(1 for c in complaints if c.get("status") == "open"),
        "in_progress": sum(1 for c in complaints if c.get("status") == "in_progress"),
        "resolved": sum(1 for c in complaints if c.get("status") == "resolved")
    }

    return render_template(
        "store_complaints.html",
        user=u,
        store=store,
        complaints=complaints,
        complaint_metrics=complaint_metrics,
        **page_context
    )


@app.route('/store/complaints/<cid>/update', methods=['POST'], endpoint='store_complaint_update')
@login_required(role='store')
def store_complaint_update(cid):
    u, store = _get_current_store_or_redirect()

    if not store:
        return redirect(url_for("store_dashboard"))

    try:
        cid_obj = ObjectId(cid)
    except Exception:
        flash("Invalid complaint.", "danger")
        return redirect(url_for("store_complaints"))

    store_id = store["_id"]
    store_id_str = str(store_id)

    complaint = mongo.customer_complaints.find_one({
        "_id": cid_obj,
        "$or": [
            {"store_id": store_id},
            {"store_id": store_id_str},
            {"store_id_str": store_id_str}
        ]
    })

    if not complaint:
        flash("Complaint not found for your store.", "danger")
        return redirect(url_for("store_complaints"))

    progress_status = (request.form.get("progress_status") or "").strip().lower()
    store_reply = (request.form.get("store_reply") or "").strip()
    store_progress_note = (request.form.get("store_progress_note") or "").strip()

    allowed_progress = {
        "received",
        "in_progress",
        "resolved"
    }

    if progress_status not in allowed_progress:
        flash("Please select a valid progress status.", "warning")
        return redirect(url_for("store_complaints"))

    if len(store_reply) > 1000:
        flash("Store reply is too long. Please keep it within 1000 characters.", "warning")
        return redirect(url_for("store_complaints"))

    if len(store_progress_note) > 1000:
        flash("Progress note is too long. Please keep it within 1000 characters.", "warning")
        return redirect(url_for("store_complaints"))

    if progress_status == "resolved":
        final_status = "resolved"
    elif progress_status == "in_progress":
        final_status = "in_progress"
    else:
        final_status = "open"

    now = datetime.utcnow().isoformat()

    

    mongo.customer_complaints.update_one(
        {"_id": cid_obj},
        {
            "$set": {
                "progress_status": progress_status,
                "status": final_status,
                "store_reply": store_reply,
                "store_progress_note": store_progress_note,
                "store_updated_by": str(u["_id"]),
                "store_updated_by_name": u.get("name", "Store User"),
                "updated_at": now
            }
        }
    )

    flash("Complaint progress updated successfully.", "success")
    return redirect(url_for("store_complaints"))





# =========================================================
# STORE PROFILE
# =========================================================

def _build_store_profile_context(store, owner):
    notification_settings = mongo.store_notification_settings.find_one({
        "store_id": store["_id"]
    }) or {
        "enabled": False
    }

    checklist = [
        {
            "label": "Store name added",
            "done": bool((store.get("store_name") or "").strip())
        },
        {
            "label": "Owner name added",
            "done": bool((owner.get("name") or store.get("owner_name") or "").strip())
        },
        {
            "label": "Phone number added",
            "done": bool((owner.get("phone") or store.get("phone") or "").strip())
        },
        {
            "label": "Store address added",
            "done": bool((store.get("address") or "").strip())
        },
        {
            "label": "Pincode added",
            "done": bool((store.get("pincode") or "").strip())
        },
        {
            "label": "Latitude and longitude added",
            "done": store.get("latitude") is not None and store.get("longitude") is not None
        },
        {
            "label": "Store description added",
            "done": bool((store.get("description") or "").strip())
        },

{
    "label": "Store intro line added",
    "done": bool((store.get("profile_intro") or "").strip())
},
{
    "label": "Store banner uploaded",
    "done": bool((store.get("banner_path") or "").strip())
},

        {
            "label": "Store logo uploaded",
            "done": bool((store.get("logo_path") or "").strip())
        },
        {
            "label": "Operating time added",
            "done": bool((store.get("opening_time") or "").strip()) and bool((store.get("closing_time") or "").strip())
        },
        {
            "label": "Working days selected",
            "done": bool(store.get("working_days"))
        },
        {
            "label": "Notifications configured",
            "done": bool(notification_settings.get("enabled"))
        },
        {
            "label": "Store account active",
            "done": bool(store.get("is_active"))
        }
    ]

    done = sum(1 for item in checklist if item["done"])
    total = len(checklist)
    percent = round((done / total) * 100) if total else 0

    return {
        "profile_checklist": checklist,
        "profile_completion": {
            "done": done,
            "total": total,
            "percent": percent
        },
        "notification_settings": notification_settings
    }


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
    pincode = (request.form.get("pincode") or "").strip()
    description = (request.form.get("description") or "").strip()
    profile_intro = (request.form.get("profile_intro") or "").strip()
    opening_time = (request.form.get("opening_time") or "").strip()
    closing_time = (request.form.get("closing_time") or "").strip()
    working_days = request.form.getlist("working_days")
    preparation_time_raw = (request.form.get("preparation_time") or "").strip()
    min_order_amount_raw = (request.form.get("min_order_amount") or "").strip()
    delivery_available = True if request.form.get("delivery_available") == "1" else False

    lat_raw = (request.form.get("latitude") or "").strip()
    lng_raw = (request.form.get("longitude") or "").strip()

    latitude = None
    longitude = None
    preparation_time = None
    min_order_amount = None

    try:
        latitude = float(lat_raw) if lat_raw else None
    except Exception:
        latitude = None

    try:
        longitude = float(lng_raw) if lng_raw else None
    except Exception:
        longitude = None

    try:
        preparation_time = int(float(preparation_time_raw)) if preparation_time_raw else None
    except Exception:
        preparation_time = None

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

    update_data = {
        "store_name": store_name,
        "owner_name": owner_name,
        "phone": phone,
        "address": address,
        "pincode": pincode,
        "description": description,
        "profile_intro": profile_intro,
        "latitude": latitude,
        "longitude": longitude,
        "opening_time": opening_time,
        "closing_time": closing_time,
        "working_days": working_days,
        "preparation_time": preparation_time,
        "min_order_amount": min_order_amount,
        "delivery_available": delivery_available,
        "profile_updated_at": now,
        "updated_at": now
    }

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

# =========================================================
# STORE NOTIFICATIONS
# =========================================================

def _store_id_values(store_id):
    return [store_id, str(store_id)]


def _store_notification_stats(store_id):
    store_id_values = _store_id_values(store_id)
    today_prefix = datetime.utcnow().date().isoformat()

    total = mongo.store_notifications.count_documents({
        "store_id": {"$in": store_id_values}
    })

    unread = mongo.store_notifications.count_documents({
        "store_id": {"$in": store_id_values},
        "is_read": False
    })

    today = mongo.store_notifications.count_documents({
        "store_id": {"$in": store_id_values},
        "created_at": {"$regex": f"^{today_prefix}"}
    })

    active = mongo.orders.count_documents({
        "store_id": {"$in": store_id_values},
        "status": {"$nin": ["DELIVERED", "CANCELLED"]}
    })

    return {
        "total": total,
        "unread": unread,
        "today": today,
        "active": active
    }


def _create_store_notification(store, title, message, notif_type="system", order=None, event_key=None):
    now = datetime.utcnow().isoformat()
    store_id = store["_id"]

    if event_key:
        existing = mongo.store_notifications.find_one({
            "store_id": {"$in": _store_id_values(store_id)},
            "event_key": event_key
        })

        if existing:
            return existing

    doc = {
        "store_id": store_id,
        "store_name": store.get("store_name", ""),
        "title": title,
        "message": message,
        "type": notif_type,
        "is_read": False,
        "is_active": True,
        "created_at": now,
        "updated_at": now
    }

    if event_key:
        doc["event_key"] = event_key

    if order:
        doc["order_id"] = order.get("_id")
        doc["order_ref"] = str(order.get("_id"))
        doc["order_status"] = order.get("status", "")
        doc["payment_status"] = order.get("payment_status", "")
        doc["customer_name"] = order.get("customer_name", "")
        doc["customer_phone"] = order.get("customer_phone", "")
        doc["total_payable"] = (
            float(order.get("total_amount") or 0)
            + float(order.get("delivery_fee") or 0)
            + float(order.get("tip_amount") or 0)
        )

    mongo.store_notifications.insert_one(doc)
    return doc


def _hydrate_store_notification(n):
    n["id"] = str(n["_id"])
    n["store_id"] = str(n.get("store_id")) if n.get("store_id") else ""
    n["order_id"] = str(n.get("order_id")) if n.get("order_id") else ""
    n["title"] = n.get("title", "Notification")
    n["message"] = n.get("message", "")
    n["type"] = n.get("type", "system")
    n["is_read"] = bool(n.get("is_read"))
    n["is_active"] = bool(n.get("is_active", True))
    return n


def _sync_store_order_notifications(store):
    store_id_values = _store_id_values(store["_id"])

    recent_orders = list(
        mongo.orders.find({
            "store_id": {"$in": store_id_values}
        }).sort("created_at", -1).limit(60)
    )

    for order in recent_orders:
        oid = str(order["_id"])
        status = (order.get("status") or "PLACED").upper()

        total_payable = (
            float(order.get("total_amount") or 0)
            + float(order.get("delivery_fee") or 0)
            + float(order.get("tip_amount") or 0)
        )

        if status not in ["DELIVERED", "CANCELLED"]:
            _create_store_notification(
                store,
                title="Active order needs attention",
                message=f"Order #{oid[-6:]} is currently {status}. Payable amount ₹ {total_payable:.2f}.",
                notif_type="new_order",
                order=order,
                event_key=f"order-active-{oid}"
            )

    recent_events = list(
        mongo.order_events.find({}).sort("created_at", -1).limit(120)
    )

    for event in recent_events:
        order_id = event.get("order_id")

        if not order_id:
            continue

        order = mongo.orders.find_one({
            "_id": order_id,
            "store_id": {"$in": store_id_values}
        })

        if not order:
            continue

        oid = str(order["_id"])
        status = (event.get("status") or order.get("status") or "").upper()
        event_id = str(event.get("_id"))

        _create_store_notification(
            store,
            title="Order status updated",
            message=f"Order #{oid[-6:]} status changed to {status}.",
            notif_type="status",
            order=order,
            event_key=f"order-event-{event_id}"
        )


@app.route('/store/notifications', methods=['GET'], endpoint='store_notifications')
@login_required(role='store')
def store_notifications_page():
    u, store = _get_current_store_or_redirect()

    if not store:
        return redirect(url_for("store_dashboard"))

    _sync_store_order_notifications(store)

    store_id_values = _store_id_values(store["_id"])

    notifications = list(
        mongo.store_notifications.find({
            "store_id": {"$in": store_id_values}
        }).sort("created_at", -1).limit(150)
    )

    notifications = [_hydrate_store_notification(n) for n in notifications]

    active_orders = list(
        mongo.orders.find({
            "store_id": {"$in": store_id_values},
            "status": {"$nin": ["DELIVERED", "CANCELLED"]}
        }).sort("created_at", -1).limit(30)
    )

    active_notifications = []

    for order in active_orders:
        oid = str(order["_id"])
        status = (order.get("status") or "PLACED").upper()

        total_payable = (
            float(order.get("total_amount") or 0)
            + float(order.get("delivery_fee") or 0)
            + float(order.get("tip_amount") or 0)
        )

        active_notifications.append({
            "id": oid,
            "title": f"Order #{oid[-6:]} needs attention",
            "message": f"Current status: {status}. Payable amount ₹ {total_payable:.2f}.",
            "type": "active_order",
            "order_id": oid,
            "created_at": order.get("created_at", "")
        })

    notification_settings = mongo.store_notification_settings.find_one({
        "store_id": store["_id"]
    }) or {
        "enabled": False
    }

    page_context = _build_store_split_page_context(store)

    return render_template(
        "store_notifications.html",
        user=u,
        store=store,
        notifications=notifications,
        active_notifications=active_notifications,
        notification_settings=notification_settings,
        notification_stats=_store_notification_stats(store["_id"]),
        **page_context
    )


@app.route('/store/notifications/toggle', methods=['POST'], endpoint='store_notifications_toggle')
@login_required(role='store')
def store_notifications_toggle():
    u, store = _get_current_store_or_redirect()

    if not store:
        return jsonify({"ok": False, "message": "Store not found"}), 404

    data = request.get_json(silent=True) or {}
    enabled = bool(data.get("enabled"))
    now = datetime.utcnow().isoformat()

    mongo.store_notification_settings.update_one(
        {"store_id": store["_id"]},
        {
            "$set": {
                "store_id": store["_id"],
                "enabled": enabled,
                "updated_at": now
            },
            "$setOnInsert": {
                "created_at": now
            }
        },
        upsert=True
    )

    _create_store_notification(
        store,
        title="Notifications enabled" if enabled else "Notifications disabled",
        message="Live order alerts were enabled for this store." if enabled else "Live order alerts were disabled for this store.",
        notif_type="system",
        event_key=f"notification-toggle-{store['_id']}-{now}"
    )

    return jsonify({
        "ok": True,
        "enabled": enabled,
        "stats": _store_notification_stats(store["_id"])
    })


@app.route('/store/notifications/poll', methods=['GET'], endpoint='store_notifications_poll')
@login_required(role='store')
def store_notifications_poll():
    u, store = _get_current_store_or_redirect()

    if not store:
        return jsonify({"ok": False, "notifications": []}), 404

    _sync_store_order_notifications(store)

    notifications = list(
        mongo.store_notifications.find({
            "store_id": {"$in": _store_id_values(store["_id"])}
        }).sort("created_at", -1).limit(20)
    )

    return jsonify({
        "ok": True,
        "notifications": [_hydrate_store_notification(n) for n in notifications],
        "stats": _store_notification_stats(store["_id"])
    })


@app.route('/store/notifications/<nid>/read', methods=['POST'], endpoint='store_notification_mark_read')
@login_required(role='store')
def store_notification_mark_read(nid):
    u, store = _get_current_store_or_redirect()

    if not store:
        return jsonify({"ok": False}), 404

    try:
        nid_obj = ObjectId(nid)
    except Exception:
        return jsonify({"ok": False}), 400

    mongo.store_notifications.update_one(
        {
            "_id": nid_obj,
            "store_id": {"$in": _store_id_values(store["_id"])}
        },
        {
            "$set": {
                "is_read": True,
                "read_at": datetime.utcnow().isoformat(),
                "updated_at": datetime.utcnow().isoformat()
            }
        }
    )

    return jsonify({
        "ok": True,
        "stats": _store_notification_stats(store["_id"])
    })


@app.route('/store/notifications/read-all', methods=['POST'], endpoint='store_notifications_mark_all_read')
@login_required(role='store')
def store_notifications_mark_all_read():
    u, store = _get_current_store_or_redirect()

    if not store:
        return jsonify({"ok": False}), 404

    mongo.store_notifications.update_many(
        {
            "store_id": {"$in": _store_id_values(store["_id"])},
            "is_read": False
        },
        {
            "$set": {
                "is_read": True,
                "read_at": datetime.utcnow().isoformat(),
                "updated_at": datetime.utcnow().isoformat()
            }
        }
    )

    return jsonify({
        "ok": True,
        "stats": _store_notification_stats(store["_id"])
    })

@app.route('/store/categories/new', methods=['POST'], endpoint='store_category_new')
@login_required(role='store')
def store_category_new():
    u, store = _get_current_store_or_redirect()

    if not store:
        return redirect(url_for("store_dashboard"))

    name = (request.form.get("name") or "").strip()
    sub_categories_raw = (request.form.get("sub_categories") or "").strip()

    if not name:
        flash("Category name is required.", "warning")
        return redirect(url_for("store_categories"))

    slug = _category_slug(name)

    if not slug:
        flash("Enter a valid category name.", "warning")
        return redirect(url_for("store_categories"))

    existing = mongo.store_categories.find_one({
        "store_id": store["_id"],
        "slug": slug
    })

    if existing:
        flash("This category already exists.", "warning")
        return redirect(url_for("store_categories"))

    sub_categories = [
        item.strip()
        for item in sub_categories_raw.split(",")
        if item.strip()
    ]

    now = datetime.utcnow().isoformat()

    category_image_path = ""
    category_image = request.files.get("category_image")

    if category_image and category_image.filename:
        if not allowed_file(category_image.filename):
            flash("Only JPG, JPEG, PNG or WEBP images are allowed for category image.", "warning")
            return redirect(url_for("store_categories"))

        category_image_path = _save_store_category_image(
            category_image,
            store["_id"],
            slug
        )

    mongo.store_categories.insert_one({
    "store_id": store["_id"],
    "name": name,
    "slug": slug,
    "sub_categories": sub_categories,
    "image_path": category_image_path,
    "category_image_path": category_image_path,
    "emoji": "🛒",
    "is_active": 1,
    "is_default": 0,
    "created_at": now,
    "updated_at": now,
})

    flash("Category added.", "success")
    return redirect(url_for("store_categories"))


@app.route('/store/categories/<cid>/update', methods=['POST'], endpoint='store_category_update')
@login_required(role='store')
def store_category_update(cid):
    u, store = _get_current_store_or_redirect()

    if not store:
        return redirect(url_for("store_dashboard"))

    cat = _get_store_category_by_id(store["_id"], cid)

    if not cat:
        flash("Category not found.", "danger")
        return redirect(url_for("store_categories"))

    old_name = cat.get("name", "")
    name = (request.form.get("name") or "").strip()
    sub_categories_raw = (request.form.get("sub_categories") or "").strip()

    if not name:
        flash("Category name is required.", "warning")
        return redirect(url_for("store_categories"))

    slug = _category_slug(name)

    duplicate = mongo.store_categories.find_one({
        "_id": {"$ne": cat["_id"]},
        "store_id": store["_id"],
        "slug": slug
    })

    if duplicate:
        flash("Another category with this name already exists.", "warning")
        return redirect(url_for("store_categories"))

    sub_categories = [
        item.strip()
        for item in sub_categories_raw.split(",")
        if item.strip()
    ]

    now = datetime.utcnow().isoformat()

    update_data = {
        "name": name,
        "slug": slug,
        "sub_categories": sub_categories,
        "updated_at": now,
    }

    category_image = request.files.get("category_image")

    if category_image and category_image.filename:
        if not allowed_file(category_image.filename):
            flash("Only JPG, JPEG, PNG or WEBP images are allowed for category image.", "warning")
            return redirect(url_for("store_categories"))

        category_image_path = _save_store_category_image(
            category_image,
            store["_id"],
            slug
        )

        update_data["image_path"] = category_image_path
        update_data["category_image_path"] = category_image_path

    mongo.store_categories.update_one(
    {"_id": cat["_id"]},
    {
        "$set": update_data
    }
)

    if old_name and old_name != name:
        mongo.products.update_many(
            {
                "store_id": store["_id"],
                "category": old_name
            },
            {
                "$set": {
                    "category": name,
                    "updated_at": now
                }
            }
        )

    flash("Category updated.", "success")
    return redirect(url_for("store_categories"))


@app.route('/store/categories/<cid>/toggle', methods=['POST'], endpoint='store_category_toggle')
@login_required(role='store')
def store_category_toggle(cid):
    u, store = _get_current_store_or_redirect()

    if not store:
        return redirect(url_for("store_dashboard"))

    cat = _get_store_category_by_id(store["_id"], cid)

    if not cat:
        flash("Category not found.", "danger")
        return redirect(url_for("store_categories"))

    new_status = 0 if int(cat.get("is_active") or 0) == 1 else 1

    mongo.store_categories.update_one(
        {"_id": cat["_id"]},
        {
            "$set": {
                "is_active": new_status,
                "updated_at": datetime.utcnow().isoformat()
            }
        }
    )

    flash("Category enabled." if new_status else "Category disabled.", "success")
    return redirect(url_for("store_categories"))


@app.route('/store/categories/<cid>/delete', methods=['POST'], endpoint='store_category_delete')
@login_required(role='store')
def store_category_delete(cid):
    u, store = _get_current_store_or_redirect()

    if not store:
        return redirect(url_for("store_dashboard"))

    cat = _get_store_category_by_id(store["_id"], cid)

    if not cat:
        flash("Category not found.", "danger")
        return redirect(url_for("store_categories"))

    product_count = _get_category_product_count(store["_id"], cat.get("name"))

    if product_count > 0:
        mongo.store_categories.update_one(
            {"_id": cat["_id"]},
            {
                "$set": {
                    "is_active": 0,
                    "updated_at": datetime.utcnow().isoformat()
                }
            }
        )

        flash("This category has products, so it was disabled instead of deleted.", "warning")
        return redirect(url_for("store_categories"))

    mongo.store_categories.delete_one({"_id": cat["_id"]})

    flash("Category deleted.", "success")
    return redirect(url_for("store_categories"))

@app.route('/store/product/new', methods=['POST'])
@app.route('/store/products/new', methods=['POST'])
@login_required(role='store')
def store_product_new():
    u = current_user()

    store = mongo.stores.find_one({"user_id": u["id"]})
    if not store:
        flash("Store not found.", "danger")
        return redirect(url_for("store_dashboard"))

    sid = store["_id"]

    name = request.form.get('name', '').strip()

    pricing = _calculate_product_pricing_from_form(request.form)

    price_per_kg = pricing["price_per_kg"]
    original_price_per_kg = pricing["original_price_per_kg"]

    try:
        stock_kg = float(request.form.get('stock_kg', '0') or 0)
    except Exception:
        stock_kg = 0

    category_id = (request.form.get("category_id") or "").strip()
    category = (request.form.get("category") or "").strip()
    sub_category = (request.form.get("sub_category") or "").strip()

    category_doc = None

    if category_id:
        category_doc = _get_store_category_by_id(sid, category_id, active_only=True)

    if not category_doc and category:
        category_doc = _get_store_category_by_name(sid, category, active_only=True)

    if not category_doc:
        flash("Please select a valid active category.", "warning")
        return redirect(url_for("store_add_product"))

    category = category_doc.get("name")
    category_id = str(category_doc["_id"])

    allowed_subs = category_doc.get("sub_categories") or []

    if not name:
        flash('Product name is required.', 'warning')
        return redirect(url_for('store_add_product'))

    if original_price_per_kg <= 0:
        flash('Price must be greater than 0.', 'warning')
        return redirect(url_for('store_add_product'))

    if price_per_kg <= 0:
        flash('Final selling price must be greater than 0.', 'warning')
        return redirect(url_for('store_add_product'))

    if stock_kg < 0:
        flash('Stock cannot be negative.', 'warning')
        return redirect(url_for('store_add_product'))

    if allowed_subs:
        if sub_category not in allowed_subs:
            flash("Please select a valid sub-category.", "warning")
            return redirect(url_for("store_add_product"))
    else:
        sub_category = None

    image = request.files.get('image')
    image_path = None

    if image and image.filename:
        if allowed_file(image.filename):
            fn = secure_filename(image.filename)
            save_as = datetime.utcnow().strftime("%Y%m%d%H%M%S_") + fn
            os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
            image.save(os.path.join(app.config["UPLOAD_FOLDER"], save_as))
            image_path = f"uploads/{save_as}"
        else:
            flash("Invalid image file type.", "warning")
            return redirect(url_for("store_add_product"))

    now = datetime.utcnow().isoformat()

    mongo.products.insert_one({
        "store_id": sid,
        "store_name": store.get("store_name", ""),

        "name": name,

        "original_price_per_kg": original_price_per_kg,
        "price_per_kg": price_per_kg,
        "discount_enabled": pricing["discount_enabled"],
        "discount_type": pricing["discount_type"],
        "discount_value": pricing["discount_value"],
        "discount_amount_per_kg": pricing["discount_amount_per_kg"],
        "discount_percent": pricing["discount_percent"],

        "stock_kg": stock_kg,

        "category_id": category_id,
        "category": category,
        "sub_category": sub_category,

        "image_path": image_path,
        "is_active": 1 if stock_kg > 0 else 0,

        "created_at": now,
        "updated_at": now
    })

    flash("Product added successfully.", "success")
    return redirect(url_for("store_products"))

@app.route('/store/product/<pid>/toggle', methods=['POST'])
@login_required(role='store')
def store_product_toggle(pid):
    u = current_user()

    store = mongo.stores.find_one({"user_id": u["id"]})
    if not store:
        flash("Store not found.", "danger")
        return redirect(url_for("store_dashboard"))

    try:
        pid_obj = ObjectId(pid)
    except Exception:
        flash("Invalid product.", "danger")
        return redirect(url_for("store_dashboard"))

    product = mongo.products.find_one({
        "_id": pid_obj,
        "store_id": store["_id"]
    })

    if not product:
        flash("Product not found.", "danger")
        return redirect(url_for("store_dashboard"))

    current_active = int(product.get("is_active") or 0)
    new_active = 0 if current_active == 1 else 1

    mongo.products.update_one(
        {"_id": pid_obj},
        {
            "$set": {
                "is_active": new_active,
                "updated_at": datetime.utcnow().isoformat()
            }
        }
    )

    flash("Product status updated.", "success")
    return redirect(url_for("store_products"))


@app.route('/store/product/<pid>/delete', methods=['POST'])
@login_required(role='store')
def store_product_delete(pid):
    u = current_user()

    store = mongo.stores.find_one({"user_id": u["id"]})
    if not store:
        flash("Store not found.", "danger")
        return redirect(url_for("store_dashboard"))

    try:
        pid_obj = ObjectId(pid)
    except Exception:
        flash("Invalid product.", "danger")
        return redirect(url_for("store_dashboard"))

    product = mongo.products.find_one({
        "_id": pid_obj,
        "store_id": store["_id"]
    })

    if not product:
        flash("Product not found.", "danger")
        return redirect(url_for("store_dashboard"))

    order_item_exists = mongo.order_items.find_one({"product_id": pid_obj})

    if order_item_exists:
        mongo.products.update_one(
            {"_id": pid_obj},
            {
                "$set": {
                    "is_active": 0,
                    "deleted_at": datetime.utcnow().isoformat(),
                    "updated_at": datetime.utcnow().isoformat()
                }
            }
        )
        flash("Product has order history, so it was disabled instead of deleted.", "warning")
    else:
        mongo.products.delete_one({"_id": pid_obj})
        flash("Product deleted.", "success")

    return redirect(url_for("store_products"))


@app.route('/store/product/<pid>/stock/add', methods=['POST'], endpoint='store_product_add_stock')
@login_required(role='store')
def store_product_add_stock(pid):
    u = current_user()

    store = mongo.stores.find_one({"user_id": u["id"]})
    if not store:
        flash("Store not found.", "danger")
        return redirect(url_for("store_dashboard"))

    try:
        pid_obj = ObjectId(pid)
    except Exception:
        flash("Invalid product.", "danger")
        return redirect(url_for("store_dashboard"))

    product = mongo.products.find_one({
        "_id": pid_obj,
        "store_id": store["_id"]
    })

    if not product:
        flash("Product not found for your store.", "warning")
        return redirect(url_for("store_dashboard"))

    try:
        add_kg = float(request.form.get("add_kg", "0") or 0)
    except ValueError:
        add_kg = 0.0

    if add_kg <= 0:
        flash("Enter a positive stock amount.", "warning")
        return redirect(url_for("store_dashboard"))

    mongo.products.update_one(
        {"_id": pid_obj},
        {
            "$inc": {"stock_kg": add_kg},
            "$set": {
                "is_active": 1,
                "updated_at": datetime.utcnow().isoformat()
            }
        }
    )

    flash(f"Added {add_kg:.2f} kg to stock.", "success")
    return redirect(url_for("store_dashboard"))


@app.route('/store/product/<pid>/edit', methods=['GET'], endpoint='store_product_edit')
@login_required(role='store')
def store_product_edit(pid):
    u = current_user()

    store = mongo.stores.find_one({"user_id": u["id"]})
    if not store:
        flash("Store not found.", "danger")
        return redirect(url_for("store_dashboard"))

    try:
        pid_obj = ObjectId(pid)
    except Exception:
        flash("Invalid product.", "danger")
        return redirect(url_for("store_dashboard"))

    product = mongo.products.find_one({
        "_id": pid_obj,
        "store_id": store["_id"]
    })

    if not product:
        flash("Product not found for your store.", "warning")
        return redirect(url_for("store_dashboard"))

    product["id"] = str(product["_id"])
    product["store_id"] = str(product.get("store_id")) if product.get("store_id") else ""

    active_categories = _get_store_categories(store["_id"], active_only=True)

    return render_template(
    "store_product_edit.html",
    user=u,
    store=store,
    product=product,
    active_categories=active_categories
)

@app.route('/store/product/<pid>/edit', methods=['POST'], endpoint='store_product_update')
@login_required(role='store')
def store_product_update(pid):
    u = current_user()

    store = mongo.stores.find_one({"user_id": u["id"]})
    if not store:
        flash("Store not found.", "danger")
        return redirect(url_for("store_dashboard"))

    try:
        pid_obj = ObjectId(pid)
    except Exception:
        flash("Invalid product.", "danger")
        return redirect(url_for("store_dashboard"))

    product = mongo.products.find_one({
        "_id": pid_obj,
        "store_id": store["_id"]
    })

    if not product:
        flash("Product not found for your store.", "warning")
        return redirect(url_for("store_dashboard"))

    name = (request.form.get("name") or "").strip()

    category_id = (request.form.get("category_id") or product.get("category_id") or "").strip()
    raw_category = (request.form.get("category") or product.get("category") or "").strip()
    sub_category = (request.form.get("sub_category") or product.get("sub_category") or "").strip()

    category_doc = None

    if category_id:
        category_doc = _get_store_category_by_id(store["_id"], category_id, active_only=True)

    if not category_doc and raw_category:
        category_doc = _get_store_category_by_name(store["_id"], raw_category, active_only=True)

    if not category_doc:
        flash("Please select a valid active category.", "warning")
        return redirect(url_for("store_product_edit", pid=pid))

    category = category_doc.get("name")
    category_id = str(category_doc["_id"])
    allowed_subs = category_doc.get("sub_categories") or []

    fallback_original_price = (
        product.get("original_price_per_kg")
        if product.get("original_price_per_kg") is not None
        else product.get("price_per_kg", 0)
    )

    pricing = _calculate_product_pricing_from_form(
        request.form,
        fallback_original_price=fallback_original_price
    )

    price = pricing["price_per_kg"]
    original_price = pricing["original_price_per_kg"]

    try:
        stock = float(request.form.get("stock_kg", "0") or 0)
    except Exception:
        stock = -1

    if not name:
        flash("Product name is required.", "warning")
        return redirect(url_for("store_product_edit", pid=pid))

    if original_price < 0:
        flash("Enter a valid non-negative price.", "warning")
        return redirect(url_for("store_product_edit", pid=pid))

    if original_price <= 0:
        flash("Price must be greater than 0.", "warning")
        return redirect(url_for("store_product_edit", pid=pid))

    if price <= 0:
        flash("Final selling price must be greater than 0.", "warning")
        return redirect(url_for("store_product_edit", pid=pid))

    if stock < 0:
        flash("Enter a valid non-negative stock.", "warning")
        return redirect(url_for("store_product_edit", pid=pid))

    if allowed_subs:
        if not sub_category:
            sub_category = product.get("sub_category") or ""

        if sub_category not in allowed_subs:
            flash("Please select a valid sub-category.", "warning")
            return redirect(url_for("store_product_edit", pid=pid))
    else:
        sub_category = None

    update_data = {
        "name": name,

        "original_price_per_kg": original_price,
        "price_per_kg": price,
        "discount_enabled": pricing["discount_enabled"],
        "discount_type": pricing["discount_type"],
        "discount_value": pricing["discount_value"],
        "discount_amount_per_kg": pricing["discount_amount_per_kg"],
        "discount_percent": pricing["discount_percent"],

        "stock_kg": stock,

        "category_id": category_id,
        "category": category,
        "sub_category": sub_category,

        "is_active": 1 if stock > 0 else int(product.get("is_active") or 0),
        "updated_at": datetime.utcnow().isoformat()
    }

    image = request.files.get("image")
    if image and image.filename:
        if not allowed_file(image.filename):
            flash("Invalid image file type.", "warning")
            return redirect(url_for("store_product_edit", pid=pid))

        fn = secure_filename(image.filename)
        save_as = datetime.utcnow().strftime("%Y%m%d%H%M%S_") + fn
        os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
        image.save(os.path.join(app.config["UPLOAD_FOLDER"], save_as))
        update_data["image_path"] = f"uploads/{save_as}"

    mongo.products.update_one(
        {"_id": pid_obj},
        {"$set": update_data}
    )

    flash("Product updated.", "success")
    return redirect(url_for("store_product_edit", pid=pid))

@app.route('/store/transactions.csv')
@login_required(role='store')
def store_txn_csv():
    """
    Download transactions for this store as CSV.
    Supported presets via ?range=day|week|month.
    You can also pass explicit ?start=YYYY-MM-DD&end=YYYY-MM-DD.
    Only PAID transactions are included.
    """
    u = current_user()

    store = mongo.stores.find_one({"user_id": u["id"]})

    if not store:
        flash("Store not found.", "danger")
        return redirect(url_for("store_dashboard"))

    preset = (request.args.get("range") or "").lower()
    start_str = request.args.get("start")
    end_str = request.args.get("end")

    if start_str and end_str:
        try:
            start_date = date.fromisoformat(start_str)
            end_date = date.fromisoformat(end_str)
        except Exception:
            flash("Invalid start/end date. Use YYYY-MM-DD.", "warning")
            return redirect(url_for("store_dashboard"))
    else:
        today = datetime.utcnow().date()

        if preset == "week":
            start_date = today - timedelta(days=today.weekday())
            end_date = start_date + timedelta(days=7)
        elif preset == "month":
            start_date = date(today.year, today.month, 1)

            if today.month == 12:
                end_date = date(today.year + 1, 1, 1)
            else:
                end_date = date(today.year, today.month + 1, 1)
        else:
            start_date = today
            end_date = today + timedelta(days=1)

    start_iso = f"{start_date.isoformat()}T00:00:00"
    end_iso = f"{end_date.isoformat()}T00:00:00"

    txns = list(
        mongo.transactions.find({
            "status": "PAID",
            "created_at": {
                "$gte": start_iso,
                "$lt": end_iso
            }
        }).sort("created_at", -1)
    )

    csv_lines = [
        "txn_id,txn_created_at,order_id,items_total,delivery_fee,tip_amount,paid_amount,txn_status"
    ]

    for t in txns:
        order_id = t.get("order_id")
        order = None

        if order_id:
            order = mongo.orders.find_one({
                "_id": order_id,
                "store_id": store["_id"]
            })

        if not order:
            continue

        csv_lines.append(",".join([
            str(t.get("_id", "")),
            str(t.get("created_at", "")),
            str(order.get("_id", "")),
            str(float(order.get("total_amount") or 0)),
            str(float(order.get("delivery_fee") or 0)),
            str(float(order.get("tip_amount") or 0)),
            str(float(t.get("amount") or 0)),
            str(t.get("status", "")),
        ]))

    data = "\n".join(csv_lines).encode("utf-8")

    return send_file(
        io.BytesIO(data),
        mimetype="text/csv",
        as_attachment=True,
        download_name="store_transactions.csv"
    )


@app.route('/store/order/<oid>/status', methods=['POST'])
@app.route('/store/orders/<oid>/status', methods=['POST'])
@login_required(role='store')
def store_order_status(oid):
    u = current_user()

    store = mongo.stores.find_one({"user_id": u["id"]})

    if not store:
        flash("Store not found.", "danger")
        return redirect(url_for("store_dashboard"))

    try:
        oid_obj = ObjectId(oid)
    except Exception:
        flash("Invalid order.", "danger")
        return redirect(url_for("store_orders"))

    allowed_statuses = {
        "PLACED",
        "CONFIRMED",
        "PREPARING",
        "READY_FOR_PICKUP",
        "ASSIGNED_TO_DELIVERY",
        "ACCEPTED_BY_DELIVERY_MAN",
        "OUT_FOR_DELIVERY",
        "DELIVERED",
        "CANCELLED",
    }

    new_status = (request.form.get("status") or "PLACED").strip().upper()
    now = datetime.utcnow().isoformat()

    if new_status not in allowed_statuses:
        flash("Invalid order status selected.", "warning")
        return redirect(url_for("store_orders"))

    order = mongo.orders.find_one({
        "_id": oid_obj,
        "store_id": store["_id"]
    })

    if not order:
        flash("Order not found.", "danger")
        return redirect(url_for("store_orders"))

    update_data = {
        "status": new_status,
        "updated_at": now
    }

    if new_status == "PREPARING":
        update_data["preparing_at"] = now

    if new_status == "READY_FOR_PICKUP":
        update_data["ready_for_pickup_at"] = now

    if new_status == "OUT_FOR_DELIVERY":
        update_data["out_for_delivery_at"] = now

    if new_status == "DELIVERED":
        update_data["delivered_at"] = now
        update_data["payment_status"] = "PAID"

    if new_status == "CANCELLED":
        update_data["cancelled_at"] = now

    mongo.orders.update_one(
        {"_id": oid_obj, "store_id": store["_id"]},
        {"$set": update_data}
    )

    mongo.order_events.insert_one({
        "order_id": oid_obj,
        "status": new_status,
        "note": "Updated by store",
        "created_at": now
    })

    _create_store_notification(
        store,
        title="Order status updated",
        message=f"Order #{str(order['_id'])[-6:]} status changed to {new_status}.",
        notif_type="status",
        order=order,
        event_key=f"status-{str(order['_id'])}-{new_status}-{now}"
    )

    # Only touch transactions when the order is delivered.
    if new_status == "DELIVERED":
        payable_amount = (
            float(order.get("total_amount") or 0)
            + float(order.get("delivery_fee") or 0)
            + float(order.get("tip_amount") or 0)
        )

        existing_txn = mongo.transactions.find_one({
            "order_id": oid_obj
        })

        if existing_txn:
            mongo.transactions.update_many(
                {"order_id": oid_obj},
                {
                    "$set": {
                        "status": "PAID",
                        "amount": payable_amount,
                        "updated_at": now
                    }
                }
            )
        else:
            mongo.transactions.insert_one({
                "order_id": oid_obj,
                "store_id": store["_id"],
                "user_id": order.get("user_id"),
                "amount": payable_amount,
                "status": "PAID",
                "method": order.get("payment_method") or "COD",
                "created_at": now,
                "updated_at": now
            })

    flash("Order status updated.", "success")
    return redirect(url_for("store_orders"))

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

# -----------------------------------------------------------------------------
# Mobile (token) orders API
# -----------------------------------------------------------------------------

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



# ----------------------
# NEWSLETTER & UPLOADS
# ----------------------
@app.route('/newsletter/subscribe', methods=['POST'])
def newsletter_subscribe():
    email = request.form.get('email', '').strip().lower()

    if not email or '@' not in email:
        flash('Please enter a valid email.', 'danger')
        return redirect(request.referrer or url_for('index'))

    existing = mongo.newsletter_subscribers.find_one({"email": email})

    if existing:
        flash('You are already subscribed.', 'info')
        return redirect(request.referrer or url_for('index'))

    mongo.newsletter_subscribers.insert_one({
        "email": email,
        "created_at": datetime.utcnow().isoformat()
    })

    flash('Subscribed to newsletter!', 'success')
    return redirect(request.referrer or url_for('index'))

@app.route('/uploads/<path:fn>')
def uploaded_file(fn):
    if '..' in fn or fn.startswith('/'):
        return abort(404)
    full = os.path.join(app.config['UPLOAD_FOLDER'], fn)
    if not os.path.isfile(full):
        return abort(404)
    return send_file(full)

@app.after_request
def add_no_cache_headers(resp):
    # help fetch() always get fresh data
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    return resp


@app.route('/__routes')
def __routes():
    return "<pre>" + "\n".join(
        f"{r.endpoint:30} {r.methods} {r}"
        for r in app.url_map.iter_rules()
    ) + "</pre>"








@app.route("/contact", methods=["GET", "POST"])
def contact():
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        email = (request.form.get("email") or "").strip()
        phone = (request.form.get("phone") or "").strip() or None
        subject = (request.form.get("subject") or "").strip()
        message = (request.form.get("message") or "").strip()

        if not name or not email or not subject or not message:
            flash("Please fill all required fields.", "warning")
            return redirect(url_for("contact"))

        mongo.contact_messages.insert_one({
            "name": name,
            "email": email,
            "phone": phone,
            "subject": subject,
            "message": message,
            "status": "NEW",
            "created_at": datetime.utcnow().isoformat()
        })

        flash("Message sent! We will contact you soon.", "success")
        return redirect(url_for("contact"))

    return render_template("contact.html", user=current_user())




@app.route("/admin/contact-messages")
@login_required(role="admin")
def admin_contact_messages():
    messages = list(
        mongo.contact_messages.find({}).sort("created_at", -1)
    )

    for m in messages:
        m["id"] = str(m["_id"])

    return render_template(
        "admin_contact_messages.html",
        user=current_user(),
        messages=messages
    )
@app.route(
    "/admin/contact-messages/<int:mid>/status",
    methods=["POST"],
    endpoint="admin_contact_message_status"
)


@app.route(
    "/admin/contact-messages/<mid>/status",
    methods=["POST"],
    endpoint="admin_contact_message_status"
)
@login_required(role="admin")
def admin_contact_message_status(mid):
    status = (request.form.get("status") or "NEW").upper()

    if status not in ("NEW", "READ", "RESOLVED"):
        status = "NEW"

    try:
        mid_obj = ObjectId(mid)
    except Exception:
        flash("Invalid message.", "danger")
        return redirect(url_for("admin_contact_messages"))

    mongo.contact_messages.update_one(
        {"_id": mid_obj},
        {
            "$set": {
                "status": status,
                "updated_at": datetime.utcnow().isoformat()
            }
        }
    )

    flash("Message status updated.", "success")
    return redirect(url_for("admin_contact_messages"))


# ==================== AUTH API ====================

@app.route("/api/auth/web-session", methods=["POST"])
@api_login_required
def api_create_web_session():
    ...
    ...
    return jsonify({
            'success': False,
            'error': f'Internal server error: {str(e)}'
        }), 500


@app.route('/api/auth/login', methods=['POST'])
def api_auth_login():
    data = request.get_json(silent=True) or {}

    email = (data.get('email') or '').lower().strip()
    password = data.get('password') or ''

    if not email or not password:
        return jsonify({
            'success': False,
            'error': 'Email and password are required'
        }), 400

    u = mongo.users.find_one({"email": email})

    if not u:
        return jsonify({
            'success': False,
            'error': 'Invalid credentials'
        }), 401

    if not check_password_hash(u.get('password_hash', ''), password):
        return jsonify({
            'success': False,
            'error': 'Invalid credentials'
        }), 401

    if not u.get('is_active'):
        return jsonify({
            'success': False,
            'error': 'Account is inactive'
        }), 403

    user_id = str(u['_id'])
    token = generate_session_token(user_id)
    now = datetime.utcnow().isoformat()
    expires_at = (datetime.utcnow() + timedelta(days=30)).isoformat()

    mongo.api_sessions.insert_one({
        "user_id": user_id,
        "token": token,
        "created_at": now,
        "expires_at": expires_at
    })

    return jsonify({
        'success': True,
        'token': token,
        'user': {
            'id': user_id,
            'name': u.get('name', ''),
            'email': u.get('email', ''),
            'phone': u.get('phone', ''),
            'role': u.get('role', '')
        }
    })

@app.route('/api/auth/register', methods=['POST'])
def api_auth_register():
    data = request.get_json(silent=True) or {}

    name = (data.get('name') or '').strip()
    email = (data.get('email') or '').lower().strip()
    phone = (data.get('phone') or '').strip()
    password = data.get('password') or ''

    if not name or not email or not password:
        return jsonify({
            'success': False,
            'error': 'Missing required fields'
        }), 400

    if len(password) < 6:
        return jsonify({
            'success': False,
            'error': 'Password must be at least 6 characters'
        }), 400

    if phone:
        phone = normalize_phone(phone)

    try:
        result = mongo.users.insert_one({
            "name": name,
            "email": email,
            "phone": phone,
            "password_hash": generate_password_hash(password),
            "role": "customer",
            "phone_verified": 1,
            "is_active": 1,
            "created_at": datetime.utcnow().isoformat()
        })
    except DuplicateKeyError:
        return jsonify({
            'success': False,
            'error': 'Email or phone already registered'
        }), 409
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

    user_id = str(result.inserted_id)
    token = generate_session_token(user_id)
    now = datetime.utcnow().isoformat()
    expires_at = (datetime.utcnow() + timedelta(days=30)).isoformat()

    mongo.api_sessions.insert_one({
        "user_id": user_id,
        "token": token,
        "created_at": now,
        "expires_at": expires_at
    })

    return jsonify({
        'success': True,
        'token': token,
        'user': {
            'id': user_id,
            'name': name,
            'email': email,
            'phone': phone,
            'role': 'customer'
        }
    })

@app.route('/api/auth/logout', methods=['POST'])
@api_login_required
def api_auth_logout(user_id):
    auth_header = request.headers.get('Authorization', '')
    token = auth_header.replace('Bearer ', '').strip()

    if token:
        mongo.api_sessions.delete_one({
            "token": token,
            "user_id": str(user_id)
        })

    return jsonify({'success': True})

# ==================== PRODUCTS API ====================

# ==================== PRODUCTS API ====================

@app.route('/api/products', methods=['GET'])
def api_products_list():
    category = (request.args.get('category') or '').strip()
    sub_category = (request.args.get('sub_category') or '').strip()
    search = (request.args.get('search') or '').strip()

    allowed_categories = ['Fresh cuts', 'Ready to cook', 'Spices']
    fresh_cut_subs = ['Curry cuts', 'Boneless & Mince', 'Offals']

    mongo_filter = {
        "is_active": 1,
        "stock_kg": {"$gt": 0}
    }

    if category:
        if category not in allowed_categories:
            return jsonify({'success': False, 'error': 'Invalid category'}), 400

        mongo_filter["category"] = category

        if sub_category:
            if category != 'Fresh cuts':
                return jsonify({'success': False, 'error': 'sub_category only valid for Fresh cuts'}), 400

            if sub_category not in fresh_cut_subs:
                return jsonify({'success': False, 'error': 'Invalid sub_category'}), 400

            mongo_filter["sub_category"] = sub_category

    if search:
        mongo_filter["$or"] = [
            {"name": {"$regex": search, "$options": "i"}},
            {"store_name": {"$regex": search, "$options": "i"}},
            {"category": {"$regex": search, "$options": "i"}},
            {"sub_category": {"$regex": search, "$options": "i"}},
        ]

    products = list(
        mongo.products.find(mongo_filter).sort("created_at", -1).limit(100)
    )

    result = []

    for p in products:
        store = None

        if p.get("store_id"):
            store = mongo.stores.find_one({"_id": p.get("store_id")})

        ratings = list(mongo.product_ratings.find({
            "product_id": p["_id"]
        }))

        rating_count = len(ratings)

        avg_rating = round(
            sum(float(r.get("rating") or 0) for r in ratings) / rating_count,
            1
        ) if rating_count else 0

        result.append({
            "id": str(p["_id"]),
            "name": p.get("name", ""),
            "price_per_kg": float(p.get("price_per_kg") or 0),
            "stock_kg": float(p.get("stock_kg") or 0),
            "image_path": p.get("image_path", ""),
            "store_name": store.get("store_name") if store else p.get("store_name", ""),
            "store_id": str(p.get("store_id")) if p.get("store_id") else "",
            "avg_rating": float(avg_rating),
            "rating_count": int(rating_count),
            "category": p.get("category", ""),
            "sub_category": p.get("sub_category", ""),
        })

    return jsonify({
        "success": True,
        "products": result
    })


@app.route('/api/products/<pid>', methods=['GET'])
def api_product_detail(pid):
    try:
        pid_obj = ObjectId(pid)
    except Exception:
        return jsonify({
            "success": False,
            "error": "Invalid product id"
        }), 400

    p = mongo.products.find_one({
        "_id": pid_obj,
        "is_active": 1,
        "stock_kg": {"$gt": 0}
    })

    if not p:
        return jsonify({
            "success": False,
            "error": "Product not found"
        }), 404

    store = None

    if p.get("store_id"):
        store = mongo.stores.find_one({"_id": p.get("store_id")})

    ratings = list(
        mongo.product_ratings.find({
            "product_id": p["_id"]
        }).sort("created_at", -1)
    )

    rating_count = len(ratings)

    avg_rating = round(
        sum(float(r.get("rating") or 0) for r in ratings) / rating_count,
        1
    ) if rating_count else 0

    reviews = []

    for r in ratings[:20]:
        customer = None

        if r.get("user_id"):
            try:
                customer = mongo.users.find_one({"_id": ObjectId(r.get("user_id"))})
            except Exception:
                customer = None

        reviews.append({
            "rating": r.get("rating"),
            "comment": r.get("comment"),
            "customer_name": customer.get("name") if customer else "Customer",
            "created_at": r.get("created_at")
        })

    return jsonify({
        "success": True,
        "product": {
            "id": str(p["_id"]),
            "name": p.get("name", ""),
            "price_per_kg": float(p.get("price_per_kg") or 0),
            "stock_kg": float(p.get("stock_kg") or 0),
            "image_path": p.get("image_path", ""),
            "store_name": store.get("store_name") if store else p.get("store_name", ""),
            "store_id": str(p.get("store_id")) if p.get("store_id") else "",
            "avg_rating": float(avg_rating),
            "rating_count": int(rating_count),
            "category": p.get("category", ""),
            "sub_category": p.get("sub_category", ""),
            "reviews": reviews
        }
    })
# ==================== CATEGORIES API ====================

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

# ==================== USER PROFILE API ====================

# ==================== USER PROFILE API ====================

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


# ==================== CART API ====================

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

        items.append({
            'id': str(ci['_id']),
            'product_id': str(product['_id']),
            'name': product.get('name', ''),
            'price_per_kg': float(product.get('price_per_kg') or 0),
            'weight_kg': float(ci.get('weight_kg') or 0),
            'image_path': product.get('image_path', ''),
            'stock_kg': float(product.get('stock_kg') or 0),
            'store_id': str(product.get('store_id')) if product.get('store_id') else None,
        })

    total = sum([
        float(item['weight_kg'] or 0) * float(item['price_per_kg'] or 0)
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


# ==================== ADDRESSES API ====================

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


# API CHECKOUT (APP)
# ====================
# API CHECKOUT (FINAL FIXED)
# ====================

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

        weight_kg = float(ci.get("weight_kg") or 0)
        stock_kg = float(product.get("stock_kg") or 0)

        if int(product.get("is_active") or 0) != 1 or stock_kg <= 0:
            return jsonify({
                "success": False,
                "error": f"{product.get('name', 'Product')} is sold out"
            }), 400

        if weight_kg > stock_kg:
            return jsonify({
                "success": False,
                "error": f"{product.get('name', 'Product')} has only {stock_kg:.2f} kg available"
            }), 400

        items.append({
            "product_id": product["_id"],
            "product_name": product.get("name", ""),
            "weight_kg": weight_kg,
            "unit_price_per_kg": float(product.get("price_per_kg") or 0),
            "line_total": weight_kg * float(product.get("price_per_kg") or 0),
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
        mongo.order_items.insert_one({
            "order_id": order_id,
            "product_id": item["product_id"],
            "product_name": item["product_name"],
            "weight_kg": float(item["weight_kg"]),
            "unit_price_per_kg": float(item["unit_price_per_kg"]),
            "line_total": float(item["line_total"]),
            "image_path": item.get("image_path", "")
        })

        mongo.products.update_one(
            {"_id": item["product_id"]},
            {"$inc": {"stock_kg": -float(item["weight_kg"])}}
        )

        updated_product = mongo.products.find_one({"_id": item["product_id"]})

        if updated_product and float(updated_product.get("stock_kg") or 0) <= 0:
            mongo.products.update_one(
                {"_id": item["product_id"]},
                {"$set": {"stock_kg": 0, "is_active": 0}}
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


print("\n=== ROUTES LOADED ===")
print(app.url_map)
print("=====================\n")



if __name__ == '__main__':
    app.run(host="0.0.0.0",
        port=5000,
        debug=True,
        use_reloader=False)




