"""Order stock reservation/restoration service extracted during Step 5.

Normal products and bundle child products use one shared stock movement path so
customer and Store cancellation cannot diverge.
"""

from bson import ObjectId
from extensions import mongo

def _money_float(value, default=0.0):
    try:
        if value is None or str(value).strip() == "":
            return default
        return float(value)
    except Exception:
        return default

def _order_item_reserved_products(item):
    """
    Returns the real product stock movements for one order item.

    Normal product item:
    - reserve/release the product itself.

    Bundle item:
    - reserve/release every child product in bundle_items_snapshot multiplied
      by the ordered bundle quantity.
    """
    reserved = []

    item_type = (item.get("item_type") or "product").strip().lower()

    if item_type == "bundle" or item.get("bundle_id"):
        bundle_quantity = _money_float(
            item.get("quantity") if item.get("quantity") is not None else item.get("cart_quantity"),
            1.0
        )

        if bundle_quantity <= 0:
            bundle_quantity = 1.0

        for child in item.get("bundle_items_snapshot") or []:
            if not isinstance(child, dict):
                continue

            child_product_id = child.get("product_id")
            child_product_id_str = str(child.get("product_id_str") or child_product_id or "").strip()

            if not child_product_id:
                try:
                    child_product_id = ObjectId(child_product_id_str)
                except Exception:
                    child_product_id = None

            child_qty = _money_float(child.get("quantity"), 0.0) * bundle_quantity

            if child_product_id and child_qty > 0:
                reserved.append({
                    "product_id": child_product_id,
                    "quantity": child_qty,
                    "product_name": child.get("product_name_snapshot") or item.get("bundle_name_snapshot") or "Bundle product"
                })

        return reserved

    product_id = item.get("product_id")
    qty = _money_float(
        item.get("quantity") if item.get("quantity") is not None else item.get("cart_quantity"),
        0.0
    )

    if product_id and qty > 0:
        reserved.append({
            "product_id": product_id,
            "quantity": qty,
            "product_name": item.get("product_name") or "One product"
        })

    return reserved

def _reserve_order_stock_items(order_items):
    """Atomically reserve product stock for an order.

    Bundle orders reserve stock from every child product inside the bundle.
    This prevents stale bundle stock from being sold when any child product is
    missing, inactive, or lower than required bundle quantity.
    """
    reserved_items = []

    for item in order_items or []:
        for stock_item in _order_item_reserved_products(item):
            product_id = stock_item.get("product_id")
            qty = _money_float(stock_item.get("quantity"), 0.0)

            if not product_id or qty <= 0:
                continue

            result = mongo.products.update_one(
                {
                    "_id": product_id,
                    "is_active": 1,
                    "stock_quantity": {"$gte": qty},
                },
                {"$inc": {"stock_quantity": -qty}}
            )

            if result.modified_count != 1:
                _release_order_stock_items(reserved_items)
                return False, f"{stock_item.get('product_name') or 'One product'} is out of stock or quantity is no longer available."

            reserved_items.append({"product_id": product_id, "quantity": qty})

            updated_product = mongo.products.find_one({"_id": product_id})
            if updated_product and float(updated_product.get("stock_quantity") or 0) <= 0:
                mongo.products.update_one(
                    {"_id": product_id},
                    {"$set": {"stock_quantity": 0, "is_active": 0}}
                )

    return True, ""

def _release_order_stock_items(order_items):
    """Return stock for a cancelled/expired/rolled-back order attempt."""
    for item in order_items or []:
        for stock_item in _order_item_reserved_products(item):
            product_id = stock_item.get("product_id")
            qty = _money_float(stock_item.get("quantity"), 0.0)

            if product_id and qty > 0:
                mongo.products.update_one(
                    {"_id": product_id},
                    {
                        "$inc": {"stock_quantity": qty},
                        "$set": {"is_active": 1},
                    }
                )
