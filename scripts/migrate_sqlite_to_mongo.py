import sqlite3
from pathlib import Path
from datetime import datetime
from pymongo import MongoClient

BASE_DIR = Path(__file__).resolve().parent.parent
SQLITE_DB_PATH = BASE_DIR / "app.db"

MONGO_URI = "mongodb://localhost:27017/chhimpheichicken_db"
mongo_client = MongoClient(MONGO_URI)
mongo_db = mongo_client["chhimpheichicken_db"]

sqlite_conn = sqlite3.connect(SQLITE_DB_PATH)
sqlite_conn.row_factory = sqlite3.Row
cursor = sqlite_conn.cursor()


def migrate_table(table_name, collection_name):
    cursor.execute(f"SELECT * FROM {table_name}")
    rows = cursor.fetchall()

    docs = []

    for row in rows:
        doc = dict(row)

        if "id" in doc:
            doc["old_sql_id"] = doc.pop("id")

        doc["migrated_at"] = datetime.utcnow()
        docs.append(doc)

    if docs:
        mongo_db[collection_name].insert_many(docs)
        print(f"✅ Migrated {len(docs)} rows: {table_name} → {collection_name}")
    else:
        print(f"⚠️ No rows found in {table_name}")


tables = [
    "users",
    "otp_codes",
    "password_reset_tokens",
    "addresses",
    "order_addresses",
    "stores",
    "products",
    "carts",
    "cart_items",
    "orders",
    "order_items",
    "order_events",
    "delivery_assignments",
    "transactions",
    "contact_messages",
    "newsletter_subscribers",
    "delivery_locations",
    "product_ratings",
    "store_ratings",
    "complaints",
    "serviceable_pincodes",
    "api_sessions",
]

for table in tables:
    try:
        migrate_table(table, table)
    except Exception as e:
        print(f"❌ Failed migrating {table}: {e}")

sqlite_conn.close()
mongo_client.close()

print("✅ SQLite to MongoDB migration completed.")