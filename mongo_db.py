import os
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/NE_Fresh")

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