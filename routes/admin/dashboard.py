"""Admin dashboard route section.

Route decorators, endpoint names, URL paths, HTTP methods and function bodies are
preserved from the Step 6 source. Shared legacy helpers are imported from
``routes.admin.shared`` during this transitional decomposition.
"""

from routes.admin.shared import *

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
    # Refund / return dashboard KPIs
    # -------------------------
    refund_pending_docs = []
    admin_review_needed_docs = []
    refund_processed_docs = []

    ready_for_refund_amount = 0.0
    refund_processed_amount = 0.0
    store_refund_deduction_amount = 0.0
    store_adjustment_due_amount = 0.0

    for order in orders:
        refund_status = _norm_status(order.get("refund_status"))
        return_status = _norm_status(order.get("return_status"))
        admin_review_status = _norm_status(order.get("admin_return_review_status"))

        refund_amount = float(order.get("refund_amount") or 0)
        store_refund_deduction = float(
            order.get("store_refund_deduction")
            if order.get("store_refund_deduction") is not None
            else order.get("refund_deduction")
            or 0
        )
        store_adjustment_due = float(order.get("store_adjustment_due") or 0)

        if refund_status in ["READY_FOR_REFUND", "PENDING"]:
            refund_pending_docs.append(order)
            ready_for_refund_amount += refund_amount

        if return_status == "NEED_ADMIN_REVIEW" and admin_review_status == "PENDING":
            admin_review_needed_docs.append(order)

        if refund_status in ["PROCESSED", "ADJUSTED"]:
            refund_processed_docs.append(order)
            refund_processed_amount += refund_amount

        store_refund_deduction_amount += store_refund_deduction
        store_adjustment_due_amount += store_adjustment_due

    refund_pending_count = len(refund_pending_docs)
    admin_review_needed_count = len(admin_review_needed_docs)
    refund_processed_count = len(refund_processed_docs)

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
    # Payment / settlement dashboard metrics
    # -------------------------
    online_paid_orders = list(
        mongo.orders.find({
            "payment_method": {
                "$in": ["ONLINE", "ONLINE_PAYMENT", "RAZORPAY"]
            },
            "payment_status": {
                "$in": ["PAID", "ONLINE_PAID", "SUCCESS"]
            }
        })
    )

    cod_rider_pending_orders = list(
        mongo.orders.find({
            "payment_method": {
                "$in": ["COD", "CASH_ON_DELIVERY", "COD_RIDER_COLLECTION"]
            },
            "status": "DELIVERED",
            "rider_cash_settlement_status": {
                "$nin": ["RECEIVED_BY_ADMIN", "RECEIVED", "SETTLED", "NOT_REQUIRED"]
            }
        })
    )

    platform_fee_received_orders = list(
        mongo.orders.find({
            "platform_fee_status": "RECEIVED"
        })
    )

    store_payout_pending_orders = list(
        mongo.orders.find({
            "status": "DELIVERED",
            "store_payout_status": {
                "$nin": ["PAID", "SETTLED", "NOT_REQUIRED"]
            }
        })
    )

    online_payment_received_amount = round(
        sum(float(o.get("total_payable") or o.get("total_amount") or 0) for o in online_paid_orders),
        2
    )

    cod_rider_cash_pending_amount = round(
        sum(float(o.get("rider_cash_to_submit") or o.get("expected_rider_cash_to_submit") or 0) for o in cod_rider_pending_orders),
        2
    )

    platform_fee_received_amount = round(
        sum(float(o.get("platform_fee") or 0) for o in platform_fee_received_orders),
        2
    )

    store_payout_pending_amount = round(
        sum(float(o.get("adjusted_store_payout") or o.get("store_payout_amount") or o.get("store_earning") or o.get("items_subtotal") or 0) for o in store_payout_pending_orders),
        2
    )


    # -------------------------
    # Quick links
    # -------------------------
    delivery_mode_settings = get_delivery_mode_settings()
    delivery_mode_ui = get_delivery_mode_ui_context(delivery_mode_settings)
    platform_fee_settings = get_platform_fee_settings()

    online_payment_allowed = bool(delivery_mode_settings.get("allow_online_payment", True))
    platform_fee_enabled = bool(platform_fee_settings.get("enabled", False))

    quick_links = [
        {"label": "Store Overview", "endpoint": "admin_store_overview"},
        {"label": "All Store Admin Profiles", "endpoint": "admin_store_list"},
        {"label": "Customers", "endpoint": "admin_customers"},
        {"label": "Customer Complaints", "endpoint": "admin_complaints"},
        {"label": "Store Payouts", "endpoint": "admin_settlements"},
        {"label": "Delivery Routing Settings", "endpoint": "admin_delivery_mode_settings"},
    ]

    if online_payment_allowed:
        quick_links.append({"label": "Online Payment Gateway", "endpoint": "admin_payment_settings"})

    if platform_fee_enabled:
        quick_links.append({"label": "Platform Fee Earnings", "endpoint": "admin_platform_earnings"})

    if delivery_mode_settings.get("return_refund_enabled", True):
        quick_links.extend([
            {"label": "Customer Refund Processing", "endpoint": "admin_refund_processing"},
            {"label": "Return / Refund Settlement Impact", "endpoint": "admin_returns_settlements"},
        ])

    if delivery_mode_settings.get("in_house_delivery_enabled", True):
        quick_links.extend([
            {"label": "In-house Delivery Overview", "endpoint": "admin_delivery_overview"},
            {"label": "Create In-house Delivery Staff", "endpoint": "admin_create_delivery"},
        ])

    if delivery_mode_settings.get("external_local_delivery_enabled", False) or delivery_mode_settings.get("third_party_shipping_enabled", False):
        quick_links.append({"label": "External Delivery Settings", "endpoint": "admin_external_delivery_settings"})

    if delivery_mode_settings.get("third_party_shipping_enabled", False):
        quick_links.append({"label": "Shiprocket / Courier Orders", "endpoint": "admin_external_delivery_orders"})

    quick_links.append({"label": "Export Transactions CSV", "endpoint": "admin_transactions_csv"})

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
                # Payment / settlement dashboard KPIs
        "online_payment_received_amount": online_payment_received_amount,
        "online_payment_received_count": len(online_paid_orders),

        "cod_rider_cash_pending_amount": cod_rider_cash_pending_amount,
        "cod_rider_cash_pending_count": len(cod_rider_pending_orders),

        "platform_fee_received_amount": platform_fee_received_amount,

        "store_payout_pending_amount": store_payout_pending_amount,
        "store_payout_pending_count": len(store_payout_pending_orders),

        # Refund / return dashboard KPIs
        "refund_pending_count": refund_pending_count,
        "admin_review_needed_count": admin_review_needed_count,
        "refund_processed_count": refund_processed_count,
        "ready_for_refund_amount": round(ready_for_refund_amount, 2),
        "refund_processed_amount": round(refund_processed_amount, 2),
        "store_refund_deduction_amount": round(store_refund_deduction_amount, 2),
        "store_adjustment_due_amount": round(store_adjustment_due_amount, 2),
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
        delivery_mode_settings=delivery_mode_settings,
        delivery_mode_ui=delivery_mode_ui,
        online_payment_allowed=online_payment_allowed,
        platform_fee_enabled=platform_fee_enabled,
        complaints_window_label="(all time)",
    )


@app.route('/admin/approvals')
@login_required(role='admin')
def admin_approvals():
    flash('Approval feature under development.', 'info')
    return redirect(url_for('admin_dashboard'))
