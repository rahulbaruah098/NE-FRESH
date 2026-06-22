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
import html
from flask import Flask, render_template, request,Response, redirect, url_for, session, flash, jsonify, send_file, abort
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from functools import wraps
from flask import make_response
from markupsafe import Markup
from collections import defaultdict
import smtplib

from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


# MongoDB imports
from bson import ObjectId
from pymongo.errors import DuplicateKeyError
from mongo_db import mongo, ensure_mongo_indexes

# ---- Env + Twilio
from dotenv import load_dotenv
load_dotenv()  # reads .env


app = Flask(__name__)

def _env_bool(name, default=False):
    raw = os.getenv(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() in ["1", "true", "yes", "on"]


def is_debug_logging_enabled():
    return _env_bool("NEFRESH_DEBUG_LOGS", False) or _env_bool("FLASK_DEBUG", False)


def log_debug(*args):
    if is_debug_logging_enabled():
        print(*args)


def log_warning(*args):
    print(*args)


# Production-safe secret handling.
# In production, set APP_SECRET_KEY or SECRET_KEY in .env / server environment.
# A random runtime key is used only as a last-resort fallback so the old hardcoded
# development key is never reused.
_app_secret = (os.getenv("APP_SECRET_KEY") or os.getenv("SECRET_KEY") or "").strip()
if not _app_secret:
    _app_secret = secrets.token_urlsafe(48)
    log_warning("[SECURITY WARNING] APP_SECRET_KEY/SECRET_KEY is not set. Using a temporary runtime key; sessions will reset on restart.")
app.secret_key = _app_secret


# WebView Session Configuration
from datetime import timedelta
app.config['SESSION_COOKIE_HTTPONLY'] = False  # kept for existing WebView compatibility
_session_cookie_secure = _env_bool("SESSION_COOKIE_SECURE", False)  # set true on HTTPS production
_session_cookie_samesite = (os.getenv("SESSION_COOKIE_SAMESITE") or "").strip()

# CSRF needs the browser session cookie to survive from GET /login to POST /login.
# Chrome/Safari reject SameSite=None cookies unless Secure=True; on localhost/http this
# made the CSRF token disappear and caused login to fail with "Security token expired".
# Use Lax for local/http by default, and allow None only when HTTPS/Secure is enabled.
if not _session_cookie_samesite:
    _session_cookie_samesite = "None" if _session_cookie_secure else "Lax"
elif _session_cookie_samesite.lower() == "none" and not _session_cookie_secure:
    log_warning("[SECURITY WARNING] SESSION_COOKIE_SAMESITE=None requires SESSION_COOKIE_SECURE=true. Falling back to Lax for local/http login compatibility.")
    _session_cookie_samesite = "Lax"

app.config['SESSION_COOKIE_SECURE'] = _session_cookie_secure
app.config['SESSION_COOKIE_SAMESITE'] = _session_cookie_samesite
app.config['SESSION_COOKIE_NAME'] = 'session'  # Consistent cookie name
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=7)
app.config['ENABLE_CSRF_PROTECTION'] = _env_bool("ENABLE_CSRF_PROTECTION", True)


# API CORS is restricted by default for production safety.
# Set CORS_ORIGINS in production, for example:
# CORS_ORIGINS=https://yourdomain.com,https://www.yourdomain.com
def _get_api_cors_origins():
    raw = (os.getenv("CORS_ORIGINS") or "").strip()
    if raw:
        if raw == "*":
            return "*"
        return [origin.strip() for origin in raw.split(",") if origin.strip()]
    return [
        "http://localhost:5000",
        "http://127.0.0.1:5000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]


_api_cors_origins = _get_api_cors_origins()
CORS(app, resources={
    r"/api/*": {
        "origins": _api_cors_origins,
        "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization"],
        "supports_credentials": bool(_api_cors_origins != "*"),
    }
})

log_debug("[RUNNING]", __file__)


# =========================================================
# BASIC CSRF PROTECTION FOR HTML FORMS
# =========================================================
# API endpoints remain exempt because the mobile app and third-party callbacks use
# token/header based flows. HTML forms automatically receive the token through the
# base templates and this validator checks unsafe same-origin form posts.
CSRF_EXEMPT_PATH_PREFIXES = (
    "/api/",
    "/static/",
)


def _get_csrf_token():
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


@app.context_processor
def _inject_csrf_helpers():
    def csrf_field():
        token = html.escape(_get_csrf_token(), quote=True)
        return Markup(f'<input type="hidden" name="csrf_token" value="{token}">')

    return {
        "csrf_token": _get_csrf_token(),
        "csrf_field": csrf_field,
    }


@app.before_request
def _protect_html_form_posts():
    if not app.config.get("ENABLE_CSRF_PROTECTION", True):
        return None

    if request.method in ["GET", "HEAD", "OPTIONS", "TRACE"]:
        return None

    path = request.path or ""
    if any(path.startswith(prefix) for prefix in CSRF_EXEMPT_PATH_PREFIXES):
        return None

    # Keep JSON API-style calls outside /api usable when they send an Authorization
    # header, but still protect normal browser form submissions.
    if request.is_json and request.headers.get("Authorization"):
        return None

    expected = session.get("_csrf_token")
    received = (
        request.form.get("csrf_token")
        or request.form.get("_csrf_token")
        or request.headers.get("X-CSRFToken")
        or request.headers.get("X-CSRF-Token")
        or ""
    )

    if not expected or not received or not secrets.compare_digest(str(expected), str(received)):
        if request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html:
            return jsonify({"ok": False, "error": "Security token expired. Please refresh and try again."}), 400
        abort(400, description="Security token expired. Please refresh and try again.")

    return None



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
    delivery_mode_settings = get_delivery_mode_settings()

    return {
        "datetime": datetime,
        "service_area": session.get("service_area"),
        "order_status_label": order_status_label,
        "delivery_mode_settings": delivery_mode_settings,
        "active_delivery_mode": delivery_mode_settings.get("active_delivery_mode", "IN_HOUSE"),
        "in_house_delivery_enabled": bool(delivery_mode_settings.get("in_house_delivery_enabled", True)),
        "external_delivery_enabled": bool(delivery_mode_settings.get("external_delivery_enabled", False)),
        "external_local_delivery_enabled": bool(delivery_mode_settings.get("external_local_delivery_enabled", False)),
        "third_party_shipping_enabled": bool(delivery_mode_settings.get("third_party_shipping_enabled", False)),
        "return_refund_enabled": bool(delivery_mode_settings.get("return_refund_enabled", True)),
        "delivery_mode_ui": get_delivery_mode_ui_context(delivery_mode_settings),
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
# Delivery boys should see only store shipment-ready, unassigned orders.
# New status: SHIPMENT_READY
# Legacy supported status: READY_FOR_PICKUP
DELIVERY_ACTIONABLE_STATUSES = ["SHIPMENT_READY", "READY_FOR_PICKUP"]

# Active orders already assigned to a delivery boy.
DELIVERY_ASSIGNED_ACTIVE_STATUSES = [
    "ASSIGNED_TO_DELIVERY",
    "REACHED_STORE",
    "PICKED_UP",
    "OUT_FOR_DELIVERY"
]

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

    default_min = float(rules["min"])
    quantity_step = float(rules["step"])

    try:
        custom_min = float(
            product.get("quantity_min")
            if product.get("quantity_min") is not None
            else product.get("min_order_quantity")
            if product.get("min_order_quantity") is not None
            else default_min
        )
    except (TypeError, ValueError):
        custom_min = default_min

    if custom_min < default_min:
        custom_min = default_min

    if unit_type == "COUNT":
        custom_min = int(round(custom_min))

        if custom_min < 1:
            custom_min = 1

    product["unit_type"] = unit_type
    product["unit_type_label"] = UNIT_TYPE_LABELS.get(unit_type, unit_type.title())
    product["unit_label"] = unit_label
    product["price_per_unit"] = price_per_unit
    product["original_price_per_unit"] = original_price_per_unit
    product["mrp_per_unit"] = mrp_per_unit
    product["stock_quantity"] = stock_quantity
    product["quantity_min"] = custom_min
    product["quantity_step"] = quantity_step
    product["quantity_message"] = f"Minimum {custom_min:g} {unit_label or 'unit'}"

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

    rules = unit_quantity_rules(unit_type, unit_label)
    default_min = float(rules["min"])
    quantity_step = float(rules["step"])

    try:
        quantity_min = float(
            form.get("quantity_min")
            or form.get("min_order_quantity")
            or default_min
        )
    except (TypeError, ValueError):
        quantity_min = default_min

    if quantity_min < default_min:
        quantity_min = default_min

    if unit_type == "COUNT":
        quantity_min = int(round(quantity_min))

        if quantity_min < 1:
            quantity_min = 1

    return {
        "unit_type": unit_type,
        "unit_label": unit_label,
        "original_price_per_unit": round(original_price_per_unit, 2),
        "price_per_unit": round(price_per_unit, 2),
        "mrp_per_unit": round(mrp_per_unit, 2),
        "stock_quantity": round(stock_quantity, 2),
        "quantity_min": quantity_min,
        "quantity_step": quantity_step,
        "quantity_message": f"Minimum {quantity_min:g} {unit_label or 'unit'}",

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

    # =========================================================
# PLATFORM FEE HELPERS
# Admin/platform owner earning from every order.
# Default is disabled, so existing checkout will not change
# until admin enables it from platform fee settings.
# =========================================================

PLATFORM_FEE_SETTINGS_KEY = "platform_fee"

DEFAULT_PLATFORM_FEE_SETTINGS = {
    "key": PLATFORM_FEE_SETTINGS_KEY,
    "enabled": False,

    # fixed / percent / fixed_plus_percent
    "fee_type": "fixed",

    # Fixed fee amount in INR.
    "fixed_amount": 0.0,

    # Percentage on product subtotal.
    "percent": 0.0,

    # Optional bounds.
    "min_fee": 0.0,
    "max_fee": 0.0,

    "display_name": "Platform Fee",
    "description": "Platform fee supports secure ordering, customer support, and platform operations.",
}


def _platform_fee_safe_float(value, default=0.0):
    try:
        if value is None or str(value).strip() == "":
            return float(default)

        number = float(value)

        if number < 0:
            return float(default)

        return float(number)
    except Exception:
        return float(default)


def get_platform_fee_settings():
    """
    Reads platform fee configuration from MongoDB.

    Collection:
        platform_settings

    Document:
        {
            "key": "platform_fee",
            "enabled": true/false,
            "fee_type": "fixed" / "percent" / "fixed_plus_percent",
            "fixed_amount": 10,
            "percent": 2,
            "min_fee": 5,
            "max_fee": 30,
            "display_name": "Platform Fee"
        }

    If no setting exists, returns safe disabled defaults.
    """
    settings = dict(DEFAULT_PLATFORM_FEE_SETTINGS)

    try:
        row = mongo.platform_settings.find_one({
            "key": PLATFORM_FEE_SETTINGS_KEY
        }) or {}

        if row:
            settings.update(row)
    except Exception:
        pass

    settings["enabled"] = bool(settings.get("enabled"))

    fee_type = (settings.get("fee_type") or "fixed").strip().lower()

    if fee_type not in ["fixed", "percent", "fixed_plus_percent"]:
        fee_type = "fixed"

    settings["fee_type"] = fee_type
    settings["fixed_amount"] = round(_platform_fee_safe_float(settings.get("fixed_amount"), 0), 2)
    settings["percent"] = round(_platform_fee_safe_float(settings.get("percent"), 0), 2)
    settings["min_fee"] = round(_platform_fee_safe_float(settings.get("min_fee"), 0), 2)
    settings["max_fee"] = round(_platform_fee_safe_float(settings.get("max_fee"), 0), 2)
    settings["display_name"] = (settings.get("display_name") or "Platform Fee").strip() or "Platform Fee"
    settings["description"] = (
        settings.get("description")
        or "Platform fee supports secure ordering, customer support, and platform operations."
    ).strip()

    return settings


def calculate_platform_fee(items_total):
    """
    Calculates admin/platform fee from item subtotal.

    Returns:
        {
            "platform_fee": 10.0,
            "admin_platform_earning": 10.0,
            "platform_fee_source": "admin_global_setting",
            "platform_fee_settings": {...}
        }

    If disabled:
        platform_fee = 0
        platform_fee_source = "disabled"
    """
    try:
        items_total = float(items_total or 0)
    except Exception:
        items_total = 0.0

    if items_total < 0:
        items_total = 0.0

    settings = get_platform_fee_settings()

    if not settings.get("enabled"):
        return {
            "platform_fee": 0.0,
            "admin_platform_earning": 0.0,
            "platform_fee_source": "disabled",
            "platform_fee_settings": settings
        }

    fee_type = settings.get("fee_type") or "fixed"
    fixed_amount = _platform_fee_safe_float(settings.get("fixed_amount"), 0)
    percent = _platform_fee_safe_float(settings.get("percent"), 0)
    min_fee = _platform_fee_safe_float(settings.get("min_fee"), 0)
    max_fee = _platform_fee_safe_float(settings.get("max_fee"), 0)

    platform_fee = 0.0

    if fee_type == "fixed":
        platform_fee = fixed_amount

    elif fee_type == "percent":
        platform_fee = items_total * (percent / 100)

    elif fee_type == "fixed_plus_percent":
        platform_fee = fixed_amount + (items_total * (percent / 100))

    if min_fee > 0 and platform_fee < min_fee:
        platform_fee = min_fee

    if max_fee > 0 and platform_fee > max_fee:
        platform_fee = max_fee

    platform_fee = round(platform_fee, 2)

    return {
        "platform_fee": platform_fee,
        "admin_platform_earning": platform_fee,
        "platform_fee_source": "admin_global_setting",
        "platform_fee_settings": settings
    }


def build_order_money_breakdown(items_total, delivery_fee=0, tip_amount=0, payment_method="COD"):
    """
    Central money breakdown for orders.

    Customer pays:
        items_total + delivery_fee + platform_fee + tip_amount

    Ownership:
        items_total => store earning
        platform_fee => admin earning
        delivery_fee/tip => delivery/delivery-settlement logic

    For COD:
        admin_platform_fee_status = DUE

    For online payment:
        admin_platform_fee_status = COLLECTED
    """
    items_total = round(_platform_fee_safe_float(items_total), 2)
    delivery_fee = round(_platform_fee_safe_float(delivery_fee), 2)
    tip_amount = round(_platform_fee_safe_float(tip_amount), 2)

    platform_result = calculate_platform_fee(items_total)
    platform_fee = round(_platform_fee_safe_float(platform_result.get("platform_fee")), 2)

    total_payable = round(items_total + delivery_fee + platform_fee + tip_amount, 2)

    payment_method_normalized = (payment_method or "COD").strip().upper()

    if payment_method_normalized in ["COD", "CASH", "CASH_ON_DELIVERY"]:
        admin_platform_fee_status = "DUE"
    else:
        admin_platform_fee_status = "COLLECTED"

    return {
        "items_subtotal": items_total,
        "total_amount": items_total,

        "delivery_fee": delivery_fee,
        "delivery_fee_amount": delivery_fee,

        "platform_fee": platform_fee,
        "admin_platform_earning": platform_fee,
        "platform_fee_source": platform_result.get("platform_fee_source"),
        "platform_fee_settings_snapshot": platform_result.get("platform_fee_settings"),

        "tip_amount": tip_amount,
        "delivery_tip_amount": tip_amount,

        "store_earning": items_total,
        "total_payable": total_payable,

        "settlement_status": "PENDING",
        "store_settlement_status": "PENDING",
        "admin_platform_fee_status": admin_platform_fee_status,
        "delivery_settlement_status": "PENDING",
    }


def _delivery_int(value, default=0):
    try:
        if value is None or str(value).strip() == "":
            return int(default)

        if isinstance(value, bool):
            return 1 if value else 0

        value_str = str(value).strip().lower()

        if value_str in ["true", "yes", "on"]:
            return 1

        if value_str in ["false", "no", "off"]:
            return 0

        return int(value)
    except Exception:
        return int(default)


def _delivery_float_or_none(value):
    try:
        if value is None or str(value).strip() == "":
            return None

        return float(value)
    except Exception:
        return None


def _delivery_float_or_default(value, default=0.0, minimum=0.0):
    try:
        if value is None or str(value).strip() == "":
            value = default
        value = float(value)
    except Exception:
        value = float(default)

    try:
        minimum = float(minimum)
    except Exception:
        minimum = 0.0

    if value < minimum:
        value = minimum

    return round(value, 3)


def parse_product_shipping_package_from_form(form, existing=None):
    """Return product-wise shipping/parcel dimensions from a store form.

    Values are optional; if blank, existing values are kept on edit or 0 is saved
    on create. Shiprocket quote/booking later falls back to global defaults when
    product values are missing.
    """
    existing = existing or {}

    def pick(name, default=0.0):
        raw = form.get(name)
        if raw is None or str(raw).strip() == "":
            raw = existing.get(name)
        return _delivery_float_or_default(raw, default, 0.0)

    weight = pick("shipping_weight_kg", existing.get("shipping_weight_kg") or 0.0)
    length = pick("shipping_length_cm", existing.get("shipping_length_cm") or 0.0)
    breadth = pick("shipping_breadth_cm", existing.get("shipping_breadth_cm") or 0.0)
    height = pick("shipping_height_cm", existing.get("shipping_height_cm") or 0.0)

    return {
        "shipping_weight_kg": weight,
        "shipping_length_cm": length,
        "shipping_breadth_cm": breadth,
        "shipping_height_cm": height,
    }


def build_checkout_package_snapshot(cart_items, external_settings=None):
    """Build one package snapshot for Shiprocket quote/booking.

    Each product can carry per-selling-unit shipping weight and dimensions.
    Checkout sums weight by cart quantity and uses the largest length/breadth/
    height as the parcel-box fallback. Missing product values safely fall back to
    Admin -> External Fare & Shiprocket Settings defaults.
    """
    external_settings = external_settings or {}
    cart_items = cart_items or []

    default_weight = _delivery_float_or_default(external_settings.get("default_package_weight_kg"), 1.0, 0.1)
    default_length = _delivery_float_or_default(external_settings.get("default_package_length_cm"), 10.0, 1.0)
    default_breadth = _delivery_float_or_default(external_settings.get("default_package_breadth_cm"), 10.0, 1.0)
    default_height = _delivery_float_or_default(external_settings.get("default_package_height_cm"), 10.0, 1.0)

    total_weight = 0.0
    max_length = 0.0
    max_breadth = 0.0
    max_height = 0.0
    product_count = 0
    used_product_dimensions = False

    for item in cart_items:
        if not item:
            continue

        try:
            quantity = float(item.get("quantity") or item.get("cart_quantity") or 1)
        except Exception:
            quantity = 1.0
        quantity = max(quantity, 1.0)

        weight = _delivery_float_or_none(item.get("shipping_weight_kg"))
        length = _delivery_float_or_none(item.get("shipping_length_cm"))
        breadth = _delivery_float_or_none(item.get("shipping_breadth_cm"))
        height = _delivery_float_or_none(item.get("shipping_height_cm"))

        if weight is not None and weight > 0:
            total_weight += float(weight) * quantity
            used_product_dimensions = True
        else:
            total_weight += default_weight * quantity

        if length is not None and length > 0:
            max_length = max(max_length, float(length))
            used_product_dimensions = True
        if breadth is not None and breadth > 0:
            max_breadth = max(max_breadth, float(breadth))
            used_product_dimensions = True
        if height is not None and height > 0:
            max_height = max(max_height, float(height))
            used_product_dimensions = True

        product_count += 1

    if product_count <= 0:
        total_weight = default_weight

    package = {
        "weight_kg": round(max(total_weight, 0.1), 3),
        "length_cm": round(max(max_length, default_length, 1.0), 2),
        "breadth_cm": round(max(max_breadth, default_breadth, 1.0), 2),
        "height_cm": round(max(max_height, default_height, 1.0), 2),
        "source": "product_dimensions" if used_product_dimensions else "admin_default_package",
        "product_count": int(product_count),
    }
    return package


DELIVERY_MODE_SETTINGS_KEY = "delivery_mode_settings"
EXTERNAL_DELIVERY_SETTINGS_KEY = "external_delivery_settings"

DELIVERY_MODE_IN_HOUSE = "IN_HOUSE"
DELIVERY_MODE_EXTERNAL_LOCAL = "EXTERNAL_LOCAL_DELIVERY"
DELIVERY_MODE_THIRD_PARTY = "THIRD_PARTY_SHIPPING"

VALID_DELIVERY_MODES = {
    DELIVERY_MODE_IN_HOUSE,
    DELIVERY_MODE_EXTERNAL_LOCAL,
    DELIVERY_MODE_THIRD_PARTY,
}

DELIVERY_ROUTING_MODE_AUTO = "AUTO_HYBRID"
DELIVERY_ROUTING_MODE_MANUAL = "MANUAL_SINGLE_MODE"

VALID_DELIVERY_ROUTING_MODES = {
    DELIVERY_ROUTING_MODE_AUTO,
    DELIVERY_ROUTING_MODE_MANUAL,
}

DELIVERY_OPERATION_IN_HOUSE_ONLY = "IN_HOUSE_ONLY"
DELIVERY_OPERATION_EXTERNAL_CONNECTED = "EXTERNAL_CONNECTED"

VALID_DELIVERY_OPERATION_MODES = {
    DELIVERY_OPERATION_IN_HOUSE_ONLY,
    DELIVERY_OPERATION_EXTERNAL_CONNECTED,
}

EXTERNAL_PAYMENT_RULE_ONLINE_ONLY = "ONLINE_ONLY"
EXTERNAL_PAYMENT_RULE_COD_STORE = "COD_STORE_COLLECTION"
EXTERNAL_PAYMENT_RULE_COD_PARTNER = "COD_PARTNER_COLLECTION"

VALID_EXTERNAL_PAYMENT_RULES = {
    EXTERNAL_PAYMENT_RULE_ONLINE_ONLY,
    EXTERNAL_PAYMENT_RULE_COD_STORE,
    EXTERNAL_PAYMENT_RULE_COD_PARTNER,
}

DELIVERY_PAYMENT_ONLINE_ONLY = "ONLINE_ONLY"
DELIVERY_PAYMENT_COD_ONLY = "COD_ONLY"
DELIVERY_PAYMENT_ONLINE_AND_COD = "ONLINE_AND_COD"

VALID_DELIVERY_PAYMENT_METHODS = {
    DELIVERY_PAYMENT_ONLINE_ONLY,
    DELIVERY_PAYMENT_COD_ONLY,
    DELIVERY_PAYMENT_ONLINE_AND_COD,
}

COD_COLLECTION_DELIVERY_BOY = "DELIVERY_BOY"
COD_COLLECTION_STORE = "STORE"
COD_COLLECTION_EXTERNAL_PARTNER = "EXTERNAL_PARTNER"

VALID_COD_COLLECTION_METHODS = {
    COD_COLLECTION_DELIVERY_BOY,
    COD_COLLECTION_STORE,
    COD_COLLECTION_EXTERNAL_PARTNER,
}

DEFAULT_DELIVERY_MODE_SETTINGS = {
    "key": DELIVERY_MODE_SETTINGS_KEY,

    # New simplified model:
    # Multiple delivery channels can be ON together and checkout selects the
    # correct channel per order. active_delivery_mode is kept only for old saved
    # records and manual fallback compatibility.
    "delivery_routing_mode": DELIVERY_ROUTING_MODE_MANUAL,
    "delivery_operation_mode": DELIVERY_OPERATION_IN_HOUSE_ONLY,
    "active_delivery_mode": DELIVERY_MODE_IN_HOUSE,

    # Channel availability switches shown on Admin -> Delivery Operation Settings.
    # In-house is a standalone operation mode. It cannot run together with the
    # connected external flow. External Local + Shiprocket can run together.
    "in_house_delivery_enabled": True,
    "external_local_delivery_enabled": False,
    "third_party_shipping_enabled": False,
    "external_delivery_enabled": False,

    # Related in-house modules.
    "delivery_boy_panel_enabled": True,
    "delivery_assignment_enabled": True,
    "delivery_tracking_enabled": True,
    "cod_rider_collection_enabled": True,

    # Related return/refund module.
    "return_refund_enabled": True,

    # Customer payment methods. Backend value COD is retained for compatibility,
    # but UI now displays it as Pay Online on Delivery.
    "delivery_payment_methods": DELIVERY_PAYMENT_ONLINE_AND_COD,
    "allow_online_payment": True,
    "allow_cod_payment": True,
    "cod_collection_method": COD_COLLECTION_DELIVERY_BOY,

    # Backward-compatible field used internally by older external-delivery code.
    "external_payment_rule": EXTERNAL_PAYMENT_RULE_COD_STORE,

    # Provider defaults. External local is deliberately manual/reference-only.
    # Shiprocket is used for third-party shipping when enabled and credentials exist.
    "external_local_provider": "LOCAL_DELIVERY_PARTNER",
    "third_party_provider": "SHIPROCKET",
}

DEFAULT_EXTERNAL_DELIVERY_SETTINGS = {
    "key": EXTERNAL_DELIVERY_SETTINGS_KEY,
    "shiprocket_enabled": False,
    "shiprocket_email": "",
    "shiprocket_password": "",
    "shiprocket_pickup_location": "",
    "shiprocket_pickup_pincode": "",
    "shiprocket_channel_id": "",
    "shiprocket_webhook_token": "",

    # Hyperlocal API fields are kept only for backward compatibility. The new
    # business rule keeps external local delivery simple: no live Rapido/Ola API
    # booking and no external local booking dashboard.
    "hyperlocal_enabled": False,
    "hyperlocal_provider": "LOCAL_DELIVERY_PARTNER",
    "hyperlocal_api_base_url": "",
    "hyperlocal_api_key": "",
    "hyperlocal_webhook_token": "",
    "manual_external_enabled": False,

    # Hard-coded assumed fare values for external local delivery and fallback
    # values for courier/Shiprocket if live quote is unavailable.
    "external_local_base_fee": 40.0,
    "external_local_per_km_fee": 8.0,
    "external_local_min_fee": 40.0,
    "external_local_max_distance_km": 25.0,
    "third_party_base_fee": 65.0,
    "third_party_per_km_fee": 0.0,
    "third_party_min_fee": 65.0,
    "third_party_max_distance_km": 9999.0,

    "default_package_weight_kg": 1.0,
    "default_package_length_cm": 10.0,
    "default_package_breadth_cm": 10.0,
    "default_package_height_cm": 10.0,

    # Optional local external delivery pickup/map settings.
    "external_default_pickup_latitude": None,
    "external_default_pickup_longitude": None,
    "external_service_zone_enabled": False,
    "external_service_zone_polygon": [],
}

def _delivery_mode_normalize(value):
    mode = (value or DELIVERY_MODE_IN_HOUSE).strip().upper()
    if mode not in VALID_DELIVERY_MODES:
        mode = DELIVERY_MODE_IN_HOUSE
    return mode


def _external_payment_rule_normalize(value):
    rule = (value or EXTERNAL_PAYMENT_RULE_ONLINE_ONLY).strip().upper()
    if rule not in VALID_EXTERNAL_PAYMENT_RULES:
        rule = EXTERNAL_PAYMENT_RULE_ONLINE_ONLY
    return rule


def _delivery_payment_methods_normalize(value):
    value = (value or "").strip().upper()
    if value not in VALID_DELIVERY_PAYMENT_METHODS:
        value = DELIVERY_PAYMENT_ONLINE_AND_COD
    return value


def _cod_collection_method_normalize(value, active_mode=None):
    value = (value or "").strip().upper()

    if active_mode == DELIVERY_MODE_IN_HOUSE:
        return COD_COLLECTION_DELIVERY_BOY

    if value not in VALID_COD_COLLECTION_METHODS or value == COD_COLLECTION_DELIVERY_BOY:
        value = COD_COLLECTION_STORE

    return value


def _payment_methods_from_legacy_external_rule(rule, active_mode):
    """
    Converts the older external_payment_rule setting into the clearer
    delivery_payment_methods + cod_collection_method fields.
    """
    rule = _external_payment_rule_normalize(rule)

    if active_mode == DELIVERY_MODE_IN_HOUSE:
        return DELIVERY_PAYMENT_ONLINE_AND_COD, COD_COLLECTION_DELIVERY_BOY

    if rule == EXTERNAL_PAYMENT_RULE_ONLINE_ONLY:
        return DELIVERY_PAYMENT_ONLINE_ONLY, COD_COLLECTION_STORE

    if rule == EXTERNAL_PAYMENT_RULE_COD_PARTNER:
        return DELIVERY_PAYMENT_ONLINE_AND_COD, COD_COLLECTION_EXTERNAL_PARTNER

    return DELIVERY_PAYMENT_ONLINE_AND_COD, COD_COLLECTION_STORE


def _external_payment_rule_from_methods(active_mode, allow_cod, cod_collection_method):
    """
    Keeps old external-delivery code compatible while the Admin UI now exposes
    clearer controls: Online, COD, or Online + COD.
    """
    if active_mode == DELIVERY_MODE_IN_HOUSE:
        return EXTERNAL_PAYMENT_RULE_COD_STORE

    if not allow_cod:
        return EXTERNAL_PAYMENT_RULE_ONLINE_ONLY

    if cod_collection_method == COD_COLLECTION_EXTERNAL_PARTNER:
        return EXTERNAL_PAYMENT_RULE_COD_PARTNER

    return EXTERNAL_PAYMENT_RULE_COD_STORE


def normalize_cod_collection_method(value, active_mode=None):
    return _cod_collection_method_normalize(value, active_mode)


def external_payment_rule_from_methods(active_mode, allow_cod, cod_collection_method):
    return _external_payment_rule_from_methods(active_mode, allow_cod, cod_collection_method)


def _delivery_routing_mode_normalize(value):
    value = (value or DELIVERY_ROUTING_MODE_AUTO).strip().upper()
    if value not in VALID_DELIVERY_ROUTING_MODES:
        value = DELIVERY_ROUTING_MODE_AUTO
    return value

def _delivery_operation_mode_normalize(value):
    value = (value or DELIVERY_OPERATION_IN_HOUSE_ONLY).strip().upper()
    if value not in VALID_DELIVERY_OPERATION_MODES:
        value = DELIVERY_OPERATION_IN_HOUSE_ONLY
    return value


def _delivery_payment_methods_from_flags(allow_online, allow_cod):
    allow_online = bool(allow_online)
    allow_cod = bool(allow_cod)

    if allow_online and allow_cod:
        return DELIVERY_PAYMENT_ONLINE_AND_COD

    if allow_cod:
        return DELIVERY_PAYMENT_COD_ONLY

    return DELIVERY_PAYMENT_ONLINE_ONLY


def _first_enabled_delivery_mode(settings):
    settings = settings or {}

    if settings.get("in_house_delivery_enabled"):
        return DELIVERY_MODE_IN_HOUSE

    if settings.get("external_local_delivery_enabled"):
        return DELIVERY_MODE_EXTERNAL_LOCAL

    if settings.get("third_party_shipping_enabled"):
        return DELIVERY_MODE_THIRD_PARTY

    return DELIVERY_MODE_IN_HOUSE


def get_delivery_mode_settings():
    """
    Delivery settings controlled from Admin Panel.

    Final simplified business rule:
    - In-house Delivery is standalone. When it is active, external local and
      Shiprocket delivery are disabled for checkout and existing in-house
      delivery-boy logic works exactly as before.
    - Connected External Delivery is separate. In that mode, in-house delivery
      is disabled and checkout automatically chooses External Local or
      Shiprocket according to distance/zone/serviceability.
    """
    settings = dict(DEFAULT_DELIVERY_MODE_SETTINGS)
    stored_row = {}

    try:
        stored_row = mongo.platform_settings.find_one({
            "key": DELIVERY_MODE_SETTINGS_KEY
        }) or {}

        if stored_row:
            settings.update(stored_row)
    except Exception:
        stored_row = {}

    legacy_active_mode = _delivery_mode_normalize(settings.get("active_delivery_mode"))

    if "delivery_operation_mode" in stored_row:
        operation_mode = _delivery_operation_mode_normalize(settings.get("delivery_operation_mode"))
    else:
        # Backward compatibility: older saves had only active_delivery_mode / channel
        # booleans. If in-house was active, treat it as standalone in-house.
        if legacy_active_mode == DELIVERY_MODE_IN_HOUSE or bool(settings.get("in_house_delivery_enabled", True)):
            operation_mode = DELIVERY_OPERATION_IN_HOUSE_ONLY
        else:
            operation_mode = DELIVERY_OPERATION_EXTERNAL_CONNECTED

    if operation_mode == DELIVERY_OPERATION_IN_HOUSE_ONLY:
        routing_mode = DELIVERY_ROUTING_MODE_MANUAL
        active_delivery_mode = DELIVERY_MODE_IN_HOUSE
        in_house_enabled = True
        external_local_enabled = False
        third_party_enabled = False
    else:
        routing_mode = DELIVERY_ROUTING_MODE_AUTO
        in_house_enabled = False
        external_local_enabled = bool(settings.get("external_local_delivery_enabled", True))
        third_party_enabled = bool(settings.get("third_party_shipping_enabled", True))

        if not external_local_enabled and not third_party_enabled:
            # External operation mode must have at least one external route.
            external_local_enabled = True

        active_delivery_mode = (
            DELIVERY_MODE_EXTERNAL_LOCAL
            if external_local_enabled
            else DELIVERY_MODE_THIRD_PARTY
        )

    external_enabled = bool(external_local_enabled or third_party_enabled)

    settings["delivery_operation_mode"] = operation_mode
    settings["delivery_routing_mode"] = routing_mode
    settings["active_delivery_mode"] = active_delivery_mode
    settings["in_house_delivery_enabled"] = bool(in_house_enabled)
    settings["external_local_delivery_enabled"] = bool(external_local_enabled)
    settings["third_party_shipping_enabled"] = bool(third_party_enabled)
    settings["external_delivery_enabled"] = bool(external_enabled)
    settings["shiprocket_shipping_enabled"] = bool(third_party_enabled)

    settings["delivery_boy_panel_enabled"] = bool(in_house_enabled)
    settings["delivery_assignment_enabled"] = bool(in_house_enabled)
    settings["delivery_tracking_enabled"] = bool(in_house_enabled)
    settings["cod_rider_collection_enabled"] = bool(in_house_enabled)

    settings["return_refund_enabled"] = bool(settings.get("return_refund_enabled", in_house_enabled))

    legacy_rule = _external_payment_rule_normalize(settings.get("external_payment_rule"))

    if "delivery_payment_methods" in stored_row:
        delivery_payment_methods = _delivery_payment_methods_normalize(settings.get("delivery_payment_methods"))
    else:
        delivery_payment_methods, _legacy_cod_method = _payment_methods_from_legacy_external_rule(
            legacy_rule,
            legacy_active_mode,
        )

    allow_online_payment = delivery_payment_methods in [
        DELIVERY_PAYMENT_ONLINE_ONLY,
        DELIVERY_PAYMENT_ONLINE_AND_COD,
    ]
    allow_cod_payment = delivery_payment_methods in [
        DELIVERY_PAYMENT_COD_ONLY,
        DELIVERY_PAYMENT_ONLINE_AND_COD,
    ]

    if not allow_online_payment and not allow_cod_payment:
        allow_online_payment = True
        delivery_payment_methods = DELIVERY_PAYMENT_ONLINE_ONLY

    # Backend keeps COD internally for compatibility. UI/business wording is
    # "Pay Online on Delivery". In-house keeps old rider collection flow.
    # External Local means customer pays Store/NE FRESH by UPI/online at handover;
    # Shiprocket is forced Online-only per order to avoid courier COD conflicts.
    cod_collection_method = (
        COD_COLLECTION_DELIVERY_BOY
        if in_house_enabled
        else COD_COLLECTION_STORE
    ) if allow_cod_payment else ""

    settings["delivery_payment_methods"] = delivery_payment_methods
    settings["allow_online_payment"] = bool(allow_online_payment)
    settings["allow_cod_payment"] = bool(allow_cod_payment)
    settings["cod_collection_method"] = cod_collection_method
    settings["external_payment_rule"] = _external_payment_rule_from_methods(
        DELIVERY_MODE_EXTERNAL_LOCAL,
        allow_cod_payment,
        COD_COLLECTION_STORE if allow_cod_payment else "",
    )

    settings["external_local_provider"] = "LOCAL_DELIVERY_PARTNER"
    settings["third_party_provider"] = "SHIPROCKET"

    return settings

def get_external_delivery_settings():
    settings = dict(DEFAULT_EXTERNAL_DELIVERY_SETTINGS)

    try:
        row = mongo.platform_settings.find_one({
            "key": EXTERNAL_DELIVERY_SETTINGS_KEY
        }) or {}

        if row:
            settings.update(row)
    except Exception:
        pass

    settings["shiprocket_enabled"] = bool(settings.get("shiprocket_enabled", False))
    settings["hyperlocal_enabled"] = bool(settings.get("hyperlocal_enabled", False))
    settings["manual_external_enabled"] = bool(settings.get("manual_external_enabled", True))

    for money_key in [
        "external_local_base_fee",
        "external_local_per_km_fee",
        "external_local_min_fee",
        "external_local_max_distance_km",
        "third_party_base_fee",
        "third_party_per_km_fee",
        "third_party_min_fee",
        "third_party_max_distance_km",
        "default_package_weight_kg",
        "default_package_length_cm",
        "default_package_breadth_cm",
        "default_package_height_cm",
    ]:
        try:
            settings[money_key] = round(float(settings.get(money_key) or DEFAULT_EXTERNAL_DELIVERY_SETTINGS[money_key]), 2)
        except Exception:
            settings[money_key] = DEFAULT_EXTERNAL_DELIVERY_SETTINGS[money_key]

    for coord_key in ["external_default_pickup_latitude", "external_default_pickup_longitude"]:
        try:
            raw_coord = settings.get(coord_key)
            settings[coord_key] = float(raw_coord) if raw_coord not in [None, ""] else None
        except Exception:
            settings[coord_key] = None

    settings["external_service_zone_enabled"] = bool(settings.get("external_service_zone_enabled", False))

    raw_polygon = settings.get("external_service_zone_polygon") or []
    if isinstance(raw_polygon, str):
        try:
            raw_polygon = json.loads(raw_polygon)
        except Exception:
            raw_polygon = []

    try:
        settings["external_service_zone_polygon"] = _clean_delivery_polygon(raw_polygon)
    except Exception:
        settings["external_service_zone_polygon"] = []

    return settings


def is_delivery_feature_enabled(feature_key="in_house_delivery_enabled", default=True):
    """
    Reads delivery-mode feature flags from Admin Delivery Mode Settings.
    Used by delivery/store/order/admin routes to block in-house delivery logic.
    """
    settings = get_delivery_mode_settings()
    return bool(settings.get(feature_key, default))


def is_return_refund_enabled():
    settings = get_delivery_mode_settings()
    return bool(settings.get("return_refund_enabled", True))


def is_in_house_delivery_enabled():
    settings = get_delivery_mode_settings()
    return bool(settings.get("in_house_delivery_enabled", True))


def is_external_delivery_enabled():
    settings = get_delivery_mode_settings()
    return bool(settings.get("external_delivery_enabled", False))


def get_active_delivery_mode():
    settings = get_delivery_mode_settings()
    return settings.get("active_delivery_mode") or DELIVERY_MODE_IN_HOUSE


def get_order_delivery_mode_snapshot(selected_mode=None):
    """
    Snapshot used when creating new orders so old orders do not change if Admin later changes routing settings.
    selected_mode should be the mode chosen by checkout routing for this specific order.
    """
    settings = get_delivery_mode_settings()

    if selected_mode:
        active_mode = _delivery_mode_normalize(selected_mode)
    elif settings.get("delivery_routing_mode") == DELIVERY_ROUTING_MODE_AUTO:
        active_mode = _first_enabled_delivery_mode(settings)
    else:
        active_mode = _delivery_mode_normalize(settings.get("active_delivery_mode"))

    in_house = active_mode == DELIVERY_MODE_IN_HOUSE
    external_enabled = active_mode in [DELIVERY_MODE_EXTERNAL_LOCAL, DELIVERY_MODE_THIRD_PARTY]

    if active_mode == DELIVERY_MODE_IN_HOUSE:
        delivery_type = "OWN_DELIVERY"
        provider_type = "IN_HOUSE"
        default_status = "NOT_APPLICABLE"
        allow_online_payment = bool(settings.get("allow_online_payment", True))
        allow_cod_payment = bool(settings.get("allow_cod_payment", True))
        cod_collection_method = COD_COLLECTION_DELIVERY_BOY if allow_cod_payment else ""
        external_payment_rule = EXTERNAL_PAYMENT_RULE_COD_STORE
        provider = "IN_HOUSE"
    elif active_mode == DELIVERY_MODE_THIRD_PARTY:
        delivery_type = "THIRD_PARTY_SHIPPING_PENDING"
        provider_type = "COURIER"
        default_status = "PENDING_SHIPROCKET_SHIPMENT"
        # Shiprocket/courier is online-only in this simplified business model.
        allow_online_payment = True
        allow_cod_payment = False
        cod_collection_method = ""
        external_payment_rule = EXTERNAL_PAYMENT_RULE_ONLINE_ONLY
        provider = "SHIPROCKET"
    else:
        delivery_type = "EXTERNAL_LOCAL_DELIVERY_REFERENCE_ONLY"
        provider_type = "LOCAL_DELIVERY"
        default_status = "ORDER_PLACED_EXTERNAL_LOCAL"
        allow_online_payment = bool(settings.get("allow_online_payment", True))
        allow_cod_payment = bool(settings.get("allow_cod_payment", True))
        cod_collection_method = COD_COLLECTION_STORE if allow_cod_payment else ""
        external_payment_rule = _external_payment_rule_from_methods(
            DELIVERY_MODE_EXTERNAL_LOCAL,
            allow_cod_payment,
            COD_COLLECTION_STORE if allow_cod_payment else "",
        )
        provider = "LOCAL_DELIVERY_PARTNER"

    delivery_payment_methods = _delivery_payment_methods_from_flags(
        allow_online_payment,
        allow_cod_payment,
    )

    return {
        "active_delivery_mode": active_mode,
        "in_house_delivery_enabled_at_order": bool(in_house),
        "external_delivery_enabled_at_order": bool(external_enabled),
        "delivery_type": delivery_type,
        "external_delivery_provider_type": provider_type,
        "external_delivery_status": default_status,
        "external_payment_rule": external_payment_rule,
        "delivery_payment_methods": delivery_payment_methods,
        "allow_online_payment": bool(allow_online_payment),
        "allow_cod_payment": bool(allow_cod_payment),
        "cod_collection_method": cod_collection_method,
        "external_local_provider": "LOCAL_DELIVERY_PARTNER",
        "third_party_provider": "SHIPROCKET",
        "external_delivery_provider": provider,
    }




# =========================================================
# DELIVERY MODE UI HELPERS
# =========================================================
def _delivery_payment_rule_label(settings):
    settings = settings or {}
    methods = settings.get("delivery_payment_methods") or DELIVERY_PAYMENT_ONLINE_AND_COD
    cod_method = settings.get("cod_collection_method") or ""

    if methods == DELIVERY_PAYMENT_ONLINE_ONLY:
        return "Online payment only"

    if methods == DELIVERY_PAYMENT_COD_ONLY:
        if cod_method == COD_COLLECTION_EXTERNAL_PARTNER:
            return "Pay Online on Delivery - partner collection"
        if cod_method == COD_COLLECTION_STORE:
            return "Pay Online on Delivery - store/NE FRESH collection"
        return "Pay Online on Delivery - delivery boy collection"

    if cod_method == COD_COLLECTION_EXTERNAL_PARTNER:
        return "Online + Pay Online on Delivery - partner collection"
    if cod_method == COD_COLLECTION_STORE:
        return "Online + Pay Online on Delivery - store/NE FRESH collection"
    return "Online + Pay Online on Delivery - delivery boy collection"


def get_delivery_mode_ui_context(settings=None):
    """
    Small template-safe dictionary used by dashboards / checkout / tracking.

    This does not change delivery logic. It only describes the currently
    active delivery mode so templates can show the correct wording, buttons
    and guidance.
    """
    settings = settings or get_delivery_mode_settings()
    active_mode = settings.get("active_delivery_mode") or DELIVERY_MODE_IN_HOUSE
    external_rule = settings.get("external_payment_rule") or EXTERNAL_PAYMENT_RULE_COD_STORE
    allow_cod_payment = bool(settings.get("allow_cod_payment", True))
    allow_online_payment = bool(settings.get("allow_online_payment", True))
    payment_rule_label = _delivery_payment_rule_label(settings)

    if settings.get("delivery_routing_mode") == DELIVERY_ROUTING_MODE_AUTO and not settings.get("_force_single_mode_ui"):
        enabled_labels = []
        if settings.get("in_house_delivery_enabled"):
            enabled_labels.append("In-house")
        if settings.get("external_local_delivery_enabled"):
            enabled_labels.append("External Local")
        if settings.get("third_party_shipping_enabled"):
            enabled_labels.append("Shiprocket")

        enabled_text = " + ".join(enabled_labels) if enabled_labels else "In-house"

        return {
            "active_mode": DELIVERY_ROUTING_MODE_AUTO,
            "mode": DELIVERY_ROUTING_MODE_AUTO,
            "is_in_house": bool(settings.get("in_house_delivery_enabled", True)),
            "is_external": True,
            "is_external_local": bool(settings.get("external_local_delivery_enabled", False)),
            "is_third_party": bool(settings.get("third_party_shipping_enabled", False)),
            "label": "Connected External Delivery Routing",
            "short_label": "External Routing",
            "icon": "🚚",
            "provider": "AUTO",
            "provider_label": f"External route selected at checkout ({enabled_text})",
            "provider_type": "External Routing",
            "fee_label": "External Route Delivery Charge",
            "checkout_note": "Checkout chooses External Local or Shiprocket before payment, calculates the delivery fee first, and saves that route inside the order.",
            "customer_track_title": "Delivery Tracking",
            "customer_track_copy": "In-house shows delivery-boy tracking after assignment. External local uses the NE FRESH order ID. Shiprocket shows AWB/courier tracking after booking.",
            "store_dashboard_title": "Connected External Delivery Routing",
            "store_dashboard_copy": "External Local orders use hard-coded fare, and outside-local orders use Shiprocket.",
            "admin_dashboard_title": "Connected External Delivery Routing",
            "admin_dashboard_copy": "Checkout chooses the external route per order based on local distance/zone and Shiprocket availability.",
            "cod_allowed": allow_cod_payment,
            "online_allowed": allow_online_payment,
            "online_only": allow_online_payment and not allow_cod_payment,
            "payment_rule": external_rule,
            "payment_rule_label": payment_rule_label,
            "primary_store_action": "Store Orders",
            "primary_admin_action": "Delivery Routing Settings",
            "view_all_label": "View Orders",
        }

    if active_mode == DELIVERY_MODE_THIRD_PARTY:
        provider = settings.get("third_party_provider") or "SHIPROCKET"
        return {
            "active_mode": active_mode,
            "mode": active_mode,
            "is_in_house": False,
            "is_external": True,
            "is_external_local": False,
            "is_third_party": True,
            "label": "Third-party Shipping",
            "short_label": "Courier / Shiprocket",
            "icon": "📦",
            "provider": provider,
            "provider_label": str(provider).replace("_", " ").title(),
            "provider_type": "Courier",
            "fee_label": "Courier Delivery Charge",
            "checkout_note": "Delivery charge is quoted from the courier/Shiprocket layer or from Admin fallback shipping settings.",
            "customer_track_title": "Courier Tracking",
            "customer_track_copy": "Your order will move through packed, courier booked, AWB generated, in transit, out for delivery and delivered stages.",
            "store_dashboard_title": "Courier Shipping Mode",
            "store_dashboard_copy": "Prepare the package and mark it ready for external courier booking. Delivery-boy assignment is hidden for this mode.",
            "admin_dashboard_title": "Courier / Shiprocket Mode Active",
            "admin_dashboard_copy": "Orders are routed to the Shiprocket shipments page for AWB/tracking updates.",
            "cod_allowed": allow_cod_payment,
            "online_allowed": allow_online_payment,
            "online_only": allow_online_payment and not allow_cod_payment,
            "payment_rule": external_rule,
            "payment_rule_label": payment_rule_label,
            "primary_store_action": "Shiprocket Shipments",
            "primary_admin_action": "Shiprocket Orders",
            "view_all_label": "View Shiprocket / Courier Orders",
        }

    if active_mode == DELIVERY_MODE_EXTERNAL_LOCAL:
        provider = settings.get("external_local_provider") or "MANUAL_HYPERLOCAL"
        return {
            "active_mode": active_mode,
            "mode": active_mode,
            "is_in_house": False,
            "is_external": True,
            "is_external_local": True,
            "is_third_party": False,
            "label": "External Local Delivery",
            "short_label": "External Local",
            "icon": "⚡",
            "provider": provider,
            "provider_label": str(provider).replace("_", " ").title(),
            "provider_type": "Hyperlocal",
            "fee_label": "External Local Delivery Charge",
            "checkout_note": "Delivery charge is calculated from Admin hard-coded local fare rules. No live Rapido/Ola/Uber booking is stored.",
            "customer_track_title": "External Local Delivery Tracking",
            "customer_track_copy": "Use your NE FRESH order reference for local delivery support. No live Rapido/Ola/Uber tracking record is stored in NE FRESH.",
            "store_dashboard_title": "External Local Delivery Mode",
            "store_dashboard_copy": "Handle this order normally and use the NE FRESH order reference. No external local booking dashboard is created.",
            "admin_dashboard_title": "External Local Delivery Mode Active",
            "admin_dashboard_copy": "External local orders use hard-coded fare and the NE FRESH order reference only. Shiprocket shipments are managed separately.",
            "cod_allowed": allow_cod_payment,
            "online_allowed": allow_online_payment,
            "online_only": allow_online_payment and not allow_cod_payment,
            "payment_rule": external_rule,
            "payment_rule_label": payment_rule_label,
            "primary_store_action": "Normal Orders",
            "primary_admin_action": "Delivery Routing Settings",
            "view_all_label": "View Normal Orders",
        }

    return {
        "active_mode": DELIVERY_MODE_IN_HOUSE,
        "mode": DELIVERY_MODE_IN_HOUSE,
        "is_in_house": True,
        "is_external": False,
        "is_external_local": False,
        "is_third_party": False,
        "label": "In-house Delivery",
        "short_label": "NE FRESH Delivery Boys",
        "icon": "🛵",
        "provider": "IN_HOUSE",
        "provider_label": "NE FRESH Delivery",
        "provider_type": "In-house",
        "fee_label": "Delivery Charge",
        "checkout_note": "Delivery charge is calculated from the existing NE FRESH delivery fee/serviceability logic.",
        "customer_track_title": "Live Delivery Tracking",
        "customer_track_copy": "Your order will move through store confirmation, packaging, delivery assignment, pickup, out for delivery and delivered stages.",
        "store_dashboard_title": "In-house Delivery Mode",
        "store_dashboard_copy": "Use store order management to prepare orders, mark shipment ready and assign NE FRESH delivery boys.",
        "admin_dashboard_title": "In-house Delivery Mode Active",
        "admin_dashboard_copy": "Delivery boy dashboard, delivery assignment and rider cash settlement are active.",
        "cod_allowed": allow_cod_payment,
        "online_allowed": allow_online_payment,
        "online_only": allow_online_payment and not allow_cod_payment,
        "payment_rule": "IN_HOUSE",
        "payment_rule_label": payment_rule_label,
        "primary_store_action": "Delivery Control",
        "primary_admin_action": "Delivery Overview",
        "view_all_label": "View All Orders",
    }


def get_order_delivery_mode_ui(order_doc=None):
    """Returns delivery-mode display data for a specific order snapshot."""
    order_doc = order_doc or {}
    settings = get_delivery_mode_settings()
    settings = dict(settings)

    order_mode = (
        order_doc.get("active_delivery_mode")
        or (DELIVERY_MODE_IN_HOUSE if order_doc.get("in_house_delivery_enabled_at_order", True) else DELIVERY_MODE_EXTERNAL_LOCAL)
        or DELIVERY_MODE_IN_HOUSE
    )
    settings["active_delivery_mode"] = _delivery_mode_normalize(order_mode)
    settings["_force_single_mode_ui"] = True

    if settings["active_delivery_mode"] == DELIVERY_MODE_THIRD_PARTY:
        settings["third_party_provider"] = (
            order_doc.get("external_delivery_provider")
            or order_doc.get("third_party_provider")
            or settings.get("third_party_provider")
            or "SHIPROCKET"
        )
    elif settings["active_delivery_mode"] == DELIVERY_MODE_EXTERNAL_LOCAL:
        settings["external_local_provider"] = (
            order_doc.get("external_delivery_provider")
            or order_doc.get("external_local_provider")
            or settings.get("external_local_provider")
            or "MANUAL_HYPERLOCAL"
        )

    settings["external_payment_rule"] = (
        order_doc.get("external_payment_rule")
        or settings.get("external_payment_rule")
        or EXTERNAL_PAYMENT_RULE_COD_STORE
    )
    settings["delivery_payment_methods"] = (
        order_doc.get("delivery_payment_methods")
        or settings.get("delivery_payment_methods")
        or DELIVERY_PAYMENT_ONLINE_AND_COD
    )
    settings["allow_online_payment"] = bool(
        order_doc.get("allow_online_payment")
        if order_doc.get("allow_online_payment") is not None
        else settings.get("allow_online_payment", True)
    )
    settings["allow_cod_payment"] = bool(
        order_doc.get("allow_cod_payment")
        if order_doc.get("allow_cod_payment") is not None
        else settings.get("allow_cod_payment", True)
    )
    settings["cod_collection_method"] = (
        order_doc.get("cod_collection_method")
        or settings.get("cod_collection_method")
        or COD_COLLECTION_DELIVERY_BOY
    )

    return get_delivery_mode_ui_context(settings)


def decorate_order_delivery_mode_display(order_doc):
    """
    Adds customer/admin/store-friendly delivery mode fields to an order dict.
    Existing database values are not changed.
    """
    if not order_doc:
        return order_doc

    ui = get_order_delivery_mode_ui(order_doc)
    order_doc["delivery_mode_ui"] = ui
    order_doc["delivery_mode_label"] = ui.get("label")
    order_doc["delivery_mode_short_label"] = ui.get("short_label")
    order_doc["delivery_mode_icon"] = ui.get("icon")
    order_doc["delivery_fee_label"] = ui.get("fee_label")
    order_doc["delivery_provider_label"] = (
        order_doc.get("external_delivery_partner_name")
        or order_doc.get("external_delivery_provider")
        or ui.get("provider_label")
    )
    order_doc["external_tracking_code"] = (
        order_doc.get("external_awb")
        or order_doc.get("external_shipment_id")
        or order_doc.get("external_order_id")
        or ""
    )
    order_doc["external_tracking_available"] = bool(
        order_doc.get("external_tracking_url")
        or order_doc.get("external_awb")
        or order_doc.get("external_order_id")
        or order_doc.get("external_shipment_id")
    )
    order_doc["external_delivery_status_label"] = (
        str(order_doc.get("external_delivery_status") or order_doc.get("external_delivery_booking_status") or "")
        .replace("_", " ")
        .title()
    )
    return order_doc


def build_delivery_mode_order_metrics(store_id=None):
    """
    Lightweight mode-aware order counts for dashboards.

    It supports both ObjectId and string store_id records and only reads orders.
    """
    query = {}

    if store_id is not None:
        store_values = [store_id, str(store_id)]
        try:
            store_values.append(ObjectId(str(store_id)))
        except Exception:
            pass
        query["store_id"] = {"$in": store_values}

    try:
        docs = list(mongo.orders.find(query, {
            "active_delivery_mode": 1,
            "in_house_delivery_enabled_at_order": 1,
            "external_delivery_enabled_at_order": 1,
            "external_delivery_booking_status": 1,
            "external_delivery_status": 1,
            "status": 1,
        }))
    except Exception:
        docs = []

    output = {
        "mode_in_house_orders": 0,
        "mode_external_local_orders": 0,
        "mode_third_party_orders": 0,
        "mode_external_orders": 0,
        "mode_external_pending_booking": 0,
        "mode_external_ready_for_booking": 0,
        "mode_external_booked": 0,
        "mode_external_delivered": 0,
        "mode_external_failed": 0,
        "mode_active_orders": 0,
        "mode_view_all_orders": len(docs),
    }

    active_mode = get_active_delivery_mode()

    for doc in docs:
        mode = _delivery_mode_normalize(
            doc.get("active_delivery_mode")
            or (DELIVERY_MODE_IN_HOUSE if doc.get("in_house_delivery_enabled_at_order", True) else DELIVERY_MODE_EXTERNAL_LOCAL)
        )

        if mode == DELIVERY_MODE_IN_HOUSE:
            output["mode_in_house_orders"] += 1
        elif mode == DELIVERY_MODE_THIRD_PARTY:
            output["mode_third_party_orders"] += 1
            output["mode_external_orders"] += 1
        else:
            output["mode_external_local_orders"] += 1
            output["mode_external_orders"] += 1

        if mode == active_mode:
            output["mode_active_orders"] += 1

        external_status = str(doc.get("external_delivery_status") or "").upper()
        booking_status = str(doc.get("external_delivery_booking_status") or "").upper()
        order_status = str(doc.get("status") or "").upper()

        if mode != DELIVERY_MODE_IN_HOUSE:
            if booking_status in ["", "PENDING_BOOKING", "NOT_BOOKED"] or external_status.startswith("PENDING"):
                output["mode_external_pending_booking"] += 1
            if booking_status in ["READY_FOR_BOOKING"] or external_status in ["READY_FOR_EXTERNAL_BOOKING"]:
                output["mode_external_ready_for_booking"] += 1
            if booking_status in ["BOOKED", "MANUAL_BOOKING_RECORDED"] or external_status in ["BOOKED", "AWB_GENERATED", "IN_TRANSIT", "OUT_FOR_DELIVERY"]:
                output["mode_external_booked"] += 1
            if order_status == "DELIVERED" or external_status in ["DELIVERED", "SHIPMENT_DELIVERED", "DELIVERY_DELIVERED"]:
                output["mode_external_delivered"] += 1
            if order_status in ["DELIVERY_FAILED", "CANCELLED"] or external_status in ["FAILED", "DELIVERY_FAILED", "CANCELLED", "RTO", "RETURNED"]:
                output["mode_external_failed"] += 1

    return output



DELIVERY_FEE_SETTINGS_KEY = "delivery_fee_settings"


def _delivery_clean_platform_fee_slabs(raw_slabs):
    cleaned = []

    if not raw_slabs:
        return cleaned

    if isinstance(raw_slabs, str):
        try:
            raw_slabs = json.loads(raw_slabs)
        except Exception:
            return cleaned

    if not isinstance(raw_slabs, list):
        return cleaned

    for row in raw_slabs:
        if not isinstance(row, dict):
            continue

        min_km = _delivery_float_or_none(row.get("min_km"))
        max_km = _delivery_float_or_none(row.get("max_km"))
        fee = _delivery_float_or_none(row.get("fee"))

        if min_km is None:
            min_km = 0.0

        if min_km < 0:
            continue

        if fee is None or fee < 0:
            continue

        if max_km is not None and max_km <= min_km:
            continue

        cleaned.append({
            "min_km": round(float(min_km), 3),
            "max_km": round(float(max_km), 3) if max_km is not None else None,
            "fee": round(float(fee), 2)
        })

    cleaned.sort(key=lambda x: float(x.get("min_km") or 0))

    return cleaned


def get_platform_delivery_fee_settings():
    """
    Admin-controlled delivery fee settings.

    Store can enable/disable delivery, but delivery fee/rate/slab/free delivery
    settings must come only from mongo.platform_settings.
    """
    settings = {}

    try:
        settings = mongo.platform_settings.find_one({
            "key": DELIVERY_FEE_SETTINGS_KEY
        }) or {}
    except Exception:
        settings = {}

    base_fee = _delivery_float_or_none(settings.get("delivery_base_fee"))

    if base_fee is None:
        base_fee = float(BASE_DELIVERY_FEE_INR)

    free_delivery_above = _delivery_float_or_none(settings.get("free_delivery_above"))

    if free_delivery_above is None:
        free_delivery_above = 0.0

    delivery_min_order_amount = _delivery_float_or_none(
        settings.get("delivery_min_order_amount")
    )

    if delivery_min_order_amount is None:
        delivery_min_order_amount = 0.0

    max_delivery_distance_km = _delivery_float_or_none(
        settings.get("max_delivery_distance_km")
    )

    slabs_enabled = bool(settings.get("delivery_fee_slabs_enabled", False))
    slabs = _delivery_clean_platform_fee_slabs(
        settings.get("delivery_fee_slabs") or []
    )

    return {
        "key": DELIVERY_FEE_SETTINGS_KEY,
        "delivery_base_fee": round(float(base_fee), 2),
        "free_delivery_above": round(float(free_delivery_above), 2),
        "delivery_min_order_amount": round(float(delivery_min_order_amount), 2),
        "max_delivery_distance_km": max_delivery_distance_km,
        "delivery_fee_slabs_enabled": bool(slabs_enabled),
        "delivery_fee_slabs": slabs,
        "delivery_boy_earning_rule": settings.get("delivery_boy_earning_rule") or "DELIVERY_FEE_PLUS_TIP",
        "notes": settings.get("notes") or "",
        "updated_at": settings.get("updated_at") or "",
        "updated_by_name": settings.get("updated_by_name") or ""
    }

def _clean_delivery_polygon(polygon):
    """
    Expected format:
    [
      [lat, lng],
      [lat, lng],
      [lat, lng]
    ]

    Returns a clean list of [lat, lng].
    """
    if not isinstance(polygon, list):
        return []

    cleaned = []

    for point in polygon:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            continue

        lat = _delivery_float_or_none(point[0])
        lng = _delivery_float_or_none(point[1])

        if lat is None or lng is None:
            continue

        if lat < -90 or lat > 90:
            continue

        if lng < -180 or lng > 180:
            continue

        cleaned.append([lat, lng])

    if len(cleaned) < 3:
        return []

    return cleaned


def point_in_polygon(lat, lng, polygon):
    """
    Ray-casting algorithm.

    IMPORTANT:
    polygon points are stored as [lat, lng].
    x = lng
    y = lat
    """
    lat = _delivery_float_or_none(lat)
    lng = _delivery_float_or_none(lng)
    polygon = _clean_delivery_polygon(polygon)

    if lat is None or lng is None or len(polygon) < 3:
        return False

    x = lng
    y = lat
    inside = False

    j = len(polygon) - 1

    for i in range(len(polygon)):
        yi = float(polygon[i][0])
        xi = float(polygon[i][1])

        yj = float(polygon[j][0])
        xj = float(polygon[j][1])

        intersects = (
            ((yi > y) != (yj > y))
            and
            (x < ((xj - xi) * (y - yi) / ((yj - yi) or 0.0000000001) + xi))
        )

        if intersects:
            inside = not inside

        j = i

    return inside


def normalize_store_delivery_status(store):
    """
    Normalizes old + new store delivery fields.

    Store controls only:
    - active / online
    - delivery enabled
    - store coordinates
    - delivery zone

    Delivery fee/rate/slab/free delivery/min order are Admin-controlled only.
    """
    store = store or {}

    is_active = _delivery_int(store.get("is_active", 1), 1)
    is_online = _delivery_int(store.get("is_online", store.get("is_open", 1)), 1)

    delivery_enabled = _delivery_int(
        store.get(
            "delivery_enabled",
            1 if store.get("delivery_available", False) else 0
        ),
        0
    )

    delivery_mode = (store.get("delivery_mode") or "polygon").strip().lower()

    if delivery_mode not in ["polygon"]:
        delivery_mode = "polygon"

    store_lat = _delivery_float_or_none(store.get("latitude"))
    store_lng = _delivery_float_or_none(store.get("longitude"))

    polygon = _clean_delivery_polygon(store.get("delivery_zone_polygon") or [])

    zone_configured = 1 if len(polygon) >= 3 else _delivery_int(
        store.get("delivery_zone_configured"),
        0
    )

    delivery_settings = get_platform_delivery_fee_settings()

    return {
        "is_active": is_active,
        "is_online": is_online,
        "delivery_enabled": delivery_enabled,
        "delivery_mode": delivery_mode,
        "store_lat": store_lat,
        "store_lng": store_lng,
        "delivery_zone_polygon": polygon,
        "delivery_zone_configured": zone_configured,

        # Admin-controlled delivery fee settings.
        "delivery_base_fee": delivery_settings.get("delivery_base_fee", BASE_DELIVERY_FEE_INR),
        "delivery_fee_slabs_enabled": delivery_settings.get("delivery_fee_slabs_enabled", False),
        "delivery_fee_slabs": delivery_settings.get("delivery_fee_slabs") or [],
        "free_delivery_above": delivery_settings.get("free_delivery_above", 0),
        "delivery_min_order_amount": delivery_settings.get("delivery_min_order_amount", 0),
        "max_delivery_distance_km": delivery_settings.get("max_delivery_distance_km"),
        "delivery_boy_earning_rule": delivery_settings.get("delivery_boy_earning_rule", "DELIVERY_FEE_PLUS_TIP"),
    }


def _clean_delivery_fee_slabs(raw_slabs):
    """
    Cleans store delivery fee slabs.

    Expected format:
    [
        {"min_km": 0, "max_km": 2, "fee": 40},
        {"min_km": 2, "max_km": 5, "fee": 60},
        {"min_km": 5, "max_km": "", "fee": 100}
    ]

    max_km can be blank/None for open-ended final slab.
    """
    cleaned = []

    if not raw_slabs:
        return cleaned

    if isinstance(raw_slabs, str):
        try:
            raw_slabs = json.loads(raw_slabs)
        except Exception:
            return cleaned

    if not isinstance(raw_slabs, list):
        return cleaned

    for row in raw_slabs:
        if not isinstance(row, dict):
            continue

        min_km = _delivery_float_or_none(row.get("min_km"))
        max_km = _delivery_float_or_none(row.get("max_km"))
        fee = _delivery_float_or_none(row.get("fee"))

        if min_km is None:
            min_km = 0.0

        if min_km < 0:
            continue

        if fee is None or fee < 0:
            continue

        if max_km is not None and max_km <= min_km:
            continue

        cleaned.append({
            "min_km": round(float(min_km), 3),
            "max_km": round(float(max_km), 3) if max_km is not None else None,
            "fee": round(float(fee), 2)
        })

    cleaned.sort(key=lambda x: float(x.get("min_km") or 0))

    return cleaned


def calculate_store_delivery_fee_details(store, distance_km, items_total=None):
    """
    Calculates delivery fee with full metadata.

    Delivery fee settings are Admin-controlled from mongo.platform_settings.
    Store document is used only for pickup location / delivery zone / delivery enabled.
    """
    store = store or {}
    settings = get_platform_delivery_fee_settings()

    base_fee = float(settings.get("delivery_base_fee") or BASE_DELIVERY_FEE_INR)
    free_delivery_above = _delivery_float_or_none(settings.get("free_delivery_above"))
    slabs_enabled = bool(settings.get("delivery_fee_slabs_enabled", False))
    slabs = _delivery_clean_platform_fee_slabs(settings.get("delivery_fee_slabs") or [])

    try:
        distance_km = float(distance_km) if distance_km is not None else None
    except Exception:
        distance_km = None

    try:
        items_total = float(items_total or 0)
    except Exception:
        items_total = 0.0

    if items_total < 0:
        items_total = 0.0

    def _apply_free_delivery_if_eligible(source, slab, calculated_fee):
        calculated_fee = round(float(calculated_fee or 0), 2)

        if (
            free_delivery_above is not None
            and free_delivery_above > 0
            and items_total >= free_delivery_above
        ):
            return {
                "delivery_fee": 0.0,
                "delivery_fee_source": "admin_free_delivery_above",
                "delivery_fee_slab": slab,
                "delivery_base_fee": round(float(base_fee), 2),
                "free_delivery_above": round(float(free_delivery_above), 2),
                "delivery_min_order_amount": round(float(settings.get("delivery_min_order_amount") or 0), 2),
                "original_delivery_fee": calculated_fee,
                "delivery_fee_before_discount": calculated_fee,
                "free_delivery_savings": calculated_fee,
                "items_total_for_free_delivery": round(float(items_total), 2),
                "original_delivery_fee_source": source,
                "delivery_fee_settings_source": "admin_platform_settings"
            }

        return {
            "delivery_fee": calculated_fee,
            "delivery_fee_source": source,
            "delivery_fee_slab": slab,
            "delivery_base_fee": round(float(base_fee), 2),
            "free_delivery_above": round(float(free_delivery_above or 0), 2),
            "delivery_min_order_amount": round(float(settings.get("delivery_min_order_amount") or 0), 2),
            "original_delivery_fee": calculated_fee,
            "delivery_fee_before_discount": calculated_fee,
            "free_delivery_savings": 0.0,
            "items_total_for_free_delivery": round(float(items_total), 2),
            "original_delivery_fee_source": source,
            "delivery_fee_settings_source": "admin_platform_settings"
        }

    if distance_km is None:
        return _apply_free_delivery_if_eligible(
            "admin_base_fee_no_distance",
            None,
            round(float(base_fee), 2)
        )

    if slabs_enabled and slabs:
        for slab in slabs:
            min_km = float(slab.get("min_km") or 0)
            max_km = slab.get("max_km")

            if max_km is None:
                if distance_km >= min_km:
                    matched_fee = round(float(slab.get("fee") or 0), 2)
                    return _apply_free_delivery_if_eligible(
                        "admin_custom_slab",
                        slab,
                        matched_fee
                    )
            else:
                max_km = float(max_km)

                if distance_km >= min_km and distance_km < max_km:
                    matched_fee = round(float(slab.get("fee") or 0), 2)
                    return _apply_free_delivery_if_eligible(
                        "admin_custom_slab",
                        slab,
                        matched_fee
                    )

    surcharge = 0

    for low, high, fee in DELIVERY_SURCHARGE_SLABS:
        if distance_km >= low and distance_km < high:
            surcharge = fee
            break

    fallback_slab = {
        "min_km": 0,
        "max_km": None,
        "fee": round(float(base_fee + surcharge), 2),
        "base_fee": round(float(base_fee), 2),
        "surcharge": round(float(surcharge), 2),
        "distance_km": round(float(distance_km), 3),
        "label": "Fallback delivery fee"
    }

    final_fee = round(float(base_fee + surcharge), 2)

    return _apply_free_delivery_if_eligible(
        "admin_global_surcharge_fallback",
        fallback_slab,
        final_fee
    )


def calculate_store_delivery_fee(store, distance_km):
    """
    Backward-compatible delivery fee helper.

    Existing code expects this function to return only a number.
    So this wrapper returns only delivery_fee.

    For full metadata, use calculate_store_delivery_fee_details().
    """
    details = calculate_store_delivery_fee_details(store, distance_km)

    return float(details.get("delivery_fee") or 0)


def check_store_serviceability(store, customer_lat, customer_lng, customer_pincode=None, items_total=None):
    """
    Single source of truth for checkout delivery permission.

    Checks:
    1. Store active
    2. Store online
    3. Delivery enabled
    4. Store coordinates
    5. Customer coordinates
    6. Delivery polygon configured
    7. Customer point inside polygon
    8. Distance + delivery fee
    """
    store = store or {}
    status = normalize_store_delivery_status(store)

    if status["is_active"] != 1:
        return {
            "ok": True,
            "serviceable": False,
            "reason": "STORE_INACTIVE",
            "message": "This store is currently unavailable.",
            "distance_km": None,
            "delivery_fee": 0
        }

    if status["is_online"] != 1:
        return {
            "ok": True,
            "serviceable": False,
            "reason": "STORE_OFFLINE",
            "message": "This store is currently offline and not accepting orders.",
            "distance_km": None,
            "delivery_fee": 0
        }

    if status["delivery_enabled"] != 1:
        return {
            "ok": True,
            "serviceable": False,
            "reason": "DELIVERY_DISABLED",
            "message": "Delivery is currently unavailable for this store.",
            "distance_km": None,
            "delivery_fee": 0
        }
    
    delivery_min_order_amount = _delivery_float_or_none(
        status.get("delivery_min_order_amount")
    )

    items_total_for_check = _delivery_float_or_none(items_total)

    if (
        delivery_min_order_amount is not None
        and delivery_min_order_amount > 0
        and items_total_for_check is not None
        and items_total_for_check < delivery_min_order_amount
    ):
        return {
            "ok": True,
            "serviceable": False,
            "reason": "DELIVERY_MIN_ORDER_NOT_MET",
            "message": f"Minimum order amount for delivery is ₹{delivery_min_order_amount:g}.",
            "distance_km": None,
            "delivery_fee": 0,
            "delivery_min_order_amount": round(float(delivery_min_order_amount), 2)
        }

    if customer_pincode and not is_serviceable_pincode(customer_pincode):
        return {
            "ok": True,
            "serviceable": False,
            "reason": "INVALID_PINCODE",
            "message": "Please enter a valid 6-digit pincode.",
            "distance_km": None,
            "delivery_fee": 0
        }

    store_lat = status["store_lat"]
    store_lng = status["store_lng"]

    if store_lat is None or store_lng is None:
        return {
            "ok": True,
            "serviceable": False,
            "reason": "STORE_COORDINATES_MISSING",
            "message": "Store pickup location is not configured.",
            "distance_km": None,
            "delivery_fee": 0
        }

    customer_lat = _delivery_float_or_none(customer_lat)
    customer_lng = _delivery_float_or_none(customer_lng)

    if customer_lat is None or customer_lng is None:
        return {
            "ok": True,
            "serviceable": False,
            "reason": "CUSTOMER_COORDINATES_MISSING",
            "message": "Please select or detect your delivery location before checkout.",
            "distance_km": None,
            "delivery_fee": 0
        }

    polygon = status["delivery_zone_polygon"]

    if len(polygon) < 3:
        return {
            "ok": True,
            "serviceable": False,
            "reason": "DELIVERY_ZONE_MISSING",
            "message": "This store has not configured its delivery zone yet.",
            "distance_km": None,
            "delivery_fee": 0
        }

    inside_zone = point_in_polygon(customer_lat, customer_lng, polygon)

    distance_km = haversine_km(store_lat, store_lng, customer_lat, customer_lng)

    if not inside_zone:
        return {
            "ok": True,
            "serviceable": False,
            "reason": "OUTSIDE_DELIVERY_ZONE",
            "message": "This store does not deliver to your selected location.",
            "distance_km": round(distance_km, 2) if distance_km is not None else None,
            "delivery_fee": 0
        }

    delivery_fee_details = calculate_store_delivery_fee_details(
        store,
        distance_km,
        items_total=items_total
    )
    delivery_fee = float(delivery_fee_details.get("delivery_fee") or 0)

    return {
        "ok": True,
        "serviceable": True,
        "reason": "SERVICEABLE",
        "message": "Delivery is available.",
        "distance_km": round(float(distance_km or 0), 2),
        "delivery_fee": round(float(delivery_fee), 2),

        # Delivery fee metadata for checkout/order snapshot.
        "delivery_fee_source": delivery_fee_details.get("delivery_fee_source") or "unknown",
        "delivery_fee_slab": delivery_fee_details.get("delivery_fee_slab"),
        "delivery_base_fee": float(delivery_fee_details.get("delivery_base_fee") or 0),
        "delivery_fee_details": delivery_fee_details
    }



def _checkout_common_delivery_block(store, customer_lat, customer_lng, customer_pincode=None, items_total=None):
    """Common checkout delivery validation shared by in-house and external modes."""
    store = store or {}
    status = normalize_store_delivery_status(store)

    if status["is_active"] != 1:
        return None, {
            "ok": True,
            "serviceable": False,
            "reason": "STORE_INACTIVE",
            "message": "This store is currently unavailable.",
            "distance_km": None,
            "delivery_fee": 0,
        }

    if status["is_online"] != 1:
        return None, {
            "ok": True,
            "serviceable": False,
            "reason": "STORE_OFFLINE",
            "message": "This store is currently offline and not accepting orders.",
            "distance_km": None,
            "delivery_fee": 0,
        }

    if status["delivery_enabled"] != 1:
        return None, {
            "ok": True,
            "serviceable": False,
            "reason": "DELIVERY_DISABLED",
            "message": "Delivery is currently unavailable for this store.",
            "distance_km": None,
            "delivery_fee": 0,
        }

    delivery_min_order_amount = _delivery_float_or_none(status.get("delivery_min_order_amount"))
    items_total_for_check = _delivery_float_or_none(items_total)

    if (
        delivery_min_order_amount is not None
        and delivery_min_order_amount > 0
        and items_total_for_check is not None
        and items_total_for_check < delivery_min_order_amount
    ):
        return None, {
            "ok": True,
            "serviceable": False,
            "reason": "DELIVERY_MIN_ORDER_NOT_MET",
            "message": f"Minimum order amount for delivery is ₹{delivery_min_order_amount:g}.",
            "distance_km": None,
            "delivery_fee": 0,
            "delivery_min_order_amount": round(float(delivery_min_order_amount), 2),
        }

    if customer_pincode and not is_serviceable_pincode(customer_pincode):
        return None, {
            "ok": True,
            "serviceable": False,
            "reason": "INVALID_PINCODE",
            "message": "Please enter a valid 6-digit pincode.",
            "distance_km": None,
            "delivery_fee": 0,
        }

    store_lat = status["store_lat"]
    store_lng = status["store_lng"]

    if store_lat is None or store_lng is None:
        return None, {
            "ok": True,
            "serviceable": False,
            "reason": "STORE_COORDINATES_MISSING",
            "message": "Store pickup location is not configured.",
            "distance_km": None,
            "delivery_fee": 0,
        }

    customer_lat = _delivery_float_or_none(customer_lat)
    customer_lng = _delivery_float_or_none(customer_lng)

    if customer_lat is None or customer_lng is None:
        return None, {
            "ok": True,
            "serviceable": False,
            "reason": "CUSTOMER_COORDINATES_MISSING",
            "message": "Please select or detect your delivery location before checkout.",
            "distance_km": None,
            "delivery_fee": 0,
        }

    distance_km = haversine_km(store_lat, store_lng, customer_lat, customer_lng)

    return {
        "status": status,
        "store_lat": store_lat,
        "store_lng": store_lng,
        "customer_lat": customer_lat,
        "customer_lng": customer_lng,
        "distance_km": distance_km,
    }, None


def _checkout_common_error_for_mode(error, mode, settings=None):
    error = dict(error or {})
    snapshot = get_order_delivery_mode_snapshot(mode)
    error.update({
        "active_delivery_mode": mode,
        "delivery_type": snapshot.get("delivery_type"),
        "external_delivery_enabled": mode in [DELIVERY_MODE_EXTERNAL_LOCAL, DELIVERY_MODE_THIRD_PARTY],
        "external_delivery_provider": snapshot.get("external_delivery_provider") or snapshot.get("third_party_provider") or snapshot.get("external_local_provider"),
        "external_delivery_provider_type": snapshot.get("external_delivery_provider_type"),
        "external_delivery_quote": {},
        "cod_allowed": bool(snapshot.get("allow_cod_payment")),
        "online_allowed": bool(snapshot.get("allow_online_payment")),
        "external_payment_rule": snapshot.get("external_payment_rule"),
    })
    return error


def _quote_external_local_delivery_for_checkout(store, common, items_total, payment_method=None):
    """Hard-coded assumed fare logic for local/Rapido-type delivery.

    No Rapido/Ola/Uber booking or payment record is created. The order itself
    remains the customer reference/tracking id.
    """
    external_settings = get_external_delivery_settings()
    settings = get_delivery_mode_settings()
    distance_km = float(common.get("distance_km") or 0)

    external_service_zone = external_settings.get("external_service_zone_polygon") or []
    if external_settings.get("external_service_zone_enabled") and len(external_service_zone) >= 3:
        inside_external_zone = point_in_polygon(
            common.get("customer_lat"),
            common.get("customer_lng"),
            external_service_zone,
        )

        if not inside_external_zone:
            return {
                "ok": True,
                "serviceable": False,
                "reason": "OUTSIDE_EXTERNAL_LOCAL_ZONE",
                "message": "This address is outside the local external delivery zone.",
                "distance_km": round(distance_km, 2),
                "delivery_fee": 0,
            }

    max_distance = _delivery_float_or_none(external_settings.get("external_local_max_distance_km"))
    if max_distance is not None and max_distance > 0 and distance_km > float(max_distance):
        return {
            "ok": True,
            "serviceable": False,
            "reason": "OUTSIDE_EXTERNAL_LOCAL_DISTANCE",
            "message": f"Local external delivery supports up to {max_distance:g} km. Shiprocket shipping may be used if enabled.",
            "distance_km": round(distance_km, 2),
            "delivery_fee": 0,
        }

    configured_min = _delivery_float_or_none(external_settings.get("external_local_min_fee")) or 0
    configured_base = _delivery_float_or_none(external_settings.get("external_local_base_fee")) or configured_min
    configured_per_km = _delivery_float_or_none(external_settings.get("external_local_per_km_fee")) or 0
    delivery_fee = round(max(
        float(configured_min or 0),
        float(configured_base or 0) + max(distance_km, 0) * float(configured_per_km or 0),
    ), 2)

    allow_online_payment = bool(settings.get("allow_online_payment", True))
    allow_cod_payment = bool(settings.get("allow_cod_payment", True))
    payment_rule = _external_payment_rule_from_methods(
        DELIVERY_MODE_EXTERNAL_LOCAL,
        allow_cod_payment,
        COD_COLLECTION_STORE if allow_cod_payment else "",
    )

    return {
        "ok": True,
        "serviceable": True,
        "reason": "EXTERNAL_LOCAL_SELECTED",
        "message": "Local external delivery selected. Customer order ID will be used as the delivery reference.",
        "distance_km": round(distance_km, 2),
        "delivery_fee": delivery_fee,
        "delivery_fee_source": "external_local_hardcoded_fare",
        "delivery_fee_slab": None,
        "delivery_base_fee": float(configured_base or 0),
        "delivery_fee_details": {
            "delivery_fee_settings_source": "external_local_hardcoded_fare",
            "delivery_mode": DELIVERY_MODE_EXTERNAL_LOCAL,
            "distance_km": round(distance_km, 3),
            "external_local_base_fee": float(configured_base or 0),
            "external_local_per_km_fee": float(configured_per_km or 0),
            "external_local_min_fee": float(configured_min or 0),
            "external_local_max_distance_km": float(max_distance or 0),
            "external_service_zone_enabled": bool(external_settings.get("external_service_zone_enabled")),
        },
        "active_delivery_mode": DELIVERY_MODE_EXTERNAL_LOCAL,
        "delivery_type": "EXTERNAL_LOCAL_DELIVERY_REFERENCE_ONLY",
        "external_delivery_enabled": True,
        "external_delivery_provider": "LOCAL_DELIVERY_PARTNER",
        "external_delivery_provider_type": "LOCAL_DELIVERY",
        "external_delivery_quote": {
            "ok": True,
            "serviceable": True,
            "provider": "LOCAL_DELIVERY_PARTNER",
            "provider_type": "LOCAL_DELIVERY",
            "delivery_fee": delivery_fee,
            "delivery_fee_source": "external_local_hardcoded_fare",
            "quote_status": "HARDCODED_LOCAL_FARE",
            "message": "Assumed local delivery fare applied. No live Rapido/Ola/Uber API booking is created.",
            "raw_response": {},
        },
        "external_payment_rule": payment_rule,
        "cod_allowed": bool(allow_cod_payment),
        "online_allowed": bool(allow_online_payment),
        "eta_minutes": int(max(20, min(120, 20 + distance_km * 5))) if distance_km else None,
    }


def _quote_shiprocket_delivery_for_checkout(store, common, customer_pincode=None, items_total=None, payment_method=None, cart_items=None):
    """Courier/Shiprocket quote logic for outside-local-range shipping."""
    external_settings = get_external_delivery_settings()
    distance_km = float(common.get("distance_km") or 0)

    max_distance = _delivery_float_or_none(external_settings.get("third_party_max_distance_km"))
    if max_distance is not None and max_distance > 0 and distance_km > float(max_distance):
        return {
            "ok": True,
            "serviceable": False,
            "reason": "SHIPROCKET_DISTANCE_LIMIT",
            "message": f"Courier shipping supports up to {max_distance:g} km from the store.",
            "distance_km": round(distance_km, 2),
            "delivery_fee": 0,
        }

    configured_min = _delivery_float_or_none(external_settings.get("third_party_min_fee")) or 0
    configured_base = _delivery_float_or_none(external_settings.get("third_party_base_fee")) or configured_min
    configured_per_km = _delivery_float_or_none(external_settings.get("third_party_per_km_fee")) or 0
    manual_fee = round(max(
        float(configured_min or 0),
        float(configured_base or 0) + max(distance_km, 0) * float(configured_per_km or 0),
    ), 2)

    payload = {
        "provider": "SHIPROCKET",
        "provider_type": "COURIER",
        "mode": DELIVERY_MODE_THIRD_PARTY,
        "payment_method": "ONLINE",
        "payment_rule": EXTERNAL_PAYMENT_RULE_ONLINE_ONLY,
        "cod_amount": 0.0,
        "order_amount": float(items_total or 0),
        "distance_km": round(distance_km, 3),
        "fallback_delivery_fee": manual_fee,
        "pickup": {
            "store_id": str(store.get("_id") or ""),
            "store_name": store.get("store_name") or store.get("name") or "NE FRESH Store",
            "latitude": common.get("store_lat"),
            "longitude": common.get("store_lng"),
            "pincode": store.get("pincode") or store.get("pickup_pincode") or external_settings.get("shiprocket_pickup_pincode") or "",
        },
        "drop": {
            "pincode": customer_pincode or "",
            "latitude": common.get("customer_lat"),
            "longitude": common.get("customer_lng"),
        },
        "package": build_checkout_package_snapshot(cart_items, external_settings),
    }

    try:
        from services.delivery_integrations.quote_service import quote_external_delivery
        quote = quote_external_delivery(payload, external_settings, DELIVERY_MODE_THIRD_PARTY, payment_rule=EXTERNAL_PAYMENT_RULE_ONLINE_ONLY)
    except Exception as exc:
        quote = {
            "ok": True,
            "serviceable": True,
            "provider": "SHIPROCKET",
            "provider_type": "COURIER",
            "delivery_fee": manual_fee,
            "delivery_fee_source": "shiprocket_exception_fallback",
            "quote_status": "EXCEPTION_FALLBACK",
            "message": f"Shiprocket quote fallback applied. {exc}",
            "raw_response": {},
        }

    delivery_fee = round(float(quote.get("delivery_fee") or manual_fee or 0), 2)

    return {
        "ok": True,
        "serviceable": bool(quote.get("serviceable", True)),
        "reason": "SHIPROCKET_SELECTED" if quote.get("serviceable", True) else "SHIPROCKET_UNAVAILABLE",
        "message": quote.get("message") or "Shiprocket/courier shipping selected.",
        "distance_km": round(distance_km, 2),
        "delivery_fee": delivery_fee,
        "delivery_fee_source": quote.get("delivery_fee_source") or "shiprocket_quote",
        "delivery_fee_slab": quote.get("delivery_fee_slab"),
        "delivery_base_fee": float(configured_base or 0),
        "delivery_fee_details": {
            "delivery_fee_settings_source": "shiprocket_quote_or_fallback",
            "delivery_mode": DELIVERY_MODE_THIRD_PARTY,
            "external_provider": quote.get("provider") or "SHIPROCKET",
            "external_provider_type": "COURIER",
            "external_quote_status": quote.get("quote_status") or "QUOTE",
            "external_quote_message": quote.get("message") or "Shiprocket/courier quote applied.",
            "external_quote_raw": quote.get("raw_response") or {},
            "distance_km": round(distance_km, 3),
            "manual_fallback_fee": manual_fee,
            "package_snapshot": payload.get("package") or {},
        },
        "external_package_snapshot": payload.get("package") or {},
        "active_delivery_mode": DELIVERY_MODE_THIRD_PARTY,
        "delivery_type": "THIRD_PARTY_SHIPPING_PENDING",
        "external_delivery_enabled": True,
        "external_delivery_provider": "SHIPROCKET",
        "external_delivery_provider_type": "COURIER",
        "external_delivery_quote": quote,
        "external_payment_rule": EXTERNAL_PAYMENT_RULE_ONLINE_ONLY,
        "cod_allowed": False,
        "online_allowed": True,
        "eta_minutes": quote.get("eta_minutes"),
    }


def check_checkout_delivery_quote(store, customer_lat, customer_lng, customer_pincode=None, items_total=None, payment_method=None, cart_items=None):
    """Checkout delivery fee and availability.

    Operation rules:
    1. In-house operation mode checks only the existing in-house/store zone flow.
    2. Connected external operation mode disables in-house and routes orders to
       External Local first, then Shiprocket/Courier for outside-local range.
    """
    settings = get_delivery_mode_settings()
    routing_mode = settings.get("delivery_routing_mode") or DELIVERY_ROUTING_MODE_AUTO

    # Manual legacy fallback is still supported, but Admin UI now uses the simplified operation mode.
    if routing_mode == DELIVERY_ROUTING_MODE_MANUAL:
        manual_mode = _delivery_mode_normalize(settings.get("active_delivery_mode"))
        if manual_mode == DELIVERY_MODE_IN_HOUSE:
            result = check_store_serviceability(
                store=store,
                customer_lat=customer_lat,
                customer_lng=customer_lng,
                customer_pincode=customer_pincode,
                items_total=items_total,
            )
            snapshot = get_order_delivery_mode_snapshot(DELIVERY_MODE_IN_HOUSE)
            result.update({
                "active_delivery_mode": DELIVERY_MODE_IN_HOUSE,
                "delivery_type": "OWN_DELIVERY",
                "external_delivery_enabled": False,
                "external_delivery_provider": "IN_HOUSE",
                "external_delivery_provider_type": "IN_HOUSE",
                "external_delivery_quote": {},
                "cod_allowed": bool(snapshot.get("allow_cod_payment")),
                "online_allowed": bool(snapshot.get("allow_online_payment")),
                "external_payment_rule": snapshot.get("external_payment_rule"),
            })
            return result

    common, common_error = _checkout_common_delivery_block(
        store=store,
        customer_lat=customer_lat,
        customer_lng=customer_lng,
        customer_pincode=customer_pincode,
        items_total=items_total,
    )

    if common_error:
        return _checkout_common_error_for_mode(common_error, _first_enabled_delivery_mode(settings), settings)

    attempted_messages = []

    if settings.get("in_house_delivery_enabled"):
        in_house_result = check_store_serviceability(
            store=store,
            customer_lat=customer_lat,
            customer_lng=customer_lng,
            customer_pincode=customer_pincode,
            items_total=items_total,
        )
        if in_house_result.get("serviceable"):
            snapshot = get_order_delivery_mode_snapshot(DELIVERY_MODE_IN_HOUSE)
            in_house_result.update({
                "active_delivery_mode": DELIVERY_MODE_IN_HOUSE,
                "delivery_type": "OWN_DELIVERY",
                "external_delivery_enabled": False,
                "external_delivery_provider": "IN_HOUSE",
                "external_delivery_provider_type": "IN_HOUSE",
                "external_delivery_quote": {},
                "cod_allowed": bool(snapshot.get("allow_cod_payment")),
                "online_allowed": bool(snapshot.get("allow_online_payment")),
                "external_payment_rule": snapshot.get("external_payment_rule"),
                "routing_reason": "IN_HOUSE_SERVICEABLE",
            })
            return in_house_result
        attempted_messages.append(in_house_result.get("message") or "In-house delivery is not available for this address.")

    if settings.get("external_local_delivery_enabled"):
        local_result = _quote_external_local_delivery_for_checkout(
            store=store,
            common=common,
            items_total=items_total,
            payment_method=payment_method,
        )
        if local_result.get("serviceable"):
            local_result["routing_reason"] = "EXTERNAL_LOCAL_DISTANCE_MATCHED"
            return local_result
        attempted_messages.append(local_result.get("message") or "External local delivery is not available for this address.")

    if settings.get("third_party_shipping_enabled"):
        shiprocket_result = _quote_shiprocket_delivery_for_checkout(
            store=store,
            common=common,
            customer_pincode=customer_pincode,
            items_total=items_total,
            payment_method=payment_method,
            cart_items=cart_items,
        )
        if shiprocket_result.get("serviceable"):
            shiprocket_result["routing_reason"] = "SHIPROCKET_OUTSIDE_LOCAL_RANGE"
            return shiprocket_result
        attempted_messages.append(shiprocket_result.get("message") or "Shiprocket/courier shipping is not available for this address.")

    return {
        "ok": True,
        "serviceable": False,
        "reason": "NO_DELIVERY_CHANNEL_AVAILABLE",
        "message": "Delivery is not available for this address. " + " ".join([m for m in attempted_messages if m]),
        "distance_km": round(float(common.get("distance_km") or 0), 2),
        "delivery_fee": 0,
        "active_delivery_mode": _first_enabled_delivery_mode(settings),
        "delivery_type": get_order_delivery_mode_snapshot(_first_enabled_delivery_mode(settings)).get("delivery_type"),
        "external_delivery_enabled": False,
        "external_delivery_provider": "",
        "external_delivery_provider_type": "",
        "external_delivery_quote": {},
        "cod_allowed": False,
        "online_allowed": False,
        "external_payment_rule": EXTERNAL_PAYMENT_RULE_ONLINE_ONLY,
    }


    # =========================================================
# STORE-CONTROLLED DELIVERY ASSIGNMENT HELPERS
# =========================================================

DELIVERY_STORE_ASSIGNABLE_STATUSES = {
    "SHIPMENT_READY",
    "READY_FOR_PICKUP",  # legacy support
    "ASSIGNED_TO_DELIVERY",
    "REACHED_STORE"
}

DELIVERY_REASSIGN_BLOCKED_STATUSES = {
    "PICKED_UP",
    "OUT_FOR_DELIVERY",
    "DELIVERED",
    "CANCELLED"
}

DELIVERY_PROGRESS_STATUSES = {
    "ASSIGNED_TO_DELIVERY",
    "REACHED_STORE",
    "PICKED_UP",
    "OUT_FOR_DELIVERY",
    "DELIVERED"
}


def _delivery_user_id(value):
    if value is None:
        return ""

    try:
        if isinstance(value, ObjectId):
            return str(value)
    except Exception:
        pass

    return str(value).strip()


def _delivery_actor_snapshot(actor=None):
    actor = actor or {}

    return {
        "actor_id": _delivery_user_id(actor.get("_id") or actor.get("id")),
        "actor_role": actor.get("role") or "",
        "actor_name": actor.get("name") or actor.get("full_name") or ""
    }


def add_order_event(order_id, status, note="", actor=None):
    """
    Consistent order timeline insert.
    Works for store, delivery boy, customer and admin events.
    """
    try:
        oid_obj = order_id if isinstance(order_id, ObjectId) else ObjectId(str(order_id))
    except Exception:
        oid_obj = order_id

    now = datetime.utcnow().isoformat()
    actor_data = _delivery_actor_snapshot(actor)

    doc = {
        "order_id": oid_obj,
        "status": (status or "").strip().upper(),
        "note": note or "",
        "created_at": now,
        "actor_id": actor_data.get("actor_id"),
        "actor_role": actor_data.get("actor_role"),
        "actor_name": actor_data.get("actor_name")
    }

    mongo.order_events.insert_one(doc)
    return doc


def get_delivery_partner_snapshot(delivery_user_id):
    """
    Returns safe delivery-boy details for saving inside orders.
    """
    uid = _delivery_user_id(delivery_user_id)

    if not uid:
        return None

    user = None

    try:
        user = mongo.users.find_one({"_id": ObjectId(uid)})
    except Exception:
        user = mongo.users.find_one({"_id": uid})

    if not user:
        return None

    if (user.get("role") or "").strip().lower() != "delivery":
        return None

    return {
        "id": str(user.get("_id")),
        "name": user.get("name") or user.get("full_name") or "Delivery Partner",
        "phone": user.get("phone") or "",
        "email": user.get("email") or "",
        "is_active": int(user.get("is_active", 1) or 0)
    }


def get_online_delivery_people_near_store(store, max_km=None):
    """
    Store-side helper.

    Returns active delivery boys with latest online GPS from delivery_availability.
    If store coordinates are present, distance from store is calculated.
    """
    store = store or {}

    store_lat = _delivery_float_or_none(store.get("latitude"))
    store_lng = _delivery_float_or_none(store.get("longitude"))

    max_km_value = None
    if max_km is not None:
        try:
            max_km_value = float(max_km)
        except Exception:
            max_km_value = None

    users = list(
        mongo.users.find({
            "role": "delivery",
            "$or": [
                {"is_active": 1},
                {"is_active": True},
                {"is_active": {"$exists": False}}
            ]
        }).sort("name", 1)
    )

    output = []

    for user in users:
        uid = str(user.get("_id"))

        availability = mongo.delivery_availability.find_one({
            "user_id": uid,
            "active": True
        })

        if not availability:
            continue

        rider_lat = _delivery_float_or_none(availability.get("latitude"))
        rider_lng = _delivery_float_or_none(availability.get("longitude"))

        distance_km = None

        if (
            store_lat is not None and
            store_lng is not None and
            rider_lat is not None and
            rider_lng is not None
        ):
            distance_km = haversine_km(store_lat, store_lng, rider_lat, rider_lng)

        if max_km_value is not None and distance_km is not None and distance_km > max_km_value:
            continue

        assigned_count = mongo.orders.count_documents({
            "delivery_partner_id": uid,
            "status": {
                "$in": [
                    "ASSIGNED_TO_DELIVERY",
                    "REACHED_STORE",
                    "PICKED_UP",
                    "OUT_FOR_DELIVERY"
                ]
            }
        })

        output.append({
            "id": uid,
            "name": user.get("name") or "Delivery Partner",
            "phone": user.get("phone") or "",
            "email": user.get("email") or "",
            "is_online": True,
            "latitude": rider_lat,
            "longitude": rider_lng,
            "distance_km": round(distance_km, 2) if distance_km is not None else None,
            "current_order_id": availability.get("current_order_id"),
            "currently_assigned_orders": assigned_count,
            "updated_at": availability.get("updated_at"),
            "active_since": availability.get("active_since")
        })

    output.sort(
        key=lambda row: (
            999999 if row.get("distance_km") is None else row.get("distance_km"),
            row.get("currently_assigned_orders", 0),
            row.get("name", "")
        )
    )

    return output


def assign_delivery_partner_to_order(order_id, delivery_user_id, actor=None, source="store_manual", allow_reassign=False):
    """
    Conflict-safe delivery assignment.

    Used by:
    - Store manual assignment
    - Store reassignment when allow_reassign=True
    - Delivery-boy self accept

    Handles:
    - Normal first assignment
    - Store reassignment before pickup
    - Reassignment after delivery boy cancelled delivery
    """

    try:
        oid_obj = order_id if isinstance(order_id, ObjectId) else ObjectId(str(order_id))
    except Exception:
        return {
            "ok": False,
            "error": "Invalid order id."
        }

    partner = get_delivery_partner_snapshot(delivery_user_id)

    if not partner:
        return {
            "ok": False,
            "error": "Delivery boy not found."
        }

    if int(partner.get("is_active") or 0) != 1:
        return {
            "ok": False,
            "error": "This delivery-boy account is disabled."
        }

    availability = mongo.delivery_availability.find_one({
        "user_id": partner["id"],
        "active": True
    })

    if not availability:
        return {
            "ok": False,
            "error": "This delivery boy is currently offline."
        }

    order = mongo.orders.find_one({"_id": oid_obj})

    if not order:
        return {
            "ok": False,
            "error": "Order not found."
        }

    status = (order.get("status") or "").strip().upper()

    if status in DELIVERY_REASSIGN_BLOCKED_STATUSES:
        return {
            "ok": False,
            "error": "Delivery assignment cannot be changed for this order status."
        }

    if status not in DELIVERY_STORE_ASSIGNABLE_STATUSES:
        return {
            "ok": False,
            "error": "Store must mark this order shipment ready before delivery assignment."
        }

    existing_partner = order.get("delivery_partner_id")
    existing_partner_id = _delivery_user_id(existing_partner)
    new_partner_id = _delivery_user_id(partner["id"])

    was_delivery_cancelled = bool(
        order.get("needs_reassignment")
        or order.get("delivery_cancelled_by_partner")
        or order.get("delivery_cancel_reason")
    )

    if existing_partner and not allow_reassign:
        if existing_partner_id == new_partner_id:
            return {
                "ok": True,
                "message": "This order is already assigned to this delivery boy.",
                "order_id": str(oid_obj),
                "delivery_partner": partner
            }

        return {
            "ok": False,
            "error": "This order already has an assigned delivery boy."
        }

    now = datetime.utcnow().isoformat()
    actor_data = _delivery_actor_snapshot(actor)

    old_partner_id = _delivery_user_id(
        order.get("delivery_partner_id")
        or order.get("previous_delivery_partner_id")
    )

    old_partner_name = (
        order.get("delivery_partner_name")
        or order.get("previous_delivery_partner_name")
        or ""
    )

    old_partner_phone = (
        order.get("delivery_partner_phone")
        or order.get("previous_delivery_partner_phone")
        or ""
    )

    previous_cancel_reason = (
        order.get("delivery_cancel_reason")
        or order.get("delivery_status_note")
        or ""
    )

    is_normal_reassign = bool(
        allow_reassign
        and existing_partner_id
        and existing_partner_id != new_partner_id
    )

    update_data = {
        "delivery_partner_id": partner["id"],
        "delivery_partner_name": partner["name"],
        "delivery_partner_phone": partner["phone"],

        "delivery_assigned_by": actor_data.get("actor_id"),
        "delivery_assigned_by_role": actor_data.get("actor_role"),
        "delivery_assigned_by_name": actor_data.get("actor_name"),
        "delivery_assignment_source": source,

        "assigned_at": now,
        "updated_at": now,
        "status": "ASSIGNED_TO_DELIVERY",

        # Clear rider-cancelled state after successful new assignment
        "needs_reassignment": False,
        "delivery_cancelled_by_partner": False,
        "delivery_cancel_reason": "",
        "delivery_cancelled_status_from": "",

        # Reassignment audit fields
        "delivery_reassigned_at": now if (was_delivery_cancelled or is_normal_reassign) else order.get("delivery_reassigned_at"),
        "delivery_reassigned_by": actor_data.get("actor_id") if (was_delivery_cancelled or is_normal_reassign) else order.get("delivery_reassigned_by"),
        "delivery_reassigned_by_name": actor_data.get("actor_name") if (was_delivery_cancelled or is_normal_reassign) else order.get("delivery_reassigned_by_name")
    }

    if old_partner_id and old_partner_id != new_partner_id:
        update_data["previous_delivery_partner_id"] = old_partner_id
        update_data["previous_delivery_partner_name"] = old_partner_name
        update_data["previous_delivery_partner_phone"] = old_partner_phone

    unassigned_filter = {
        "_id": oid_obj,
        "status": {
            "$in": [
                "SHIPMENT_READY",
                "READY_FOR_PICKUP"  # legacy support
            ]
        },
        "$or": [
            {"delivery_partner_id": {"$exists": False}},
            {"delivery_partner_id": None},
            {"delivery_partner_id": ""}
        ]
    }

    reassign_filter = {
        "_id": oid_obj,
        "status": {
            "$in": [
                "SHIPMENT_READY",
                "READY_FOR_PICKUP",  # legacy support
                "ASSIGNED_TO_DELIVERY",
                "REACHED_STORE"
            ]
        },
        "$or": [
            {
                "$and": [
                    {"delivery_partner_id": {"$exists": True}},
                    {"delivery_partner_id": {"$ne": None}},
                    {"delivery_partner_id": {"$ne": ""}}
                ]
            },
            {"needs_reassignment": True},
            {"delivery_cancelled_by_partner": True}
        ]
    }

    if allow_reassign:
        update_filter = reassign_filter
    else:
        update_filter = unassigned_filter

    update_payload = {
        "$set": update_data
    }

    if was_delivery_cancelled or is_normal_reassign:
        history_entry = {
            "action": "reassigned_after_delivery_cancel" if was_delivery_cancelled else "reassigned_by_store",
            "previous_delivery_partner_id": old_partner_id,
            "previous_delivery_partner_name": old_partner_name,
            "previous_delivery_partner_phone": old_partner_phone,
            "previous_cancel_reason": previous_cancel_reason,
            "new_delivery_partner_id": partner["id"],
            "new_delivery_partner_name": partner["name"],
            "new_delivery_partner_phone": partner["phone"],
            "at": now,
            "by": actor_data.get("actor_role") or "store",
            "actor_id": actor_data.get("actor_id"),
            "actor_name": actor_data.get("actor_name")
        }

        update_payload["$push"] = {
            "delivery_history": history_entry
        }

    result = mongo.orders.update_one(
        update_filter,
        update_payload
    )

    if result.modified_count < 1:
        latest = mongo.orders.find_one({"_id": oid_obj}) or {}
        latest_partner = latest.get("delivery_partner_id")
        latest_status = (latest.get("status") or "").strip().upper()

        if latest_partner and not allow_reassign:
            return {
                "ok": False,
                "error": "This order has just been assigned to another delivery boy."
            }

        if latest_status in DELIVERY_REASSIGN_BLOCKED_STATUSES:
            return {
                "ok": False,
                "error": "This order has moved forward and delivery partner cannot be changed now."
            }

        if latest_status not in ["SHIPMENT_READY", "READY_FOR_PICKUP"] and not allow_reassign:
            return {
                "ok": False,
                "error": "This order is no longer available for delivery assignment."
        }

        return {
            "ok": False,
            "error": "Delivery assignment could not be updated. Please refresh and try again."
        }

    # Set new delivery boy current order
    mongo.delivery_availability.update_one(
        {"user_id": partner["id"]},
        {
            "$set": {
                "current_order_id": str(oid_obj),
                "updated_at": now
            }
        },
        upsert=True
    )

    # Clear old delivery boy current order during reassignment
    if old_partner_id and old_partner_id != new_partner_id:
        mongo.delivery_availability.update_one(
            {
                "user_id": old_partner_id,
                "current_order_id": str(oid_obj)
            },
            {
                "$set": {
                    "current_order_id": None,
                    "updated_at": now
                }
            }
        )

    if was_delivery_cancelled:
        add_order_event(
            oid_obj,
            "DELIVERY_REASSIGNED",
            f"Delivery reassigned to {partner['name']} after previous rider cancellation.",
            actor
        )
    elif is_normal_reassign:
        add_order_event(
            oid_obj,
            "DELIVERY_REASSIGNED",
            f"Delivery reassigned to {partner['name']}.",
            actor
        )
    else:
        add_order_event(
            oid_obj,
            "ASSIGNED_TO_DELIVERY",
            f"Assigned to {partner['name']}",
            actor
        )

    return {
        "ok": True,
        "message": "Delivery boy reassigned successfully." if (was_delivery_cancelled or is_normal_reassign) else "Delivery boy assigned successfully.",
        "order_id": str(oid_obj),
        "delivery_partner": partner,
        "was_reassignment": bool(was_delivery_cancelled or is_normal_reassign)
    }


def clear_delivery_assignment(order_id, actor=None, reason="Delivery assignment cleared."):
    """
    Clears delivery assignment before pickup/out-for-delivery.
    Store can use this for correction/reassignment.
    """
    try:
        oid_obj = order_id if isinstance(order_id, ObjectId) else ObjectId(str(order_id))
    except Exception:
        return {
            "ok": False,
            "error": "Invalid order id."
        }

    order = mongo.orders.find_one({"_id": oid_obj})

    if not order:
        return {
            "ok": False,
            "error": "Order not found."
        }

    status = (order.get("status") or "").strip().upper()

    if status in DELIVERY_REASSIGN_BLOCKED_STATUSES:
        return {
            "ok": False,
            "error": "Delivery assignment cannot be cleared after pickup/out-for-delivery/delivery."
        }

    old_partner_id = order.get("delivery_partner_id")
    now = datetime.utcnow().isoformat()

    mongo.orders.update_one(
        {"_id": oid_obj},
        {
            "$set": {
                "status": "SHIPMENT_READY",
                "delivery_partner_id": None,
                "delivery_partner_name": "",
                "delivery_partner_phone": "",
                "delivery_assignment_source": "",
                "delivery_status_note": reason,
                "updated_at": now
            }
        }
    )

    if old_partner_id:
        mongo.delivery_availability.update_one(
            {
                "user_id": str(old_partner_id),
                "current_order_id": str(oid_obj)
            },
            {
                "$set": {
                    "current_order_id": None,
                    "updated_at": now
                }
            }
        )

    add_order_event(
        oid_obj,
        "SHIPMENT_READY",
        reason,
        actor
    )

    return {
        "ok": True,
        "message": "Delivery assignment cleared."
    }

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
    """
    Security-safe admin seeding.
    The old hardcoded admin@chhimphei.local / admin123 fallback has been removed.
    To seed an admin on a fresh database, set ADMIN_SEED_EMAIL and
    ADMIN_SEED_PASSWORD in the environment before first run.
    """
    admin_email = (os.getenv("ADMIN_SEED_EMAIL") or os.getenv("ADMIN_EMAIL") or "").strip().lower()
    admin_password = (os.getenv("ADMIN_SEED_PASSWORD") or os.getenv("ADMIN_PASSWORD") or "").strip()
    admin_name = (os.getenv("ADMIN_SEED_NAME") or "Administrator").strip() or "Administrator"
    admin_phone = (os.getenv("ADMIN_SEED_PHONE") or "").strip()

    if not admin_email:
        return

    admin = mongo.users.find_one({"email": admin_email})

    if not admin:
        if not admin_password or len(admin_password) < 10:
            log_warning("[SECURITY WARNING] ADMIN_SEED_PASSWORD is missing/too short. Admin seed skipped.")
            return

        mongo.users.insert_one({
            "name": admin_name,
            "email": admin_email,
            "phone": admin_phone,
            "password_hash": generate_password_hash(admin_password),
            "role": "admin",
            "phone_verified": 1,
            "is_active": 1,
            "created_at": datetime.utcnow().isoformat()
        })
        return

    if admin.get("password_hash") == "!!set_in_app!!":
        if not admin_password or len(admin_password) < 10:
            log_warning("[SECURITY WARNING] Existing admin placeholder password found, but ADMIN_SEED_PASSWORD is missing/too short.")
            return

        mongo.users.update_one(
            {"_id": admin["_id"]},
            {"$set": {"password_hash": generate_password_hash(admin_password)}}
        )

def send_sms(phone: str, message: str) -> bool:
    log_debug(f"[DEV SMS] to={phone} :: {message}")
    return True

with app.app_context():
    ensure_admin_seed_password()

# =========================================================
# CUSTOMER CANCEL ORDER
# =========================================================
# New flow:
# PLACED -> CONFIRMED -> PACKAGING -> SHIPMENT_READY
#
# Legacy support:
# PREPARING is kept because old orders may already have it.
#
# Customer can cancel before shipment is ready / delivery assignment.
CANCELLABLE_STATUSES = {
    "PLACED",
    "CONFIRMED",
    "PACKAGING",
    "PREPARING"
}

def is_cancellable(status: str) -> bool:
    return status and status.upper() in CANCELLABLE_STATUSES


def order_status_label(status):
    """
    Display label helper for old and new order statuses.
    Keeps old DB statuses readable while showing the new business wording.
    """
    status = (status or "").strip().upper()

    labels = {
        "PLACED": "Placed",
        "CONFIRMED": "Confirmed",
        "PREPARING": "Packaging",
        "PACKAGING": "Packaging",
        "READY_FOR_PICKUP": "Shipment Ready",
        "SHIPMENT_READY": "Shipment Ready",
        "ASSIGNED_TO_DELIVERY": "Assigned To Delivery",
        "ACCEPTED_BY_DELIVERY_MAN": "Accepted By Delivery Boy",
        "REACHED_STORE": "Reached Store",
        "PICKED_UP": "Picked Up",
        "OUT_FOR_DELIVERY": "Out For Delivery",
        "DELIVERED": "Delivered",
        "DELIVERY_FAILED": "Delivery Failed",
        "CANCELLED": "Cancelled",
        "CANCELED": "Cancelled",
        "RETURN_REQUESTED": "Return Requested",
        "STORE_APPROVED": "Store Approved",
        "STORE_REJECTED": "Store Rejected",
        "NEED_ADMIN_REVIEW": "Need Admin Review",
        "RETURN_COMPLETED": "Return Completed",
    }

    return labels.get(status, status.replace("_", " ").title() if status else "")

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

    def _track_float(value):
        try:
            if value is None or str(value).strip() == "":
                return None
            number = float(value)
            if not math.isfinite(number):
                return None
            return number
        except Exception:
            return None

    def _clean_lat_lng(lat_value, lng_value):
        """
        Returns clean map-safe coordinates.

        Also fixes accidental swapped lat/lng values:
        - correct Assam example: lat 26.x, lng 92.x
        - wrong swapped example: lat 92.x, lng 26.x
        """
        lat = _track_float(lat_value)
        lng = _track_float(lng_value)

        if lat is None or lng is None:
            return None, None

        # Fix swapped lat/lng when lat looks like longitude and lng looks like latitude.
        if abs(lat) > 90 and abs(lng) <= 90:
            lat, lng = lng, lat

        # Extra swap guard for India/Assam-style values.
        if 65 <= lat <= 100 and 5 <= lng <= 40:
            lat, lng = lng, lat

        if lat < -90 or lat > 90:
            return None, None

        if lng < -180 or lng > 180:
            return None, None

        return round(lat, 7), round(lng, 7)

    def _safe_id(value):
        if value is None:
            return ""
        try:
            if isinstance(value, ObjectId):
                return str(value)
        except Exception:
            pass
        return str(value)

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
    else:
        addr = None

    # ------------------------------------------------------------
    # Customer delivery point
    # Priority:
    # 1. order_addresses final checkout latitude/longitude
    # 2. order.delivery_latitude / order.delivery_longitude snapshot
    # 3. saved address latitude/longitude snapshot
    # ------------------------------------------------------------
    customer_lat, customer_lng = _clean_lat_lng(
        addr.get("latitude") if addr else None,
        addr.get("longitude") if addr else None
    )

    customer_source = (addr.get("location_source") if addr else "") or ""

    if customer_lat is None or customer_lng is None:
        customer_lat, customer_lng = _clean_lat_lng(
            order.get("delivery_latitude"),
            order.get("delivery_longitude")
        )
        customer_source = order.get("delivery_location_source") or "order_delivery_snapshot"

    if (customer_lat is None or customer_lng is None) and addr:
        customer_lat, customer_lng = _clean_lat_lng(
            addr.get("saved_address_latitude"),
            addr.get("saved_address_longitude")
        )
        customer_source = "saved_address_snapshot"

    if addr:
        addr["latitude"] = customer_lat
        addr["longitude"] = customer_lng
        addr["location_source"] = customer_source

    # ------------------------------------------------------------
    # Store pickup point
    # Priority:
    # 1. stores collection current latitude/longitude
    # 2. order store_latitude/store_longitude snapshot
    # ------------------------------------------------------------
    store_doc = None
    store_id = order.get("store_id")

    if store_id:
        try:
            store_doc = mongo.stores.find_one({"_id": store_id})
        except Exception:
            store_doc = None

        if not store_doc:
            try:
                store_doc = mongo.stores.find_one({"_id": ObjectId(str(store_id))})
            except Exception:
                store_doc = mongo.stores.find_one({"_id": str(store_id)})

    store_lat, store_lng = _clean_lat_lng(
        store_doc.get("latitude") if store_doc else None,
        store_doc.get("longitude") if store_doc else None
    )

    if store_lat is None or store_lng is None:
        store_lat, store_lng = _clean_lat_lng(
            order.get("store_latitude"),
            order.get("store_longitude")
        )

    store_view = {
        "id": _safe_id(store_doc.get("_id") if store_doc else store_id),
        "store_name": (
            store_doc.get("store_name")
            if store_doc
            else order.get("store_name")
            or "Store"
        ),
        "address": store_doc.get("address") if store_doc else "",
        "latitude": store_lat,
        "longitude": store_lng,
    }

    order["store_latitude"] = store_lat
    order["store_longitude"] = store_lng

    # ------------------------------------------------------------
    # Rider live point
    # Priority:
    # 1. latest delivery_locations for this order
    # 2. delivery_availability for assigned rider
    # ------------------------------------------------------------
    latest_rider_location = mongo.delivery_locations.find_one(
        {"order_id": oid_obj},
        sort=[("recorded_at", -1)]
    )

    rider_lat = None
    rider_lng = None
    rider_updated_at = ""
    rider_source = ""

    if latest_rider_location:
        rider_lat, rider_lng = _clean_lat_lng(
            latest_rider_location.get("latitude"),
            latest_rider_location.get("longitude")
        )
        rider_updated_at = latest_rider_location.get("recorded_at") or ""
        rider_source = "delivery_locations"

    if rider_lat is None or rider_lng is None:
        delivery_partner_id = _safe_id(order.get("delivery_partner_id"))

        if delivery_partner_id:
            availability = mongo.delivery_availability.find_one({
                "user_id": delivery_partner_id,
                "active": True
            }) or {}

            rider_lat, rider_lng = _clean_lat_lng(
                availability.get("latitude"),
                availability.get("longitude")
            )
            rider_updated_at = availability.get("updated_at") or ""
            rider_source = "delivery_availability"

    rider_view = {
        "id": _safe_id(order.get("delivery_partner_id")),
        "name": order.get("delivery_partner_name") or "",
        "phone": order.get("delivery_partner_phone") or "",
        "latitude": rider_lat,
        "longitude": rider_lng,
        "updated_at": rider_updated_at,
        "source": rider_source,
    }

    tracking_map = {
        "order_id": str(oid_obj),
        "customer": {
            "label": "Delivery Address",
            "latitude": customer_lat,
            "longitude": customer_lng,
            "source": customer_source,
            "address": {
                "line1": addr.get("line1") if addr else "",
                "line2": addr.get("line2") if addr else "",
                "city": addr.get("city") if addr else "",
                "state": addr.get("state") if addr else "",
                "pincode": addr.get("pincode") if addr else "",
            }
        },
        "store": {
            "label": store_view.get("store_name") or "Store",
            "latitude": store_lat,
            "longitude": store_lng,
            "address": store_view.get("address") or "",
        },
        "rider": rider_view
    }

    events = list(mongo.order_events.find({"order_id": oid_obj}).sort("created_at", 1))

    for e in events:
        e["id"] = str(e["_id"])

    return {
        "order": order,
        "items": items,
        "address": addr,
        "events": events,
        "store": store_view,
        "tracking_map": tracking_map,
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
    Includes operational, delivery and serviceability fields for admin UI.
    """
    rows = []

    def _row_int(value, default=0):
        try:
            if value is None or str(value).strip() == "":
                return int(default)
            if isinstance(value, bool):
                return 1 if value else 0
            value_str = str(value).strip().lower()
            if value_str in ["true", "yes", "on"]:
                return 1
            if value_str in ["false", "no", "off"]:
                return 0
            return int(value)
        except Exception:
            return int(default)

    def _row_float(value, default=None):
        try:
            if value is None or str(value).strip() == "":
                return default
            return float(value)
        except Exception:
            return default

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

        is_online = _row_int(
            store.get("is_online", store.get("is_open", 1)),
            1
        )

        delivery_enabled = _row_int(
            store.get(
                "delivery_enabled",
                1 if store.get("delivery_available", False) else 0
            ),
            0
        )

        delivery_zone_polygon = store.get("delivery_zone_polygon") or []
        delivery_zone_configured = 1 if len(delivery_zone_polygon) >= 3 else _row_int(
            store.get("delivery_zone_configured"),
            0
        )

        latitude = _row_float(store.get("latitude"))
        longitude = _row_float(store.get("longitude"))

        rows.append({
            "id": sid_str,
            "store_id": sid_str,
            "store_name": store.get("store_name") or store.get("name") or "Store",
            "address": store.get("address") or "",
            "city": store.get("city") or "",
            "state": store.get("state") or "",
            "pincode": store.get("pincode") or "",

            "latitude": latitude,
            "longitude": longitude,

            "image_url": store.get("image_url") or store.get("logo") or "",

            # Admin/account status.
            "is_active": _row_int(store.get("is_active", 1), 1),

            # Store operational status.
            "is_online": is_online,
            "is_open": is_online,

            # Delivery/serviceability status.
            "delivery_enabled": delivery_enabled,
            "delivery_available": bool(delivery_enabled),
            "delivery_mode": store.get("delivery_mode") or "polygon",
            "delivery_zone_polygon": delivery_zone_polygon,
            "delivery_zone_configured": delivery_zone_configured,
            "delivery_base_fee": _row_float(store.get("delivery_base_fee"), 40),

            "created_at": store.get("created_at") or "",
            "updated_at": store.get("updated_at") or "",

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

    try:
        metrics.update(build_delivery_mode_order_metrics(sid))
    except Exception:
        pass

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



# ----------------EMAIL SETUP---------
def send_email(to_email, subject, body):
    smtp_host = (os.getenv("SMTP_HOST") or "").strip()
    smtp_port = int(os.getenv("SMTP_PORT", 587))
    smtp_user = (os.getenv("SMTP_USER") or "").strip()
    smtp_password = (os.getenv("SMTP_PASSWORD") or "").strip()
    smtp_from = (os.getenv("SMTP_FROM") or "").strip()
    smtp_from_name = (os.getenv("SMTP_FROM_NAME") or "NELOCALS").strip()

    to_email = (to_email or "").strip()
    subject = (subject or "").strip()
    body = body or ""

    if not smtp_host:
        raise RuntimeError("SMTP_HOST is missing in .env")

    if not smtp_user:
        raise RuntimeError("SMTP_USER is missing in .env")

    if not smtp_password:
        raise RuntimeError("SMTP_PASSWORD is missing in .env")

    if not smtp_from:
        raise RuntimeError("SMTP_FROM is missing in .env")

    if not to_email:
        raise RuntimeError("Recipient email is missing.")

    msg = MIMEMultipart("alternative")
    msg["From"] = f"{smtp_from_name} <{smtp_from}>"
    msg["To"] = to_email
    msg["Subject"] = subject
    msg["Reply-To"] = smtp_from

    msg.attach(MIMEText(body, "html", "utf-8"))

    server = None

    try:
        server = smtplib.SMTP(smtp_host, smtp_port, timeout=30)
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(smtp_user, smtp_password)

        failed_recipients = server.sendmail(
            smtp_from,
            [to_email],
            msg.as_string()
        )

        if failed_recipients:
            raise RuntimeError(f"SMTP rejected recipient(s): {failed_recipients}")

        log_debug(f"[EMAIL SENT] to={to_email} subject={subject}")

        return {
            "ok": True,
            "to": to_email,
            "subject": subject,
            "error": ""
        }

    except Exception as exc:
        log_warning(f"[EMAIL ERROR] to={to_email} subject={subject} error={exc}")
        raise

    finally:
        try:
            if server:
                server.quit()
        except Exception:
            pass


CONTACT_AUTO_REPLY_SETTINGS_KEY = "contact_auto_reply_settings"


def _default_contact_auto_reply_subject():
    return "We received your message - NELOCALS"


def _default_contact_auto_reply_body():
    return (
        "Dear {name},\n\n"
        "Thank you for contacting NELOCALS.\n\n"
        "We have received your message regarding: {subject}.\n\n"
        "Our admin/contact team will review your message and contact you as soon as possible.\n\n"
        "Thank you,\n"
        "NELOCALS Admin Team"
    )


def get_contact_auto_reply_settings():
    settings = mongo.platform_settings.find_one({
        "key": CONTACT_AUTO_REPLY_SETTINGS_KEY
    }) or {}

    # Default remains ON for existing installations, but Admin can now turn it OFF.
    enabled = bool(settings.get("enabled", True))

    return {
        "enabled": enabled,
        "subject": settings.get("subject") or _default_contact_auto_reply_subject(),
        "body": settings.get("body") or _default_contact_auto_reply_body(),
        "updated_at": settings.get("updated_at") or "",
        "updated_by_name": settings.get("updated_by_name") or ""
    }


def build_contact_auto_reply_email(contact_doc):
    contact_doc = contact_doc or {}
    settings = get_contact_auto_reply_settings()

    name = contact_doc.get("name") or "there"
    subject_text = contact_doc.get("subject") or "your message"
    message_text = contact_doc.get("message") or ""

    email_subject = settings.get("subject") or _default_contact_auto_reply_subject()

    raw_body = settings.get("body") or _default_contact_auto_reply_body()

    try:
        raw_body = raw_body.format(
            name=name,
            subject=subject_text,
            message=message_text
        )
    except Exception:
        # If admin accidentally writes invalid placeholders,
        # still send the saved text instead of blocking the email.
        pass

    safe_body = html.escape(raw_body).replace("\n", "<br>")

    email_body = f"""
    <div style="font-family:Arial,sans-serif;line-height:1.6;color:#1F332A;">
      <h2 style="color:#00A859;margin-bottom:8px;">NELOCALS</h2>

      <div style="margin:16px 0;padding:14px;border-left:4px solid #00A859;background:#F3FFF8;">
        {safe_body}
      </div>
    </div>
    """

    return email_subject, email_body


def send_contact_auto_reply(contact_doc):
    settings = get_contact_auto_reply_settings()

    if not settings.get("enabled"):
        return {
            "enabled": False,
            "sent": False,
            "error": "",
            "subject": "",
            "body": ""
        }

    contact_doc = contact_doc or {}
    to_email = (contact_doc.get("email") or "").strip()

    if not to_email:
        return {
            "enabled": True,
            "sent": False,
            "error": "Missing recipient email.",
            "subject": "",
            "body": ""
        }

    try:
        subject, body = build_contact_auto_reply_email(contact_doc)
        send_email(to_email, subject, body)

        return {
            "enabled": True,
            "sent": True,
            "error": "",
            "subject": subject,
            "body": body
        }
    except Exception as exc:
        return {
            "enabled": True,
            "sent": False,
            "error": str(exc),
            "subject": "",
            "body": ""
        }
















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



log_debug("\n=== ROUTES LOADED ===")
log_debug(app.url_map)
log_debug("=====================\n")



if __name__ == '__main__':
    app.run(host="0.0.0.0",
        port=int(os.getenv("PORT", "5000")),
        debug=os.getenv("FLASK_DEBUG", "0").strip().lower() in ["1", "true", "yes", "on"],
        use_reloader=False)

# Export all shared globals/helpers, including underscore-prefixed legacy helpers,
# so split route modules can preserve original app.py logic unchanged.
__all__ = [name for name in globals() if not name.startswith('__')]
