"""Public routes extracted from the updated app.py.

NELOCALS SEARCH STORE RESULTS HIDDEN FINAL

Logic, decorators, endpoint names and route paths are intentionally preserved.
Only the file location changed.
"""

from app_core import *

import re
import requests
import math
from collections import defaultdict
from datetime import timedelta


# =========================================================
# HOMEPAGE MARKETPLACE RANKING HELPERS
# =========================================================

_HOME_NEW_WINDOW_DAYS = 14
_HOME_POPULAR_WINDOW_DAYS = 30
_HOME_TREND_WINDOW_DAYS = 7
_HOME_REVIEW_MIN_COUNT = 1


def _home_safe_float(value, default=0.0):
    try:
        if value is None or str(value).strip() == "":
            return float(default)
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _home_safe_int(value, default=0):
    try:
        return int(float(value if value is not None else default))
    except (TypeError, ValueError):
        return int(default)


def _home_metric_norm(value, maximum):
    value = max(_home_safe_float(value, 0.0), 0.0)
    maximum = max(_home_safe_float(maximum, 0.0), 0.0)

    if maximum <= 0:
        return 0.0

    # Log normalization prevents one historic/high-volume product from
    # completely dominating every other product.
    return math.log1p(value) / math.log1p(maximum)


def _home_product_publish_dt(product):
    """
    Stable freshness timestamp.

    Prefer an explicit first-publication/activation timestamp when a product
    has one. Fall back to created_at. Never use updated_at, because editing an
    old product must not make it "new" again.
    """
    for field in (
        "published_at",
        "first_published_at",
        "first_activated_at",
        "activated_at",
        "created_at",
    ):
        parsed = _parse_home_dt(product.get(field))
        if parsed:
            return parsed

    return None


def _home_order_item_product_movements(item):
    """
    Expand one order item into real product quantities.

    Normal items count the product itself.
    Bundle items count each child product multiplied by bundle quantity.
    This mirrors the stock-reservation meaning already used by checkout.
    """
    movements = []

    item_type = str(item.get("item_type") or "product").strip().lower()

    if item_type == "bundle" or item.get("bundle_id"):
        bundle_qty = _home_safe_float(
            item.get("quantity")
            if item.get("quantity") is not None
            else item.get("cart_quantity"),
            1.0
        )

        if bundle_qty <= 0:
            bundle_qty = 1.0

        for child in item.get("bundle_items_snapshot") or []:
            if not isinstance(child, dict):
                continue

            product_id = (
                child.get("product_id")
                or child.get("product_id_str")
            )

            child_qty = _home_safe_float(child.get("quantity"), 0.0) * bundle_qty

            if product_id and child_qty > 0:
                movements.append((str(product_id), child_qty))

        return movements

    product_id = item.get("product_id") or item.get("product_id_str")
    quantity = _home_safe_float(
        item.get("quantity")
        if item.get("quantity") is not None
        else item.get("cart_quantity"),
        0.0
    )

    if product_id and quantity > 0:
        movements.append((str(product_id), quantity))

    return movements


def _home_recent_commerce_metrics(product_ids, now_dt):
    """
    Build recent marketplace signals from existing live data.

    Purchase/order signals:
      - 7-day quantity
      - 30-day quantity
      - 30-day order count
      - 30-day unique buyers

    Cart signal:
      - currently retained cart rows touched in the last 30 days

    No new tracking collection is introduced here.
    """
    product_ids = {str(pid) for pid in product_ids if pid}

    metrics = {
        pid: {
            "sales_qty_7d": 0.0,
            "sales_qty_30d": 0.0,
            "orders_30d": set(),
            "buyers_30d": set(),
            "cart_intent_7d": 0,
            "cart_intent_30d": 0,
        }
        for pid in product_ids
    }

    if not product_ids:
        return metrics

    cutoff_30 = now_dt - timedelta(days=_HOME_POPULAR_WINDOW_DAYS)
    cutoff_7 = now_dt - timedelta(days=_HOME_TREND_WINDOW_DAYS)

    # Fetch commercially valid recent orders. Parsing is done in Python so
    # old string timestamps and datetime timestamps remain backward compatible.
    recent_orders = list(
        mongo.orders.find(
            {
                "status": {
                    "$nin": [
                        "CANCELLED",
                        "PENDING_PAYMENT",
                        "PAYMENT_PENDING",
                        "ONLINE_PENDING",
                    ]
                }
            },
            {
                "_id": 1,
                "user_id": 1,
                "created_at": 1,
                "status": 1,
            }
        ).sort("created_at", -1).limit(5000)
    )

    order_meta = {}

    for order in recent_orders:
        order_dt = _parse_home_dt(order.get("created_at"))

        if not order_dt or order_dt < cutoff_30:
            continue

        order_meta[str(order.get("_id"))] = {
            "order_id": order.get("_id"),
            "created_at": order_dt,
            "buyer_id": str(order.get("user_id") or ""),
        }

    if order_meta:
        order_ids = [
            row["order_id"]
            for row in order_meta.values()
            if row.get("order_id") is not None
        ]

        for item in mongo.order_items.find(
            {"order_id": {"$in": order_ids}},
            {
                "order_id": 1,
                "item_type": 1,
                "bundle_id": 1,
                "product_id": 1,
                "product_id_str": 1,
                "quantity": 1,
                "cart_quantity": 1,
                "bundle_items_snapshot": 1,
            }
        ):
            meta = order_meta.get(str(item.get("order_id")))

            if not meta:
                continue

            order_dt = meta["created_at"]
            buyer_id = meta["buyer_id"]
            order_id_key = str(item.get("order_id"))

            for product_id, quantity in _home_order_item_product_movements(item):
                if product_id not in metrics:
                    continue

                row = metrics[product_id]
                row["sales_qty_30d"] += quantity
                row["orders_30d"].add(order_id_key)

                if buyer_id:
                    row["buyers_30d"].add(buyer_id)

                if order_dt >= cutoff_7:
                    row["sales_qty_7d"] += quantity

    # Cart rows are an intent signal only. They are deliberately weighted below
    # purchases and unique buyers in the Popular score.
    for cart_item in mongo.cart_items.find(
        {},
        {
            "cart_id": 1,
            "item_type": 1,
            "bundle_id": 1,
            "product_id": 1,
            "product_id_str": 1,
            "quantity": 1,
            "cart_quantity": 1,
            "bundle_items_snapshot": 1,
            "created_at": 1,
            "updated_at": 1,
        }
    ):
        touched_dt = _parse_home_dt(
            cart_item.get("updated_at") or cart_item.get("created_at")
        )

        if not touched_dt or touched_dt < cutoff_30:
            continue

        # For a normal cart product, one retained cart row is one intent signal.
        # Bundle snapshots can also contribute intent to child products.
        movements = _home_order_item_product_movements(cart_item)

        for product_id, _quantity in movements:
            if product_id not in metrics:
                continue

            metrics[product_id]["cart_intent_30d"] += 1

            if touched_dt >= cutoff_7:
                metrics[product_id]["cart_intent_7d"] += 1

    for row in metrics.values():
        row["orders_30d"] = len(row["orders_30d"])
        row["buyers_30d"] = len(row["buyers_30d"])

    return metrics


def _home_rating_metrics(product_ids, now_dt):
    """
    Build product rating data once for the homepage and calculate a
    confidence-weighted/Bayesian rating.

    A product needs at least _HOME_REVIEW_MIN_COUNT ratings to qualify for
    Best Rated.
    """
    product_ids = {str(pid) for pid in product_ids if pid}

    ratings_by_product = {
        pid: {
            "rating_total": 0.0,
            "rating_count": 0,
            "recent_review_count": 0,
        }
        for pid in product_ids
    }

    if not product_ids:
        return ratings_by_product, 0.0

    object_ids = []

    for pid in product_ids:
        try:
            if ObjectId.is_valid(pid):
                object_ids.append(ObjectId(pid))
        except Exception:
            pass

    rating_query = {
        "$or": [
            {"product_id": {"$in": object_ids}},
            {"product_id": {"$in": list(product_ids)}},
        ]
    }

    recent_review_cutoff = now_dt - timedelta(days=90)

    marketplace_rating_total = 0.0
    marketplace_rating_count = 0

    for rating in mongo.product_ratings.find(
        rating_query,
        {
            "product_id": 1,
            "rating": 1,
            "created_at": 1,
        }
    ):
        product_id = str(rating.get("product_id") or "")

        if product_id not in ratings_by_product:
            continue

        value = _home_safe_float(rating.get("rating"), 0.0)

        if value <= 0:
            continue

        row = ratings_by_product[product_id]
        row["rating_total"] += value
        row["rating_count"] += 1

        marketplace_rating_total += value
        marketplace_rating_count += 1

        review_dt = _parse_home_dt(rating.get("created_at"))

        if review_dt and review_dt >= recent_review_cutoff:
            row["recent_review_count"] += 1

    marketplace_average = (
        marketplace_rating_total / marketplace_rating_count
        if marketplace_rating_count > 0
        else 0.0
    )

    for row in ratings_by_product.values():
        count = row["rating_count"]

        row["avg_rating"] = (
            row["rating_total"] / count
            if count > 0
            else 0.0
        )

        if count > 0 and marketplace_average > 0:
            minimum_confidence = float(_HOME_REVIEW_MIN_COUNT)

            row["weighted_rating"] = (
                (count / (count + minimum_confidence)) * row["avg_rating"]
                + (minimum_confidence / (count + minimum_confidence))
                * marketplace_average
            )
        else:
            row["weighted_rating"] = 0.0

    return ratings_by_product, marketplace_average



def _home_recent_viewer_key():
    """
    Read the current shopper identity without creating a guest history token.

    Guest tokens are created only when a product is actually viewed.
    """
    user = current_user()

    if user:
        if str(user.get("role") or "").strip().lower() != "customer":
            return ""

        user_id = str(user.get("id") or user.get("_id") or "").strip()

        return f"user:{user_id}" if user_id else ""

    token = str(session.get("_recent_product_viewer_key") or "").strip()

    return f"guest:{token}" if token else ""


def _home_recently_viewed_products(products, now_dt, limit=10):
    """
    Resolve recent-view events back to the current live homepage product pool.

    This automatically removes stale/deactivated products and products from
    inactive stores because those products are not present in `products`.
    Out-of-stock products remain eligible because the homepage intentionally
    keeps active unavailable products visible.
    """
    viewer_key = _home_recent_viewer_key()

    if not viewer_key:
        return []

    cutoff_dt = now_dt - timedelta(days=30)

    recent_events = list(
        mongo.product_view_events.find(
            {
                "viewer_key": viewer_key,
                "last_viewed_at": {"$gte": cutoff_dt}
            },
            {
                "product_id": 1,
                "product_id_str": 1,
                "last_viewed_at": 1
            }
        ).sort("last_viewed_at", -1).limit(30)
    )

    if not recent_events:
        return []

    product_map = {
        str(product.get("_id")): product
        for product in products
        if product.get("_id")
    }

    selected = []
    selected_ids = set()

    for event in recent_events:
        product_id = str(
            event.get("product_id_str")
            or event.get("product_id")
            or ""
        ).strip()

        if not product_id or product_id in selected_ids:
            continue

        product = product_map.get(product_id)

        if not product:
            continue

        selected.append(product)
        selected_ids.add(product_id)

        if len(selected) >= limit:
            break

    return selected


def _home_soft_unique(primary_rows, already_used_ids, limit=10):
    """
    Prefer not to repeat cards already used by an earlier homepage section,
    but never sacrifice the section completely when the catalogue is small.
    """
    selected = []
    selected_ids = set()

    for product in primary_rows:
        product_id = str(product.get("_id") or product.get("id") or "")

        if not product_id or product_id in already_used_ids:
            continue

        selected.append(product)
        selected_ids.add(product_id)

        if len(selected) >= limit:
            return selected

    if len(selected) < limit:
        for product in primary_rows:
            product_id = str(product.get("_id") or product.get("id") or "")

            if not product_id or product_id in selected_ids:
                continue

            selected.append(product)
            selected_ids.add(product_id)

            if len(selected) >= limit:
                break

    return selected


@app.route('/')
def index():
    user = current_user()
    allow, pin = _session_pin_is_serviceable()

    products = []
    latest_products = []
    new_products = []
    popular_products = []
    discount_products = []
    featured_products = []
    best_rated_products = []
    recently_viewed_products = []
    stores = []
    recommended_stores = []
    new_stores = []
    categories = []
    product_rating_map = {}
    store_rating_map = {}
    cart_lookup = {}

    if session.get("service_area") and not allow:
        flash("Please enter a valid 6-digit pincode.", "warning")
    else:
        # Homepage ranking pool:
        # - active products only
        # - out-of-stock / below-minimum-stock products remain visible
        # - orderability is decided from stock_quantity >= quantity_min
        # - no "latest 80" ranking limitation
        products = list(mongo.products.find({
            "is_active": 1
        }).sort("created_at", -1))

        # Exclude products belonging to a disabled/inactive store.
        # Products without a store_id are preserved for backward compatibility.
        active_store_ids = {
            str(store_id)
            for store_id in mongo.stores.distinct("_id", {"is_active": 1})
        }

        products = [
            product
            for product in products
            if not product.get("store_id")
            or str(product.get("store_id")) in active_store_ids
        ]

        # Homepage cart lookup for customer users
        # This lets homepage product cards show:
        # Added to Cart / Plus / Minus / Remove
        if user and user.get("role") == "customer":
            cid = get_or_create_cart(user["id"])

            cart_items = list(mongo.cart_items.find({
                "cart_id": cid
            }))

            for ci in cart_items:
                product_id = ci.get("product_id")

                if product_id:
                    cart_lookup[str(product_id)] = {
                        "cart_item_id": str(ci["_id"]),
                        "cart_quantity": cart_item_quantity(ci)
                    }

        now_dt = datetime.utcnow()

        product_ids = {
            str(product.get("_id"))
            for product in products
            if product.get("_id")
        }

        commerce_metrics = _home_recent_commerce_metrics(
            product_ids,
            now_dt
        )

        rating_metrics, marketplace_average_rating = _home_rating_metrics(
            product_ids,
            now_dt
        )

        # Apply rating data and stable display fields before ranking.
        # Existing _hydrate_home_product is kept for current price/store/etc.
        # behavior, while the authoritative rating values are replaced with
        # the single-pass metrics calculated above.
        for p in products:
            hydrate_product_unit_fields(p)
            _hydrate_home_product(p)

            product_id = str(p.get("_id"))
            rating_row = rating_metrics.get(product_id) or {}
            cart_row = cart_lookup.get(product_id)

            p["avg_rating"] = round(
                _home_safe_float(rating_row.get("avg_rating"), 0.0),
                1
            )
            p["rating_count"] = _home_safe_int(
                rating_row.get("rating_count"),
                0
            )
            p["weighted_rating"] = _home_safe_float(
                rating_row.get("weighted_rating"),
                0.0
            )
            p["recent_review_count"] = _home_safe_int(
                rating_row.get("recent_review_count"),
                0
            )

            p["in_cart"] = bool(cart_row)
            p["cart_item_id"] = cart_row.get("cart_item_id") if cart_row else ""
            p["cart_quantity"] = cart_row.get("cart_quantity") if cart_row else 0

            product_rating_map[p["id"]] = {
                "avg": p.get("avg_rating", 0),
                "count": p.get("rating_count", 0)
            }

        # ---------------------------------------------------------
        # RECENTLY VIEWED PRODUCTS
        # Personal, unique, newest-view-first, 30-day history.
        # ---------------------------------------------------------
        recently_viewed_products = _home_recently_viewed_products(
            products,
            now_dt,
            limit=10
        )

        # ---------------------------------------------------------
        # Shared metric maxima for fair/log-normalized scoring.
        # ---------------------------------------------------------
        max_sales_7d = max(
            [
                _home_safe_float(row.get("sales_qty_7d"), 0.0)
                for row in commerce_metrics.values()
            ] or [0.0]
        )
        max_sales_30d = max(
            [
                _home_safe_float(row.get("sales_qty_30d"), 0.0)
                for row in commerce_metrics.values()
            ] or [0.0]
        )
        max_orders_30d = max(
            [
                _home_safe_float(row.get("orders_30d"), 0.0)
                for row in commerce_metrics.values()
            ] or [0.0]
        )
        max_buyers_30d = max(
            [
                _home_safe_float(row.get("buyers_30d"), 0.0)
                for row in commerce_metrics.values()
            ] or [0.0]
        )
        max_cart_30d = max(
            [
                _home_safe_float(row.get("cart_intent_30d"), 0.0)
                for row in commerce_metrics.values()
            ] or [0.0]
        )
        max_cart_7d = max(
            [
                _home_safe_float(row.get("cart_intent_7d"), 0.0)
                for row in commerce_metrics.values()
            ] or [0.0]
        )

        # ---------------------------------------------------------
        # 1) NEW ON NELOCALS
        #
        # Rolling latest-arrivals logic:
        # - products published within the last 14 days are genuine "new"
        # - newest publication always ranks first
        # - if fewer than 10 genuine-new products exist, fill the remaining
        #   slots with the most recently published older products
        # - therefore the section does not become blank just because stores
        #   have not published anything during the last 14 days
        # - every newly published product enters at the front and gradually
        #   pushes the oldest visible arrival out of the 10-card window
        #
        # updated_at is never used, so editing an old product cannot make it
        # a new arrival again.
        # ---------------------------------------------------------
        new_cutoff = now_dt - timedelta(days=_HOME_NEW_WINDOW_DAYS)

        all_arrivals_ranked = [
            p for p in products
            if _home_product_publish_dt(p) is not None
        ]

        for p in all_arrivals_ranked:
            published_dt = _home_product_publish_dt(p)

            p["_home_published_at"] = published_dt
            p["_home_is_fresh_new"] = bool(
                published_dt
                and new_cutoff <= published_dt <= now_dt
            )

        all_arrivals_ranked.sort(
            key=lambda p: (
                p.get("_home_published_at") or datetime.min,
                str(p.get("_id") or "")
            ),
            reverse=True
        )

        fresh_new_products = [
            p for p in all_arrivals_ranked
            if p.get("_home_is_fresh_new")
        ]

        older_recent_arrivals = [
            p for p in all_arrivals_ranked
            if not p.get("_home_is_fresh_new")
        ]

        new_products = fresh_new_products[:10]

        if len(new_products) < 10:
            needed = 10 - len(new_products)
            new_products.extend(
                older_recent_arrivals[:needed]
            )

        # ---------------------------------------------------------
        # 2) POPULAR ITEMS NEARBY
        #
        # Current service-area gate still comes from
        # _session_pin_is_serviceable(). Within that valid catalogue,
        # rank current buying momentum instead of lifetime sales:
        #
        #   35% 7-day quantity sold
        #   20% 30-day quantity sold
        #   15% unique 30-day buyers
        #   10% 30-day order count
        #   10% recent cart intent
        #   10% confidence-weighted rating
        #
        # The 7-day signal makes trends decay naturally.
        # ---------------------------------------------------------
        popular_ranked = []

        for p in products:
            product_id = str(p.get("_id"))
            commerce = commerce_metrics.get(product_id) or {}
            rating_row = rating_metrics.get(product_id) or {}

            rating_component = (
                min(
                    max(
                        _home_safe_float(
                            rating_row.get("weighted_rating"),
                            0.0
                        ) / 5.0,
                        0.0
                    ),
                    1.0
                )
                if _home_safe_int(rating_row.get("rating_count"), 0) > 0
                else 0.0
            )

            popularity_score = (
                0.35 * _home_metric_norm(
                    commerce.get("sales_qty_7d"),
                    max_sales_7d
                )
                + 0.20 * _home_metric_norm(
                    commerce.get("sales_qty_30d"),
                    max_sales_30d
                )
                + 0.15 * _home_metric_norm(
                    commerce.get("buyers_30d"),
                    max_buyers_30d
                )
                + 0.10 * _home_metric_norm(
                    commerce.get("orders_30d"),
                    max_orders_30d
                )
                + 0.10 * _home_metric_norm(
                    commerce.get("cart_intent_30d"),
                    max_cart_30d
                )
                + 0.10 * rating_component
            )

            p["_home_popularity_score"] = popularity_score
            p["_home_recent_sales_7d"] = _home_safe_float(
                commerce.get("sales_qty_7d"),
                0.0
            )
            p["_home_recent_sales_30d"] = _home_safe_float(
                commerce.get("sales_qty_30d"),
                0.0
            )

            popular_ranked.append(p)

        popular_ranked.sort(
            key=lambda p: (
                _home_safe_float(
                    p.get("_home_popularity_score"),
                    0.0
                ),
                _home_safe_float(
                    p.get("_home_recent_sales_7d"),
                    0.0
                ),
                _home_safe_float(
                    p.get("_home_recent_sales_30d"),
                    0.0
                ),
                _home_safe_float(
                    p.get("weighted_rating"),
                    0.0
                ),
                _home_product_publish_dt(p) or datetime.min,
            ),
            reverse=True
        )

        used_new_ids = {
            str(p.get("_id"))
            for p in new_products
            if p.get("_id")
        }

        popular_products = _home_soft_unique(
            popular_ranked,
            used_new_ids,
            limit=10
        )

        # ---------------------------------------------------------
        # Existing discount section logic remains unchanged.
        # ---------------------------------------------------------
        discount_products = [
            p for p in products
            if bool(p.get("discount_enabled"))
            and float(p.get("discount_amount_per_unit") or 0) > 0
        ]

        discount_products = sorted(
            discount_products,
            key=lambda x: (
                float(x.get("discount_percent") or 0),
                float(x.get("discount_amount_per_unit") or 0)
            ),
            reverse=True
        )[:10]

        # ---------------------------------------------------------
        # 3) BEST RATED PRODUCTS
        #
        # Best Rated is rating-first, but still confidence-aware:
        # - product must have at least 3 real ratings
        # - confidence-weighted/Bayesian rating is primary
        # - raw average star rating is the first tie-breaker
        # - then rating count, recent ratings and recent purchases
        #
        # This prevents a single 5-star rating from automatically outranking
        # a product with a consistently excellent rating from many customers.
        # ---------------------------------------------------------
        best_rated_ranked = [
            p for p in products
            if _home_safe_int(p.get("rating_count"), 0)
            >= _HOME_REVIEW_MIN_COUNT
            and _home_safe_float(p.get("avg_rating"), 0.0) > 0
        ]

        best_rated_ranked.sort(
            key=lambda p: (
                _home_safe_float(
                    p.get("weighted_rating"),
                    0.0
                ),
                _home_safe_float(
                    p.get("avg_rating"),
                    0.0
                ),
                math.log1p(
                    max(_home_safe_int(p.get("rating_count"), 0), 0)
                ),
                _home_safe_int(
                    p.get("recent_review_count"),
                    0
                ),
                _home_safe_float(
                    (commerce_metrics.get(str(p.get("_id"))) or {}).get(
                        "sales_qty_30d"
                    ),
                    0.0
                ),
                _home_product_publish_dt(p) or datetime.min,
            ),
            reverse=True
        )

        used_new_and_popular_ids = used_new_ids | {
            str(p.get("_id"))
            for p in popular_products
            if p.get("_id")
        }

        best_rated_products = _home_soft_unique(
            best_rated_ranked,
            used_new_and_popular_ids,
            limit=10
        )

        # Preserve this existing variable for backward compatibility with any
        # other template/page code, but Best Rated no longer depends on it.
        latest_products = sorted(
            products,
            key=lambda p: _home_product_publish_dt(p) or datetime.min,
            reverse=True
        )[:10]

        featured_products = (
            popular_products[:10]
            if popular_products
            else latest_products[:10]
        )

        # Real-time homepage categories from store_categories collection
        # Disabled categories must NOT appear again through products.
        category_map = {}
        active_category_names = set()
        active_category_store_keys = set()
        active_global_category_names = set()
        disabled_category_names = set()
        disabled_category_store_keys = set()

        all_store_categories = list(
            mongo.store_categories.find({}).sort("name", 1)
        )

        for cat in all_store_categories:
            cat_name = (cat.get("name") or "").strip()

            if not cat_name:
                continue

            cat_key = cat_name.lower()
            store_id_str = str(cat.get("store_id")) if cat.get("store_id") else ""

            raw_active = cat.get("is_active", None)

            is_active_category = raw_active in [1, True, "1", "true", "True"]

            if not is_active_category:
                disabled_category_names.add(cat_key)

                if store_id_str:
                    disabled_category_store_keys.add((store_id_str, cat_key))

                continue

            active_category_names.add(cat_key)

            if store_id_str:
                active_category_store_keys.add((store_id_str, cat_key))
            else:
                active_global_category_names.add(cat_key)

            category_image_path = (
                cat.get("category_image_path")
                or cat.get("image_path")
                or cat.get("icon_path")
                or ""
            )

            if cat_key not in category_map:
                category_map[cat_key] = {
                    "id": str(cat.get("_id")),
                    "name": cat_name,
                    "count": 0,
                    "emoji": cat.get("emoji") or cat.get("icon") or "🛒",
                    "image_path": category_image_path,
                    "category_image_path": category_image_path,
                    "store_id": store_id_str,
                    "sub_categories": cat.get("sub_categories") or [],
                    "is_active": 1
                }
            else:
                if category_image_path and not category_map[cat_key].get("category_image_path"):
                    category_map[cat_key]["image_path"] = category_image_path
                    category_map[cat_key]["category_image_path"] = category_image_path

        # Count only products whose category is still active.
        # Important: Do NOT recreate disabled categories from products.
        for p in products:
            cat_name = (p.get("category") or "").strip()

            if not cat_name:
                continue

            cat_key = cat_name.lower()
            product_store_id = str(p.get("store_id")) if p.get("store_id") else ""

            if cat_key in disabled_category_names:
                continue

            if product_store_id and (product_store_id, cat_key) in disabled_category_store_keys:
                continue

            category_is_active_for_product = False

            if product_store_id and (product_store_id, cat_key) in active_category_store_keys:
                category_is_active_for_product = True
            elif cat_key in active_global_category_names:
                category_is_active_for_product = True
            elif not product_store_id and cat_key in active_category_names:
                category_is_active_for_product = True

            if not category_is_active_for_product:
                continue

            if cat_key in category_map:
                category_map[cat_key]["count"] += 1

        categories = sorted(
            [
                c for c in category_map.values()
                if int(c.get("count") or 0) > 0
            ],
            key=lambda x: x["name"].lower()
        )

        stores = list(mongo.stores.find({
            "is_active": 1
        }).sort("created_at", -1).limit(30))

        for s in stores:
            s["id"] = str(s["_id"])
            s["store_name"] = s.get("store_name", "Store")
            s["address"] = s.get("address", "")
            s["logo_path"] = s.get("logo_path", "")
            s["banner_path"] = s.get("banner_path", "")
            s["profile_intro"] = (
                s.get("profile_intro")
                or s.get("description")
                or "Fresh groceries and daily essentials from this store."
            ).strip()
            s["description"] = (s.get("description") or "").strip()
            s["is_open"] = int(s.get("is_open", 1))
            s["created_at"] = s.get("created_at", "")

            s["product_count"] = mongo.products.count_documents({
                "store_id": s["_id"],
                "is_active": 1
            })

            store_avg_rating, store_rating_count = _home_store_rating_summary(s["_id"])

            s["avg_rating"] = store_avg_rating
            s["rating_count"] = store_rating_count

            store_rating_map[s["id"]] = {
                "avg": store_avg_rating,
                "count": store_rating_count
            }

        recommended_stores = sorted(
            stores,
            key=lambda x: (
                float(x.get("avg_rating") or 0),
                int(x.get("rating_count") or 0),
                int(x.get("product_count") or 0)
            ),
            reverse=True
        )[:10]

        new_stores = stores[:10]

    return render_template(
        'index.html',
        user=user,
        products=products,
        latest_products=latest_products,
        new_products=new_products,
        popular_products=popular_products,
        discount_products=discount_products,
        featured_products=featured_products,
        best_rated_products=best_rated_products,
        recently_viewed_products=recently_viewed_products,
        categories=categories,
        stores=stores,
        recommended_stores=recommended_stores,
        new_stores=new_stores,
        product_rating_map=product_rating_map,
        store_rating_map=store_rating_map
    )

def _public_notification_priority(value):
    priority = (value or "medium").strip().lower()

    if priority not in ["high", "medium", "low"]:
        priority = "medium"

    return priority


def _public_notification_priority_rank(priority):
    priority = _public_notification_priority(priority)

    if priority == "high":
        return 1

    if priority == "medium":
        return 2

    return 3


@app.route("/api/homepage/notifications", methods=["GET"], endpoint="api_homepage_notifications")
def api_homepage_notifications():
    notifications = list(
        mongo.homepage_notifications.find({
            "is_active": 1,
            "show_ticker": 1,
            "$or": [
                {"display_location": "homepage"},
                {"display_location": "all"},
                {"display_location": {"$exists": False}}
            ]
        }).sort([
            ("priority_rank", 1),
            ("created_at", -1)
        ]).limit(20)
    )

    items = []

    for n in notifications:
        priority = _public_notification_priority(n.get("priority"))

        items.append({
            "id": str(n.get("_id")),
            "title": n.get("title", ""),
            "message": n.get("message", ""),
            "priority": priority,
            "priority_rank": _public_notification_priority_rank(priority),
            "link_url": n.get("link_url", ""),
            "show_popup": int(n.get("show_popup", 0) or 0),
            "created_at": n.get("created_at", "")
        })

    return jsonify({
        "ok": True,
        "count": len(items),
        "notifications": items
    })

@app.route('/legal/privacy')
def legal_privacy():
    return render_template('legal/privacy.html', user=current_user())

@app.route('/legal/security')
def legal_security():
    return render_template('legal/security.html', user=current_user())

@app.route('/legal/terms')
def legal_terms():
    return render_template('legal/terms.html', user=current_user())

@app.route('/help')
def legal_help():
    return render_template('legal/help.html', user=current_user())

@app.route('/report-fraud')
def legal_report_fraud():
    return render_template('legal/report_fraud.html', user=current_user())

@app.route('/about')
def about():
    """
    About Us page for NELOCALS marketplace.
    """
    company_info = {
        "name": "NELOCALS",
        "year": 2026,
        "location": "Northeast India",
        "fssai": "",
        "phone": "",
        "website": "",
        "supported_by": "Ayanant Ventures Pvt. Ltd.",
    }

    u = current_user()
    cart_count = 0

    if u:
        cid = get_or_create_cart(u["id"])
        cart_count = mongo.cart_items.count_documents({"cart_id": cid})

    return render_template(
        "about.html",
        info=company_info,
        user=u,
        cart_count=cart_count
    )

@app.route("/search")
def search():
    q = (request.args.get("q", "") or "").strip()
    user = current_user()

    products = []
    stores = []

    if q:
        products = list(
            mongo.products.find({
                "is_active": 1,
                "stock_quantity": {"$gt": 0},
                "$or": [
                    {"name": {"$regex": q, "$options": "i"}},
                    {"category": {"$regex": q, "$options": "i"}},
                    {"sub_category": {"$regex": q, "$options": "i"}},
                    # Store-name matching is hidden for customer/public navbar search for now.
                    # {"store_name": {"$regex": q, "$options": "i"}},
                ]
            }).sort("created_at", -1).limit(50)
        )

        for p in products:
            p["id"] = str(p["_id"])

            # Store lookup/display is hidden in customer/public navbar search for now.
            # Kept here commented for future re-enable without deleting old logic.
            # store = None
            # if p.get("store_id"):
            #     store = mongo.stores.find_one({"_id": p["store_id"]})
            #
            # p["store_name"] = store.get("store_name") if store else p.get("store_name", "")
            # p["store_id"] = str(p.get("store_id")) if p.get("store_id") else ""
            p["store_name"] = ""
            p["store_id"] = str(p.get("store_id")) if p.get("store_id") else ""

        # Store result search is hidden in customer/public navbar search for now.
        # Kept commented, not deleted, so Admin/store search logic can be restored later if needed.
        # stores = list(
        #     mongo.stores.find({
        #         "$or": [
        #             {"store_name": {"$regex": q, "$options": "i"}},
        #             {"address": {"$regex": q, "$options": "i"}},
        #         ]
        #     }).sort("store_name", 1).limit(30)
        # )
        #
        # for s in stores:
        #     s["id"] = str(s["_id"])
        #     s["product_count"] = mongo.products.count_documents({
        #         "store_id": s["_id"],
        #         "is_active": 1,
        #         "stock_quantity": {"$gt": 0}
        #     })
        stores = []

    return render_template("search.html", user=user, q=q, products=products, stores=stores)

NEWSLETTER_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]{2,}$")
BREVO_CONTACTS_API_URL = "https://api.brevo.com/v3/contacts"


def _newsletter_now():
    return datetime.utcnow().isoformat()


def _get_brevo_newsletter_config():
    api_key = (os.getenv("BREVO_API_KEY") or "").strip()
    list_id_raw = (os.getenv("BREVO_NEWSLETTER_LIST_ID") or "").strip()

    if not api_key:
        return None, None, "BREVO_API_KEY is missing in .env"

    if not list_id_raw:
        return None, None, "BREVO_NEWSLETTER_LIST_ID is missing in .env"

    try:
        list_id = int(list_id_raw)
    except ValueError:
        return None, None, "BREVO_NEWSLETTER_LIST_ID must be a number"

    return api_key, list_id, None


def _sync_newsletter_email_to_brevo(email):
    api_key, list_id, config_error = _get_brevo_newsletter_config()

    if config_error:
        return {
            "ok": False,
            "status_code": 500,
            "message": config_error,
            "brevo_response": None
        }

    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "api-key": api_key
    }

    payload = {
        "email": email,
        "listIds": [list_id],
        "updateEnabled": True
    }

    try:
        response = requests.post(
            BREVO_CONTACTS_API_URL,
            headers=headers,
            json=payload,
            timeout=15
        )

        try:
            brevo_response = response.json()
        except Exception:
            brevo_response = {
                "raw": response.text
            }

        if response.status_code in [200, 201, 204]:
            return {
                "ok": True,
                "status_code": response.status_code,
                "message": "Synced with Brevo.",
                "brevo_response": brevo_response
            }

        return {
            "ok": False,
            "status_code": response.status_code,
            "message": "Brevo rejected the newsletter subscription.",
            "brevo_response": brevo_response
        }

    except requests.RequestException as e:
        return {
            "ok": False,
            "status_code": 502,
            "message": str(e),
            "brevo_response": None
        }


@app.route('/newsletter/subscribe', methods=['POST'])
def newsletter_subscribe():
    data = request.get_json(silent=True) or {}

    email = (
        data.get("email")
        or request.form.get("email")
        or ""
    )

    email = str(email).strip().lower()

    wants_json = (
        request.is_json
        or "application/json" in str(request.headers.get("Accept", ""))
    )

    def fail_response(message, status_code=400):
        if wants_json:
            return jsonify({
                "ok": False,
                "message": message
            }), status_code

        flash(message, "danger")
        return redirect(request.referrer or url_for("index"))

    def success_response(message, status_code=200):
        if wants_json:
            return jsonify({
                "ok": True,
                "message": message
            }), status_code

        flash(message, "success")
        return redirect(request.referrer or url_for("index"))

    if not email:
        return fail_response("Please enter your email address.", 400)

    if not NEWSLETTER_EMAIL_RE.match(email):
        return fail_response("Please enter a valid email address.", 400)

    now = _newsletter_now()

    try:
        mongo.newsletter_subscribers.create_index(
            "email",
            unique=True
        )

        existing = mongo.newsletter_subscribers.find_one({
            "email": email
        })

        if existing and existing.get("brevo_synced") is True:
            mongo.newsletter_subscribers.update_one(
                {
                    "email": email
                },
                {
                    "$set": {
                        "is_active": True,
                        "source": "footer",
                        "updated_at": now,
                        "last_subscribed_at": now
                    }
                }
            )

            return success_response("You are already subscribed.", 200)

        mongo.newsletter_subscribers.update_one(
            {
                "email": email
            },
            {
                "$set": {
                    "email": email,
                    "source": "footer",
                    "is_active": True,
                    "brevo_synced": False,
                    "brevo_status": "pending",
                    "updated_at": now,
                    "last_subscribed_at": now
                },
                "$setOnInsert": {
                    "created_at": now
                }
            },
            upsert=True
        )

        brevo_result = _sync_newsletter_email_to_brevo(email)

        if not brevo_result.get("ok"):
            mongo.newsletter_subscribers.update_one(
                {
                    "email": email
                },
                {
                    "$set": {
                        "brevo_synced": False,
                        "brevo_status": "failed",
                        "brevo_status_code": brevo_result.get("status_code"),
                        "brevo_error": brevo_result.get("message"),
                        "brevo_response": brevo_result.get("brevo_response"),
                        "updated_at": now
                    }
                }
            )

            print("[NEWSLETTER BREVO ERROR]", brevo_result)

            return fail_response(
                "Could not subscribe right now. Please try again.",
                502
            )

        mongo.newsletter_subscribers.update_one(
            {
                "email": email
            },
            {
                "$set": {
                    "brevo_synced": True,
                    "brevo_status": "synced",
                    "brevo_status_code": brevo_result.get("status_code"),
                    "brevo_response": brevo_result.get("brevo_response"),
                    "updated_at": now
                },
                "$unset": {
                    "brevo_error": ""
                }
            }
        )

        return success_response(
            "Subscribed! You’ll receive fresh updates soon.",
            201
        )

    except Exception as e:
        print("[NEWSLETTER ERROR]", str(e))

        return fail_response(
            "Something went wrong. Please try again.",
            500
        )

@app.route('/uploads/<path:fn>')
def uploaded_file(fn):
    if '..' in fn or fn.startswith('/'):
        return abort(404)
    full = os.path.join(app.config['UPLOAD_FOLDER'], fn)
    if not os.path.isfile(full):
        return abort(404)
    return send_file(full)

@app.route('/__routes')
def __routes():
    if not is_debug_logging_enabled():
        abort(404)

    return "<pre>" + "\n".join(
        f"{r.endpoint:30} {r.methods} {r}"
        for r in app.url_map.iter_rules()
    ) + "</pre>"

@app.route("/contact", methods=["GET", "POST"])
def contact():
    user = current_user()

    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        email = (request.form.get("email") or "").strip().lower()
        phone = (request.form.get("phone") or "").strip()
        subject = (request.form.get("subject") or "").strip()
        message = (request.form.get("message") or "").strip()

        source = (request.form.get("source") or "Contact Page").strip()
        recipient_type = (request.form.get("recipient_type") or "admin").strip().lower()
        page_context = (request.form.get("page_context") or "").strip()

        if not name or not email or not subject or not message:
            flash("Please fill all required contact form fields.", "warning")
            return redirect(url_for("contact"))

        if "@" not in email or "." not in email:
            flash("Please enter a valid email address.", "warning")
            return redirect(url_for("contact"))

        if len(message) < 10:
            flash("Please enter a message with at least 10 characters.", "warning")
            return redirect(url_for("contact"))

        now = datetime.utcnow().isoformat()

        contact_doc = {
            "name": name,
            "email": email,
            "phone": phone,
            "subject": subject,
            "message": message,

            "source": source,
            "recipient_type": recipient_type,
            "page_context": page_context,

            "status": "NEW",
            "priority": "NORMAL",

            "user_id": str(user.get("_id") or user.get("id") or "") if user else "",
            "user_role": user.get("role") if user else "guest",

            "ip_address": request.headers.get("X-Forwarded-For", request.remote_addr),
            "user_agent": request.headers.get("User-Agent", ""),

            "created_at": now,
            "updated_at": now,
            "read_at": "",
            "resolved_at": "",
            "admin_note": ""
        }

        insert_result = mongo.contact_messages.insert_one(contact_doc)

        # Automatic acknowledgement email is sent immediately only when
        # Admin has enabled the contact auto-acknowledgement switch.
        auto_reply_result = send_contact_auto_reply(contact_doc)
        auto_reply_now = datetime.utcnow().isoformat()
        auto_reply_enabled_now = bool(auto_reply_result.get("enabled"))

        mongo.contact_messages.update_one(
            {"_id": insert_result.inserted_id},
            {
                "$set": {
                    "auto_reply_enabled_at_submit": auto_reply_enabled_now,
                    "auto_reply_sent": bool(auto_reply_result.get("sent")),
                    "auto_reply_error": auto_reply_result.get("error") or "",
                    "auto_reply_sent_at": auto_reply_now if auto_reply_result.get("sent") else "",

                    # Snapshot of the auto-template used at submit time.
                    # This is blank when auto acknowledgement is disabled.
                    "auto_reply_subject_snapshot": auto_reply_result.get("subject") or "",
                    "auto_reply_body_snapshot": auto_reply_result.get("body") or "",

                    "manual_reply_sent": False,
                    "manual_reply_sent_at": "",
                    "last_manual_reply_subject": "",
                    "last_manual_reply_message": "",
                    "reply_logs": [
                        {
                            "type": "AUTO_ACKNOWLEDGEMENT_ON_SUBMIT" if auto_reply_enabled_now else "AUTO_ACKNOWLEDGEMENT_DISABLED_ON_SUBMIT",
                            "sent": bool(auto_reply_result.get("sent")),
                            "error": auto_reply_result.get("error") or "",
                            "subject": auto_reply_result.get("subject") or "",
                            "created_at": auto_reply_now
                        }
                    ]
                }
            }
        )

        flash("Message submitted successfully. NELOCALS admin will contact you soon.", "success")
        return redirect(url_for("contact"))

    return render_template("contact.html", user=user)



@app.route("/help")
def help_page():
    return render_template("legal/help.html")


@app.route("/privacy")
def privacy_page():
    return render_template("legal/privacy.html")


@app.route("/report-fraud")
def report_fraud_page():
    return render_template("legal/report_fraud.html")


@app.route("/security")
def security_page():
    return render_template("legal/security.html")


@app.route("/support")
def support_page():
    return render_template("legal/support.html")


@app.route("/terms")
def terms_page():
    return render_template("legal/terms.html")
