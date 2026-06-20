"""Unified checkout quote helpers for NE FRESH external delivery modes."""

from .hyperlocal_service import quote_hyperlocal_delivery
from .shiprocket_service import quote_shiprocket_delivery


def _safe_float(value, default=0.0):
    try:
        if value is None or str(value).strip() == "":
            return float(default)
        return float(value)
    except Exception:
        return float(default)


def quote_external_delivery(payload, settings, mode, payment_rule="ONLINE_ONLY"):
    """Quote external delivery fee for checkout.

    mode:
      - EXTERNAL_LOCAL_DELIVERY => hyperlocal/Rapido/Zomato type
      - THIRD_PARTY_SHIPPING => courier/Shiprocket type

    The function always returns a structured response. If real partner APIs are
    not configured, manual fallback quotes are returned instead of breaking the
    order flow.
    """
    mode = (mode or "").upper()
    payment_rule = (payment_rule or "ONLINE_ONLY").upper()
    cod_allowed = payment_rule in {"COD_STORE_COLLECTION", "COD_PARTNER_COLLECTION"}

    if mode == "THIRD_PARTY_SHIPPING":
        result = quote_shiprocket_delivery(payload, settings)
    else:
        result = quote_hyperlocal_delivery(payload, settings)

    result = dict(result or {})
    result.setdefault("ok", True)
    result.setdefault("serviceable", True)
    result.setdefault("delivery_fee", _safe_float(payload.get("fallback_delivery_fee"), 0.0))
    result["delivery_fee"] = round(_safe_float(result.get("delivery_fee"), 0.0), 2)
    result["cod_allowed"] = bool(cod_allowed)
    result["online_allowed"] = True
    result["payment_rule"] = payment_rule
    return result
