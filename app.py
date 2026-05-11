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
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, send_file, abort
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

@app.route('/')
def index():
    allow, pin = _session_pin_is_serviceable()

    if session.get("service_area") and not allow:
        flash("Please enter a valid 6-digit pincode.", "warning")
        products = []
    else:
        products = list(mongo.products.find({
            "is_active": 1,
            "stock_kg": {"$gt": 0}
        }).sort("created_at", -1).limit(12))

        for p in products:
            p["id"] = str(p["_id"])

            ratings = list(mongo.product_ratings.find({
                "product_id": p["_id"]
            }))

            rating_count = len(ratings)

            if rating_count > 0:
                avg_rating = round(
                    sum(float(r.get("rating") or 0) for r in ratings) / rating_count,
                    1
                )
            else:
                avg_rating = 0

            p["avg_rating"] = avg_rating
            p["rating_count"] = rating_count

            store = None
            if p.get("store_id"):
                store = mongo.stores.find_one({"_id": p["store_id"]})

            p["store_name"] = store.get("store_name") if store else ""
            p["store_id"] = str(p.get("store_id")) if p.get("store_id") else ""

    product_rating_map = {}
    store_rating_map = {}

    for p in products:
        product_rating_map[p["id"]] = {
            "avg": p.get("avg_rating", 0),
            "count": p.get("rating_count", 0)
        }

        sid = p.get("store_id")
        if sid:
            store_rating_map[sid] = {"avg": 0, "count": 0}

    return render_template(
        'index.html',
        user=current_user(),
        products=products,
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
            "is_active": 1,
            "stock_kg": {"$gt": 0}
        }).sort("created_at", -1))

        for p in products:
            p["id"] = str(p["_id"])

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
        return jsonify({'ok': False, 'msg': f'Max available is {stock:.2f} kg'}), 409

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
                    {"$set": {"is_active": 0, "stock_kg": 0}}
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

    u = current_user()
    is_staff = bool(u and (u.get("role") in ("admin", "store")))

    if not is_staff and (
        int(p.get("is_active") or 0) != 1 or float(p.get("stock_kg") or 0) <= 0
    ):
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

    return render_template(
        'product.html',
        user=u,
        product=p,
        rating=rating_summary,
        reviews=reviews
    )

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

    allow, pin = _session_pin_is_serviceable()

    if session.get("service_area") and not allow:
        flash("Please enter a valid 6-digit pincode.", "warning")
        products = []
    else:
        products = list(
            mongo.products.find({
                "store_id": sid_obj,
                "is_active": 1,
                "stock_kg": {"$gt": 0}
            }).sort("created_at", -1)
        )

        for p in products:
            p["id"] = str(p["_id"])
            p["store_id"] = str(p.get("store_id")) if p.get("store_id") else ""
            p["store_name"] = store.get("store_name", "")

    return render_template("store_catalog.html", user=user, store=store, products=products)

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
    

@app.route('/admin/create-delivery', methods=['GET','POST'])
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
            mongo.users.insert_one({
                "name": name,
                "email": email,
                "phone": phone,
                "password_hash": generate_password_hash(password),
                "role": "delivery",
                "phone_verified": 1,
                "is_active": 1,
                "created_at": datetime.utcnow().isoformat()
            })

        except DuplicateKeyError:
            flash("This email or phone is already registered. Please use different details.", "error")
            return redirect(url_for('admin_create_delivery'))
        except Exception as e:
            flash(f"Failed to create delivery partner: {str(e)}", "error")
            return redirect(url_for('admin_create_delivery'))

        flash("Delivery partner created.", "success")
        return redirect(url_for('admin_create_delivery'))

    return render_template('admin_create_delivery.html', user=current_user())

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

@app.route('/admin/users')
@login_required(role='admin')
def admin_users():
    users = list(mongo.users.find({}).sort("created_at", -1))

    for u in users:
        u["id"] = str(u["_id"])
        u["is_active"] = int(u.get("is_active") or 0)
        u["phone_verified"] = int(u.get("phone_verified") or 0)

    return render_template("admin_users.html", users=users, user=current_user())

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
@app.route('/store')
@login_required(role='store')
def store_dashboard():
    u = current_user()

    store = mongo.stores.find_one({"user_id": u["id"]})

    if not store:
        flash("Store not found.", "danger")
        return redirect(url_for("index"))

    sid = store["_id"]
    store["id"] = str(store["_id"])

    store_orders = list(mongo.orders.find({"store_id": sid}))

    delivered_orders = [
        o for o in store_orders
        if o.get("status") == "DELIVERED"
    ]

    gmv_total = sum([
        float(o.get("total_amount") or 0)
        + float(o.get("delivery_fee") or 0)
        + float(o.get("tip_amount") or 0)
        for o in delivered_orders
    ])

    paid_transactions = list(mongo.transactions.find({
        "order_id": {"$in": [o["_id"] for o in delivered_orders]},
        "status": "PAID"
    }))

    paid_total = sum([
        float(t.get("amount") or 0)
        for t in paid_transactions
    ])

    unique_customers = len(set([
        o.get("user_id")
        for o in store_orders
        if o.get("user_id")
    ]))

    metrics = {
        "total_orders": len(store_orders),
        "gmv_total": float(gmv_total),
        "paid_total": float(paid_total),
        "txn_count": len(paid_transactions),
        "unique_customers": unique_customers,
    }

    products = list(mongo.products.find({"store_id": sid}).sort("created_at", -1))

    for p in products:
        p["id"] = str(p["_id"])
        p["store_id"] = str(p.get("store_id")) if p.get("store_id") else ""

    active_orders = list(mongo.orders.find({
        "store_id": sid,
        "status": {"$nin": ["DELIVERED", "CANCELLED"]}
    }).sort("created_at", -1))

    orders = []

    for o in active_orders:
        customer = mongo.users.find_one({"_id": ObjectId(o["user_id"])}) if o.get("user_id") else None
        addr = mongo.order_addresses.find_one({"order_id": o["_id"]})

        o["id"] = str(o["_id"])
        o["customer_name"] = customer.get("name") if customer else o.get("customer_name", "")
        o["customer_phone"] = customer.get("phone") if customer else o.get("customer_phone", "")

        o["addr_line1"] = addr.get("line1") if addr else ""
        o["addr_line2"] = addr.get("line2") if addr else ""
        o["addr_city"] = addr.get("city") if addr else ""
        o["addr_state"] = addr.get("state") if addr else ""
        o["addr_pincode"] = addr.get("pincode") if addr else ""
        o["addr_lat"] = addr.get("latitude") if addr else None
        o["addr_lng"] = addr.get("longitude") if addr else None

        orders.append(o)

    return render_template(
    "store_dashboard.html",
    user=u,
    store=store,
    products=products,
    orders=orders,
    metrics=metrics
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



@app.route('/store/product/new', methods=['POST'])
@login_required(role='store')
def store_product_new():
    u = current_user()

    store = mongo.stores.find_one({"user_id": u["id"]})
    if not store:
        flash("Store not found.", "danger")
        return redirect(url_for("store_dashboard"))

    sid = store["_id"]

    name = request.form.get('name', '').strip()

    try:
        price_per_kg = float(request.form.get('price_per_kg', '0') or 0)
    except Exception:
        price_per_kg = 0

    try:
        stock_kg = float(request.form.get('stock_kg', '0') or 0)
    except Exception:
        stock_kg = 0

    category = (request.form.get('category') or '').strip()
    sub_category = (request.form.get('sub_category') or '').strip()

    allowed_categories = ['Fresh cuts', 'Ready to cook', 'Spices']
    fresh_cut_subs = ['Curry cuts', 'Boneless & Mince', 'Offals']

    if not name:
        flash('Product name is required.', 'warning')
        return redirect(url_for('store_dashboard'))

    if price_per_kg <= 0:
        flash('Price must be greater than 0.', 'warning')
        return redirect(url_for('store_dashboard'))

    if stock_kg < 0:
        flash('Stock cannot be negative.', 'warning')
        return redirect(url_for('store_dashboard'))

    if category not in allowed_categories:
        flash('Invalid category selected.', 'warning')
        return redirect(url_for('store_dashboard'))

    if category == 'Fresh cuts':
        if sub_category not in fresh_cut_subs:
            flash('Please select a valid sub-category for Fresh cuts.', 'warning')
            return redirect(url_for('store_dashboard'))
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
            return redirect(url_for("store_dashboard"))

    mongo.products.insert_one({
        "store_id": sid,
        "store_name": store.get("store_name", ""),
        "name": name,
        "price_per_kg": price_per_kg,
        "stock_kg": stock_kg,
        "image_path": image_path,
        "is_active": 1,
        "category": category,
        "sub_category": sub_category,
        "created_at": datetime.utcnow().isoformat(),
        "updated_at": datetime.utcnow().isoformat()
    })

    flash('Product added.', 'success')
    return redirect(url_for('store_dashboard'))

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
    return redirect(url_for("store_dashboard"))


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

    return redirect(url_for("store_dashboard"))


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

    return render_template("store_product_edit.html", user=u, product=product)

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

    raw_category = (
        request.form.get("category")
        or product.get("category")
        or ""
    ).strip()

    raw_sub_category = (
        request.form.get("sub_category")
        or product.get("sub_category")
        or ""
    ).strip()

    category_map = {
        "fresh cuts": "Fresh cuts",
        "fresh cut": "Fresh cuts",
        "fresh_cuts": "Fresh cuts",
        "fresh-cuts": "Fresh cuts",
        "ready to cook": "Ready to cook",
        "ready to Cook": "Ready to cook",
        "ready_to_cook": "Ready to cook",
        "ready-to-cook": "Ready to cook",
        "spices": "Spices",
    }

    sub_category_map = {
        "curry cuts": "Curry cuts",
        "curry cut": "Curry cuts",
        "curry_cuts": "Curry cuts",
        "curry-cuts": "Curry cuts",
        "boneless & mince": "Boneless & Mince",
        "boneless and mince": "Boneless & Mince",
        "boneless_mince": "Boneless & Mince",
        "boneless-mince": "Boneless & Mince",
        "offals": "Offals",
    }

    category = category_map.get(raw_category.lower(), raw_category)
    sub_category = sub_category_map.get(raw_sub_category.lower(), raw_sub_category)

    try:
        price = float(request.form.get("price_per_kg", "0") or 0)
    except Exception:
        price = -1

    try:
        stock = float(request.form.get("stock_kg", "0") or 0)
    except Exception:
        stock = -1

    if not name:
        flash("Product name is required.", "warning")
        return redirect(url_for("store_product_edit", pid=pid))

    if price < 0:
        flash("Enter a valid non-negative price.", "warning")
        return redirect(url_for("store_product_edit", pid=pid))

    if stock < 0:
        flash("Enter a valid non-negative stock.", "warning")
        return redirect(url_for("store_product_edit", pid=pid))

    allowed_categories = ["Fresh cuts", "Ready to cook", "Spices"]
    fresh_cut_subs = ["Curry cuts", "Boneless & Mince", "Offals"]

    if not category:
        category = product.get("category") or "Ready to cook"

    if category not in allowed_categories:
        flash(f"Invalid category selected: {category}", "warning")
        return redirect(url_for("store_product_edit", pid=pid))

    if category == "Fresh cuts":
        if not sub_category:
            sub_category = product.get("sub_category") or "Curry cuts"

        if sub_category not in fresh_cut_subs:
            flash(f"Invalid sub-category selected: {sub_category}", "warning")
            return redirect(url_for("store_product_edit", pid=pid))
    else:
        sub_category = None

    update_data = {
        "name": name,
        "price_per_kg": price,
        "stock_kg": stock,
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
    return redirect(url_for("store_dashboard"))


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
        return redirect(url_for("store_dashboard"))

    new_status = request.form.get("status", "PLACED").upper()
    now = datetime.utcnow().isoformat()

    order = mongo.orders.find_one({
        "_id": oid_obj,
        "store_id": store["_id"]
    })

    if not order:
        flash("Order not found.", "danger")
        return redirect(url_for("store_dashboard"))

    update_data = {
        "status": new_status,
        "updated_at": now
    }

    if new_status == "DELIVERED":
        update_data["payment_status"] = "PAID"

    mongo.orders.update_one(
        {"_id": oid_obj},
        {"$set": update_data}
    )

    mongo.order_events.insert_one({
        "order_id": oid_obj,
        "status": new_status,
        "note": "Updated by store",
        "created_at": now
    })

    if new_status == "DELIVERED":
        mongo.transactions.update_many(
            {"order_id": oid_obj},
            {"$set": {"status": "PAID", "updated_at": now}}
        )

    flash("Order status updated.", "success")
    return redirect(url_for("store_dashboard"))


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



