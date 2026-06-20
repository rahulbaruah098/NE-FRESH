"""External delivery routes for NE FRESH.

Adds provider-ready external local delivery and third-party shipping dashboards
without changing existing in-house delivery-boy routes.
"""

from app_core import *
from services.delivery_integrations.base import build_external_delivery_payload
from services.delivery_integrations.shiprocket_service import create_shiprocket_booking
from services.delivery_integrations.hyperlocal_service import create_hyperlocal_booking


EXTERNAL_ORDER_MODES = [
    DELIVERY_MODE_EXTERNAL_LOCAL,
    DELIVERY_MODE_THIRD_PARTY,
]

EXTERNAL_DELIVERED_STATUSES = {
    "DELIVERED",
    "DELIVERY_DELIVERED",
    "SHIPMENT_DELIVERED",
}

EXTERNAL_FAILED_STATUSES = {
    "FAILED",
    "DELIVERY_FAILED",
    "CANCELLED",
    "CANCELED",
    "RTO",
    "RETURNED",
}


def _external_now():
    return datetime.utcnow().isoformat()


def _external_safe_text(value, fallback=""):
    value = "" if value is None else str(value).strip()
    return value if value else fallback


def _external_object_id(value):
    try:
        return ObjectId(str(value))
    except Exception:
        return None


def _get_external_order_or_redirect(oid):
    oid_obj = _external_object_id(oid)
    if not oid_obj:
        return None, None

    order = mongo.orders.find_one({"_id": oid_obj})
    if not order:
        return oid_obj, None

    if (order.get("active_delivery_mode") or "") not in EXTERNAL_ORDER_MODES:
        return oid_obj, None

    return oid_obj, order


def _current_store_doc():
    user = current_user() or {}
    if not user:
        return None, None

    store = mongo.stores.find_one({"user_id": user.get("id")})

    if not store:
        try:
            store = mongo.stores.find_one({"user_id": str(user.get("_id"))})
        except Exception:
            store = None

    return user, store


def _external_store_filter(store):
    store = store or {}
    store_id = store.get("_id")
    store_id_str = str(store_id) if store_id else ""

    return {
        "$or": [
            {"store_id": store_id},
            {"store_id": store_id_str},
            {"store_name": store.get("store_name")},
            {"store_name": store.get("name")},
        ]
    }


def _decorate_external_order(order):
    row = dict(order or {})
    row["id"] = str(row.get("_id") or row.get("id") or "")
    row["short_id"] = row["id"][-6:] if row["id"] else ""
    row["active_delivery_mode"] = row.get("active_delivery_mode") or "EXTERNAL_LOCAL_DELIVERY"
    row["external_delivery_status"] = row.get("external_delivery_status") or row.get("external_delivery_booking_status") or "PENDING"
    row["external_delivery_provider"] = row.get("external_delivery_provider") or row.get("external_delivery_partner_name") or "MANUAL"
    row["external_delivery_provider_type"] = row.get("external_delivery_provider_type") or row.get("external_delivery_partner_type") or "HYPERLOCAL"
    row["external_tracking_url"] = row.get("external_tracking_url") or ""
    row["external_awb"] = row.get("external_awb") or ""

    for key in ["items_subtotal", "delivery_fee", "platform_fee", "tip_amount", "total_payable", "external_delivery_charge", "external_delivery_fee_amount"]:
        try:
            row[key] = round(float(row.get(key) or 0), 2)
        except Exception:
            row[key] = 0.0

    return row


def _load_external_order_payload(order):
    oid = order.get("_id") or order.get("id")
    address = mongo.order_addresses.find_one({
        "$or": [
            {"order_id": oid},
            {"order_id": str(oid)},
        ]
    }) or {}

    items = list(mongo.order_items.find({
        "$or": [
            {"order_id": oid},
            {"order_id": str(oid)},
        ]
    }))

    settings = get_external_delivery_settings()
    return build_external_delivery_payload(order, address=address, items=items, settings=settings)


def _append_external_history(oid_obj, status, note="", provider_payload=None):
    entry = {
        "status": _external_safe_text(status).upper(),
        "note": note or "",
        "at": _external_now(),
        "payload": provider_payload or {},
    }

    mongo.orders.update_one(
        {"_id": oid_obj},
        {"$push": {"external_status_history": entry}}
    )

    return entry


def _apply_external_status_to_order(oid_obj, order, status, note="", provider_payload=None):
    status = _external_safe_text(status).upper()
    now = _external_now()

    update_data = {
        "external_delivery_status": status,
        "external_delivery_last_update_at": now,
        "external_delivery_last_note": note or "",
        "updated_at": now,
    }

    if status in EXTERNAL_DELIVERED_STATUSES:
        update_data.update({
            "status": "DELIVERED",
            "delivered_at": now,
            "payment_collection_status": (
                "PAID"
                if (order.get("payment_method") or "").upper() == "ONLINE"
                else order.get("payment_collection_status") or "PENDING_SETTLEMENT"
            ),
            "store_payout_status": order.get("store_payout_status") or "PENDING_AFTER_DELIVERY",
            "order_settlement_status": order.get("order_settlement_status") or "STORE_PAYOUT_PENDING",
        })
    elif status in EXTERNAL_FAILED_STATUSES:
        update_data.update({
            "delivery_failed_at": now,
            "delivery_failed_reason": note or status,
        })

    mongo.orders.update_one(
        {"_id": oid_obj},
        {
            "$set": update_data,
            "$push": {
                "external_status_history": {
                    "status": status,
                    "note": note or "",
                    "at": now,
                    "payload": provider_payload or {},
                }
            }
        }
    )

    try:
        add_order_event(oid_obj, status, note or f"External delivery status updated to {status}.", current_user())
    except Exception:
        pass


@app.route("/admin/external-delivery-settings", methods=["GET", "POST"], endpoint="admin_external_delivery_settings")
@login_required(role="admin")
def admin_external_delivery_settings():
    admin_user = current_user() or {}

    if request.method == "POST":
        existing = get_external_delivery_settings()
        now = _external_now()

        def _bool(name, default=False):
            return str(request.form.get(name, "1" if default else "0")).strip().lower() in ["1", "true", "yes", "on"]

        def _money(name, default):
            try:
                return round(float(request.form.get(name) or default), 2)
            except Exception:
                return default

        update_data = {
            "key": EXTERNAL_DELIVERY_SETTINGS_KEY,
            "shiprocket_enabled": _bool("shiprocket_enabled", existing.get("shiprocket_enabled", False)),
            "shiprocket_email": _external_safe_text(request.form.get("shiprocket_email")),
            "shiprocket_pickup_location": _external_safe_text(request.form.get("shiprocket_pickup_location")),
            "shiprocket_channel_id": _external_safe_text(request.form.get("shiprocket_channel_id")),
            "shiprocket_webhook_token": _external_safe_text(request.form.get("shiprocket_webhook_token")),

            "hyperlocal_enabled": _bool("hyperlocal_enabled", existing.get("hyperlocal_enabled", False)),
            "hyperlocal_provider": _external_safe_text(request.form.get("hyperlocal_provider"), "MANUAL_HYPERLOCAL").upper(),
            "hyperlocal_api_base_url": _external_safe_text(request.form.get("hyperlocal_api_base_url")),
            "hyperlocal_webhook_token": _external_safe_text(request.form.get("hyperlocal_webhook_token")),
            "manual_external_enabled": _bool("manual_external_enabled", True),

            "external_local_base_fee": _money("external_local_base_fee", 40.0),
            "external_local_per_km_fee": _money("external_local_per_km_fee", 8.0),
            "external_local_min_fee": _money("external_local_min_fee", 40.0),
            "external_local_max_distance_km": _money("external_local_max_distance_km", 25.0),
            "third_party_base_fee": _money("third_party_base_fee", 65.0),
            "third_party_per_km_fee": _money("third_party_per_km_fee", 0.0),
            "third_party_min_fee": _money("third_party_min_fee", 65.0),
            "third_party_max_distance_km": _money("third_party_max_distance_km", 9999.0),

            "default_package_weight_kg": _money("default_package_weight_kg", 1.0),
            "default_package_length_cm": _money("default_package_length_cm", 10.0),
            "default_package_breadth_cm": _money("default_package_breadth_cm", 10.0),
            "default_package_height_cm": _money("default_package_height_cm", 10.0),

            "updated_at": now,
            "updated_by": str(admin_user.get("_id") or admin_user.get("id") or ""),
            "updated_by_name": admin_user.get("name") or admin_user.get("email") or "Admin",
        }

        # Do not overwrite saved secrets if fields are left blank.
        shiprocket_password = _external_safe_text(request.form.get("shiprocket_password"))
        if shiprocket_password:
            update_data["shiprocket_password"] = shiprocket_password

        hyperlocal_api_key = _external_safe_text(request.form.get("hyperlocal_api_key"))
        if hyperlocal_api_key:
            update_data["hyperlocal_api_key"] = hyperlocal_api_key

        mongo.platform_settings.update_one(
            {"key": EXTERNAL_DELIVERY_SETTINGS_KEY},
            {"$set": update_data, "$setOnInsert": {"created_at": now}},
            upsert=True,
        )

        flash("External delivery provider settings saved successfully.", "success")
        return redirect(url_for("admin_external_delivery_settings"))

    return render_template(
        "admin_external_delivery_settings.html",
        user=admin_user,
        settings=get_external_delivery_settings(),
        delivery_mode_settings=get_delivery_mode_settings(),
        active_group="delivery",
        active_page="external_delivery_settings",
    )


@app.route("/admin/external-delivery-orders", methods=["GET"], endpoint="admin_external_delivery_orders")
@login_required(role="admin")
def admin_external_delivery_orders():
    status_filter = _external_safe_text(request.args.get("status")).upper()
    mode_filter = _external_safe_text(request.args.get("mode")).upper()
    q = _external_safe_text(request.args.get("q")).lower()

    query = {"active_delivery_mode": {"$in": EXTERNAL_ORDER_MODES}}
    if mode_filter in EXTERNAL_ORDER_MODES:
        query["active_delivery_mode"] = mode_filter
    if status_filter:
        query["external_delivery_status"] = status_filter

    docs = list(mongo.orders.find(query).sort("created_at", -1).limit(300))
    orders = []
    for doc in docs:
        row = _decorate_external_order(doc)
        if q:
            haystack = " ".join([
                row.get("id", ""), row.get("customer_name", ""), row.get("customer_phone", ""),
                row.get("store_name", ""), row.get("external_delivery_provider", ""), row.get("external_awb", ""),
            ]).lower()
            if q not in haystack:
                continue
        orders.append(row)

    metrics = {
        "total": len(orders),
        "pending": sum(1 for o in orders if "PENDING" in (o.get("external_delivery_status") or "")),
        "booked": sum(1 for o in orders if "BOOK" in (o.get("external_delivery_status") or "")),
        "delivered": sum(1 for o in orders if (o.get("external_delivery_status") or "").upper() in EXTERNAL_DELIVERED_STATUSES),
    }

    return render_template(
        "admin_external_delivery_orders.html",
        user=current_user(),
        orders=orders,
        metrics=metrics,
        status_filter=status_filter,
        mode_filter=mode_filter,
        q=q,
        active_group="delivery",
        active_page="external_delivery_orders",
    )


@app.route("/admin/external-delivery/orders/<oid>/book", methods=["POST"], endpoint="admin_external_delivery_book_order")
@login_required(role="admin")
def admin_external_delivery_book_order(oid):
    oid_obj, order = _get_external_order_or_redirect(oid)
    if not oid_obj or not order:
        flash("External delivery order not found.", "danger")
        return redirect(url_for("admin_external_delivery_orders"))

    provider = _external_safe_text(
        request.form.get("provider")
        or order.get("external_delivery_provider")
        or ("SHIPROCKET" if order.get("active_delivery_mode") == DELIVERY_MODE_THIRD_PARTY else "MANUAL_HYPERLOCAL")
    ).upper()

    payload = _load_external_order_payload(order)
    payload["provider"] = provider

    settings = get_external_delivery_settings()

    if provider == "SHIPROCKET" or order.get("active_delivery_mode") == DELIVERY_MODE_THIRD_PARTY:
        result = create_shiprocket_booking(payload, settings)
    else:
        result = create_hyperlocal_booking(payload, settings)

    now = _external_now()
    update_data = {
        "external_delivery_provider": provider,
        "external_delivery_partner_name": provider,
        "external_delivery_payload": payload,
        "external_delivery_response": result.get("raw_response") or result,
        "external_booking_attempted_at": now,
        "updated_at": now,
    }

    if result.get("ok"):
        update_data.update({
            "external_delivery_status": result.get("status") or "BOOKED",
            "external_delivery_booking_status": "BOOKED",
            "external_order_id": result.get("external_order_id") or order.get("external_order_id") or "",
            "external_shipment_id": result.get("external_shipment_id") or order.get("external_shipment_id") or "",
            "external_awb": result.get("external_awb") or order.get("external_awb") or "",
            "external_tracking_url": result.get("external_tracking_url") or order.get("external_tracking_url") or "",
            "external_label_url": result.get("external_label_url") or order.get("external_label_url") or "",
            "external_manifest_url": result.get("external_manifest_url") or order.get("external_manifest_url") or "",
        })
        flash(result.get("message") or "External delivery booking saved.", "success")
    else:
        update_data.update({
            "external_delivery_status": result.get("status") or "BOOKING_FAILED",
            "external_delivery_booking_status": "BOOKING_FAILED",
            "external_booking_error": result.get("message") or "External booking failed.",
        })
        flash(result.get("message") or "External delivery booking failed.", "danger")

    mongo.orders.update_one(
        {"_id": oid_obj},
        {
            "$set": update_data,
            "$push": {
                "external_status_history": {
                    "status": update_data.get("external_delivery_status"),
                    "note": result.get("message") or "External delivery booking attempted.",
                    "at": now,
                    "payload": result,
                }
            }
        }
    )

    return redirect(request.referrer or url_for("admin_external_delivery_orders"))


@app.route("/admin/external-delivery/orders/<oid>/status", methods=["POST"], endpoint="admin_external_delivery_update_status")
@login_required(role="admin")
def admin_external_delivery_update_status(oid):
    oid_obj, order = _get_external_order_or_redirect(oid)
    if not oid_obj or not order:
        flash("External delivery order not found.", "danger")
        return redirect(url_for("admin_external_delivery_orders"))

    status = _external_safe_text(request.form.get("external_delivery_status"), "PENDING").upper()
    note = _external_safe_text(request.form.get("external_delivery_note"))

    _apply_external_status_to_order(oid_obj, order, status, note)
    flash("External delivery status updated.", "success")
    return redirect(request.referrer or url_for("admin_external_delivery_orders"))


@app.route("/store/external-delivery", methods=["GET"], endpoint="store_external_delivery")
@login_required(role="store")
def store_external_delivery():
    user, store = _current_store_doc()
    if not store:
        flash("Store not found.", "danger")
        return redirect(url_for("store_dashboard"))

    docs = list(mongo.orders.find({
        "$and": [
            {"active_delivery_mode": {"$in": EXTERNAL_ORDER_MODES}},
            _external_store_filter(store),
        ]
    }).sort("created_at", -1).limit(200))

    orders = [_decorate_external_order(doc) for doc in docs]

    return render_template(
        "store_external_delivery.html",
        user=user,
        store=store,
        orders=orders,
        active_page="external_delivery",
    )


@app.route("/store/external-delivery/orders/<oid>/ready", methods=["POST"], endpoint="store_external_delivery_ready")
@login_required(role="store")
def store_external_delivery_ready(oid):
    user, store = _current_store_doc()
    if not store:
        flash("Store not found.", "danger")
        return redirect(url_for("store_dashboard"))

    oid_obj = _external_object_id(oid)
    if not oid_obj:
        flash("Invalid order.", "danger")
        return redirect(url_for("store_external_delivery"))

    order = mongo.orders.find_one({
        "_id": oid_obj,
        "active_delivery_mode": {"$in": EXTERNAL_ORDER_MODES},
        **_external_store_filter(store),
    })

    if not order:
        flash("External delivery order not found for your store.", "danger")
        return redirect(url_for("store_external_delivery"))

    now = _external_now()
    mongo.orders.update_one(
        {"_id": oid_obj},
        {
            "$set": {
                "status": "SHIPMENT_READY",
                "shipment_ready_at": now,
                "ready_for_pickup_at": now,
                "external_delivery_status": "READY_FOR_EXTERNAL_BOOKING",
                "external_delivery_booking_status": "READY_FOR_BOOKING",
                "updated_at": now,
            },
            "$push": {
                "external_status_history": {
                    "status": "READY_FOR_EXTERNAL_BOOKING",
                    "note": "Store marked order ready for external delivery booking.",
                    "at": now,
                    "payload": {},
                }
            }
        }
    )

    try:
        add_order_event(oid_obj, "READY_FOR_EXTERNAL_BOOKING", "Store marked order ready for external delivery booking.", user)
    except Exception:
        pass

    flash("Order marked ready for external delivery booking.", "success")
    return redirect(url_for("store_external_delivery"))


@app.route("/api/external-delivery/webhook/<provider>", methods=["POST"], endpoint="api_external_delivery_webhook")
def api_external_delivery_webhook(provider):
    provider = _external_safe_text(provider).upper()
    payload = request.get_json(silent=True) or request.form.to_dict() or {}
    settings = get_external_delivery_settings()

    token = request.headers.get("x-api-key") or request.headers.get("X-API-Key") or request.args.get("token") or ""
    expected_tokens = [
        settings.get("shiprocket_webhook_token") if provider == "SHIPROCKET" else "",
        settings.get("hyperlocal_webhook_token") if provider != "SHIPROCKET" else "",
    ]
    expected_tokens = [t for t in expected_tokens if t]

    if expected_tokens and token not in expected_tokens:
        return jsonify({"ok": False, "error": "Invalid webhook token."}), 403

    external_order_id = _external_safe_text(payload.get("external_order_id") or payload.get("order_id"))
    external_shipment_id = _external_safe_text(payload.get("external_shipment_id") or payload.get("shipment_id"))
    external_awb = _external_safe_text(payload.get("awb") or payload.get("awb_code"))
    status = _external_safe_text(payload.get("status") or payload.get("current_status") or payload.get("shipment_status"), "UPDATED").upper()
    note = _external_safe_text(payload.get("message") or payload.get("note"), f"{provider} webhook update")

    clauses = []
    if external_order_id:
        clauses.append({"external_order_id": external_order_id})
    if external_shipment_id:
        clauses.append({"external_shipment_id": external_shipment_id})
    if external_awb:
        clauses.append({"external_awb": external_awb})

    if not clauses:
        return jsonify({"ok": False, "error": "No external order/shipment/AWB id supplied."}), 400

    order = mongo.orders.find_one({"$or": clauses})
    if not order:
        return jsonify({"ok": False, "error": "Matching order not found."}), 404

    oid_obj = order.get("_id")
    _apply_external_status_to_order(oid_obj, order, status, note, provider_payload=payload)

    return jsonify({"ok": True, "order_id": str(oid_obj), "status": status})
