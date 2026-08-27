"""Admin delivery management route section.

Route decorators, endpoint names, URL paths, HTTP methods and function bodies are
preserved from the Step 6 source. Shared legacy helpers are imported from
``routes.admin.shared`` during this transitional decomposition.
"""

from routes.admin.shared import *

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


@app.route('/admin/delivery-history', methods=['GET'], endpoint='admin_delivery_history')
@login_required(role='admin')
def admin_delivery_history():
    """
    Admin Delivery Boy History.

    Global rider-wise delivery history across all stores.
    This is read-only and does not affect delivery assignment/status flow.
    """
    q = (request.args.get("q") or "").strip()
    status_filter = (request.args.get("status") or "").strip().upper()
    delivery_user_filter = (request.args.get("delivery_user_id") or "").strip()
    store_filter = (request.args.get("store_id") or "").strip()
    payment_type_filter = (request.args.get("payment_type") or "").strip().upper()
    date_from = (request.args.get("from") or "").strip()
    date_to = (request.args.get("to") or "").strip()

    allowed_history_statuses = {
        "DELIVERED",
        "DELIVERY_FAILED",
        "CANCELLED",
        "READY_FOR_PICKUP",
        "ASSIGNED_TO_DELIVERY",
        "ACCEPTED_BY_DELIVERY_MAN",
        "REACHED_STORE",
        "PICKED_UP",
        "OUT_FOR_DELIVERY"
    }

    def _adh_float(value, default=0.0):
        try:
            if value is None or str(value).strip() == "":
                return float(default)

            return float(value)
        except Exception:
            return float(default)

    def _adh_safe_str(value):
        if value is None:
            return ""

        try:
            if isinstance(value, ObjectId):
                return str(value)
        except Exception:
            pass

        return str(value)

    def _adh_history_entries(order):
        entries = order.get("delivery_history") or []
        return [entry for entry in entries if isinstance(entry, dict)]

    def _adh_latest_action(order, action_name):
        matched = []

        for entry in _adh_history_entries(order):
            if entry.get("action") == action_name:
                matched.append(entry)

        return matched[-1] if matched else {}

    def _adh_latest_value(order, *keys):
        for entry in reversed(_adh_history_entries(order)):
            for key in keys:
                value = entry.get(key)
                if value not in [None, ""]:
                    return value

        return ""

    def _adh_effective_partner_id(order):
        return (
            order.get("delivery_partner_id")
            or order.get("previous_delivery_partner_id")
            or _adh_latest_value(
                order,
                "delivery_partner_id",
                "previous_delivery_partner_id",
                "old_delivery_partner_id",
                "delivery_user_id",
                "previous_delivery_user_id"
            )
            or ""
        )

    def _adh_effective_partner_name(order):
        return (
            order.get("delivery_partner_name")
            or order.get("previous_delivery_partner_name")
            or _adh_latest_value(
                order,
                "delivery_partner_name",
                "previous_delivery_partner_name",
                "old_delivery_partner_name",
                "delivery_user_name",
                "previous_delivery_user_name"
            )
            or ""
        )

    def _adh_effective_partner_phone(order):
        return (
            order.get("delivery_partner_phone")
            or order.get("previous_delivery_partner_phone")
            or _adh_latest_value(
                order,
                "delivery_partner_phone",
                "previous_delivery_partner_phone",
                "old_delivery_partner_phone",
                "delivery_user_phone",
                "previous_delivery_user_phone"
            )
            or ""
        )

    def _adh_partner_lookup(partner_id, name="", phone=""):
        partner_id_str = _adh_safe_str(partner_id)

        if not partner_id_str:
            return {
                "id": "",
                "name": name or "Unknown Delivery Boy",
                "phone": phone or ""
            }

        delivery_user = None

        try:
            if ObjectId.is_valid(partner_id_str):
                delivery_user = mongo.users.find_one({"_id": ObjectId(partner_id_str)})
        except Exception:
            delivery_user = None

        if not delivery_user:
            try:
                delivery_user = mongo.users.find_one({"_id": partner_id_str})
            except Exception:
                delivery_user = None

        if delivery_user:
            name = name or delivery_user.get("name") or delivery_user.get("username") or ""
            phone = phone or delivery_user.get("phone") or delivery_user.get("contact") or ""

        return {
            "id": partner_id_str,
            "name": name or "Unknown Delivery Boy",
            "phone": phone or ""
        }

    def _adh_has_rider_cancel(order):
        if order.get("delivery_cancelled_by_partner"):
            return True

        if order.get("delivery_cancelled_at") or order.get("delivery_cancel_reason"):
            return True

        if _adh_latest_action(order, "cancelled_by_delivery_partner"):
            return True

        return False

    def _adh_record_at(order):
        rider_cancel_entry = _adh_latest_action(order, "cancelled_by_delivery_partner")

        return (
            order.get("delivered_at")
            or order.get("delivery_failed_at")
            or rider_cancel_entry.get("at")
            or order.get("delivery_cancelled_at")
            or order.get("out_for_delivery_at")
            or order.get("picked_up_at")
            or order.get("reached_store_at")
            or order.get("delivery_assigned_at")
            or order.get("assigned_at")
            or order.get("updated_at")
            or order.get("created_at")
            or ""
        )

    def _adh_assignment_source_label(source):
        source = (source or "").strip().lower()

        if source == "rider_self":
            return "Accepted by rider"

        if source == "store_manual":
            return "Assigned by store"

        if source == "store_reassign":
            return "Reassigned by store"

        if source == "admin_manual":
            return "Assigned by admin"

        if source == "admin_reassign":
            return "Reassigned by admin"

        if source:
            return source.replace("_", " ").title()

        return "Not assigned"

    def _adh_apply_status_label(row, has_rider_cancel_history):
        status = (row.get("status") or "").strip().upper()

        if has_rider_cancel_history and status in {
            "READY_FOR_PICKUP",
            "CANCELLED"
        }:
            row["history_type"] = "rider_cancelled"
            row["history_label"] = "Rider Cancelled Assignment"

        elif status == "DELIVERED":
            row["history_type"] = "delivered"
            row["history_label"] = "Delivered"

        elif status == "DELIVERY_FAILED":
            row["history_type"] = "failed"
            row["history_label"] = "Delivery Failed"

        elif status in {
            "ASSIGNED_TO_DELIVERY",
            "ACCEPTED_BY_DELIVERY_MAN",
            "REACHED_STORE",
            "PICKED_UP",
            "OUT_FOR_DELIVERY"
        }:
            row["history_type"] = "active"
            row["history_label"] = "Active Delivery"

        elif status == "READY_FOR_PICKUP":
            row["history_type"] = "ready"
            row["history_label"] = "Ready For Pickup"

        elif status == "CANCELLED":
            row["history_type"] = "cancelled"
            row["history_label"] = "Cancelled"

        else:
            row["history_type"] = "record"
            row["history_label"] = status.replace("_", " ").title() if status else "Record"

        return row

    def _adh_decorate_order(row):
        items_subtotal = _adh_float(
            row.get("items_subtotal")
            if row.get("items_subtotal") is not None
            else row.get("total_amount")
        )

        delivery_fee = _adh_float(row.get("delivery_fee"))
        platform_fee = _adh_float(row.get("platform_fee"))

        tip_amount = _adh_float(
            row.get("tip_amount")
            if row.get("tip_amount") is not None
            else row.get("delivery_tip_amount")
        )

        total_payable = _adh_float(
            row.get("total_payable"),
            items_subtotal + delivery_fee + platform_fee + tip_amount
        )

        payment_method = (row.get("payment_method") or "COD").strip().upper()
        payment_status = (row.get("payment_status") or "PENDING").strip().upper()
        payment_collection_status = (row.get("payment_collection_status") or "").strip().upper()
        payment_collection_channel = (row.get("payment_collection_channel") or "").strip().upper()
        upi_delivery_reconciliation_status = (row.get("upi_delivery_reconciliation_status") or "").strip().upper()

        cod_payment_methods = {
            "COD",
            "CASH_ON_DELIVERY",
            "COD_RIDER_COLLECTION"
        }
        collected_payment_statuses = {
            "PAID",
            "COLLECTED",
            "ONLINE_PAID",
            "COLLECTED_BY_RIDER",
            "COD_COLLECTED_BY_RIDER",
            "COD_UPI_RECORDED"
        }

        is_cod_order = payment_method in cod_payment_methods
        is_cod_upi = is_cod_order and payment_collection_channel == "UPI"
        is_cod_collected = bool(
            is_cod_order
            and (
                payment_status in collected_payment_statuses
                or payment_collection_status in {"COLLECTED", "PAID"}
            )
        )

        if is_cod_order and not is_cod_collected:
            amount_to_collect = total_payable
        else:
            amount_to_collect = 0.0

        cod_collected_amount = (
            _adh_float(row.get("cod_collected_amount"), total_payable)
            if is_cod_collected
            else 0.0
        )

        if is_cod_collected:
            cod_display_amount = cod_collected_amount
            if is_cod_upi and upi_delivery_reconciliation_status == "VERIFIED":
                cod_display_label = "UPI verified"
            elif is_cod_upi:
                cod_display_label = "UPI recorded · verification pending"
            else:
                cod_display_label = "Cash collected"
        elif is_cod_order:
            cod_display_amount = amount_to_collect
            cod_display_label = "To collect"
        else:
            cod_display_amount = 0.0
            cod_display_label = "Not applicable"

        delivery_fee_plus_tip = delivery_fee + tip_amount

        row["items_subtotal"] = round(items_subtotal, 2)
        row["delivery_fee"] = round(delivery_fee, 2)
        row["platform_fee"] = round(platform_fee, 2)
        row["tip_amount"] = round(tip_amount, 2)
        row["total_payable"] = round(total_payable, 2)
        row["payment_method"] = payment_method
        row["payment_status"] = payment_status
        row["payment_collection_status"] = payment_collection_status
        row["payment_collection_channel"] = payment_collection_channel
        row["upi_delivery_reconciliation_status"] = upi_delivery_reconciliation_status
        row["collection_channel_label"] = (
            "UPI" if is_cod_upi
            else ("Cash" if is_cod_order else ("Razorpay" if payment_collection_channel == "RAZORPAY" else "Online"))
        )
        row["is_cod_order"] = is_cod_order
        row["is_cod_collected"] = is_cod_collected
        row["amount_to_collect"] = round(amount_to_collect, 2)
        row["cod_collected_amount"] = round(cod_collected_amount, 2)
        row["cod_display_amount"] = round(cod_display_amount, 2)
        row["cod_display_label"] = cod_display_label
        row["delivery_fee_plus_tip"] = round(delivery_fee_plus_tip, 2)

        # Store earning is product/items subtotal only.
        # Platform fee belongs to admin/platform.
        row["store_earning"] = round(items_subtotal, 2)
        row["admin_platform_earning"] = round(platform_fee, 2)

        row["delivery_assignment_source_label"] = _adh_assignment_source_label(
            row.get("delivery_assignment_source")
        )

        row["assigned_at"] = row.get("delivery_assigned_at") or row.get("assigned_at") or ""
        row["reached_store_at"] = row.get("reached_store_at") or ""
        row["picked_up_at"] = row.get("picked_up_at") or ""
        row["out_for_delivery_at"] = row.get("out_for_delivery_at") or ""
        row["delivered_at"] = row.get("delivered_at") or ""
        row["delivery_failed_at"] = row.get("delivery_failed_at") or ""
        row["delivery_failed_reason"] = row.get("delivery_failed_reason") or ""
        row["delivery_failed_note"] = row.get("delivery_failed_note") or ""

        rider_cancel_entry = _adh_latest_action(row, "cancelled_by_delivery_partner")

        row["rider_cancel_reason"] = (
            rider_cancel_entry.get("reason")
            or row.get("delivery_cancel_reason")
            or ""
        )

        row["rider_cancelled_at"] = (
            rider_cancel_entry.get("at")
            or row.get("delivery_cancelled_at")
            or ""
        )

        row["rider_cancelled_status_from"] = (
            rider_cancel_entry.get("status_before_cancel")
            or row.get("delivery_cancelled_status_from")
            or ""
        )

        return row

    stores = list(mongo.stores.find({}).sort("store_name", 1))

    store_lookup = {}
    store_filter_doc = None

    for store in stores:
        sid = _adh_safe_str(store.get("_id"))

        store_lookup[sid] = {
            "id": sid,
            "store_name": store.get("store_name") or store.get("name") or "Store",
            "phone": store.get("phone") or store.get("owner_phone") or "",
        }

        if store_filter and sid == store_filter:
            store_filter_doc = store

    delivery_users = list(
        mongo.users.find({
            "role": {"$regex": "^delivery$", "$options": "i"}
        }).sort("name", 1)
    )

    delivery_people_map = {}

    for delivery_user in delivery_users:
        did = _adh_safe_str(delivery_user.get("_id"))
        delivery_people_map[did] = {
            "id": did,
            "name": delivery_user.get("name") or delivery_user.get("username") or "Delivery Boy",
            "phone": delivery_user.get("phone") or delivery_user.get("contact") or ""
        }

    raw_orders = list(
        mongo.orders.find({}).sort("updated_at", -1)
    )

    history_orders = []
    rider_summary_map = {}

    for order in raw_orders:
        status = (order.get("status") or "").strip().upper()
        has_rider_cancel_history = _adh_has_rider_cancel(order)

        has_delivery_activity = bool(
            order.get("delivery_partner_id")
            or order.get("previous_delivery_partner_id")
            or order.get("delivery_history")
            or status in allowed_history_statuses
            or has_rider_cancel_history
        )

        if not has_delivery_activity:
            continue

        if status not in allowed_history_statuses and not has_rider_cancel_history:
            continue

        order_store_id = _adh_safe_str(order.get("store_id"))
        order_store_name = (order.get("store_name") or "").strip()

        store_info = store_lookup.get(order_store_id)

        if not store_info and order_store_name:
            store_info = {
                "id": order_store_id,
                "store_name": order_store_name,
                "phone": ""
            }

        if not store_info:
            store_info = {
                "id": order_store_id,
                "store_name": "Unknown Store",
                "phone": ""
            }

        if store_filter:
            if order_store_id != store_filter:
                continue

        effective_partner_id = _adh_effective_partner_id(order)

        if not effective_partner_id:
            continue

        effective_partner_id_str = _adh_safe_str(effective_partner_id)

        if delivery_user_filter and effective_partner_id_str != delivery_user_filter:
            continue

        partner_info = _adh_partner_lookup(
            effective_partner_id_str,
            _adh_effective_partner_name(order),
            _adh_effective_partner_phone(order)
        )

        delivery_people_map[effective_partner_id_str] = partner_info

        payment_method = (order.get("payment_method") or "COD").strip().upper()

        if payment_type_filter == "COD" and payment_method != "COD":
            continue

        if payment_type_filter == "ONLINE" and payment_method == "COD":
            continue

        row = dict(order)
        row["id"] = _adh_safe_str(row.get("_id") or "")
        row["store_id_str"] = store_info.get("id") or order_store_id
        row["store_name"] = store_info.get("store_name") or "Unknown Store"
        row["store_phone"] = store_info.get("phone") or ""

        row["delivery_partner_id"] = effective_partner_id_str
        row["delivery_partner_id_str"] = effective_partner_id_str
        row["delivery_partner_name"] = partner_info.get("name") or "Unknown Delivery Boy"
        row["delivery_partner_phone"] = partner_info.get("phone") or ""

        customer = None

        if row.get("user_id"):
            try:
                customer = mongo.users.find_one({"_id": ObjectId(str(row.get("user_id")))})
            except Exception:
                customer = None

        row["customer_name"] = (
            row.get("customer_name")
            or (customer.get("name") if customer else "")
            or "Customer"
        )

        row["customer_phone"] = (
            row.get("customer_phone")
            or (customer.get("phone") if customer else "")
            or ""
        )

        row = _adh_decorate_order(row)

        record_at = _adh_record_at(order)
        row["record_at"] = record_at

        row = _adh_apply_status_label(row, has_rider_cancel_history)

        if status_filter:
            if status_filter == "RIDER_CANCELLED":
                if row.get("history_type") != "rider_cancelled":
                    continue
            elif status_filter != status and status_filter != (row.get("history_type") or "").upper():
                continue

        if date_from and record_at and str(record_at)[:10] < date_from:
            continue

        if date_to and record_at and str(record_at)[:10] > date_to:
            continue

        if q:
            haystack = " ".join([
                str(row.get("id") or ""),
                str(row.get("store_name") or ""),
                str(row.get("customer_name") or ""),
                str(row.get("customer_phone") or ""),
                str(row.get("delivery_partner_name") or ""),
                str(row.get("delivery_partner_phone") or ""),
                str(row.get("history_label") or ""),
                str(row.get("payment_method") or ""),
                str(row.get("payment_status") or ""),
                str(row.get("delivery_failed_reason") or ""),
                str(row.get("delivery_failed_note") or ""),
                str(row.get("rider_cancel_reason") or "")
            ]).lower()

            if q.lower() not in haystack:
                continue

        if effective_partner_id_str not in rider_summary_map:
            rider_summary_map[effective_partner_id_str] = {
                "delivery_partner_id": effective_partner_id_str,
                "delivery_partner_name": row.get("delivery_partner_name") or "Delivery Boy",
                "delivery_partner_phone": row.get("delivery_partner_phone") or "",
                "stores_served_set": set(),
                "store_names_set": set(),
                "total_orders": 0,
                "delivered": 0,
                "failed": 0,
                "rider_cancelled": 0,
                "active": 0,
                "cancelled": 0,
                "cod_collected": 0.0,
                "delivery_fee": 0.0,
                "tip": 0.0,
                "delivery_earning": 0.0,
                "platform_fee": 0.0,
                "store_earning": 0.0,
                "last_record_at": "",
            }

        rider_row = rider_summary_map[effective_partner_id_str]

        rider_row["stores_served_set"].add(row.get("store_id_str") or row.get("store_name") or "")
        rider_row["store_names_set"].add(row.get("store_name") or "Unknown Store")

        rider_row["total_orders"] += 1

        if row.get("history_type") == "delivered":
            rider_row["delivered"] += 1
        elif row.get("history_type") == "failed":
            rider_row["failed"] += 1
        elif row.get("history_type") == "rider_cancelled":
            rider_row["rider_cancelled"] += 1
        elif row.get("history_type") == "active":
            rider_row["active"] += 1
        elif row.get("history_type") == "cancelled":
            rider_row["cancelled"] += 1

        rider_row["cod_collected"] += _adh_float(row.get("cod_collected_amount"))
        rider_row["delivery_fee"] += _adh_float(row.get("delivery_fee"))
        rider_row["tip"] += _adh_float(row.get("tip_amount"))
        rider_row["delivery_earning"] += _adh_float(row.get("delivery_fee_plus_tip"))
        rider_row["platform_fee"] += _adh_float(row.get("platform_fee"))
        rider_row["store_earning"] += _adh_float(row.get("store_earning"))

        if record_at and str(record_at) > str(rider_row.get("last_record_at") or ""):
            rider_row["last_record_at"] = record_at

        history_orders.append(row)

    history_orders.sort(
        key=lambda x: str(x.get("record_at") or ""),
        reverse=True
    )

    rider_summary_rows = list(rider_summary_map.values())

    for rider_row in rider_summary_rows:
        rider_row["stores_served"] = len([
            sid for sid in rider_row.get("stores_served_set", set())
            if sid
        ])

        rider_row["store_names"] = ", ".join(
            sorted([
                name for name in rider_row.get("store_names_set", set())
                if name
            ])
        )

        rider_row.pop("stores_served_set", None)
        rider_row.pop("store_names_set", None)

        rider_row["cod_collected"] = round(_adh_float(rider_row.get("cod_collected")), 2)
        rider_row["delivery_fee"] = round(_adh_float(rider_row.get("delivery_fee")), 2)
        rider_row["tip"] = round(_adh_float(rider_row.get("tip")), 2)
        rider_row["delivery_earning"] = round(_adh_float(rider_row.get("delivery_earning")), 2)
        rider_row["platform_fee"] = round(_adh_float(rider_row.get("platform_fee")), 2)
        rider_row["store_earning"] = round(_adh_float(rider_row.get("store_earning")), 2)

    rider_summary_rows.sort(
        key=lambda x: (
            str(x.get("last_record_at") or ""),
            int(x.get("total_orders") or 0)
        ),
        reverse=True
    )

    history_metrics = {
        "total": len(history_orders),
        "total_delivery_boys": len(rider_summary_rows),
        "total_stores": len({
            row.get("store_id_str") or row.get("store_name") or ""
            for row in history_orders
            if row.get("store_id_str") or row.get("store_name")
        }),
        "delivered": sum(1 for r in history_orders if r.get("history_type") == "delivered"),
        "failed": sum(1 for r in history_orders if r.get("history_type") == "failed"),
        "rider_cancelled": sum(1 for r in history_orders if r.get("history_type") == "rider_cancelled"),
        "active": sum(1 for r in history_orders if r.get("history_type") == "active"),
        "cancelled": sum(1 for r in history_orders if r.get("history_type") == "cancelled"),
        "cod_collected": round(sum(_adh_float(r.get("cod_collected_amount")) for r in history_orders), 2),
        "delivery_fee": round(sum(_adh_float(r.get("delivery_fee")) for r in history_orders), 2),
        "tip": round(sum(_adh_float(r.get("tip_amount")) for r in history_orders), 2),
        "delivery_earning": round(sum(_adh_float(r.get("delivery_fee_plus_tip")) for r in history_orders), 2),
        "platform_fee": round(sum(_adh_float(r.get("platform_fee")) for r in history_orders), 2),
        "store_earning": round(sum(_adh_float(r.get("store_earning")) for r in history_orders), 2),
    }

    return render_template(
        "admin_delivery_history.html",
        user=current_user(),
        active_group="delivery",
        active_page="delivery_history",
        orders=history_orders,
        rider_summary_rows=rider_summary_rows,
        delivery_people=list(delivery_people_map.values()),
        stores=[
            {
                "id": _adh_safe_str(store.get("_id")),
                "store_name": store.get("store_name") or store.get("name") or "Store"
            }
            for store in stores
        ],
        history_metrics=history_metrics,
        q=q,
        status_filter=status_filter,
        delivery_user_filter=delivery_user_filter,
        store_filter=store_filter,
        payment_type_filter=payment_type_filter,
        date_from=date_from,
        date_to=date_to
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
