"""Order lifecycle and visibility rules extracted during Step 5.

This module contains state/visibility decisions only.  Payment settlement and
refund calculations remain in their existing routes until the dedicated
finance extraction stage.
"""

CANCELLABLE_STATUSES = {
    "PLACED",
    "CONFIRMED",
    "PACKAGING",
    "PREPARING"
}

def is_cancellable(status: str) -> bool:
    return status and status.upper() in CANCELLABLE_STATUSES

STORE_ACTIVE_TERMINAL_STATUSES = {
    "DELIVERED",
    "DELIVERY_DELIVERED",
    "SHIPMENT_DELIVERED",
    "ORDER_DELIVERED",
    "CANCELLED",
    "CANCELED",
    "CANCELLED_VOID",
    "ORDER_CANCELLED_BY_CUSTOMER",
    "ORDER_CANCELLED_BY_STORE",
    "CUSTOMER_CANCELLED_BEFORE_DELIVERY",
    "STORE_CANCELLED_BEFORE_DELIVERY",
    "UNPAID_ORDER_CANCELLED",
    "DELIVERY_CANCELLED_BY_RIDER",
    "RIDER_CANCELLED",
}

DELIVERY_STATUS_ALLOWED = {
    "REACHED_STORE",
    "PICKED_UP",
    "OUT_FOR_DELIVERY",
    "DELIVERED",
    "DELIVERY_FAILED",
}

DELIVERY_ALLOWED_TRANSITIONS = {
    "ASSIGNED_TO_DELIVERY": {"REACHED_STORE", "PICKED_UP", "OUT_FOR_DELIVERY"},
    "REACHED_STORE": {"PICKED_UP", "OUT_FOR_DELIVERY"},
    "PICKED_UP": {"OUT_FOR_DELIVERY"},
    "OUT_FOR_DELIVERY": {"DELIVERED", "DELIVERY_FAILED"},
}

def store_order_visible_to_store(order):
    """Keep unpaid online attempts out of Store operational queues."""
    order = order or {}
    status = (order.get("status") or "").strip().upper()
    payment_method = (order.get("payment_method") or "COD").strip().upper()
    payment_status = (order.get("payment_status") or "").strip().upper()
    payment_collection_status = (order.get("payment_collection_status") or "").strip().upper()

    if status in {"PENDING_PAYMENT", "PAYMENT_PENDING", "ONLINE_PENDING"}:
        return False

    if payment_method in {"COD", "CASH_ON_DELIVERY", "COD_RIDER_COLLECTION"}:
        return True

    if payment_status in {"PAID", "ONLINE_PAID", "SUCCESS"}:
        return True

    return payment_collection_status in {"PAID", "ONLINE_PAID", "COLLECTED", "PAID_REFUND_PENDING"}

def is_store_order_active(order):
    """True for Store-visible orders that have not reached a terminal state."""
    if not store_order_visible_to_store(order):
        return False
    status = str((order or {}).get("status") or "").strip().upper()
    return status not in STORE_ACTIVE_TERMINAL_STATUSES

def is_delivery_transition_allowed(current_status, new_status):
    """Preserve the Delivery portal's existing status transition policy."""
    current = (current_status or "").strip().upper()
    new = (new_status or "").strip().upper()
    if new not in DELIVERY_STATUS_ALLOWED:
        return False
    if current == "DELIVERED":
        return False
    return new in DELIVERY_ALLOWED_TRANSITIONS.get(current, DELIVERY_STATUS_ALLOWED)
