import os
import io
import math
from io import BytesIO
import secrets
from datetime import datetime, timedelta
from random import randint
import csv, zipfile, json
from datetime import date
import time
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, send_file, abort
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from functools import wraps
from flask import make_response
from sqlite3 import IntegrityError

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


# NOTE: db.py must define these
from db import (
    init_db, query, execute, add_order_event,
    create_password_reset_token, get_valid_reset_token, consume_reset_token,
    # Live GPS helpers
    save_delivery_location, get_latest_location_for_order,
    # Ratings + complaints helpers
    add_product_rating, add_store_rating,
    get_product_rating_summary, get_store_rating_summary,
    file_complaint, list_recent_complaints, update_complaint_status,
    # NEW admin helpers
    render_export_to_csv_zip_bytes, get_user_by_id, can_delete_user_hard, hard_delete_user,
    # NEW for atomic checkout + stock changes
    get_conn
)

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
# DELIVERY CONFIG
# ----------------------
BASE_DELIVERY_FEE_INR = 40
DELIVERY_SURCHARGE_SLABS = [
    (0, 2, 0),
    (2, 5, 15),
    (5, 7, 25),
    (7, 10, 35),
]
MAX_DELIVERY_KM = 10.0

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


def _ensure_contact_messages_status_column():
    try:
        cols = [r["name"] for r in query("PRAGMA table_info(contact_messages)")]
        if "status" not in cols:
            execute("ALTER TABLE contact_messages ADD COLUMN status TEXT DEFAULT 'NEW'")
    except Exception:
        pass

# ======================
# SERVICEABLE PINCODES
# ======================
def _ensure_serviceable_table():
    try:
        execute("""
            CREATE TABLE IF NOT EXISTS serviceable_pincodes (
              pincode TEXT PRIMARY KEY,
              label   TEXT
            )
        """)
    except Exception:
        pass

# Default seed (Aizawl)
SEED_PINS = [
    ("796001", "Aizawl"),
    ("796004", "Aizawl"),
    ("796005", "Aizawl"),
    ("796007", "Aizawl"),
    ("796008", "Aizawl"),
    ("796009", "Aizawl"),
    ("796012", "Aizawl"),
    ("796014", "Aizawl"),
    ("796015", "Aizawl"),
    ("796017", "Aizawl"),
]
def _seed_pincodes_if_empty():
    if mongo.serviceable_pincodes.count_documents({}) == 0:
        for pc, label in SEED_PINS:
            mongo.serviceable_pincodes.update_one(
                {"pincode": pc},
                {"$setOnInsert": {"pincode": pc, "label": label}},
                upsert=True
            )

with app.app_context():
    ensure_mongo_indexes()


def normalize_phone(phone: str) -> str:
    """
    Normalize to E.164. If user typed a 10-digit Indian number, prefix +91.
    If already starts with '+', return as-is.
    """
    p = (phone or "").strip().replace(" ", "")
    if p.startswith("+"):
        return p
    digits = "".join(ch for ch in p if ch.isdigit())
    if len(digits) == 10:
        return "+91" + digits
    return "+" + digits if digits and not digits.startswith("+") else digits


def _clean_pin(pin) -> str:
    """Keep digits only and trim spaces (handles '796001 ', 796001, etc.)."""
    if pin is None:
        return ""
    s = str(pin).strip()
    return "".join(ch for ch in s if ch.isdigit())

def get_serviceable_pincodes():
    rows = mongo.serviceable_pincodes.find({}, {"pincode": 1, "_id": 0}).sort("pincode", 1)
    return [r["pincode"] for r in rows]

def is_serviceable_pincode(pin: str) -> bool:
    clean_pin = _clean_pin(pin)
    if not clean_pin:
        return False
    # normalize every row from DB too
    pins = [_clean_pin(r) for r in get_serviceable_pincodes()]
    return clean_pin in set(pins)

@app.route("/api/service/pincodes")
def api_service_pincodes():
    return jsonify({"ok": True, "pincodes": get_serviceable_pincodes()})

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
        flash(f"Sorry, we currently serve select pincodes only. Your pincode {pincode} is not serviceable.", "warning")
    else:
        flash(f"Location set to {pincode}.", "success")
    return redirect(request.referrer or url_for("index"))

# ----------------------
# ADMIN: Manage serviceable pincodes
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
    try:
        execute("""
            CREATE TABLE IF NOT EXISTS api_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                token TEXT UNIQUE NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)
    except Exception:
        pass

with app.app_context():
    ensure_mongo_indexes()
    _seed_pincodes_if_empty()

@app.route("/admin/pincodes", methods=["GET"], endpoint="admin_pincodes")
@login_required(role='admin')
def admin_pincodes():
    pins = list(mongo.serviceable_pincodes.find({}, {"_id": 0}).sort("pincode", 1))
    return render_template("admin_pincodes.html", user=current_user(), pincodes=pins)

@app.route("/admin/pincodes/add", methods=["POST"], endpoint="admin_pincodes_add")
@login_required(role='admin')
def admin_pincodes_add():
    pin = (request.form.get("pincode") or "").strip()
    label = (request.form.get("label") or "").strip() or None

    if not pin.isdigit():
        flash("Enter a numeric pincode.", "warning")
        return redirect(url_for("admin_pincodes"))

    existing = mongo.serviceable_pincodes.find_one({"pincode": pin})
    if existing:
        flash("Pincode already exists.", "danger")
        return redirect(url_for("admin_pincodes"))

    mongo.serviceable_pincodes.insert_one({
        "pincode": pin,
        "label": label
    })

    flash(f"Pincode {pin} added.", "success")
    return redirect(url_for("admin_pincodes"))

@app.route("/admin/pincodes/<pin>/delete", methods=["POST"], endpoint="admin_pincodes_delete")
@login_required(role='admin')
def admin_pincodes_delete(pin):
    mongo.serviceable_pincodes.delete_one({"pincode": pin})
    flash(f"Pincode {pin} removed.", "info")
    return redirect(url_for("admin_pincodes"))

# ----------------------
# MISC UTILS
# ----------------------
app.config["UPLOAD_FOLDER"] = os.path.join(os.path.dirname(__file__), "uploads")
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024
os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

ALLOWED_EXTS = {"jpg","jpeg","png","webp"}
def allowed_file(filename): 
    return "." in filename and filename.rsplit(".",1)[1].lower() in ALLOWED_EXTS

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
        flash(f"Sorry, we currently serve select pincodes only. Your pincode {pin or '(none)'} is not serviceable.", "warning")
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
        flash(f"Sorry, we currently serve select pincodes only. Your pincode {pin or '(none)'} is not serviceable.", "warning")
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

    return render_template('products.html', products=products, user=current_user())

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
            flash(
                f"Sorry, we currently deliver only to allowed pincodes. Your address pincode {sel_pin or '(none)'} is not serviceable.",
                "danger"
            )
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

        if km is not None and km > MAX_DELIVERY_KM:
            flash(f"Delivery distance ({km:.1f} km) exceeds our limit of {MAX_DELIVERY_KM} km.", "danger")
            return redirect(url_for("checkout"))

        if km is None:
            delivery_fee = BASE_DELIVERY_FEE_INR
        else:
            extra = None
            for low, high, fee in DELIVERY_SURCHARGE_SLABS:
                last_high = DELIVERY_SURCHARGE_SLABS[-1][1]
                if (km >= low) and (km < high or high == last_high):
                    extra = fee
                    break

            if extra is None:
                flash("Delivery not available for this distance.", "danger")
                return redirect(url_for("checkout"))

            delivery_fee = BASE_DELIVERY_FEE_INR + extra

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
        max_km=MAX_DELIVERY_KM,
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

    raw_orders = list(
        mongo.orders.find({
            "$or": [
                {"delivery_partner_id": u["id"]},
                {"delivery_partner_id": None},
                {"delivery_partner_id": {"$exists": False}}
            ]
        }).sort("created_at", -1)
    )

    orders = []

    for o in raw_orders:
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

        orders.append(o)

    return render_template(
        'delivery_dashboard.html',
        user=u,
        orders=orders
    )


@app.route('/delivery/order/<oid>/assign', methods=['POST'])
@login_required(role='delivery')
def delivery_assign(oid):
    u = current_user()

    try:
        oid_obj = ObjectId(oid)
    except Exception:
        flash("Invalid order.", "danger")
        return redirect(url_for("delivery_dashboard"))

    order = mongo.orders.find_one({"_id": oid_obj})

    if not order:
        flash("Order not found.", "danger")
        return redirect(url_for("delivery_dashboard"))

    existing_partner = order.get("delivery_partner_id")

    if existing_partner and existing_partner != u["id"]:
        flash("This order is already assigned to another delivery partner.", "warning")
        return redirect(url_for("delivery_dashboard"))

    now = datetime.utcnow().isoformat()

    mongo.orders.update_one(
        {"_id": oid_obj},
        {
            "$set": {
                "delivery_partner_id": u["id"],
                "status": "ASSIGNED_TO_DELIVERY",
                "updated_at": now
            }
        }
    )

    mongo.order_events.insert_one({
        "order_id": oid_obj,
        "status": "ASSIGNED_TO_DELIVERY",
        "note": "Assigned to delivery partner",
        "created_at": now
    })

    flash('Order assigned to you.', 'success')
    return redirect(url_for('delivery_dashboard'))


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

    last_id = request.args.get("last_id", "").strip()

    query_filter = {
        "store_id": store["_id"]
    }

    if last_id:
        try:
            query_filter["_id"] = {"$gt": ObjectId(last_id)}
        except Exception:
            pass

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
            "total_payable": total_payable
        })

    return jsonify({
        "ok": True,
        "new": new_items,
        "next_last_id": next_last_id
    })

@app.route('/api/alerts/delivery', methods=['GET'])
@login_required(role='delivery')
def api_alerts_delivery():
    since = request.args.get('since') or ""

    query_filter = {
        "$or": [
            {"delivery_partner_id": None},
            {"delivery_partner_id": {"$exists": False}}
        ]
    }

    if since:
        query_filter["created_at"] = {"$gt": since}
    else:
        query_filter["created_at"] = {
            "$gt": (datetime.utcnow() - timedelta(minutes=2)).isoformat()
        }

    rows = list(
        mongo.orders.find(query_filter).sort("created_at", -1)
    )

    new_items = []

    for o in rows:
        total_payable = (
            float(o.get("total_amount") or 0)
            + float(o.get("delivery_fee") or 0)
            + float(o.get("tip_amount") or 0)
        )

        new_items.append({
            "order_id": str(o["_id"]),
            "created_at": o.get("created_at"),
            "total_payable": total_payable
        })

    return jsonify({
        "ok": True,
        "new": new_items
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
        flash(f"Sorry, we currently serve select pincodes only. Your pincode {pin or '(none)'} is not serviceable.", "warning")
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
    try:
        cols = [row['name'] for row in query(f"PRAGMA table_info({table})")]
        return all(col in cols for col in columns)
    except Exception:
        return False

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

@app.route('/admin/dashboard')
@login_required(role='admin')
def admin_dashboard():
    delivered_orders = list(mongo.orders.find({"status": "DELIVERED"}))

    gmv = sum([
        float(o.get("total_amount") or 0)
        + float(o.get("delivery_fee") or 0)
        + float(o.get("tip_amount") or 0)
        for o in delivered_orders
    ])

    metrics = {
        "users": mongo.users.count_documents({}),
        "stores": mongo.stores.count_documents({}),
        "products": mongo.products.count_documents({}),
        "orders": mongo.orders.count_documents({}),
        "gmv": gmv,
    }

    by_store = []

    stores = list(mongo.stores.find({}).sort("store_name", 1))

    for s in stores:
        sid = s["_id"]

        store_orders = list(mongo.orders.find({"store_id": sid}))

        delivered_store_orders = [
            o for o in store_orders
            if o.get("status") == "DELIVERED"
        ]

        revenue = sum([
            float(o.get("total_amount") or 0)
            + float(o.get("delivery_fee") or 0)
            + float(o.get("tip_amount") or 0)
            for o in delivered_store_orders
        ])

        by_store.append({
            "store_id": str(sid),
            "store_name": s.get("store_name", ""),
            "orders": len(store_orders),
            "revenue": revenue,
        })

    by_store = sorted(
        by_store,
        key=lambda x: float(x.get("revenue") or 0),
        reverse=True
    )

    top_store_complaints = []
    top_delivery_complaints = []

        # ======================
    # PERFORMANCE & QUALITY - MongoDB
    # ======================
    since_dt = datetime.utcnow() - timedelta(days=30)
    since_iso = since_dt.isoformat()

    # Top rated stores
    top_rated_stores = []

    store_rating_groups = list(mongo.store_ratings.aggregate([
        {
            "$match": {
                "created_at": {"$gte": since_iso}
            }
        },
        {
            "$group": {
                "_id": "$store_id",
                "avg_rating": {"$avg": "$rating"},
                "rating_count": {"$sum": 1}
            }
        },
        {
            "$sort": {
                "avg_rating": -1,
                "rating_count": -1
            }
        },
        {
            "$limit": 10
        }
    ]))

    for row in store_rating_groups:
        store = mongo.stores.find_one({"_id": row["_id"]}) if row.get("_id") else None

        top_rated_stores.append({
            "store_name": store.get("store_name") if store else "Unknown Store",
            "avg_rating": round(float(row.get("avg_rating") or 0), 1),
            "rating_count": int(row.get("rating_count") or 0)
        })

    # Top rated products
    top_rated_products = []

    product_rating_groups = list(mongo.product_ratings.aggregate([
        {
            "$match": {
                "created_at": {"$gte": since_iso}
            }
        },
        {
            "$group": {
                "_id": "$product_id",
                "product_name": {"$first": "$product_name"},
                "avg_rating": {"$avg": "$rating"},
                "rating_count": {"$sum": 1}
            }
        },
        {
            "$sort": {
                "avg_rating": -1,
                "rating_count": -1
            }
        },
        {
            "$limit": 10
        }
    ]))

    for row in product_rating_groups:
        product = mongo.products.find_one({"_id": row["_id"]}) if row.get("_id") else None

        top_rated_products.append({
            "product_name": product.get("name") if product else row.get("product_name", "Unknown Product"),
            "avg_rating": round(float(row.get("avg_rating") or 0), 1),
            "rating_count": int(row.get("rating_count") or 0)
        })

    # Most complained delivery partners
    most_complained_delivery = []

    delivery_complaint_groups = list(mongo.complaints.aggregate([
        {
            "$match": {
                "target_type": "delivery",
                "created_at": {"$gte": since_iso}
            }
        },
        {
            "$group": {
                "_id": "$target_id",
                "complaint_count": {"$sum": 1}
            }
        },
        {
            "$sort": {
                "complaint_count": -1
            }
        },
        {
            "$limit": 10
        }
    ]))

    for row in delivery_complaint_groups:
        partner = None

        if row.get("_id"):
            try:
                partner = mongo.users.find_one({"_id": ObjectId(str(row["_id"]))})
            except Exception:
                partner = mongo.users.find_one({"_id": row["_id"]})

        most_complained_delivery.append({
            "delivery_name": partner.get("name") if partner else "Delivery Partner",
            "complaint_count": int(row.get("complaint_count") or 0)
        })

    # Most complained stores
    most_complained_stores = []

    store_complaint_groups = list(mongo.complaints.aggregate([
        {
            "$match": {
                "target_type": "store",
                "created_at": {"$gte": since_iso}
            }
        },
        {
            "$group": {
                "_id": "$target_id",
                "complaint_count": {"$sum": 1}
            }
        },
        {
            "$sort": {
                "complaint_count": -1
            }
        },
        {
            "$limit": 10
        }
    ]))

    for row in store_complaint_groups:
        store = mongo.stores.find_one({"_id": row["_id"]}) if row.get("_id") else None

        most_complained_stores.append({
            "store_name": store.get("store_name") if store else "Unknown Store",
            "complaint_count": int(row.get("complaint_count") or 0)
        })

    return render_template(
        "admin_dashboard.html",
        user=current_user(),
        metrics=metrics,
        by_store=by_store,
        top_store_complaints=top_store_complaints,
        top_delivery_complaints=top_delivery_complaints,
         top_rated_stores=top_rated_stores,
        top_rated_products=top_rated_products,
        most_complained_delivery=most_complained_delivery,
        most_complained_stores=most_complained_stores
    )

@app.route('/admin/approvals')
@login_required(role='admin')
def admin_approvals():
    flash('Approval feature under development.', 'info')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/create-store', methods=['GET','POST'])
@login_required(role='admin')
def admin_create_store():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').lower().strip()
        phone = request.form.get('phone', '').strip()
        password = request.form.get('password', '')
        store_name = request.form.get('store_name', '').strip()
        address = request.form.get('address', '').strip()

        lat_raw = request.form.get('latitude')
        lng_raw = request.form.get('longitude')

        latitude = None
        longitude = None

        try:
            latitude = float(lat_raw) if lat_raw and str(lat_raw).strip() else None
        except Exception:
            latitude = None

        try:
            longitude = float(lng_raw) if lng_raw and str(lng_raw).strip() else None
        except Exception:
            longitude = None

        if not name or not email or not phone or not password or not store_name:
            flash("Please fill all required fields.", "warning")
            return redirect(url_for('admin_create_store'))

        existing = mongo.users.find_one({"email": email})
        if existing:
            flash("Email already exists. Use a different email.", "warning")
            return redirect(url_for('admin_create_store'))

        try:
            result = mongo.users.insert_one({
                "name": name,
                "email": email,
                "phone": normalize_phone(phone),
                "password_hash": generate_password_hash(password),
                "role": "store",
                "phone_verified": 1,
                "is_active": 1,
                "created_at": datetime.utcnow().isoformat()
            })

            user_id = str(result.inserted_id)

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
            flash("Email or phone already exists.", "danger")
            return redirect(url_for('admin_create_store'))
        except Exception as e:
            flash(f"Store creation failed: {e}", "danger")
            return redirect(url_for('admin_create_store'))

        flash("Store created.", "success")
        return redirect(url_for('admin_create_store'))

    return render_template('admin_create_store.html', user=current_user())

@app.route('/admin/create-delivery', methods=['GET','POST'])
@login_required(role='admin')
def admin_create_delivery():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').lower().strip()
        phone = request.form.get('phone', '').strip()
        password = request.form.get('password', '')

        if not name or not email or not phone or not password:
            flash("Please fill all required fields.", "error")
            return redirect(url_for('admin_create_delivery'))

        existing = mongo.users.find_one({"email": email})
        if existing:
            flash("Email already exists. Use a different email.", "error")
            return redirect(url_for('admin_create_delivery'))

        try:
            mongo.users.insert_one({
                "name": name,
                "email": email,
                "phone": normalize_phone(phone),
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
    rows = query('''
        SELECT t.id as txn_id, t.created_at, o.id as order_id, o.total_amount, t.amount, t.status
        FROM transactions t JOIN orders o ON o.id = t.order_id
        ORDER BY t.created_at DESC
    ''')
    csv_lines = ['txn_id,created_at,order_id,total_amount,amount,status']
    for r in rows:
        csv_lines.append(f"{r['txn_id']},{r['created_at']},{r['order_id']},{r['total_amount']},{r['amount']},{r['status']}")
    data = "\n".join(csv_lines).encode('utf-8')
    return send_file(io.BytesIO(data), mimetype='text/csv', as_attachment=True, download_name='transactions.csv')

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

    view = (request.args.get("view") or "auto").lower()

    def is_phone_ua():
        ua = (request.user_agent.string or "").lower()
        return any(k in ua for k in ("android", "iphone", "ipad", "mobile"))

    if view == "mobile":
        template = "store_dashboard_mobile.html"
    elif view == "desktop":
        template = "store_dashboard.html"
    else:
        template = "store_dashboard_mobile.html" if is_phone_ua() else "store_dashboard.html"

    return render_template(
        template,
        user=u,
        store=store,
        products=products,
        orders=orders,
        metrics=metrics
    )


@app.route('/store/delivered-orders')
@login_required(role='store')
def store_delivered_orders():
    u = current_user()

    store = mongo.stores.find_one({"user_id": u["id"]})

    if not store:
        flash("Store not found.", "danger")
        return redirect(url_for("store_dashboard"))

    sid = store["_id"]
    store["id"] = str(store["_id"])

    delivered_raw = list(mongo.orders.find({
        "store_id": sid,
        "status": "DELIVERED"
    }).sort("created_at", -1))

    delivered = []

    for o in delivered_raw:
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

        delivered.append(o)

    return render_template(
        "store_delivered_orders.html",
        user=u,
        store=store,
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
    Supported presets via ?range=day|week|month (UTC dates).
    You can also pass explicit ?start=YYYY-MM-DD&end=YYYY-MM-DD (end exclusive).
    Only PAID transactions are included.
    """
    u = current_user()
    srow = query('SELECT id, store_name FROM stores WHERE user_id=?', (u['id'],))
    if not srow:
        flash('Store not found.', 'danger')
        return redirect(url_for('store_dashboard'))
    sid = srow[0]['id']

    # --- Parse preset or explicit dates
    preset = (request.args.get('range') or '').lower()  # 'day' | 'week' | 'month' | ''
    start_str = request.args.get('start')
    end_str   = request.args.get('end')    # exclusive end

    def iso(d): return d.isoformat()

    # Compute start/end (UTC date bounds)
    if start_str and end_str:
        # explicit range
        try:
            start_date = date.fromisoformat(start_str)
            end_date   = date.fromisoformat(end_str)
        except Exception:
            flash('Invalid start/end date. Use YYYY-MM-DD.', 'warning')
            return redirect(url_for('store_dashboard'))
    else:
        today = datetime.utcnow().date()
        if preset == 'day':
            start_date = today
            end_date   = today + timedelta(days=1)
        elif preset == 'week':
            # Monday..Sunday window
            start_date = today - timedelta(days=today.weekday())
            end_date   = start_date + timedelta(days=7)
        elif preset == 'month':
            start_date = date(today.year, today.month, 1)
            if today.month == 12:
                end_date = date(today.year + 1, 1, 1)
            else:
                end_date = date(today.year, today.month + 1, 1)
        else:
            # default: today
            start_date = today
            end_date   = today + timedelta(days=1)

    # Convert date-only bounds to ISO datetimes (inclusive start, exclusive end)
    start_iso = f"{iso(start_date)}T00:00:00"
    end_iso   = f"{iso(end_date)}T00:00:00"

    # Fetch PAID transactions for this store in window
    rows = query("""
        SELECT
          t.id                AS txn_id,
          t.created_at        AS txn_created_at,
          o.id                AS order_id,
          o.total_amount      AS items_total,
          COALESCE(o.delivery_fee,0) AS delivery_fee,
          COALESCE(o.tip_amount,0)   AS tip_amount,
          t.amount            AS paid_amount,
          t.status            AS txn_status
        FROM transactions t
        JOIN orders o ON o.id = t.order_id
        WHERE o.store_id = ?
          AND t.status = 'PAID'
          AND t.created_at >= ?
          AND t.created_at < ?
        ORDER BY t.created_at DESC
    """, (sid, start_iso, end_iso))

    # Build CSV
    csv_lines = [
        "txn_id,txn_created_at,order_id,items_total,delivery_fee,tip_amount,paid_amount,txn_status"
    ]
    for r in rows:
        csv_lines.append(",".join([
            str(r["txn_id"]),
            str(r["txn_created_at"]),
            str(r["order_id"]),
            f'{float(r["items_total"] or 0):.2f}',
            f'{float(r["delivery_fee"] or 0):.2f}',
            f'{float(r["tip_amount"] or 0):.2f}',
            f'{float(r["paid_amount"] or 0):.2f}',
            str(r["txn_status"]),
        ]))

    data = "\n".join(csv_lines).encode("utf-8")
    # Nice filename: store_<id>_<range>_YYYYMMDD.csv
    stamp = datetime.utcnow().strftime("%Y%m%d")
    label = preset or "day"
    fn = f"store_{sid}_txns_{label}_{stamp}.csv"

    return send_file(
        io.BytesIO(data),
        mimetype="text/csv",
        as_attachment=True,
        download_name=fn
    )



@app.route('/store/order/<oid>/status', methods=['POST'])
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
    email = request.form.get('email','').strip().lower()
    if not email or '@' not in email:
        flash('Please enter a valid email.','danger'); return redirect(request.referrer or url_for('index'))
    try:
        execute('INSERT INTO newsletter_subscribers (email, created_at) VALUES (?,?)', (email, datetime.utcnow().isoformat()))
        flash('Subscribed to newsletter!','success')
    except Exception:
        flash('You are already subscribed.','info')
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

        execute("""
            INSERT INTO contact_messages (name, email, phone, subject, message, status, created_at)
            VALUES (?,?,?,?,?,?,?)
        """, (name, email, phone, subject, message, "NEW", datetime.utcnow().isoformat()))

        flash("Message sent! We will contact you soon.", "success")
        return redirect(url_for("contact"))

    return render_template("contact.html", user=current_user())




@app.route("/admin/contact-messages")
@login_required(role="admin")
def admin_contact_messages():
    messages = query("""
        SELECT *
        FROM contact_messages
        ORDER BY created_at DESC
    """)
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
@login_required(role="admin")
def admin_contact_message_status(mid):
    status = (request.form.get("status") or "NEW").upper()
    if status not in ("NEW", "READ", "RESOLVED"):
        status = "NEW"

    execute(
        "UPDATE contact_messages SET status=? WHERE id=?",
        (status, mid)
    )

    return redirect(url_for("admin_contact_messages"))


# ==================== AUTH API ====================

@app.route("/api/auth/web-session", methods=["POST"])
@api_login_required
def api_create_web_session():
    """
    Create a web session for mobile app users.
    
    This endpoint allows mobile app users (who authenticate with JWT tokens)
    to access the web dashboard by generating a session cookie.
    
    Flow:
    1. Mobile app authenticates via /api/auth/login (gets JWT token)
    2. Mobile app calls this endpoint with JWT token in Authorization header
    3. Backend validates JWT, creates Flask session, returns session identifier
    4. Mobile app injects session cookie into WebView
    5. WebView can now access protected web routes as authenticated user
    
    Returns:
        JSON with session cookie that mobile app should inject into WebView
    """
    try:
        # Get user_id from session (set by @login_required_api decorator)
        user_id = session.get('user_id')
        
        if not user_id:
            return jsonify({
                'success': False,
                'error': 'User not found in session'
            }), 401
        
        # Fetch user details from database
        rows = query("SELECT * FROM users WHERE id=?", (user_id,))
        if not rows:
            return jsonify({
                'success': False,
                'error': 'User not found'
            }), 404
        
        user = dict(rows[0])
        
        # Verify user is a store owner
        if user.get('role') != 'store':
            return jsonify({
                'success': False,
                'error': 'Only store owners can access the web dashboard'
            }), 403
        
        # Create Flask session (same as regular web login)
        session['user_id'] = user['id']
        session['user_role'] = user['role']
        session.permanent = True  # Make session permanent (respects PERMANENT_SESSION_LIFETIME)
        session.modified = True
        
        # Generate unique session identifier for tracking
        import secrets
        session_identifier = secrets.token_urlsafe(32)
        session['mobile_session_id'] = session_identifier
        
        # Log session creation
        print(f"✅ Web session created for user {user['id']} ({user['email']})")
        
        # Return session information to mobile app
        return jsonify({
            'success': True,
            'message': 'Web session created successfully',
            'cookie_name': app.config.get('SESSION_COOKIE_NAME', 'session'),
            'session_cookie': session_identifier,
            'user': {
                'id': user['id'],
                'name': user['name'],
                'email': user['email'],
                'role': user['role']
            }
        }), 200
        
    except Exception as e:
        print(f"❌ Error creating web session: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': f'Internal server error: {str(e)}'
        }), 500
    
# # ---------------------------------
# # STEP 3: (OPTIONAL) ADD SESSION CLEANUP
# # ---------------------------------
# # Paste this if you want to clean up expired sessions

# @app.route("/api/auth/cleanup-sessions", methods=["POST"])
# @admin_approvals
# def api_cleanup_sessions():
#     """
#     Clean up expired mobile sessions (admin only).
    
#     This is optional but recommended for production.
#     You can run this periodically via a cron job or task scheduler.
#     """
#     try:
#         # If using custom web_sessions table (see alternative approach):
#         # execute('''
#         #     DELETE FROM web_sessions 
#         #     WHERE expires_at < ?
#         # ''', (datetime.utcnow().isoformat(),))
        
#         return jsonify({
#             'success': True,
#             'message': 'Session cleanup completed'
#         }), 200
        
#     except Exception as e:
#         print(f"❌ Error cleaning up sessions: {e}")
#         return jsonify({
#             'success': False,
#             'error': str(e)
#         }), 500


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

    # ✅ Category filter
    if category:
        if category not in allowed_categories:
            return jsonify({'success': False, 'error': 'Invalid category'}), 400

        mongo_filter["category"] = category

        # ✅ Sub-category filter only for Fresh cuts
        if sub_category:
            if category != 'Fresh cuts':
                return jsonify({'success': False, 'error': 'sub_category only valid for Fresh cuts'}), 400

            if sub_category not in fresh_cut_subs:
                return jsonify({'success': False, 'error': 'Invalid sub_category'}), 400

            mongo_filter["sub_category"] = sub_category

    # ✅ Search filter
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

        if rating_count > 0:
            avg_rating = round(
                sum(float(r.get("rating") or 0) for r in ratings) / rating_count,
                1
            )
        else:
            avg_rating = 0

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

    if rating_count > 0:
        avg_rating = round(
            sum(float(r.get("rating") or 0) for r in ratings) / rating_count,
            1
        )
    else:
        avg_rating = 0

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

@app.route('/api/user/profile', methods=['GET'])
@api_login_required
def api_user_profile(user_id):
    rows = query("SELECT * FROM users WHERE id=?", (user_id,))
    if not rows:
        return jsonify({'success': False, 'error': 'User not found'}), 404
    
    u = dict(rows[0])
    
    return jsonify({
        'success': True,
        'user': {
            'id': u['id'],
            'name': u['name'],
            'email': u['email'],
            'phone': u['phone'],
            'role': u['role']
        }
    })

@app.route('/api/user/profile', methods=['PUT'])
@api_login_required
def api_user_profile_update(user_id):
    data = request.get_json(silent=True) or {}
    
    name = data.get('name', '').strip()
    phone = data.get('phone', '').strip()
    
    if name:
        execute("UPDATE users SET name=? WHERE id=?", (name, user_id))
    if phone:
        execute("UPDATE users SET phone=? WHERE id=?", (phone, user_id))
    
    return jsonify({'success': True})

# ==================== CART API ====================

@app.route('/api/cart', methods=['GET'])
@api_login_required
def api_cart_get(user_id):
    cid = get_or_create_cart(user_id)
    
    items = query('''
                  SELECT ci.id AS cart_item_id, ci.weight_kg, ci.product_id,
                    p.name, p.price_per_kg, p.image_path, p.stock_kg,
                    p.store_id AS store_id
                        FROM cart_items ci
                        JOIN products p ON p.id = ci.product_id
                        WHERE ci.cart_id = ?
                    ''', (cid,))
    
    total = sum([(row['weight_kg'] or 0) * (row['price_per_kg'] or 0) for row in items])
    
    return jsonify({
        'success': True,
        'items': [{
            'id': item['cart_item_id'],
            'product_id': item['product_id'],
            'name': item['name'],
            'price_per_kg': float(item['price_per_kg'] or 0),
            'weight_kg': float(item['weight_kg'] or 0),
            'image_path': item['image_path'],
            'stock_kg': float(item['stock_kg'] or 0),
            'store_id': int(item['store_id']) if item['store_id'] is not None else None,
        } for item in items],
        'total': float(total)
    }), 200

@app.route('/api/cart/clear', methods=['POST'])
@api_login_required
def api_cart_clear(user_id):
    cid = get_or_create_cart(user_id)

    mongo.cart_items.delete_many({
        "cart_id": cid
    })

    return jsonify({
        "ok": True,
        "success": True,
        "cart_count": 0
    })


@app.route('/api/orders/<oid>/cancel', methods=['POST'])
@api_login_required
def api_order_cancel(user_id, oid):
    try:
        oid_obj = ObjectId(oid)
    except Exception:
        return jsonify({
            "success": False,
            "error": "Invalid order id"
        }), 400

    order_doc = mongo.orders.find_one({
        "_id": oid_obj,
        "user_id": str(user_id)
    })

    if not order_doc:
        return jsonify({
            "success": False,
            "error": "Order not found"
        }), 404

    cancellable_statuses = ["PLACED", "CONFIRMED", "PREPARING"]

    if order_doc.get("status") not in cancellable_statuses:
        return jsonify({
            "success": False,
            "error": "This order can no longer be cancelled"
        }), 400

    order_items = list(mongo.order_items.find({
        "order_id": oid_obj
    }))

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
                "cancelled_at": now,
                "updated_at": now
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
        "note": "Cancelled by customer via mobile app",
        "created_at": now
    })

    return jsonify({
        "success": True,
        "message": "Order cancelled successfully"
    })


@app.route('/api/cart', methods=['POST'])
@api_login_required
def api_cart_add_json(user_id):
    data = request.get_json(silent=True) or {}

    product_id = data.get('product_id')
    weight_kg = data.get('weight_kg', 1)

    try:
        product_id = int(product_id)
        weight_kg = float(weight_kg)
    except Exception:
        return jsonify({'success': False, 'error': 'Invalid product or weight'}), 400

    if weight_kg < 0.25:
        return jsonify({'success': False, 'error': 'Minimum 0.25 kg'}), 400

    # round to 0.25 steps
    weight_kg = round(round(weight_kg * 4) / 4, 2)

    # Fetch product with store_id
    prow = query("SELECT stock_kg, is_active, store_id FROM products WHERE id=?", (product_id,))
    if not prow:
        return jsonify({'success': False, 'error': 'Product not found'}), 404

    stock = float(prow[0]['stock_kg'] or 0)
    active = int(prow[0]['is_active'] or 0)
    new_store_id = int(prow[0]['store_id']) if prow[0]['store_id'] is not None else None

    if active != 1 or stock <= 0:
        return jsonify({'success': False, 'error': 'This item is sold out'}), 409

    if weight_kg > stock:
        return jsonify({'success': False, 'error': f'Max available is {stock:.2f} kg'}), 409

    cid = get_or_create_cart(user_id)

    # Single-store enforcement
    existing_store = query("""
        SELECT DISTINCT p.store_id AS store_id
        FROM cart_items ci
        JOIN products p ON p.id = ci.product_id
        WHERE ci.cart_id=?
    """, (cid,))

    if existing_store and new_store_id is not None:
        cart_store_id = int(existing_store[0]["store_id"])
        if cart_store_id != new_store_id:
            return jsonify({
                "success": False,
                "code": "DIFF_STORE",
                "error": "Your cart already has items from another store. Please clear the cart first."
            }), 409

    # Add/update
    rows = query('SELECT id FROM cart_items WHERE cart_id=? AND product_id=?', (cid, product_id))
    if rows:
        execute('UPDATE cart_items SET weight_kg=? WHERE id=?', (weight_kg, rows[0]['id']))
        cart_item_id = rows[0]['id']
    else:
        execute('INSERT INTO cart_items (cart_id, product_id, weight_kg) VALUES (?,?,?)', (cid, product_id, weight_kg))
        # if you have lastrowid helper, use it; else ignore
        cart_item_id = None

    c = query("SELECT COUNT(*) AS c FROM cart_items WHERE cart_id=?", (cid,))
    cart_count = int(c[0]["c"] or 0) if c else 0

    return jsonify({
        'success': True,
        'cart_count': cart_count,
        'store_id': new_store_id,
        'cart_item_id': cart_item_id
    }), 200


@app.route("/api/addresses", methods=["POST"])
@api_login_required
def api_address_create(user_id):
    data = request.get_json(silent=True) or {}

    line1 = (data.get("line1") or "").strip()
    line2 = (data.get("line2") or "").strip()
    city  = (data.get("city") or "").strip()
    state = (data.get("state") or "").strip()
    pincode = (data.get("pincode") or "").strip()
    lat = data.get("latitude")
    lng = data.get("longitude")
    label = (data.get("label") or "").strip() or "Home"
    is_def = 1 if bool(data.get("is_default", True)) else 0  # default True for app

    if not line1:
        return jsonify({"success": False, "error": "Address line1 is required"}), 400

    if not pincode or len(pincode) != 6 or not pincode.isdigit():
        return jsonify({"success": False, "error": "Valid 6-digit pincode required"}), 400

    if not is_serviceable_pincode(pincode):
        return jsonify({"success": False, "error": "Pincode not serviceable"}), 400

    if is_def:
        execute("UPDATE addresses SET is_default=0 WHERE user_id=?", (user_id,))

    aid = execute("""
        INSERT INTO addresses (user_id,label,line1,line2,city,state,pincode,latitude,longitude,is_default,created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """, (
        user_id, label, line1, line2, city, state, pincode,
        float(lat) if lat is not None and str(lat).strip() != "" else None,
        float(lng) if lng is not None and str(lng).strip() != "" else None,
        is_def, datetime.utcnow().isoformat()
    ))

    return jsonify({"success": True, "address_id": aid}), 201


@app.route("/api/addresses", methods=["GET"])
@api_login_required
def api_addresses_list(user_id):
    rows = query(
        "SELECT id,label,line1,line2,city,state,pincode,latitude,longitude,is_default,created_at "
        "FROM addresses WHERE user_id=? ORDER BY is_default DESC, id DESC",
        (user_id,),
    )

    return jsonify({
        "success": True,
        "addresses": [{
            "id": r["id"],
            "label": r["label"],
            # Keep app-friendly keys expected by Flutter UI
            "address_line_1": r["line1"],
            "address_line_2": r["line2"],
            "city": r["city"],
            "state": r["state"],
            "pincode": r["pincode"],
            "latitude": float(r["latitude"]) if r["latitude"] is not None else None,
            "longitude": float(r["longitude"]) if r["longitude"] is not None else None,
            "is_default": bool(r["is_default"]),
            "created_at": r["created_at"],
        } for r in rows]
    }), 200


def query_one(sql, params=()):
    rows = query(sql, params)
    return rows[0] if rows else None


@app.route("/api/addresses/<int:address_id>", methods=["DELETE"])
@api_login_required
def api_delete_address(user_id, address_id):
    # Ensure the address exists and belongs to this user
    row = query_one(
        "SELECT id FROM addresses WHERE id=? AND user_id=?",
        (address_id, user_id),
    )
    if not row:
        return jsonify({"success": False, "error": "Address not found"}), 404

    # Delete
    execute(
        "DELETE FROM addresses WHERE id=? AND user_id=?",
        (address_id, user_id),
    )

    return jsonify({"success": True}), 200


# Optional POST fallback delete (if your app uses POST instead of DELETE)
@app.route("/api/addresses/<int:address_id>/delete", methods=["POST"])
@api_login_required
def api_addresses_delete_post(user_id, address_id):
    return api_delete_address(user_id, address_id)


# ====================
# API CHECKOUT (FINAL FIXED)
# - Uses address_id from app (recommended)
# - Falls back to default address if address_id not sent
# - Creates order + items + transactions + order_addresses + order_events
# - Clears cart
# ====================

# @app.route('/api/checkout', methods=['POST'])
# @api_login_required
# def api_checkout(user_id):


#     data = request.get_json(silent=True) or {}

#     payment_method = (data.get('payment_method') or 'COD').strip() or 'COD'
#     tip_amount = data.get('tip_amount', 0)

#     # ✅ Preferred: address_id sent by app
#     address_id = data.get('address_id')

#     # --- Load cart items for this user ---
#     cid = get_or_create_cart(user_id)
#     items = query('''
#         SELECT ci.product_id, ci.weight_kg, p.price_per_kg, p.store_id,
#                p.stock_kg, p.is_active
#         FROM cart_items ci
#         JOIN products p ON p.id = ci.product_id
#         WHERE ci.cart_id = ?
#     ''', (cid,))

#     if not items:
#         return jsonify({'success': False, 'error': 'Cart is empty'}), 400

#     # --- Fetch address (address_id OR default) ---
#     addr = None
#     if address_id is not None:
#         try:
#             aid = int(address_id)
#         except Exception:
#             return jsonify({'success': False, 'error': 'Invalid address_id'}), 400

#         rows = query("SELECT * FROM addresses WHERE id=? AND user_id=?", (aid, user_id))
#         if not rows:
#             return jsonify({'success': False, 'error': 'Address not found'}), 404
#         addr = rows[0]
#     else:
#         rows = query(
#             "SELECT * FROM addresses WHERE user_id=? ORDER BY is_default DESC, id DESC LIMIT 1",
#             (user_id,)
#         )
#         if not rows:
#             return jsonify({'success': False, 'error': 'No delivery address found'}), 400
#         addr = rows[0]

#     # --- Validate address fields ---
#     line1 = (addr.get('line1') or '').strip()
#     pincode = (addr.get('pincode') or '').strip()

#     if not line1 or not pincode:
#         return jsonify({'success': False, 'error': 'Address line1 and pincode are required'}), 400

#     if len(pincode) != 6 or not pincode.isdigit():
#         return jsonify({'success': False, 'error': 'Invalid pincode'}), 400

#     if not is_serviceable_pincode(pincode):
#         return jsonify({'success': False, 'error': 'Pincode not serviceable'}), 400

#     # --- Validate items & stock ---
#     for it in items:
#         if int(it['is_active'] or 0) != 1:
#             return jsonify({'success': False, 'error': 'Product inactive'}), 400
#         if float(it['stock_kg'] or 0) <= 0:
#             return jsonify({'success': False, 'error': 'Item sold out'}), 409
#         if float(it['weight_kg'] or 0) > float(it['stock_kg'] or 0):
#             return jsonify({'success': False, 'error': 'Insufficient stock'}), 409

#     # ✅ Single-store checkout assumption (same as your website checkout)
#     store_id = int(items[0]['store_id'])

#     # --- Tip normalize ---
#     try:
#         tip_amount = float(tip_amount or 0)
#     except Exception:
#         tip_amount = 0.0
#     if tip_amount < 0:
#         tip_amount = 0.0
#     if tip_amount > 10000:
#         tip_amount = 10000.0
#     tip_amount = round(tip_amount, 2)

#     # --- Calculate distance & delivery fee (same pattern as website) ---
#     now = datetime.utcnow().isoformat()
#     store_row = query("SELECT * FROM stores WHERE id=?", (store_id,))
#     store = store_row[0] if store_row else None

#     km = None
#     try:
#         store_lat = store['latitude'] if store and 'latitude' in store.keys() else None
#         store_lng = store['longitude'] if store and 'longitude' in store.keys() else None
#         addr_lat = addr.get('latitude')
#         addr_lng = addr.get('longitude')

#         if store_lat is not None and store_lng is not None and addr_lat is not None and addr_lng is not None:
#             km = haversine_km(float(store_lat), float(store_lng), float(addr_lat), float(addr_lng))
#     except Exception:
#         km = None

#     if km is not None and km > MAX_DELIVERY_KM:
#         return jsonify({'success': False, 'error': f'Delivery distance ({km:.1f} km) exceeds limit'}), 400

#     if km is None:
#         delivery_fee = BASE_DELIVERY_FEE_INR
#     else:
#         extra = None
#         for low, high, fee in DELIVERY_SURCHARGE_SLABS:
#             last_high = DELIVERY_SURCHARGE_SLABS[-1][1]
#             if (km >= low) and (km < high or high == last_high):
#                 extra = fee
#                 break
#         if extra is None:
#             return jsonify({'success': False, 'error': 'Delivery not available for this distance'}), 400
#         delivery_fee = BASE_DELIVERY_FEE_INR + extra

#     items_total = sum(float(it['weight_kg']) * float(it['price_per_kg']) for it in items)
#     total_payable = float(items_total) + float(delivery_fee) + float(tip_amount)

#     # --- Transaction + Order creation (atomic) ---
#     conn = get_conn()
#     try:
#         cur = conn.cursor()
#         cur.execute("BEGIN IMMEDIATE")

#         # Re-check stock inside transaction
#         cur.execute('''
#             SELECT ci.product_id, ci.weight_kg, p.price_per_kg, p.store_id,
#                    p.stock_kg, p.is_active
#             FROM cart_items ci
#             JOIN products p ON p.id = ci.product_id
#             WHERE ci.cart_id = ?
#         ''', (cid,))
#         tx_items = cur.fetchall()

#         if not tx_items:
#             conn.rollback()
#             return jsonify({'success': False, 'error': 'Cart is empty'}), 400

#         for it in tx_items:
#             stock = float(it["stock_kg"] or 0)
#             need  = float(it["weight_kg"] or 0)
#             if int(it["is_active"] or 0) != 1 or stock <= 0:
#                 conn.rollback()
#                 return jsonify({'success': False, 'error': 'One or more items are sold out'}), 409
#             if need > stock:
#                 conn.rollback()
#                 return jsonify({'success': False, 'error': 'Insufficient stock'}), 409

#         tx_items_total = sum(float(it["weight_kg"]) * float(it["price_per_kg"]) for it in tx_items)

#         # 1) Create order
#         cur.execute('''
#             INSERT INTO orders (user_id, store_id, total_amount, status, payment_status, created_at, delivery_fee, distance_km, tip_amount)
#             VALUES (?,?,?,?,?,?,?,?,?)
#         ''', (
#             user_id, store_id, tx_items_total,
#             'PLACED', 'PENDING', now,
#             float(delivery_fee),
#             float(km) if km is not None else None,
#             float(tip_amount)
#         ))
#         order_id = cur.lastrowid

#         # 2) order_items + stock updates
#         for it in tx_items:
#             pid = int(it["product_id"])
#             need = float(it["weight_kg"])
#             price = float(it["price_per_kg"])
#             line_total = need * price

#             cur.execute('''
#                 INSERT INTO order_items (order_id, product_id, weight_kg, unit_price_per_kg, line_total)
#                 VALUES (?,?,?,?,?)
#             ''', (order_id, pid, need, price, line_total))

#             cur.execute("UPDATE products SET stock_kg = stock_kg - ? WHERE id=?", (need, pid))

#         # 3) transactions
#         cur.execute('''
#             INSERT INTO transactions (order_id, amount, payment_method, status, created_at)
#             VALUES (?,?,?,?,?)
#         ''', (order_id, float(total_payable), payment_method, 'PENDING', now))

#         # 4) order_addresses snapshot
#         cur.execute('''
#             INSERT INTO order_addresses (order_id, line1, line2, city, state, pincode, latitude, longitude, created_at)
#             VALUES (?,?,?,?,?,?,?,?,?)
#         ''', (
#             order_id,
#             (addr.get('line1') or ''),
#             (addr.get('line2') or ''),
#             (addr.get('city') or ''),
#             (addr.get('state') or ''),
#             (addr.get('pincode') or ''),
#             addr.get('latitude'),
#             addr.get('longitude'),
#             now
#         ))

#         # 5) order_events
#         cur.execute(
#             "INSERT INTO order_events (order_id, status, note, created_at) VALUES (?,?,?,?)",
#             (order_id, 'PLACED', '', now)
#         )

#         # 6) clear cart
#         cur.execute("DELETE FROM cart_items WHERE cart_id=?", (cid,))

#         conn.commit()

#     except Exception as e:
#         conn.rollback()
#         return jsonify({'success': False, 'error': str(e)}), 500
#     finally:
#         conn.close()

#     return jsonify({
#         'success': True,
#         'order_id': order_id,
#         'total_payable': round(float(total_payable), 2),
#         'delivery_fee': float(delivery_fee),
#         'distance_km': float(km) if km is not None else None,
#         'message': 'Order placed successfully'
#     }), 201


#     # ======================



# API CHECKOUT (APP)
# ======================
@app.route('/api/checkout', methods=['POST'])
@api_login_required
def api_checkout(user_id):
    data = request.get_json(silent=True) or {}

    payment_method = (data.get('payment_method') or 'COD').strip() or 'COD'
    tip_amount = data.get('tip_amount', 0)

    # --- Address from app (Option A) ---
    # Expected:
    # address: {
    #   line1, line2, city, state, pincode, latitude, longitude, is_default(optional)
    # }
    addr_in = data.get('address') or {}
    if not isinstance(addr_in, dict):
        addr_in = {}

    line1 = (addr_in.get('line1') or '').strip()
    line2 = (addr_in.get('line2') or '').strip()
    city  = (addr_in.get('city') or '').strip()
    state = (addr_in.get('state') or '').strip()
    pincode = (addr_in.get('pincode') or '').strip()

    # lat/lng may be None
    latitude = addr_in.get('latitude')
    longitude = addr_in.get('longitude')

    if not line1 or not pincode:
        return jsonify({'success': False, 'error': 'Address line1 and pincode are required'}), 400

    if len(pincode) != 6 or not pincode.isdigit():
        return jsonify({'success': False, 'error': 'Invalid pincode'}), 400

    # serviceability check
    if not is_serviceable_pincode(pincode):
        return jsonify({'success': False, 'error': 'Pincode not serviceable'}), 400

    # --- Load cart items for this user ---
    # --- Load cart items (APP can send items directly; else fallback to DB cart_items) ---
    cid = get_or_create_cart(user_id)

    items_in = data.get('items')
    items = []

    def _as_float(x, default=0.0):
        try:
            return float(x)
        except Exception:
            return default

    if isinstance(items_in, list) and len(items_in) > 0:
        # ✅ Build items from payload
        agg = {}  # product_id -> weight_kg
        for row in items_in:
            if not isinstance(row, dict):
                continue
            pid = row.get("product_id")
            wkg = row.get("weight_kg")
            try:
                pid = int(pid)
            except Exception:
                pid = 0
            wkg = _as_float(wkg, 0.0)
            if pid <= 0 or wkg <= 0:
                continue
            agg[pid] = agg.get(pid, 0.0) + wkg

        if not agg:
            return jsonify({'success': False, 'error': 'Cart is empty'}), 400

        # fetch product data
        qmarks = ",".join(["?"] * len(agg))
        prows = query(f'''
            SELECT id, price_per_kg, store_id, stock_kg, is_active
            FROM products
            WHERE id IN ({qmarks})
        ''', tuple(agg.keys()))

        pmap = {int(r["id"]): r for r in prows}

        for pid, wkg in agg.items():
            p = pmap.get(pid)
            if not p:
                return jsonify({'success': False, 'error': f'Invalid product_id: {pid}'}), 400
            items.append({
                "product_id": pid,
                "weight_kg": wkg,
                "price_per_kg": p["price_per_kg"],
                "store_id": p["store_id"],
                "stock_kg": p["stock_kg"],
                "is_active": p["is_active"],
            })

    else:
        # ✅ Fallback to website cart_items table
        items = query('''
            SELECT ci.product_id, ci.weight_kg, p.price_per_kg, p.store_id,
                p.stock_kg, p.is_active
            FROM cart_items ci
            JOIN products p ON p.id = ci.product_id
            WHERE ci.cart_id = ?
        ''', (cid,))

        if not items:
            return jsonify({'success': False, 'error': 'Cart is empty'}), 400


    # Validate items & stock
    for it in items:
        if int(it['is_active'] or 0) != 1:
            return jsonify({'success': False, 'error': 'Product inactive'}), 400
        if float(it['stock_kg'] or 0) <= 0:
            return jsonify({'success': False, 'error': 'Item sold out'}), 409
        if float(it['weight_kg'] or 0) > float(it['stock_kg'] or 0):
            return jsonify({'success': False, 'error': 'Insufficient stock'}), 409

    # IMPORTANT: single-store checkout (same assumption as website)
    store_id = int(items[0]['store_id'])

    # --- Save/Upsert address into addresses table (Option A) ---
    # We try to insert a new address row and mark default.
    now = datetime.utcnow().isoformat()

    # Tip
    try:
        tip_amount = float(tip_amount or 0)
    except Exception:
        tip_amount = 0.0
    if tip_amount < 0:
        tip_amount = 0.0
    if tip_amount > 10000:
        tip_amount = 10000.0
    tip_amount = round(tip_amount, 2)

    # --- Calculate distance & delivery fee ---
    store_row = query("SELECT * FROM stores WHERE id=?", (store_id,))
    store = store_row[0] if store_row else None

    km = None
    if store and latitude is not None and longitude is not None:
        try:
            store_lat = store['latitude'] if 'latitude' in store.keys() else None
            store_lng = store['longitude'] if 'longitude' in store.keys() else None
            if store_lat is not None and store_lng is not None:
                km = haversine_km(store_lat, store_lng, float(latitude), float(longitude))
        except Exception:
            km = None

    if km is not None and km > MAX_DELIVERY_KM:
        return jsonify({'success': False, 'error': f'Delivery distance ({km:.1f} km) exceeds limit'}), 400

    # Delivery fee slab (same logic pattern as website)
    if km is None:
        delivery_fee = BASE_DELIVERY_FEE_INR
    else:
        extra = None
        for low, high, fee in DELIVERY_SURCHARGE_SLABS:
            last_high = DELIVERY_SURCHARGE_SLABS[-1][1]
            if (km >= low) and (km < high or high == last_high):
                extra = fee
                break
        if extra is None:
            return jsonify({'success': False, 'error': 'Delivery not available for this distance'}), 400
        delivery_fee = BASE_DELIVERY_FEE_INR + extra

    items_total = sum(float(it['weight_kg']) * float(it['price_per_kg']) for it in items)
    total_payable = items_total + float(delivery_fee) + float(tip_amount)

    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")

        # 1) Insert address into addresses table
        # Make this the default address (simple approach)
        try:
            cur.execute("UPDATE addresses SET is_default=0 WHERE user_id=?", (user_id,))
        except Exception:
            # if column doesn't exist, ignore
            pass

        # Insert (try with common columns)
        # Your website uses: line1,line2,city,state,pincode,latitude,longitude,is_default,created_at
        addr_id = None
        try:
            cur.execute('''
                INSERT INTO addresses (user_id, line1, line2, city, state, pincode, latitude, longitude, is_default, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)
            ''', (user_id, line1, line2, city, state, pincode, latitude, longitude, 1, now))
            addr_id = cur.lastrowid
        except Exception:
            # fallback if created_at not present
            cur.execute('''
                INSERT INTO addresses (user_id, line1, line2, city, state, pincode, latitude, longitude, is_default)
                VALUES (?,?,?,?,?,?,?,?,?)
            ''', (user_id, line1, line2, city, state, pincode, latitude, longitude, 1))
            addr_id = cur.lastrowid

        # 2) Create order
        cur.execute('''
            INSERT INTO orders (
                user_id, store_id, total_amount,
                status, payment_status, created_at,
                delivery_fee, distance_km, tip_amount
            )
            VALUES (?,?,?,?,?,?,?,?,?)
        ''', (
            user_id, store_id, items_total,
            'PLACED', 'PENDING', now,
            float(delivery_fee), float(km) if km is not None else None, tip_amount
        ))
        order_id = cur.lastrowid

        # 3) Order items + stock update
        for it in items:
            pid = int(it['product_id'])
            need = float(it['weight_kg'])
            price = float(it['price_per_kg'])
            line_total = need * price

            cur.execute('''
                INSERT INTO order_items (
                    order_id, product_id, weight_kg,
                    unit_price_per_kg, line_total
                )
                VALUES (?,?,?,?,?)
            ''', (order_id, pid, need, price, line_total))

            cur.execute("UPDATE products SET stock_kg = stock_kg - ? WHERE id=?", (need, pid))

        # 4) Transaction
        cur.execute('''
            INSERT INTO transactions (order_id, amount, payment_method, status, created_at)
            VALUES (?,?,?,?,?)
        ''', (order_id, float(total_payable), payment_method, 'PENDING', now))

        # 5) Order address snapshot
        cur.execute('''
            INSERT INTO order_addresses (
                order_id, line1, line2, city, state,
                pincode, latitude, longitude, created_at
            )
            VALUES (?,?,?,?,?,?,?,?,?)
        ''', (
            order_id,
            line1, line2, city, state, pincode,
            latitude, longitude, now
        ))

        # 6) Event
        cur.execute(
            "INSERT INTO order_events (order_id, status, note, created_at) VALUES (?,?,?,?)",
            (order_id, 'PLACED', '', now)
        )

        # 7) Clear cart
        cur.execute("DELETE FROM cart_items WHERE cart_id=?", (cid,))

        conn.commit()

    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        conn.close()

    return jsonify({
        'success': True,
        'order_id': order_id,
        'total_payable': round(float(total_payable), 2),
        'delivery_fee': float(delivery_fee),
        'distance_km': float(km) if km is not None else None,
        'message': 'Order placed successfully'
    }), 201








print("\n=== ROUTES LOADED ===")
print(app.url_map)
print("=====================\n")



if __name__ == '__main__':
    app.run(host="0.0.0.0",
        port=5000,
        debug=True,
        use_reloader=False)
