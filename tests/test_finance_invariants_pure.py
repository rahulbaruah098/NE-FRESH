from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import re
import pytest
from tests.support.source_contracts import project_root
from tests.support.extract_source import load_source_definitions

ROOT = project_root()
PLATFORM_FEES = ROOT / "services" / "platform_fees.py"
FINANCE_RECONCILIATION = ROOT / "services" / "finance_reconciliation.py"
DELIVERY_MONTHLY = ROOT / "services" / "delivery_monthly_settlement.py"


def _money_namespace(settings):
    ns = load_source_definitions(
        PLATFORM_FEES,
        function_names={"_platform_fee_safe_float", "calculate_platform_fee", "build_order_money_breakdown"},
        namespace={},
    )
    ns["get_platform_fee_settings"] = lambda: dict(settings)
    return ns

@pytest.mark.pure
@pytest.mark.finance
def test_platform_fee_disabled_keeps_customer_total_clean():
    settings = {"enabled": False, "fee_type": "fixed", "fixed_amount": 10, "percent": 0, "min_fee": 0, "max_fee": 0}
    ns = _money_namespace(settings)
    result = ns["build_order_money_breakdown"](100, delivery_fee=30, tip_amount=5, payment_method="COD")
    assert result["platform_fee"] == 0.0
    assert result["store_earning"] == 100.0
    assert result["total_payable"] == 135.0
    assert result["admin_platform_fee_status"] == "DUE"

@pytest.mark.pure
@pytest.mark.finance
def test_platform_fee_percent_and_bounds_are_preserved():
    settings = {"enabled": True, "fee_type": "percent", "fixed_amount": 0, "percent": 10, "min_fee": 5, "max_fee": 12}
    ns = _money_namespace(settings)
    assert ns["calculate_platform_fee"](20)["platform_fee"] == 5.0
    assert ns["calculate_platform_fee"](100)["platform_fee"] == 10.0
    assert ns["calculate_platform_fee"](500)["platform_fee"] == 12.0
    online = ns["build_order_money_breakdown"](100, 30, 5, "ONLINE")
    assert online["total_payable"] == 145.0
    assert online["store_earning"] == 100.0
    assert online["admin_platform_fee_status"] == "COLLECTED"


def _finance_ns():
    return load_source_definitions(
        FINANCE_RECONCILIATION,
        function_names={"finance_money", "finance_order_has_unresolved_refund", "finance_reconciliation_snapshot"},
        assignment_names={"COD_COLLECTION_DELIVERY_BOY", "COD_COLLECTION_STORE", "COD_COLLECTION_EXTERNAL_PARTNER"},
        namespace={},
    )

@pytest.mark.pure
@pytest.mark.finance
def test_prepaid_online_customer_money_is_business_money_and_store_payout_can_unlock():
    fn = _finance_ns()["finance_reconciliation_snapshot"]
    row = fn({"payment_method":"ONLINE","payment_status":"PAID","payment_reconciliation_status":"VERIFIED","status":"DELIVERED","platform_fee":20,"store_payout_status":"PENDING_AFTER_DELIVERY"})
    assert row["customer_payment_reconciled"] is True
    assert row["payment_receiver"] == "ADMIN_PLATFORM"
    assert row["platform_fee_reconciled"] is True
    assert row["store_payout_required"] is True
    assert row["store_payout_eligible"] is True

@pytest.mark.pure
@pytest.mark.finance
def test_store_direct_cod_requires_no_admin_store_payout_but_platform_fee_can_be_due():
    fn = _finance_ns()["finance_reconciliation_snapshot"]
    row = fn({"payment_method":"COD","cod_collection_method":"STORE","payment_received_by":"STORE","payment_reconciliation_status":"VERIFIED_AT_STORE","payment_status":"PAID","status":"DELIVERED","platform_fee":20,"platform_fee_status":"DUE","store_payout_status":"NOT_REQUIRED"})
    assert row["is_store_collection"] is True
    assert row["customer_payment_reconciled"] is True
    assert row["payment_receiver"] == "STORE"
    assert row["store_payout_required"] is False
    assert row["store_payout_status"] == "NOT_REQUIRED"
    assert row["store_payout_eligible"] is False
    assert row["platform_fee_reconciliation_status"] == "DUE_FROM_STORE"

@pytest.mark.pure
@pytest.mark.finance
def test_external_partner_remittance_blocks_store_payout_until_business_receives_money():
    fn = _finance_ns()["finance_reconciliation_snapshot"]
    pending = fn({"payment_method":"COD","cod_collection_method":"EXTERNAL_PARTNER","external_cod_remittance_status":"PENDING","status":"DELIVERED","platform_fee":20,"store_payout_status":"PENDING_AFTER_DELIVERY"})
    assert pending["is_partner_collection"] is True
    assert pending["customer_payment_reconciled"] is False
    assert pending["store_payout_eligible"] is False
    verified = fn({"payment_method":"COD","cod_collection_method":"EXTERNAL_PARTNER","external_cod_remittance_status":"VERIFIED","status":"DELIVERED","platform_fee":20,"store_payout_status":"PENDING_AFTER_DELIVERY"})
    assert verified["customer_payment_reconciled"] is True
    assert verified["payment_receiver"] == "ADMIN_PLATFORM"
    assert verified["store_payout_eligible"] is True

@pytest.mark.pure
@pytest.mark.finance
def test_in_house_cod_upi_and_cash_reconcile_without_treating_rider_earnings_as_customer_money():
    fn = _finance_ns()["finance_reconciliation_snapshot"]
    upi = fn({"payment_method":"COD","payment_collection_channel":"UPI","upi_delivery_reconciliation_status":"VERIFIED","status":"DELIVERED","platform_fee":20,"store_payout_status":"PENDING_AFTER_DELIVERY"})
    assert upi["is_in_house_upi"] is True
    assert upi["customer_payment_reconciled"] is True
    assert upi["payment_receiver"] == "ADMIN_PLATFORM"
    cash = fn({"payment_method":"COD","payment_collection_channel":"CASH","rider_cash_settlement_status":"VERIFIED","status":"DELIVERED","platform_fee":20,"store_payout_status":"PENDING_AFTER_DELIVERY"})
    assert cash["is_in_house_cash"] is True
    assert cash["customer_payment_reconciled"] is True
    assert cash["payment_receiver"] == "ADMIN_PLATFORM"

@pytest.mark.pure
@pytest.mark.finance
def test_unresolved_refund_blocks_store_payout():
    fn = _finance_ns()["finance_reconciliation_snapshot"]
    row = fn({"payment_method":"ONLINE","payment_status":"PAID","payment_reconciliation_status":"VERIFIED","status":"DELIVERED","platform_fee":20,"refund_status":"READY_FOR_REFUND","store_payout_status":"PENDING_AFTER_DELIVERY"})
    assert row["refund_unresolved"] is True
    assert row["store_payout_eligible"] is False
    assert "refund" in row["store_payout_block_reason"].lower()

@pytest.mark.pure
@pytest.mark.finance
def test_delivery_monthly_period_uses_india_time_and_monthly_model_remains_separate():
    ns = load_source_definitions(
        DELIVERY_MONTHLY,
        function_names={"delivery_monthly_period_from_utc", "delivery_order_uses_monthly_payout"},
        assignment_names={"DELIVERY_PAYOUT_MODEL_MONTHLY_V1", "_DELIVERY_SETTLEMENT_IST"},
        namespace={"datetime":datetime,"timedelta":timedelta,"timezone":timezone,"re":re},
    )
    # 31 July 20:00 UTC is already 1 August 01:30 in India.
    assert ns["delivery_monthly_period_from_utc"]("2026-07-31T20:00:00") == "2026-08"
    assert ns["delivery_order_uses_monthly_payout"]({"delivery_payout_model":"MONTHLY_V1"}) is True
    assert ns["delivery_order_uses_monthly_payout"]({"delivery_payout_model":"NOT_REQUIRED"}) is False
