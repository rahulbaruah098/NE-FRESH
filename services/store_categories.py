"""Store category persistence/query helpers extracted during Step 4."""

import re
from datetime import datetime

from bson import ObjectId

from extensions import mongo

DEFAULT_STORE_CATEGORIES = [
    {
        "name": "Fresh cuts",
        "sub_categories": ["Curry cuts", "Boneless & Mince", "Offals"],
    },
    {
        "name": "Ready to cook",
        "sub_categories": [],
    },
    {
        "name": "Spices",
        "sub_categories": [],
    },
]

def _category_slug(name):
    name = (name or "").strip().lower()
    name = re.sub(r"[^a-z0-9]+", "-", name)
    return name.strip("-")

def _ensure_store_categories(store_id):
    existing_count = mongo.store_categories.count_documents({
        "store_id": store_id
    })

    if existing_count > 0:
        return

    now = datetime.utcnow().isoformat()

    docs = []
    for cat in DEFAULT_STORE_CATEGORIES:
        docs.append({
            "store_id": store_id,
            "name": cat["name"],
            "slug": _category_slug(cat["name"]),
            "sub_categories": cat.get("sub_categories", []),
            "image_path": "",
            "category_image_path": "",
            "emoji": "🛒",
            "is_active": 1,
            "is_default": 1,
            "created_at": now,
            "updated_at": now,
})

    if docs:
        mongo.store_categories.insert_many(docs)

def _get_store_categories(store_id, active_only=False):
    _ensure_store_categories(store_id)

    query = {"store_id": store_id}

    if active_only:
        query["is_active"] = 1

    categories = list(
        mongo.store_categories.find(query).sort([
            ("is_active", -1),
            ("name", 1)
        ])
    )

    for cat in categories:
        cat["id"] = str(cat["_id"])

    return categories

def _get_store_category_by_id(store_id, category_id, active_only=False):
    try:
        category_obj_id = ObjectId(category_id)
    except Exception:
        return None

    query = {
        "_id": category_obj_id,
        "store_id": store_id
    }

    if active_only:
        query["is_active"] = 1

    cat = mongo.store_categories.find_one(query)

    if cat:
        cat["id"] = str(cat["_id"])

    return cat

def _get_store_category_by_name(store_id, name, active_only=False):
    slug = _category_slug(name)

    query = {
        "store_id": store_id,
        "slug": slug
    }

    if active_only:
        query["is_active"] = 1

    cat = mongo.store_categories.find_one(query)

    if cat:
        cat["id"] = str(cat["_id"])

    return cat

def _get_category_product_count(store_id, category_name):
    return mongo.products.count_documents({
        "store_id": store_id,
        "category": category_name
    })
