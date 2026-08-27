"""Shared value normalization/display helpers extracted during Step 4."""

def _clean_pin(pin) -> str:
    """Keep digits only and trim spaces."""
    if pin is None:
        return ""
    s = str(pin).strip()
    return "".join(ch for ch in s if ch.isdigit())

def _clean_state(state) -> str:
    return (state or "").strip().lower()

def is_assam_state(state) -> bool:
    return _clean_state(state) in {
        "assam",
        "as"
    }

def _norm_status(value):
    return (str(value).strip().upper() if value is not None else "")

def _norm_role(value):
    return (str(value).strip().lower() if value is not None else "")

def normalize_phone(phone):
    phone = (phone or "").strip()
    if not phone:
        return ""

    # Keep leading + if present, remove spaces/dashes/brackets
    has_plus = phone.startswith("+")
    digits = "".join(ch for ch in phone if ch.isdigit())

    if not digits:
        return ""

    if has_plus:
        return "+" + digits

    # Default India format for 10-digit numbers
    if len(digits) == 10:
        return "+91" + digits

    return digits

def order_status_label(status):
    """
    Display label helper for old and new order statuses.
    Keeps old DB statuses readable while showing the new business wording.
    """
    status = (status or "").strip().upper()

    labels = {
        "PLACED": "Placed",
        "CONFIRMED": "Confirmed",
        "PREPARING": "Packaging",
        "PACKAGING": "Packaging",
        "READY_FOR_PICKUP": "Shipment Ready",
        "SHIPMENT_READY": "Shipment Ready",
        "ASSIGNED_TO_DELIVERY": "Assigned To Delivery",
        "ACCEPTED_BY_DELIVERY_MAN": "Accepted By Delivery Boy",
        "REACHED_STORE": "Reached Store",
        "PICKED_UP": "Picked Up",
        "OUT_FOR_DELIVERY": "Out For Delivery",
        "DELIVERED": "Delivered",
        "DELIVERY_FAILED": "Delivery Failed",
        "CANCELLED": "Cancelled",
        "CANCELED": "Cancelled",
        "RETURN_REQUESTED": "Return Requested",
        "STORE_APPROVED": "Store Approved",
        "STORE_REJECTED": "Store Rejected",
        "NEED_ADMIN_REVIEW": "Need Admin Review",
        "RETURN_COMPLETED": "Return Completed",
    }

    return labels.get(status, status.replace("_", " ").title() if status else "")
