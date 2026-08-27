import pytest
from services.finance_actions import (
    build_delivery_monthly_batch_doc,
    build_rider_cash_received_state,
    build_store_platform_fee_received_state,
    build_upi_delivery_verified_state,
    calculate_refund_finance_state,
    calculate_store_payout_base,
)


@pytest.mark.pure
@pytest.mark.finance
def test_store_platform_fee_transition_keeps_direct_collection_reconciled():
    state = build_store_platform_fee_received_state(
        "order1", 12.5, {"id": "admin1", "name": "Admin"}, "UPI", "REF123", "ok", "2026-08-26T10:00:00"
    )
    assert state["order_update"]["platform_fee_status"] == "RECEIVED"
    assert state["order_update"]["store_payout_status"] == "NOT_REQUIRED"
    assert state["order_update"]["order_settlement_status"] == "BUSINESS_RECONCILED"
    assert state["transaction_update"]["status"] == "PAID"


@pytest.mark.pure
@pytest.mark.finance
def test_upi_delivery_transition_never_creates_rider_cash_liability():
    state = build_upi_delivery_verified_state(
        {
            "upi_delivery_reference": "UPI123456",
            "platform_fee": 10,
            "store_payout_amount": 90,
            "cod_collected_amount": 120,
        },
        "order1", {"id": "admin1", "name": "Admin"}, "verified", "2026-08-26T10:00:00"
    )
    update = state["order_update"]
    assert update["payment_reconciliation_status"] == "VERIFIED"
    assert update["rider_cash_settlement_status"] == "NOT_REQUIRED"
    assert update["rider_cash_to_submit"] == 0.0
    assert update["store_payout_status"] == "PENDING_AFTER_DELIVERY"


@pytest.mark.pure
@pytest.mark.finance
def test_rider_cash_transition_keeps_customer_cash_separate_from_monthly_earning():
    state = build_rider_cash_received_state(
        {"rider_cash_to_submit": 150, "platform_fee": 10, "store_payout_amount": 120},
        "order1", {"id": "admin1", "name": "Admin"}, "received", "2026-08-26T10:00:00"
    )
    assert state["rider_cash_to_submit"] == 150.0
    assert state["order_update"]["rider_cash_settlement_status"] == "RECEIVED"
    assert state["order_update"]["platform_fee_status"] == "RECEIVED"
    assert state["order_update"]["store_payout_status"] == "PENDING_AFTER_DELIVERY"
    assert "delivery_monthly" not in state["order_update"]


@pytest.mark.pure
@pytest.mark.finance
def test_pending_store_payout_refund_reduces_current_payout():
    result = calculate_refund_finance_state(
        {
            "status": "DELIVERED",
            "payment_method": "ONLINE",
            "payment_status": "PAID",
            "payment_reconciliation_status": "VERIFIED",
            "platform_fee_status": "RECEIVED",
            "platform_fee": 10,
            "store_payout_status": "PENDING_AFTER_DELIVERY",
            "store_payout_amount": 100,
            "total_payable": 120,
        },
        30, 0, 2, 0,
    )
    assert result["refund_amount"] == 32.0
    assert result["adjusted_store_payout"] == 70.0
    assert result["store_adjustment_due"] == 0.0
    assert result["settlement_impact"] == "DEDUCT_FROM_PENDING_PAYOUT"
    assert result["next_store_payout_status"] == "PENDING_AFTER_DELIVERY"
    assert result["net_platform_fee_after_refund"] == 8.0


@pytest.mark.pure
@pytest.mark.finance
def test_refund_after_store_already_received_money_creates_carry_forward_due():
    result = calculate_refund_finance_state(
        {
            "status": "DELIVERED",
            "payment_method": "ONLINE",
            "payment_status": "PAID",
            "payment_reconciliation_status": "VERIFIED",
            "platform_fee_status": "RECEIVED",
            "platform_fee": 10,
            "store_payout_status": "PAID",
            "store_payout_amount": 100,
            "total_payable": 120,
        },
        25, 0, 0, 0,
    )
    assert result["store_already_received_order_money"] is True
    assert result["adjusted_store_payout"] == 100.0
    assert result["store_adjustment_due"] == 25.0
    assert result["settlement_impact"] == "ADJUST_FROM_NEXT_PAYOUT"


@pytest.mark.pure
@pytest.mark.finance
def test_store_direct_collection_refund_also_creates_future_adjustment():
    result = calculate_refund_finance_state(
        {
            "status": "DELIVERED",
            "payment_method": "COD",
            "cod_collection_method": "STORE",
            "payment_received_by": "STORE",
            "payment_status": "PAID",
            "payment_reconciliation_status": "VERIFIED_AT_STORE",
            "platform_fee": 10,
            "platform_fee_status": "DUE_FROM_STORE",
            "store_payout_status": "NOT_REQUIRED",
            "store_payout_amount": 100,
            "total_payable": 110,
        },
        20, 0, 0, 0,
    )
    assert result["store_already_received_order_money"] is True
    assert result["store_adjustment_due"] == 20.0
    assert result["next_store_payout_status"] == "NOT_REQUIRED"


@pytest.mark.pure
@pytest.mark.finance
def test_cancel_refund_does_not_create_store_refund_recovery():
    result = calculate_refund_finance_state(
        {
            "status": "CANCELLED",
            "payment_method": "ONLINE",
            "payment_status": "PAID",
            "payment_reconciliation_status": "VERIFIED",
            "store_payout_status": "PENDING_AFTER_DELIVERY",
            "store_payout_amount": 100,
            "total_payable": 110,
            "platform_fee": 10,
        },
        100, 0, 10, 0,
    )
    assert result["store_refund_deduction"] == 0.0
    assert result["store_adjustment_due"] == 0.0
    assert result["next_store_payout_status"] == "NOT_REQUIRED"
    assert result["payment_status_after_refund"] == "REFUNDED"


@pytest.mark.pure
@pytest.mark.finance
def test_store_payout_base_preserves_refund_deduction_formula():
    result = calculate_store_payout_base({
        "store_earning": 150,
        "store_refund_deduction": 35,
    })
    assert result["original_store_payout_amount"] == 150.0
    assert result["adjusted_store_payout"] == 115.0
    assert result["settlement_impact"] == "DEDUCT_FROM_PENDING_PAYOUT"


@pytest.mark.pure
@pytest.mark.finance
def test_monthly_delivery_batch_is_delivery_fee_plus_tip_only():
    gross, batch = build_delivery_monthly_batch_doc(
        [
            {"delivery_partner_name": "Rider", "delivery_fee": 30, "tip_amount": 5},
            {"delivery_partner_name": "Rider", "delivery_boy_payout_amount": 40, "delivery_fee": 999, "tip_amount": 999},
        ],
        [{"_id": "o1"}, {"_id": "o2"}],
        "rider1", "2026-07", "UPI", "TXN1", "monthly", {"id": "admin1", "name": "Admin"}, "2026-08-01T00:00:00",
    )
    assert gross == 75.0
    assert batch["amount_paid"] == 75.0
    assert batch["order_count"] == 2
    assert batch["period"] == "2026-07"
