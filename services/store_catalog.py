"""Non-financial Store catalog helpers extracted during Step 4."""

from extensions import mongo
from helpers.identifiers import _store_identity_values
from services.product_units import hydrate_product_unit_fields

def _get_store_products(store_id):
    products = list(
        mongo.products.find({"store_id": {"$in": _store_identity_values(store_id)}}).sort("created_at", -1)
    )

    for p in products:
        p["id"] = str(p["_id"])
        p["store_id"] = str(p.get("store_id")) if p.get("store_id") else ""
        hydrate_product_unit_fields(p)

    return products
