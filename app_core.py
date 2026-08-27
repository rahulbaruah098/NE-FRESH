import os
import io
import re
import math
from io import BytesIO
import secrets
from datetime import datetime, timedelta, timezone
from random import randint
import csv, zipfile, json
from datetime import date,datetime
import time
import html
from flask import render_template, request,Response, redirect, url_for, session, flash, jsonify, send_file, abort
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from functools import wraps
from flask import make_response
from collections import defaultdict

# MongoDB imports
from bson import ObjectId
from pymongo.errors import DuplicateKeyError

# ---- Extracted infrastructure (Step 3)
from extensions import mongo
from logging_config import is_debug_logging_enabled, log_debug, log_warning
from security import (
    CSRF_EXEMPT_PATH_PREFIXES,
    _get_csrf_token,
    _inject_csrf_helpers,
    _protect_html_form_posts,
    add_no_cache_headers,
)
from template_context import (
    FOOTER_LINKS,
    configure_template_context,
    inject_cart_count,
    inject_footer_links,
    inject_globals,
    inject_site_brand_settings,
    register_template_context_processors,
)
from uploads import ALLOWED_EXTS, allowed_file

# ---- Extracted low-risk domain helpers/services (Step 4)
from helpers.formatting import (
    _clean_pin,
    _clean_state,
    _norm_role,
    _norm_status,
    is_assam_state,
    normalize_phone,
    order_status_label,
)
from helpers.identifiers import _order_identity_values, _store_identity_values
from services.product_pricing import _calculate_product_pricing_from_form, _safe_float
from services.product_units import (
    UNIT_OPTIONS,
    UNIT_TYPE_LABELS,
    build_unit_product_update_from_form,
    cart_item_quantity,
    hydrate_product_unit_fields,
    normalize_quantity_by_unit,
    normalize_unit_label,
    normalize_unit_type,
    product_mrp_per_unit,
    product_original_price_per_unit,
    product_price_per_unit,
    product_stock_quantity,
    product_unit_label,
    product_unit_type,
    unit_quantity_rules,
)
from services.store_notifications import (
    _create_store_notification,
    _hydrate_store_notification,
    _store_id_values,
    _store_notification_stats,
    _sync_store_order_notifications,
)
from services.product_bundles import (
    BUNDLE_DISCOUNT_TYPES,
    _bundle_money_float,
    _bundle_object_id_string,
    _bundle_quantity_float,
    build_bundle_cart_snapshot,
    build_bundle_item_snapshots,
    build_live_product_bundle,
    build_product_bundle_document,
    calculate_bundle_pricing,
    calculate_bundle_stock,
    is_product_bundle_customer_available,
    normalize_bundle_discount_type,
    normalize_bundle_product_ids,
    notify_store_bundle_restock_needed,
    validate_product_bundle_for_cart,
)
from services.store_categories import (
    DEFAULT_STORE_CATEGORIES,
    _category_slug,
    _ensure_store_categories,
    _get_category_product_count,
    _get_store_categories,
    _get_store_category_by_id,
    _get_store_category_by_name,
)
from services.store_catalog import _get_store_products
from services.store_profile import _build_store_profile_context
from helpers.numbers import (
    _delivery_float_or_default,
    _delivery_float_or_none,
    _delivery_int,
    _get_float_or_none,
)
from services.delivery_operations import (
    BASE_DELIVERY_FEE_INR,
    DELIVERY_ACCEPT_RADIUS_KM,
    DELIVERY_ACTIONABLE_STATUSES,
    DELIVERY_ASSIGNED_ACTIVE_STATUSES,
    DELIVERY_MODE,
    DELIVERY_PROGRESS_STATUSES,
    DELIVERY_REASSIGN_BLOCKED_STATUSES,
    DELIVERY_STORE_ASSIGNABLE_STATUSES,
    DELIVERY_SURCHARGE_SLABS,
    MAX_DELIVERY_KM,
    _delivery_actor_snapshot,
    _delivery_now,
    _delivery_user_id,
    _driver_distance_to_store_km,
    _get_delivery_availability,
    _hydrate_delivery_order,
    _is_delivery_active,
    add_order_event,
    assign_delivery_partner_to_order,
    calculate_delivery_fee_by_distance,
    clear_delivery_assignment,
    get_delivery_partner_snapshot,
    get_online_delivery_people_near_store,
    haversine_km,
)
from services.order_lifecycle import CANCELLABLE_STATUSES, is_cancellable
from services.order_tracking import get_order_full
from services.email_sender import send_email as _shared_send_email

# ---- Extracted protected finance services (Step 6)
from services.platform_fees import (
    DEFAULT_PLATFORM_FEE_SETTINGS,
    PLATFORM_FEE_SETTINGS_KEY,
    _platform_fee_safe_float,
    build_order_money_breakdown,
    calculate_platform_fee,
    get_platform_fee_settings,
)
from services.finance_reconciliation import (
    COD_COLLECTION_DELIVERY_BOY,
    COD_COLLECTION_EXTERNAL_PARTNER,
    COD_COLLECTION_STORE,
    FINANCE_PAYMENT_PENDING,
    FINANCE_PAYMENT_RECONCILED,
    VALID_COD_COLLECTION_METHODS,
    finance_money,
    finance_order_has_unresolved_refund,
    finance_reconciliation_snapshot,
)
from services.store_finance_adjustments import (
    FINANCE_STORE_ADJUSTMENT_APPLIED,
    FINANCE_STORE_ADJUSTMENT_OPEN,
    FINANCE_STORE_ADJUSTMENT_PARTIAL,
    finance_apply_store_adjustments,
    finance_create_store_adjustment,
    finance_rollback_store_adjustments,
    finance_store_id_values,
    finance_store_outstanding_adjustment_total,
)
from services.delivery_monthly_settlement import (
    DELIVERY_MONTHLY_BATCH_STATUS_PAID,
    DELIVERY_MONTHLY_STATUS_ACCRUED,
    DELIVERY_MONTHLY_STATUS_PAID,
    DELIVERY_MONTHLY_STATUS_PENDING_DELIVERY,
    DELIVERY_PAYOUT_MODEL_MONTHLY_V1,
    DELIVERY_PAYOUT_MODEL_NOT_REQUIRED,
    _DELIVERY_SETTLEMENT_IST,
    delivery_monthly_current_period,
    delivery_monthly_payment_is_reconciled,
    delivery_monthly_period_from_utc,
    delivery_monthly_period_is_closed,
    delivery_monthly_period_label,
    delivery_order_uses_monthly_payout,
    delivery_partner_id_values,
)

from app_factory import get_base_app

app = get_base_app()

log_debug("[RUNNING]", __file__)



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
# Template context processors were extracted to template_context.py in Step 3.
# They are configured and registered near the end of this module after the
# legacy business providers they depend on have been defined.

# ----------------------
# ----------------------
# DELIVERY CONFIG
# ----------------------

# Assam-wide delivery enabled:
# no fixed pincode list and no max-distance blocking.
# Delivery fee is calculated by distance slabs.




# ----------------------
# DELIVERY PARTNER LIVE MODE
# ----------------------
# Delivery boys should see only store shipment-ready, unassigned orders.
# New status: SHIPMENT_READY
# Legacy supported status: READY_FOR_PICKUP

# Active orders already assigned to a delivery boy.

# Only drivers within this radius from the store pickup point can accept.
# If store coordinates are missing, distance check is skipped.









# =========================================================
# PRODUCT DISCOUNT HELPERS
# =========================================================







# =========================================================
# PRODUCT BUNDLE HELPERS
# =========================================================













































# =========================================================
# PRODUCT UNIT HELPERS
# Supports kg, gram, liter, ml, packet, piece, bottle, box, etc.
# Single source of truth:
# quantity, unit_type, unit_label, price_per_unit, stock_quantity
# =========================================================


















































    # =========================================================
# PLATFORM FEE HELPERS
# Admin/platform owner earning from every order.
# Default is disabled, so existing checkout will not change
# until admin enables it from platform fee settings.
# =========================================================












# =========================================================
# DELIVERY PARTNER MONTHLY PAYOUT MODEL
# =========================================================
# Customer/order money always remains business money. A delivery partner's
# compensation is never deducted from Cash/UPI collections in MONTHLY_V1.
# Delivery fee + tip accrue separately and are paid once per closed calendar month.









































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

    # Customer payment methods. Backend value COD is retained for compatibility.
    # Customer-facing meaning is Pay on Delivery: payment is due when the order
    # arrives and can be collected as cash or, when configured, official UPI.
    "delivery_payment_methods": DELIVERY_PAYMENT_ONLINE_AND_COD,
    "allow_online_payment": True,
    "allow_cod_payment": True,
    "cod_collection_method": COD_COLLECTION_DELIVERY_BOY,

    # Optional official UPI destination for COD / Pay-on-Delivery orders.
    # This is a public payment address, not a secret. Delivery partners never
    # receive/store any UPI credentials.
    "pay_on_delivery_upi_enabled": False,
    "pay_on_delivery_upi_id": "",
    "pay_on_delivery_upi_name": "NE LOCALS",

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

    # Backend keeps COD internally for compatibility. Customer-facing meaning is
    # Pay on Delivery. In-house delivery can record the final collection channel as
    # CASH or official UPI. External Local continues to use Store/NE FRESH collection;
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
            return "Pay on Delivery - partner collection"
        if cod_method == COD_COLLECTION_STORE:
            return "Pay on Delivery - store/NE FRESH collection"
        return "Pay on Delivery - delivery boy collection"

    if cod_method == COD_COLLECTION_EXTERNAL_PARTNER:
        return "Online + Pay on Delivery - partner collection"
    if cod_method == COD_COLLECTION_STORE:
        return "Online + Pay on Delivery - store/NE FRESH collection"
    return "Online + Pay on Delivery - delivery boy collection"


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


















def _ensure_contact_messages_status_column():
    # MongoDB does not need table/column migration.
    return

# ======================
# ASSAM-WIDE DELIVERY
# ======================

def _seed_pincodes_if_empty():
    # Backward-compatible no-op. Old fixed pincode seeding is disabled.
    # Database initialization is now explicit via scripts/init_db.py.
    return



















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

# Database indexes and seed operations are intentionally NOT executed during
# module import. Run `python scripts/init_db.py` explicitly during initial
# setup/deployment so multi-worker production servers do not repeat mutations.


# ----------------------
# MISC UTILS
# ----------------------
# Upload configuration and allowed_file() were extracted to uploads.py.



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

# Optional admin database bootstrap was extracted to database_init.py.

def send_sms(phone: str, message: str) -> bool:
    log_debug(f"[DEV SMS] to={phone} :: {message}")
    return True

# Admin seeding is deployment/setup work, not application import work.
# Run `python scripts/init_db.py` explicitly when a fresh environment needs it.

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











def _store_order_money(value, default=0.0):
    try:
        if value is None or str(value).strip() == "":
            return float(default)
        return float(value)
    except Exception:
        return float(default)


def _store_order_total_payable(order_doc):
    """
    Returns the customer payable amount for a store order without double-counting.

    New orders store total_payable as the final checkout amount. Older rows may
    only have items_subtotal / total_amount plus delivery/platform/tip fields,
    so this helper falls back safely. This keeps dashboard GMV/payment values
    accurate for in-house, external-local and courier delivery modes.
    """
    if not order_doc:
        return 0.0

    if order_doc.get("total_payable") is not None:
        return round(_store_order_money(order_doc.get("total_payable")), 2)

    delivery_fee = _store_order_money(
        order_doc.get("delivery_fee_amount")
        if order_doc.get("delivery_fee_amount") is not None
        else order_doc.get("delivery_fee")
    )
    platform_fee = _store_order_money(order_doc.get("platform_fee"))
    tip_amount = _store_order_money(
        order_doc.get("tip_amount")
        if order_doc.get("tip_amount") is not None
        else order_doc.get("delivery_tip_amount")
    )

    if order_doc.get("items_subtotal") is not None:
        return round(_store_order_money(order_doc.get("items_subtotal")) + delivery_fee + platform_fee + tip_amount, 2)

    if order_doc.get("store_earning") is not None:
        return round(_store_order_money(order_doc.get("store_earning")) + delivery_fee + platform_fee + tip_amount, 2)

    # Current NE FRESH order rows use total_amount as final customer payable.
    # Do not add delivery/tip again here, otherwise dashboard values get inflated.
    return round(_store_order_money(order_doc.get("total_amount")), 2)


def _store_order_items_subtotal(order_doc):
    if not order_doc:
        return 0.0

    if order_doc.get("items_subtotal") is not None:
        return round(_store_order_money(order_doc.get("items_subtotal")), 2)

    if order_doc.get("store_earning") is not None:
        return round(_store_order_money(order_doc.get("store_earning")), 2)

    total_payable = _store_order_total_payable(order_doc)
    delivery_fee = _store_order_money(
        order_doc.get("delivery_fee_amount")
        if order_doc.get("delivery_fee_amount") is not None
        else order_doc.get("delivery_fee")
    )
    platform_fee = _store_order_money(order_doc.get("platform_fee"))
    tip_amount = _store_order_money(
        order_doc.get("tip_amount")
        if order_doc.get("tip_amount") is not None
        else order_doc.get("delivery_tip_amount")
    )

    return round(max(total_payable - delivery_fee - platform_fee - tip_amount, 0), 2)


def _get_store_orders(store_id):
    orders = list(
        mongo.orders.find({"store_id": {"$in": _store_identity_values(store_id)}}).sort("created_at", -1)
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

        row["items_subtotal"] = _store_order_items_subtotal(o)
        row["delivery_fee"] = _store_order_money(o.get("delivery_fee_amount") if o.get("delivery_fee_amount") is not None else o.get("delivery_fee"))
        row["platform_fee"] = _store_order_money(o.get("platform_fee"))
        row["tip_amount"] = _store_order_money(o.get("tip_amount") if o.get("tip_amount") is not None else o.get("delivery_tip_amount"))
        row["total_payable"] = _store_order_total_payable(o)
        row["total_amount"] = row["total_payable"]

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
    paid_online_transactions = []
    paid_online_order_ids = set()

    if delivered_order_ids:
        paid_transactions = list(mongo.transactions.find({
            "order_id": {"$in": _order_identity_values(delivered_order_ids)},
            "status": {"$in": ["PAID", "ONLINE_PAID", "SUCCESS", "COMPLETED", "CAPTURED"]}
        }))

        cod_methods = {"COD", "CASH_ON_DELIVERY", "COD_RIDER_COLLECTION"}
        paid_online_transactions = [
            t for t in paid_transactions
            if str(t.get("payment_method") or t.get("method") or "").strip().upper() not in cod_methods
        ]
        paid_online_order_ids = {
            str(t.get("order_id"))
            for t in paid_online_transactions
            if t.get("order_id")
        }

    # Delivered Customer GMV is delivery-mode agnostic:
    # it sums the final customer payable captured on delivered orders. That
    # amount already includes the correct in-house / external / courier delivery
    # fee, platform fee and tip snapshot from checkout. Cancelled and open orders
    # are intentionally excluded.
    gmv_total = sum(_store_order_total_payable(o) for o in delivered_orders)
    delivered_items_subtotal = sum(_store_order_items_subtotal(o) for o in delivered_orders)
    delivered_delivery_fee_total = sum(_store_order_money(o.get("delivery_fee")) for o in delivered_orders)
    delivered_platform_fee_total = sum(_store_order_money(o.get("platform_fee")) for o in delivered_orders)
    delivered_tip_total = sum(_store_order_money(o.get("tip_amount")) for o in delivered_orders)

    paid_online_total = sum(_store_order_money(t.get("amount")) for t in paid_online_transactions)

    cod_methods = {"COD", "CASH_ON_DELIVERY", "COD_RIDER_COLLECTION"}
    cod_collected_statuses = {
        "COD_COLLECTED",
        "COLLECTED",
        "PAID",
        "RECEIVED",
        "COLLECTED_BY_RIDER",
        "COD_COLLECTED_BY_RIDER",
        "COD_UPI_RECORDED",
    }

    cod_collected_total = 0.0
    cod_collected_order_ids = set()

    for o in delivered_orders:
        payment_method = str(o.get("payment_method") or "").strip().upper()
        payment_status = str(o.get("payment_status") or "").strip().upper()
        collection_status = str(o.get("payment_collection_status") or "").strip().upper()
        order_id_text = str(o.get("_id"))

        is_cod_order = payment_method in cod_methods
        is_cod_collected = bool(
            is_cod_order
            and (
                payment_status in cod_collected_statuses
                or collection_status in {"COLLECTED", "PAID", "RECEIVED"}
            )
        )

        if not is_cod_collected:
            continue

        collected_amount = _store_order_money(
            o.get("cod_collected_amount"),
            _store_order_total_payable(o)
        )
        cod_collected_total += collected_amount
        cod_collected_order_ids.add(order_id_text)

    paid_total = paid_online_total + cod_collected_total

    paid_delivered_orders = len(paid_online_order_ids | cod_collected_order_ids)

    pending_payment_orders = [
        o for o in orders
        if (o.get("status") or "").upper() not in {"DELIVERED", "CANCELLED"}
        and (o.get("payment_status") or "").upper() in {"", "PENDING", "UNPAID", "COD_PENDING", "PENDING_PAYMENT"}
    ]

    pending_payment_value = sum(_store_order_total_payable(o) for o in pending_payment_orders)

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
        "delivered_customer_gmv": float(gmv_total),
        "delivered_items_subtotal": float(delivered_items_subtotal),
        "delivered_delivery_fee_total": float(delivered_delivery_fee_total),
        "delivered_platform_fee_total": float(delivered_platform_fee_total),
        "delivered_tip_total": float(delivered_tip_total),
        "paid_total": float(paid_total),
        "paid_online_total": float(paid_online_total),
        "cod_collected_total": float(cod_collected_total),
        "pending_payment_orders": len(pending_payment_orders),
        "pending_payment_value": float(pending_payment_value),
        "txn_count": len(paid_transactions),
        "paid_delivered_orders": paid_delivered_orders,
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







# =========================================================
# STORE NOTIFICATIONS
# =========================================================

















# ----------------EMAIL SETUP---------
def send_email(to_email, subject, body):
    """Compatibility wrapper around the shared SMTP email transport."""
    return _shared_send_email(to_email, subject, body)


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


# Response cache headers are registered by security.py.
















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



# Register extracted template context processors only after all legacy business
# providers are defined. This keeps current template globals identical while
# avoiding a circular import back into app_core.py.
configure_template_context(
    mongo=mongo,
    current_user=current_user,
    get_or_create_cart=get_or_create_cart,
    get_delivery_mode_settings=get_delivery_mode_settings,
    get_platform_fee_settings=get_platform_fee_settings,
    order_status_label=order_status_label,
    get_delivery_mode_ui_context=get_delivery_mode_ui_context,
)
register_template_context_processors(app)

log_debug("\n=== APP CORE READY ===")
log_debug("Shared helpers/context processors loaded; route registration is owned by app_factory.py.")
log_debug("======================\n")



# Export all shared globals/helpers, including underscore-prefixed legacy helpers,
# so split route modules can preserve original app.py logic unchanged.
__all__ = [name for name in globals() if not name.startswith('__')]
