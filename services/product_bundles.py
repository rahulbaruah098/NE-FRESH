"""Product bundle helpers extracted during Step 4.

Bundle pricing, stock and document behavior is preserved from app_core.py.
"""

import math
import re
from datetime import datetime

from bson import ObjectId

from extensions import mongo
from services.product_units import hydrate_product_unit_fields, product_price_per_unit, product_stock_quantity
from services.store_notifications import _create_store_notification

BUNDLE_DISCOUNT_TYPES = {
    "none",
    "fixed_price",
    "percent",
    "amount",
}

def _bundle_money_float(value, default=0.0):
    try:
        if value is None or str(value).strip() == "":
            return float(default)
        return float(value)
    except Exception:
        return float(default)

def _bundle_quantity_float(value, default=1.0):
    try:
        if value is None or str(value).strip() == "":
            return float(default)
        qty = float(value)
    except Exception:
        qty = float(default)

    if qty <= 0:
        qty = float(default)

    return round(qty, 3)

def normalize_bundle_discount_type(value):
    value = (value or "none").strip().lower()

    if value in {"fixed", "fixed-price", "bundle_fixed_price"}:
        value = "fixed_price"

    if value in {"percentage", "bundle_percent", "bundle_percentage", "bundle_percentage_discount"}:
        value = "percent"

    if value in {"fixed_amount", "bundle_amount", "bundle_amount_discount"}:
        value = "amount"

    if value not in BUNDLE_DISCOUNT_TYPES:
        value = "none"

    return value

def _bundle_object_id_string(value):
    if value is None:
        return ""

    try:
        if isinstance(value, ObjectId):
            return str(value)
    except Exception:
        pass

    return str(value)

def normalize_bundle_product_ids(values):
    """
    Keeps selected products unique and valid.
    Used by store bundle create/edit and later cart/checkout bundle validation.
    """
    if values is None:
        values = []

    if isinstance(values, str):
        values = [values]

    cleaned = []
    seen = set()

    for raw in values:
        value = str(raw or "").strip()

        if not value or value in seen:
            continue

        if not ObjectId.is_valid(value):
            continue

        seen.add(value)
        cleaned.append(value)

    return cleaned

def build_bundle_item_snapshots(products, quantities_by_product_id=None):
    """
    Creates safe child-product snapshots for a product bundle.
    This does not mutate product stock and does not write to DB.
    """
    quantities_by_product_id = quantities_by_product_id or {}
    snapshots = []

    for product in products or []:
        if not product:
            continue

        product_id = product.get("_id") or product.get("id")
        product_id_str = _bundle_object_id_string(product_id)

        if not product_id_str:
            continue

        hydrate_product_unit_fields(product)

        quantity = _bundle_quantity_float(
            quantities_by_product_id.get(product_id_str)
            if product_id_str in quantities_by_product_id
            else product.get("bundle_quantity"),
            1.0
        )

        # Important:
        # Bundle base total must use the product's ORIGINAL/base price, not the
        # current product offer/final selling price. Bundle discounts are applied
        # separately at bundle level, so product-level discounts do not double-apply.
        original_price_per_unit = _bundle_money_float(
            product.get("original_price_per_unit")
            if product.get("original_price_per_unit") is not None
            else product.get("original_price")
            if product.get("original_price") is not None
            else product.get("mrp")
            if product.get("mrp") is not None
            else product_price_per_unit(product),
            0
        )

        current_selling_price = product_price_per_unit(product)

        line_total = original_price_per_unit * quantity
        current_line_total = current_selling_price * quantity

        snapshot = {
            "product_id": product_id if isinstance(product_id, ObjectId) else ObjectId(product_id_str),
            "product_id_str": product_id_str,
            "product_name_snapshot": product.get("name") or product.get("product_name") or "Product",
            "unit_type_snapshot": product.get("unit_type") or "WEIGHT",
            "unit_label_snapshot": product.get("unit_label") or "kg",
            "quantity": round(quantity, 3),

            # Original/base price used for bundle pricing.
            "price_per_unit_snapshot": round(original_price_per_unit, 2),
            "line_total_snapshot": round(line_total, 2),

            # Current product selling price kept only for display/reference.
            "current_price_per_unit_snapshot": round(current_selling_price, 2),
            "current_line_total_snapshot": round(current_line_total, 2),

            "stock_quantity_snapshot": round(product_stock_quantity(product), 3),
            "is_active_snapshot": int(product.get("is_active", 1) or 0),
        }

        snapshots.append(snapshot)

    return snapshots

def calculate_bundle_pricing(items, discount_type="none", discount_value=0, bundle_price=None):
    """
    Calculates bundle price and savings from child product snapshots.
    Discount is applied at bundle-line level only. Child products are not double-discounted here.
    """
    items_total = 0.0

    for item in items or []:
        items_total += _bundle_money_float(item.get("line_total_snapshot"), 0)

    discount_type = normalize_bundle_discount_type(discount_type)
    discount_value = _bundle_money_float(discount_value, 0)

    if discount_value < 0:
        discount_value = 0

    final_price = items_total
    discount_amount = 0.0
    discount_percent = 0.0

    if discount_type == "fixed_price":
        fixed_price = _bundle_money_float(bundle_price if bundle_price is not None else discount_value, 0)

        # If no manual final bundle price is entered, do not turn the bundle price into 0.
        # The bundle should keep the selected products' original total unless a real bundle offer is added.
        if fixed_price <= 0:
            discount_type = "none"
            discount_value = 0
            final_price = items_total
            discount_amount = 0.0
            discount_percent = 0.0
        else:
            if fixed_price > items_total and items_total > 0:
                fixed_price = items_total

            final_price = fixed_price
            discount_amount = max(items_total - final_price, 0)
            discount_percent = (discount_amount / items_total * 100) if items_total else 0

    elif discount_type == "percent" and items_total > 0:
        if discount_value > 100:
            discount_value = 100

        discount_percent = discount_value
        discount_amount = items_total * (discount_percent / 100)
        final_price = items_total - discount_amount

    elif discount_type == "amount" and items_total > 0:
        if discount_value > items_total:
            discount_value = items_total

        discount_amount = discount_value
        final_price = items_total - discount_amount
        discount_percent = (discount_amount / items_total * 100) if items_total else 0

    else:
        discount_type = "none"
        discount_value = 0
        final_price = items_total

    if final_price < 0:
        final_price = 0

    return {
        "items_total": round(items_total, 2),
        "bundle_price": round(final_price, 2),
        "discount_type": discount_type,
        "discount_value": round(discount_value, 2),
        "discount_amount": round(discount_amount, 2),
        "discount_percent": round(discount_percent, 2),
        "savings_amount": round(discount_amount, 2),
    }

def calculate_bundle_stock(items):
    """
    Bundle stock is limited by the least available child product stock.
    Example: if the bundle needs 2 kg potato and 1 kg onion, stock is min(potato/2, onion/1).
    """
    if not items:
        return {
            "max_bundle_stock": 0,
            "stock_status": "OUT_OF_STOCK",
            "stock_blockers": ["No products added to bundle."],
        }

    max_counts = []
    blockers = []

    for item in items:
        product_name = item.get("product_name_snapshot") or "Product"
        required_qty = _bundle_quantity_float(item.get("quantity"), 1)
        available_qty = _bundle_money_float(item.get("stock_quantity_snapshot"), 0)
        is_active = int(item.get("is_active_snapshot", 1) or 0) == 1

        if not is_active:
            blockers.append(f"{product_name} is inactive.")
            max_counts.append(0)
            continue

        if required_qty <= 0:
            blockers.append(f"{product_name} has invalid bundle quantity.")
            max_counts.append(0)
            continue

        possible = math.floor(available_qty / required_qty)

        if possible <= 0:
            blockers.append(f"{product_name} is out of stock for this bundle.")

        max_counts.append(max(possible, 0))

    max_bundle_stock = int(min(max_counts)) if max_counts else 0

    if max_bundle_stock <= 0:
        stock_status = "OUT_OF_STOCK"
    elif max_bundle_stock <= 5:
        stock_status = "LOW_STOCK"
    else:
        stock_status = "IN_STOCK"

    return {
        "max_bundle_stock": max_bundle_stock,
        "stock_status": stock_status,
        "stock_blockers": blockers,
    }

def build_live_product_bundle(bundle, notify_store=False, notification_context="product_bundle"):
    """
    Rebuilds bundle price and stock from current child product records.

    Customer-facing bundle display and cart validation must use live stock, not
    old stock snapshots saved when the bundle was created. If any bundle child
    product is inactive, missing, or out of stock for the required quantity, the
    bundle becomes unavailable for customers. When notify_store=True, the store
    gets one restock notification per bundle per day.
    """
    if not bundle:
        return None

    bundle = dict(bundle)
    original_items = bundle.get("items") or []
    product_ids = []
    quantities = {}
    missing_product_names = []

    for item in original_items:
        if not isinstance(item, dict):
            continue

        pid = str(item.get("product_id_str") or item.get("product_id") or "").strip()
        product_name = item.get("product_name_snapshot") or "Product"

        if not pid:
            missing_product_names.append(product_name)
            continue

        product_ids.append(pid)
        quantities[pid] = item.get("quantity") or 1

    product_ids = normalize_bundle_product_ids(product_ids)
    object_ids = [ObjectId(pid) for pid in product_ids if ObjectId.is_valid(pid)]

    products = list(mongo.products.find({"_id": {"$in": object_ids}})) if object_ids else []
    product_map = {str(p.get("_id")): p for p in products}
    ordered_products = []
    blockers = []

    for pid in product_ids:
        product = product_map.get(pid)

        if not product:
            blockers.append("A product in this bundle is missing or deleted.")
            continue

        ordered_products.append(product)

    for name in missing_product_names:
        blockers.append(f"{name} is missing from this bundle.")

    items = build_bundle_item_snapshots(
        ordered_products,
        quantities_by_product_id=quantities
    )

    pricing = calculate_bundle_pricing(
        items,
        discount_type=bundle.get("discount_type") or "none",
        discount_value=bundle.get("discount_value") or 0,
        bundle_price=bundle.get("bundle_price")
    )

    stock = calculate_bundle_stock(items)

    if blockers:
        stock["max_bundle_stock"] = 0
        stock["stock_status"] = "OUT_OF_STOCK"
        stock["stock_blockers"] = list(stock.get("stock_blockers") or []) + blockers

    bundle["items"] = items
    bundle.update(pricing)
    bundle.update(stock)

    if notify_store and int(bundle.get("max_bundle_stock") or 0) <= 0:
        notify_store_bundle_restock_needed(bundle, notification_context=notification_context)

    return bundle

def is_product_bundle_customer_available(bundle):
    """
    True only when the bundle should be visible/orderable for customers.
    """
    if not bundle:
        return False

    if int(bundle.get("is_deleted", 0) or 0) == 1:
        return False

    if int(bundle.get("is_active", 0) or 0) != 1:
        return False

    if int(bundle.get("max_bundle_stock") or 0) <= 0:
        return False

    if (bundle.get("stock_status") or "").upper() == "OUT_OF_STOCK":
        return False

    return True

def notify_store_bundle_restock_needed(bundle, notification_context="product_bundle"):
    """
    Creates a store notification when a customer-facing bundle becomes unavailable
    because one or more child products need restocking/reactivation.
    """
    try:
        store_id = bundle.get("store_id")
        store = None

        if store_id:
            store = mongo.stores.find_one({"_id": store_id})

        if not store and store_id:
            try:
                store = mongo.stores.find_one({"_id": ObjectId(str(store_id))})
            except Exception:
                store = None

        if not store and bundle.get("store_id_str"):
            try:
                store = mongo.stores.find_one({"_id": ObjectId(str(bundle.get("store_id_str")))})
            except Exception:
                store = None

        if not store:
            return None

        bundle_id = _bundle_object_id_string(bundle.get("_id")) or _bundle_object_id_string(bundle.get("id"))
        today_key = datetime.utcnow().date().isoformat()
        event_key = f"bundle-restock-{bundle_id}-{today_key}"

        blockers = bundle.get("stock_blockers") or []
        blocker_text = "; ".join([str(x) for x in blockers if x]) or "One or more products in this bundle need restocking."

        return _create_store_notification(
            store,
            title="Bundle restock needed",
            message=f"Bundle '{bundle.get('bundle_name') or 'Product Bundle'}' is hidden from customers because: {blocker_text}",
            notif_type="bundle_restock",
            order=None,
            event_key=event_key
        )
    except Exception:
        return None

def build_product_bundle_document(store, form, products, quantities_by_product_id=None, existing_bundle=None, image_path=None, actor=None):
    """
    Builds the DB document/update payload for a product bundle.
    Used by store bundle create/edit routes.
    """
    existing_bundle = existing_bundle or {}
    now = datetime.utcnow().isoformat()

    bundle_name = (
        form.get("bundle_name")
        or form.get("name")
        or existing_bundle.get("bundle_name")
        or ""
    ).strip()

    description = (form.get("description") or existing_bundle.get("description") or "").strip()

    # Product bundles are intentionally independent from category/sub-category.
    # A store can combine products from different categories in one bundle.
    category_id = ""
    category = ""
    sub_category = ""

    items = build_bundle_item_snapshots(products, quantities_by_product_id=quantities_by_product_id)

    discount_type = normalize_bundle_discount_type(form.get("discount_type") or existing_bundle.get("discount_type"))
    discount_value = _bundle_money_float(form.get("discount_value"), existing_bundle.get("discount_value", 0))
    fixed_bundle_price = form.get("bundle_price") if form.get("bundle_price") not in [None, ""] else existing_bundle.get("bundle_price")

    pricing = calculate_bundle_pricing(
        items,
        discount_type=discount_type,
        discount_value=discount_value,
        bundle_price=fixed_bundle_price,
    )

    stock = calculate_bundle_stock(items)

    active_raw = form.get("is_active")
    if active_raw is None:
        is_active = int(existing_bundle.get("is_active", 1) or 0)
    else:
        is_active = 1 if str(active_raw).strip().lower() in {"1", "true", "yes", "on", "active"} else 0

    doc = {
        "store_id": store.get("_id"),
        "store_id_str": str(store.get("_id")),
        "store_name": store.get("store_name") or store.get("name") or "",
        "bundle_name": bundle_name,
        "bundle_slug": re.sub(r"[^a-z0-9]+", "-", bundle_name.lower()).strip("-"),
        "description": description,
        "category_id": category_id,
        "category": category,
        "sub_category": sub_category,
        "items": items,
        **pricing,
        **stock,
        "is_active": is_active,
        "is_deleted": int(existing_bundle.get("is_deleted", 0) or 0),
        "start_date": (form.get("start_date") or existing_bundle.get("start_date") or "").strip(),
        "end_date": (form.get("end_date") or existing_bundle.get("end_date") or "").strip(),
        "updated_at": now,
    }

    if image_path is not None:
        doc["image_path"] = image_path
    elif existing_bundle.get("image_path"):
        doc["image_path"] = existing_bundle.get("image_path")
    else:
        doc["image_path"] = ""

    if actor:
        doc["updated_by"] = str(actor.get("_id") or actor.get("id") or "")
        doc["updated_by_name"] = actor.get("name") or "Store User"

    if not existing_bundle:
        doc["created_at"] = now
        if actor:
            doc["created_by"] = str(actor.get("_id") or actor.get("id") or "")
            doc["created_by_name"] = actor.get("name") or "Store User"

    return doc

def validate_product_bundle_for_cart(bundle, quantity=1):
    """
    Read-only cart/checkout validation. It does not reduce stock.
    """
    if not bundle:
        return False, "Bundle not found."

    if int(bundle.get("is_deleted", 0) or 0) == 1:
        return False, "This bundle is no longer available."

    if int(bundle.get("is_active", 0) or 0) != 1:
        return False, "This bundle is currently inactive."

    stock = calculate_bundle_stock(bundle.get("items") or [])
    max_stock = int(stock.get("max_bundle_stock") or 0)

    requested_qty = int(_bundle_quantity_float(quantity, 1))

    if requested_qty <= 0:
        requested_qty = 1

    if max_stock < requested_qty:
        return False, "Not enough stock available for this bundle."

    return True, ""

def build_bundle_cart_snapshot(bundle, quantity=1):
    """
    Builds a snapshot suitable for cart_items/order_items.
    """
    quantity = int(_bundle_quantity_float(quantity, 1))

    if quantity <= 0:
        quantity = 1

    bundle_price = _bundle_money_float(bundle.get("bundle_price"), 0)
    savings = _bundle_money_float(bundle.get("savings_amount"), 0)

    return {
        "item_type": "bundle",
        "bundle_id": bundle.get("_id"),
        "bundle_id_str": _bundle_object_id_string(bundle.get("_id")),
        "product_id": None,
        "bundle_name_snapshot": bundle.get("bundle_name") or "Product Bundle",
        "bundle_items_snapshot": bundle.get("items") or [],
        "cart_quantity": quantity,
        "quantity": quantity,
        "unit_type": "COUNT",
        "unit_label": "bundle",
        "price_per_unit_snapshot": round(bundle_price, 2),
        "bundle_price_snapshot": round(bundle_price, 2),
        "bundle_savings_snapshot": round(savings, 2),
        "line_total": round(bundle_price * quantity, 2),
    }
