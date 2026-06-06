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
    - original_price_per_unit = store-entered base price for selected unit
    - price_per_unit = final customer selling price after discount
    - discount can be disabled, percent-based, or fixed-amount based
    """

    original_price = _safe_float(
        request_form.get("original_price_per_unit") or request_form.get("price_per_unit"),
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
        "original_price_per_unit": round(original_price, 2),
        "price_per_unit": round(final_price, 2),
        "discount_enabled": bool(discount_enabled and discount_amount > 0),
        "discount_type": discount_type,
        "discount_value": round(discount_value, 2),
        "discount_amount_per_unit": round(discount_amount, 2),
        "discount_percent": round(discount_percent, 2)
    }


# =========================================================
# PRODUCT UNIT HELPERS
# Supports kg, gram, liter, ml, packet, piece, bottle, box, etc.
# Single source of truth:
# quantity, unit_type, unit_label, price_per_unit, stock_quantity
# =========================================================

UNIT_OPTIONS = {
    "WEIGHT": ["kg", "gram"],
    "VOLUME": ["liter", "ml"],
    "COUNT": [
        "piece",
        "packet",
        "bottle",
        "box",
        "tray",
        "dozen",
        "bunch",
        "bundle",
        "set",
        "jar",
        "can",
        "pouch",
        "tin",
        "bag",
        "crate",
        "roll",
        "custom",
    ],
}

UNIT_TYPE_LABELS = {
    "WEIGHT": "Weight",
    "VOLUME": "Volume",
    "COUNT": "Count / Unit",
}


def normalize_unit_type(value):
    value = (value or "").strip().upper()

    if value in UNIT_OPTIONS:
        return value

    return "WEIGHT"


def normalize_unit_label(unit_type, unit_label, custom_unit_label=None):
    unit_type = normalize_unit_type(unit_type)

    unit_label = (unit_label or "").strip().lower()
    custom_unit_label = (custom_unit_label or "").strip().lower()

    allowed_labels = UNIT_OPTIONS.get(unit_type, [])

    if unit_label == "custom" and custom_unit_label:
        return custom_unit_label

    if unit_label in allowed_labels and unit_label != "custom":
        return unit_label

    if unit_type == "VOLUME":
        return "liter"

    if unit_type == "COUNT":
        return "piece"

    return "kg"


def unit_quantity_rules(unit_type, unit_label):
    unit_type = normalize_unit_type(unit_type)
    unit_label = (unit_label or "").strip().lower()

    if unit_label == "kg":
        return {
            "min": 0.25,
            "step": 0.25,
            "message": "Minimum 0.25 kg",
        }

    if unit_label == "gram":
        return {
            "min": 50,
            "step": 50,
            "message": "Minimum 50 gram",
        }

    if unit_label == "liter":
        return {
            "min": 0.25,
            "step": 0.25,
            "message": "Minimum 0.25 liter",
        }

    if unit_label == "ml":
        return {
            "min": 50,
            "step": 50,
            "message": "Minimum 50 ml",
        }

    return {
        "min": 1,
        "step": 1,
        "message": f"Minimum 1 {unit_label or 'unit'}",
    }


def normalize_quantity_by_unit(quantity, unit_type, unit_label):
    unit_type = normalize_unit_type(unit_type)
    unit_label = (unit_label or "").strip().lower()

    try:
        quantity = float(quantity or 0)
    except (TypeError, ValueError):
        quantity = 0

    rules = unit_quantity_rules(unit_type, unit_label)
    min_value = float(rules["min"])
    step_value = float(rules["step"])

    if quantity < min_value:
        return None, rules["message"]

    if step_value > 0:
        quantity = round(round(quantity / step_value) * step_value, 2)

    if unit_type == "COUNT":
        quantity = int(round(quantity))

        if quantity < 1:
            return None, rules["message"]

    return quantity, None


def product_unit_type(product):
    return normalize_unit_type(product.get("unit_type") or "WEIGHT")


def product_unit_label(product):
    unit_type = product_unit_type(product)

    return normalize_unit_label(
        unit_type,
        product.get("unit_label") or "kg",
        product.get("custom_unit_label")
    )


def product_price_per_unit(product):
    try:
        return float(product.get("price_per_unit") or 0)
    except (TypeError, ValueError):
        return 0.0


def product_original_price_per_unit(product):
    value = product.get("original_price_per_unit")

    if value is None:
        value = product.get("price_per_unit")

    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def product_mrp_per_unit(product):
    try:
        return float(product.get("mrp_per_unit") or product.get("old_price") or 0)
    except (TypeError, ValueError):
        return 0.0


def product_stock_quantity(product):
    try:
        return float(product.get("stock_quantity") or 0)
    except (TypeError, ValueError):
        return 0.0


def hydrate_product_unit_fields(product):
    unit_type = product_unit_type(product)
    unit_label = product_unit_label(product)
    rules = unit_quantity_rules(unit_type, unit_label)

    price_per_unit = product_price_per_unit(product)
    original_price_per_unit = product_original_price_per_unit(product)
    mrp_per_unit = product_mrp_per_unit(product)
    stock_quantity = product_stock_quantity(product)

    product["unit_type"] = unit_type
    product["unit_type_label"] = UNIT_TYPE_LABELS.get(unit_type, unit_type.title())
    product["unit_label"] = unit_label
    product["price_per_unit"] = price_per_unit
    product["original_price_per_unit"] = original_price_per_unit
    product["mrp_per_unit"] = mrp_per_unit
    product["stock_quantity"] = stock_quantity
    product["quantity_min"] = rules["min"]
    product["quantity_step"] = rules["step"]
    product["quantity_message"] = rules["message"]

    return product


def cart_item_quantity(cart_item):
    value = cart_item.get("cart_quantity")

    if value is None:
        value = cart_item.get("quantity")

    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def build_unit_product_update_from_form(form, fallback_original_price=0):
    unit_type = normalize_unit_type(form.get("unit_type"))

    unit_label = normalize_unit_label(
        unit_type,
        form.get("unit_label"),
        form.get("custom_unit_label")
    )

    pricing = _calculate_product_pricing_from_form(
        form,
        fallback_original_price=fallback_original_price
    )

    original_price_per_unit = float(pricing.get("original_price_per_unit") or 0)
    price_per_unit = float(pricing.get("price_per_unit") or 0)
    discount_amount_per_unit = float(pricing.get("discount_amount_per_unit") or 0)

    try:
        mrp_per_unit = float(form.get("mrp_per_unit") or 0)
    except (TypeError, ValueError):
        mrp_per_unit = 0.0

    try:
        stock_quantity = float(form.get("stock_quantity") or 0)
    except (TypeError, ValueError):
        stock_quantity = 0.0

    return {
        "unit_type": unit_type,
        "unit_label": unit_label,
        "original_price_per_unit": round(original_price_per_unit, 2),
        "price_per_unit": round(price_per_unit, 2),
        "mrp_per_unit": round(mrp_per_unit, 2),
        "stock_quantity": round(stock_quantity, 2),

        "discount_enabled": pricing.get("discount_enabled", False),
        "discount_type": pricing.get("discount_type", "percent"),
        "discount_value": pricing.get("discount_value", 0),
        "discount_amount_per_unit": round(discount_amount_per_unit, 2),
        "discount_percent": pricing.get("discount_percent", 0),
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




# Store location data in session; front-end JS should call this after getting geolocation & pincode


# Backend Update for app.py
# Add this endpoint after the /api/service/pincodes endpoint (around line 197)



# /detect-location?lat=..&lng=..&pincode=..&address=..

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
    hydrate_product_unit_fields(p)

    p["discount_enabled"] = bool(p.get("discount_enabled"))
    p["discount_percent"] = float(p.get("discount_percent") or 0)
    p["discount_amount_per_unit"] = float(p.get("discount_amount_per_unit") or 0)

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



# ----------------------
# LEGAL & HELP PAGES
# ----------------------





# ----------------------
# AUTH
# ----------------------

# ---------- Forgot Password ----------



# ---------- Register + OTP ----------








# ----------------------
# CUSTOMER PROFILE + ADDRESSES
# ----------------------












# =========================================================
# CUSTOMER COMPLAINTS
# =========================================================


# ----------------------
# CATALOG + CART
# ----------------------


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








# ----------------------
# CHECKOUT + ORDERS
# ----------------------


# Orders list

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
        item["quantity"] = float(item.get("quantity") or item.get("cart_quantity") or 0)
        item["unit_label"] = item.get("unit_label") or "unit"
        item["unit_type"] = item.get("unit_type") or "COUNT"
        item["price_per_unit"] = float(item.get("price_per_unit") or item.get("unit_price") or 0)
        item["unit_price"] = item["price_per_unit"]
        item["line_total"] = float(item.get("line_total") or (item["quantity"] * item["price_per_unit"]))

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




# ---------- Feedback ----------
def _clamp_rating(val):
    try:
        v = int(val)
    except (TypeError, ValueError):
        return None
    if v < 1 or v > 5:
        return None
    return v


# ----------------------
# DELIVERY
# ----------------------






# ----------------------
# DELIVERY API — Customer polls rider location
# ----------------------

# --- Product detail with ratings ---

# ======================
# CUSTOMER PRODUCT REVIEW
# ======================




# ----------- ALERTS -----------



# ======================
# UNIVERSAL SEARCH
# ======================

# ======================
# STORE CATALOG PAGE (also gated)
# ======================
# ======================
# STORE CATALOG PAGE / PUBLIC STORE PROFILE
# ======================

# ======================
# CUSTOMER STORE REVIEW
# ======================



# ----------------------
# Ratings routes — disabled (from feedback only)
# ----------------------




# ----------------------
# Complaints
# ----------------------

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
    buckets = defaultdict(lambda: {"sold": 0, "sold_quantity": 0.0, "revenue": 0.0})

    for row in mongo.order_items.find({}):
        pid = row.get("product_id")
        if pid is None:
            continue

        key = str(pid)
        buckets[key]["sold"] += 1
        buckets[key]["sold_quantity"] += float(row.get("quantity") or row.get("cart_quantity") or 0)
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
            "sold_quantity": round(agg["sold_quantity"], 2),
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
















# ---- Enable/Disable/Delete/Export per-user ----













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
















# ----------------------
# STORE
# ----------------------



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
        hydrate_product_unit_fields(p)

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











# =========================================================
# STORE REVIEWS
# =========================================================



# =========================================================
# STORE PRODUCT REVIEWS
# =========================================================


# =========================================================
# STORE COMPLAINTS
# =========================================================







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































# -----------------------------------------------------------------------------
# Mobile (token) orders API
# -----------------------------------------------------------------------------








# ----------------------
# NEWSLETTER & UPLOADS
# ----------------------


@app.after_request
def add_no_cache_headers(resp):
    # help fetch() always get fresh data
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    return resp
















# ==================== AUTH API ====================






# ==================== PRODUCTS API ====================

# ==================== PRODUCTS API ====================



# ==================== CATEGORIES API ====================


# ==================== USER PROFILE API ====================

# ==================== USER PROFILE API ====================





# ==================== CART API ====================





# ==================== ADDRESSES API ====================









# API CHECKOUT (APP)
# ====================
# API CHECKOUT (FINAL FIXED)
# ====================



print("\n=== ROUTES LOADED ===")
print(app.url_map)
print("=====================\n")



if __name__ == '__main__':
    app.run(host="0.0.0.0",
        port=5000,
        debug=True,
        use_reloader=False)

# Export all shared globals/helpers, including underscore-prefixed legacy helpers,
# so split route modules can preserve original app.py logic unchanged.
__all__ = [name for name in globals() if not name.startswith('__')]
