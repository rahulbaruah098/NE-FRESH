from pathlib import Path
import ast
import pytest

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "app_core.py"


def _top_level_function_names(path):
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    return {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def test_step6_finance_modules_exist():
    expected = [
        "services/platform_fees.py",
        "services/finance_reconciliation.py",
        "services/store_finance_adjustments.py",
        "services/delivery_monthly_settlement.py",
        "services/payment_gateway.py",
        "services/refund_policy.py",
        "services/finance_actions.py",
    ]
    assert [rel for rel in expected if not (ROOT / rel).is_file()] == []


def test_app_core_no_longer_defines_extracted_finance_functions():
    names = _top_level_function_names(CORE)
    extracted = {
        "_platform_fee_safe_float", "get_platform_fee_settings", "calculate_platform_fee",
        "build_order_money_breakdown", "finance_money", "finance_order_has_unresolved_refund",
        "finance_reconciliation_snapshot", "finance_store_id_values", "finance_create_store_adjustment",
        "finance_store_outstanding_adjustment_total", "finance_apply_store_adjustments",
        "finance_rollback_store_adjustments", "delivery_monthly_period_from_utc",
        "delivery_monthly_period_label", "delivery_monthly_current_period",
        "delivery_monthly_period_is_closed", "delivery_partner_id_values",
        "delivery_order_uses_monthly_payout", "delivery_monthly_payment_is_reconciled",
    }
    assert sorted(names & extracted) == []


def test_step6_modules_do_not_import_app_core_back():
    for rel in [
        "services/platform_fees.py", "services/finance_reconciliation.py",
        "services/store_finance_adjustments.py", "services/delivery_monthly_settlement.py",
        "services/payment_gateway.py", "services/refund_policy.py", "services/finance_actions.py",
    ]:
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "from app_core import" not in text
        assert "import app_core" not in text


def test_app_core_keeps_step6_compatibility_exports():
    text = CORE.read_text(encoding="utf-8")
    for required in [
        "from services.platform_fees import (",
        "from services.finance_reconciliation import (",
        "from services.store_finance_adjustments import (",
        "from services.delivery_monthly_settlement import (",
    ]:
        assert required in text
    assert "__all__ = [name for name in globals() if not name.startswith('__')]" in text


def test_finance_heavy_routes_use_explicit_step6_imports():
    checks = {
        "routes/orders/shared.py": ["from services.platform_fees import", "from services.payment_gateway import"],
        "routes/admin/shared.py": ["from services.finance_reconciliation import", "from services.finance_actions import"],
        "routes/store/shared.py": ["from services.finance_reconciliation import"],
        "routes/delivery/shared.py": ["from services.delivery_monthly_settlement import"],
        "routes/external_delivery/routes.py": ["from services.finance_reconciliation import"],
    }
    for rel, required in checks.items():
        text = (ROOT / rel).read_text(encoding="utf-8")
        for needle in required:
            assert needle in text, f"{needle!r} missing from {rel}"


def test_payment_gateway_helpers_moved_out_of_route_modules():
    orders_names = _top_level_function_names(ROOT / "routes/orders/shared.py")
    admin_names = _top_level_function_names(ROOT / "routes/admin/shared.py")
    assert not ({"_get_razorpay_env_keys", "get_checkout_payment_gateway_settings",
                 "get_server_payment_gateway_settings", "get_razorpay_client_from_settings"} & orders_names)
    assert not ({"_admin_get_razorpay_env_status", "_admin_get_payment_gateway_settings"} & admin_names)


def test_refund_policy_helpers_moved_out_of_route_modules():
    orders_names = _top_level_function_names(ROOT / "routes/orders/shared.py")
    admin_names = _top_level_function_names(ROOT / "routes/admin/shared.py")
    assert "get_return_refund_policy_settings" not in orders_names
    assert "_admin_get_return_refund_policy_settings" not in admin_names


@pytest.mark.finance
def test_razorpay_secret_remains_environment_only_in_shared_gateway_service():
    text = (ROOT / "services/payment_gateway.py").read_text(encoding="utf-8")
    assert 'os.getenv("RAZORPAY_TEST_KEY_SECRET")' in text
    assert 'os.getenv("RAZORPAY_LIVE_KEY_SECRET")' in text
    assert 'settings.get("razorpay_key_secret")' in text
    # Server secret must not be read from the Mongo platform-settings document.
    assert 'row.get("razorpay_key_secret")' not in text


@pytest.mark.finance
def test_admin_financial_transitions_use_compare_and_set_guards():
    settlements_text = (ROOT / "routes/admin/settlements.py").read_text(encoding="utf-8")
    refunds_text = (ROOT / "routes/admin/refunds.py").read_text(encoding="utf-8")
    assert '"upi_delivery_reconciliation_status": {"$ne": "VERIFIED"}' in settlements_text
    assert '"rider_cash_settlement_status": {"$ne": "RECEIVED"}' in settlements_text
    assert '"refund_status": {"$nin": ["PROCESSED", "ADJUSTED", "REJECTED", "NOT_REQUIRED", "VOID"]}' in refunds_text
    assert '"store_payout_status": {"$nin": ["PAID", "PROCESSING", "NOT_REQUIRED"]}' in settlements_text


def test_dead_duplicate_store_platform_fee_block_is_removed():
    text = (ROOT / "routes/admin/settlements.py").read_text(encoding="utf-8")
    tree = ast.parse(text)
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "admin_settlement_store_platform_fee_received")
    direct_returns = [n for n in fn.body if isinstance(n, ast.Return)]
    assert len(direct_returns) == 1
