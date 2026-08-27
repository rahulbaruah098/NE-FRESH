"""Canonical read-only reconciliation rules for NE FRESH customer/business money.

Delivery-partner earnings remain a separate monthly compensation leg.
"""

FINANCE_PAYMENT_RECONCILED = "VERIFIED"

FINANCE_PAYMENT_PENDING = "PENDING"

def finance_money(value, default=0.0):
    try:
        if value is None or str(value).strip() == "":
            return round(float(default), 2)
        return round(float(value), 2)
    except Exception:
        return round(float(default), 2)

def finance_order_has_unresolved_refund(order):
    """Return True only while a return/refund can still change the business payout."""
    if not isinstance(order, dict):
        return False

    refund_status = (order.get("refund_status") or "").strip().upper()
    return_status = (order.get("return_status") or "").strip().upper()

    closed_refund = {
        "", "NOT_REQUIRED", "PROCESSED", "ADJUSTED", "REJECTED", "VOID", "REFUNDED"
    }
    active_return = {
        "RETURN_REQUESTED", "RETURN_PICKED_UP", "RETURNED_TO_STORE",
        "STORE_APPROVED", "NEED_ADMIN_REVIEW", "ADMIN_RETURN_REVIEW_PENDING"
    }

    if refund_status and refund_status not in closed_refund:
        return True
    return return_status in active_return

def finance_reconciliation_snapshot(order):
    """
    Canonical read-only interpretation of the customer-payment / business-money leg.

    Customer/order money always belongs to Admin/Store/business. Delivery-partner
    earnings are handled separately by the monthly payout model.
    """
    order = order or {}

    payment_method = (order.get("payment_method") or "").strip().upper()
    payment_status = (order.get("payment_status") or "").strip().upper()
    payment_flow = (order.get("payment_flow") or order.get("official_payment_mode") or "").strip().upper()
    active_mode = (order.get("active_delivery_mode") or "").strip().upper()
    cod_method = (order.get("cod_collection_method") or "").strip().upper()
    channel = (order.get("payment_collection_channel") or "").strip().upper()
    payment_received_by = (order.get("payment_received_by") or "").strip().upper()
    payment_reconciliation = (order.get("payment_reconciliation_status") or "").strip().upper()
    upi_reconciliation = (order.get("upi_delivery_reconciliation_status") or "").strip().upper()
    rider_cash_status = (order.get("rider_cash_settlement_status") or "").strip().upper()
    partner_remittance = (order.get("external_cod_remittance_status") or "").strip().upper()
    platform_fee_status = (order.get("platform_fee_status") or "").strip().upper()
    store_payout_status = (order.get("store_payout_status") or "").strip().upper()
    status = (order.get("status") or "").strip().upper()

    paid_values = {"PAID", "ONLINE_PAID", "SUCCESS", "COMPLETED", "CAPTURED"}
    verified_values = {"VERIFIED", "RECEIVED", "RECEIVED_BY_ADMIN", "SETTLED", "PAID"}

    is_cod = payment_method in {"COD", "CASH_ON_DELIVERY", "COD_RIDER_COLLECTION"}
    is_online = payment_method in {"ONLINE", "ONLINE_PAYMENT", "RAZORPAY"}
    store_collection = bool(
        is_cod and (
            cod_method == COD_COLLECTION_STORE
            or payment_flow in {"COD_STORE_COLLECTION", "PAY_ON_DELIVERY_STORE_ONLINE"}
            or payment_received_by == "STORE"
        )
    )
    partner_collection = bool(
        is_cod and (
            cod_method == COD_COLLECTION_EXTERNAL_PARTNER
            or payment_flow == "COD_PARTNER_COLLECTION"
        )
    )
    in_house_upi = bool(is_cod and channel == "UPI" and not store_collection and not partner_collection)
    in_house_cash = bool(is_cod and not store_collection and not partner_collection and not in_house_upi)

    customer_reconciled = False
    customer_status = "PENDING_PAYMENT"
    receiver = "PENDING"
    receiver_label = "Pending"
    collection_label = "Pending"

    if is_online:
        customer_reconciled = payment_status in paid_values or payment_reconciliation in verified_values
        customer_status = "VERIFIED" if customer_reconciled else "PENDING_PAYMENT"
        receiver = "ADMIN_PLATFORM" if customer_reconciled else "PENDING"
        receiver_label = "Admin / Platform" if customer_reconciled else "Pending"
        collection_label = "Prepaid Online"
    elif store_collection:
        store_payment_recorded = bool(
            payment_received_by == "STORE"
            and (
                payment_reconciliation in {"VERIFIED", "VERIFIED_AT_STORE", "STORE_CONFIRMED"}
                or payment_status in paid_values
                or (order.get("payment_collection_status") or "").strip().upper() in {"COLLECTED_BY_STORE", "PAID"}
            )
        )
        customer_reconciled = store_payment_recorded
        customer_status = "VERIFIED_AT_STORE" if customer_reconciled else "PENDING_STORE_COLLECTION"
        receiver = "STORE" if customer_reconciled else "PENDING"
        receiver_label = "Store" if customer_reconciled else "Pending Store Collection"
        collection_label = "Pay on Delivery · Store"
    elif partner_collection:
        customer_reconciled = partner_remittance in verified_values
        customer_status = "VERIFIED" if customer_reconciled else "PENDING_PARTNER_REMITTANCE"
        receiver = "ADMIN_PLATFORM" if customer_reconciled else "EXTERNAL_PARTNER"
        receiver_label = "Admin / Business" if customer_reconciled else "External Partner · Remittance Pending"
        collection_label = "Pay on Delivery · External Partner"
    elif in_house_upi:
        customer_reconciled = upi_reconciliation == "VERIFIED"
        customer_status = "VERIFIED" if customer_reconciled else "PENDING_UPI_VERIFICATION"
        receiver = "ADMIN_PLATFORM" if customer_reconciled else "PENDING"
        receiver_label = "Admin / Official UPI" if customer_reconciled else "Official UPI · Verification Pending"
        collection_label = "Pay on Delivery · UPI"
    elif in_house_cash:
        customer_reconciled = rider_cash_status in verified_values
        customer_status = "VERIFIED" if customer_reconciled else "PENDING_RIDER_CASH"
        receiver = "ADMIN_PLATFORM" if customer_reconciled else "DELIVERY_PARTNER"
        receiver_label = "Admin / Business" if customer_reconciled else "Delivery Partner · Cash Pending"
        collection_label = "Pay on Delivery · Cash"
    else:
        customer_reconciled = payment_reconciliation in verified_values or payment_status in paid_values
        customer_status = "VERIFIED" if customer_reconciled else "PENDING_PAYMENT"
        receiver = payment_received_by or ("ADMIN_PLATFORM" if customer_reconciled else "PENDING")
        receiver_label = receiver.replace("_", " ").title() if receiver else "Pending"
        collection_label = payment_method.replace("_", " ").title() if payment_method else "Payment"

    platform_fee = finance_money(order.get("platform_fee"), 0)
    refund_platform_fee = finance_money(
        order.get("refund_platform_fee")
        if order.get("refund_platform_fee") is not None
        else order.get("platform_fee_adjustment"),
        0,
    )
    net_platform_fee = round(max(platform_fee - refund_platform_fee, 0), 2)

    if net_platform_fee <= 0:
        platform_reconciled = True
        platform_status = "NOT_REQUIRED" if platform_fee <= 0 else "ADJUSTED"
    elif platform_fee_status == "RECEIVED":
        platform_reconciled = True
        platform_status = "RECEIVED"
    elif store_collection and customer_reconciled:
        platform_reconciled = False
        platform_status = "DUE_FROM_STORE"
    elif partner_collection and not customer_reconciled:
        platform_reconciled = False
        platform_status = "PENDING_PARTNER_REMITTANCE"
    elif in_house_upi and not customer_reconciled:
        platform_reconciled = False
        platform_status = "PENDING_UPI_VERIFICATION"
    elif in_house_cash and not customer_reconciled:
        platform_reconciled = False
        platform_status = "PENDING_RIDER_CASH"
    elif is_online and not customer_reconciled:
        platform_reconciled = False
        platform_status = "PENDING_PAYMENT"
    elif customer_reconciled:
        # Admin/official business received the customer payment, so the Platform Fee
        # is already part of business money for every non-Store-direct collection.
        platform_reconciled = True
        platform_status = "RECEIVED"
    else:
        platform_reconciled = False
        platform_status = platform_fee_status or "PENDING_PAYMENT_RECONCILIATION"

    store_payout_required = not store_collection
    if store_collection and customer_reconciled:
        store_payout_status_effective = "NOT_REQUIRED"
    else:
        store_payout_status_effective = store_payout_status or "PENDING_AFTER_DELIVERY"

    unresolved_refund = finance_order_has_unresolved_refund(order)
    store_payout_eligible = bool(
        status == "DELIVERED"
        and store_payout_required
        and customer_reconciled
        and not unresolved_refund
        and store_payout_status_effective not in {"PAID", "SETTLED", "PROCESSING", "NOT_REQUIRED"}
    )

    if store_collection and not customer_reconciled:
        payout_block_reason = "Store is configured to receive the customer payment directly. Record/reconcile that customer payment; Admin Store payout is not required."
    elif not store_payout_required:
        payout_block_reason = "Store already received the customer payment directly. Admin Store payout is not required."
    elif not customer_reconciled:
        payout_block_reason = "Customer payment must be reconciled to the business before Store payout."
    elif unresolved_refund:
        payout_block_reason = "Resolve the active return/refund before Store payout."
    elif store_payout_status_effective in {"PAID", "SETTLED"}:
        payout_block_reason = "Store payout is already settled."
    elif store_payout_status_effective == "PROCESSING":
        payout_block_reason = "Store payout is currently being processed."
    else:
        payout_block_reason = ""

    return {
        "payment_method": payment_method,
        "payment_flow": payment_flow,
        "active_delivery_mode": active_mode,
        "cod_collection_method": cod_method,
        "collection_channel": channel,
        "collection_label": collection_label,
        "customer_payment_reconciled": bool(customer_reconciled),
        "payment_reconciliation_status": customer_status,
        "payment_receiver": receiver,
        "payment_receiver_label": receiver_label,
        "is_store_collection": bool(store_collection),
        "is_partner_collection": bool(partner_collection),
        "is_in_house_upi": bool(in_house_upi),
        "is_in_house_cash": bool(in_house_cash),
        "platform_fee": platform_fee,
        "refund_platform_fee": refund_platform_fee,
        "net_platform_fee": net_platform_fee,
        "platform_fee_reconciled": bool(platform_reconciled),
        "platform_fee_reconciliation_status": platform_status,
        "business_reconciliation_complete": bool(customer_reconciled and platform_reconciled),
        "store_payout_required": bool(store_payout_required),
        "store_payout_status": store_payout_status_effective,
        "store_payout_eligible": bool(store_payout_eligible),
        "store_payout_block_reason": payout_block_reason,
        "refund_unresolved": bool(unresolved_refund),
    }

COD_COLLECTION_DELIVERY_BOY = "DELIVERY_BOY"

COD_COLLECTION_STORE = "STORE"

COD_COLLECTION_EXTERNAL_PARTNER = "EXTERNAL_PARTNER"

VALID_COD_COLLECTION_METHODS = {
    COD_COLLECTION_DELIVERY_BOY,
    COD_COLLECTION_STORE,
    COD_COLLECTION_EXTERNAL_PARTNER,
}
