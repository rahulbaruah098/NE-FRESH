import os
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()


def _is_production_env():
    raw = (
        os.getenv("APP_ENV")
        or os.getenv("FLASK_ENV")
        or os.getenv("ENV")
        or ""
    ).strip().lower()
    return raw in {"production", "prod", "live"}


MONGO_URI = (os.getenv("MONGO_URI") or "").strip()

if not MONGO_URI:
    if _is_production_env():
        raise RuntimeError("MONGO_URI must be set in the production server environment.")
    MONGO_URI = "mongodb://localhost:27017/NE_Fresh"

if _is_production_env() and "localhost" in MONGO_URI.lower():
    print("[PRODUCTION WARNING] MONGO_URI points to localhost. Confirm production MongoDB backups are enabled.")

client = MongoClient(MONGO_URI)
mongo = client.get_database()


def ensure_mongo_indexes():
    mongo.users.create_index("email", unique=True)
    mongo.users.create_index("phone", unique=True, sparse=True)
    mongo.users.create_index("role")

    mongo.products.create_index("store_id")
    mongo.products.create_index("is_active")
    mongo.orders.create_index("user_id")
    mongo.orders.create_index("store_id")
    mongo.cart_items.create_index("cart_id")
    mongo.cart_items.create_index("product_id")
    mongo.orders.create_index("status")
    mongo.orders.create_index("created_at")
    mongo.orders.create_index("store_id")
    mongo.orders.create_index("user_id")
    mongo.orders.create_index("delivery_partner_id")

    mongo.order_items.create_index("product_id")
    mongo.complaints.create_index("target_type")
    mongo.complaints.create_index("store_id")
    mongo.complaints.create_index("delivery_partner_id")

    mongo.store_ratings.create_index("store_id")
    mongo.product_ratings.create_index("product_id")
    mongo.delivery_ratings.create_index("delivery_partner_id")

    mongo.transactions.create_index("status")
    mongo.transactions.create_index("order_id")

    mongo.delivery_availability.create_index("user_id")

    # High-frequency compound indexes used by the current Admin / Store /
    # Delivery dashboards and notification/history pages. These are additive
    # performance indexes only; they do not change document structure or logic.
    mongo.products.create_index(
        [("is_active", 1), ("created_at", -1)],
        name="products_active_created_at"
    )
    mongo.products.create_index(
        [("store_id", 1), ("is_active", 1), ("created_at", -1)],
        name="products_store_active_created_at"
    )
    mongo.product_bundles.create_index(
        [("is_active", 1), ("is_deleted", 1), ("updated_at", -1)],
        name="product_bundles_active_deleted_updated"
    )

    mongo.orders.create_index(
        [("store_id", 1), ("status", 1), ("created_at", -1)],
        name="orders_store_status_created_at"
    )
    mongo.orders.create_index(
        [("delivery_partner_id", 1), ("status", 1), ("created_at", -1)],
        name="orders_delivery_status_created_at"
    )
    mongo.orders.create_index(
        [("user_id", 1), ("created_at", -1)],
        name="orders_user_created_at"
    )
    mongo.orders.create_index(
        [("payment_collection_channel", 1), ("upi_delivery_reconciliation_status", 1), ("delivered_at", -1)],
        name="orders_upi_delivery_reconciliation"
    )
    mongo.orders.create_index(
        [("upi_delivery_reference", 1)],
        name="orders_upi_delivery_reference",
        sparse=True
    )

    mongo.orders.create_index(
        [("delivery_payout_model", 1), ("delivery_monthly_period", 1), ("delivery_partner_id", 1), ("status", 1)],
        name="orders_delivery_monthly_payout"
    )
    mongo.delivery_partner_monthly_settlements.create_index(
        [("delivery_partner_id_str", 1), ("period", 1)],
        name="delivery_partner_monthly_unique",
        unique=True
    )
    mongo.delivery_partner_monthly_settlements.create_index(
        [("period", -1), ("status", 1)],
        name="delivery_partner_monthly_period_status"
    )

    # Stage 2C finance-reconciliation indexes.
    mongo.orders.create_index(
        [("payment_received_by", 1), ("platform_fee_status", 1), ("delivered_at", -1)],
        name="orders_business_receiver_platform_fee"
    )
    mongo.orders.create_index(
        [("cod_collection_method", 1), ("external_cod_remittance_status", 1), ("delivered_at", -1)],
        name="orders_external_cod_remittance"
    )
    mongo.orders.create_index(
        [("store_payout_status", 1), ("refund_status", 1), ("delivered_at", -1)],
        name="orders_store_payout_refund"
    )
    mongo.store_finance_adjustments.create_index(
        [("adjustment_key", 1)],
        name="store_finance_adjustment_unique",
        unique=True
    )
    mongo.store_finance_adjustments.create_index(
        [("store_id_str", 1), ("status", 1), ("created_at", 1)],
        name="store_finance_adjustment_open"
    )

    mongo.transactions.create_index(
        [("order_id", 1), ("status", 1)],
        name="transactions_order_status"
    )

    mongo.store_notifications.create_index(
        [("store_id", 1), ("created_at", -1)],
        name="store_notifications_store_created_at"
    )
    mongo.store_notifications.create_index(
        [("store_id", 1), ("is_read", 1), ("created_at", -1)],
        name="store_notifications_store_read_created_at"
    )
    mongo.delivery_notifications.create_index(
        [("delivery_user_id", 1), ("is_active", 1), ("created_at", -1)],
        name="delivery_notifications_user_active_created_at"
    )
    mongo.delivery_notifications.create_index(
        [("delivery_user_id", 1), ("is_read", 1), ("is_active", 1)],
        name="delivery_notifications_user_read_active"
    )
