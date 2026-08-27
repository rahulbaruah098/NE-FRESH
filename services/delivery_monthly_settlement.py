"""Delivery Partner monthly payout period and reconciliation helpers.

Customer/order money is never netted against Delivery Partner monthly earnings.
"""

import re
from datetime import datetime, timedelta, timezone
from bson import ObjectId
from services.finance_reconciliation import finance_reconciliation_snapshot

DELIVERY_PAYOUT_MODEL_MONTHLY_V1 = "MONTHLY_V1"

DELIVERY_PAYOUT_MODEL_NOT_REQUIRED = "NOT_REQUIRED"

DELIVERY_MONTHLY_STATUS_PENDING_DELIVERY = "PENDING_DELIVERY"

DELIVERY_MONTHLY_STATUS_ACCRUED = "MONTHLY_ACCRUED"

DELIVERY_MONTHLY_STATUS_PAID = "PAID_MONTHLY"

DELIVERY_MONTHLY_BATCH_STATUS_PAID = "PAID"

_DELIVERY_SETTLEMENT_IST = timezone(timedelta(hours=5, minutes=30))

def delivery_monthly_period_from_utc(value=None):
    """
    Return YYYY-MM in India time for a delivery timestamp.

    Existing order timestamps are mostly naive UTC ISO strings created with
    datetime.utcnow().  Treat naive values as UTC before converting to IST so a
    delivery just after midnight in India is assigned to the correct month.
    """
    dt = None

    if isinstance(value, datetime):
        dt = value
    elif value not in [None, ""]:
        raw = str(value).strip()
        if raw:
            try:
                if raw.endswith("Z"):
                    raw = raw[:-1] + "+00:00"
                dt = datetime.fromisoformat(raw)
            except Exception:
                try:
                    dt = datetime.strptime(raw[:19], "%Y-%m-%dT%H:%M:%S")
                except Exception:
                    dt = None

    if dt is None:
        dt = datetime.utcnow().replace(tzinfo=timezone.utc)
    elif dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)

    return dt.astimezone(_DELIVERY_SETTLEMENT_IST).strftime("%Y-%m")

def delivery_monthly_period_label(period):
    try:
        return datetime.strptime(str(period) + "-01", "%Y-%m-%d").strftime("%B %Y")
    except Exception:
        return str(period or "")

def delivery_monthly_current_period():
    return datetime.now(_DELIVERY_SETTLEMENT_IST).strftime("%Y-%m")

def delivery_monthly_period_is_closed(period):
    period = str(period or "").strip()
    if not re.match(r"^\d{4}-\d{2}$", period):
        return False
    return period < delivery_monthly_current_period()

def delivery_partner_id_values(value):
    values = []
    raw = str(value or "").strip()
    if not raw:
        return values

    values.append(raw)
    try:
        if ObjectId.is_valid(raw):
            values.append(ObjectId(raw))
    except Exception:
        pass
    return values

def delivery_order_uses_monthly_payout(order):
    return (
        isinstance(order, dict)
        and (order.get("delivery_payout_model") or "").strip().upper() == DELIVERY_PAYOUT_MODEL_MONTHLY_V1
    )

def delivery_monthly_payment_is_reconciled(order):
    """
    Whether the customer/business payment leg for an in-house delivered order
    is already safely with Admin/Store and no driver-held business money remains.
    This controls whether a closed rider month can be paid.
    """
    if not isinstance(order, dict):
        return False
    return bool(finance_reconciliation_snapshot(order).get("customer_payment_reconciled"))
