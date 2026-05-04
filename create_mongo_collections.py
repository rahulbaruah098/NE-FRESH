from pymongo import MongoClient

MONGO_URI = "mongodb://localhost:27017"
DB_NAME = "NE_Fresh"

client = MongoClient(MONGO_URI)
db = client[DB_NAME]

collections = [
    "addresses",
    "api_sessions",
    "cart_items",
    "carts",
    "complaints",
    "contact_messages",
    "delivery_assignments",
    "delivery_locations",
    "newsletter_subscribers",
    "order_addresses",
    "order_events",
    "order_items",
    "orders",
    "otp_codes",
    "password_reset_tokens",
    "product_ratings",
    "products",
    "serviceable_pincodes",
    "store_ratings",
    "stores",
    "transactions",
    "users",
]

for name in collections:
    if name not in db.list_collection_names():
        db.create_collection(name)
        print(f"Created collection: {name}")
    else:
        print(f"Already exists: {name}")

print("MongoDB collections created successfully in NE_Fresh.")
client.close()