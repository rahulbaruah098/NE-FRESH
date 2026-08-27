import ast
from pathlib import Path
import pytest
from tests.support.source_contracts import project_root

ROOT = project_root()
ORDER_CUSTOMER_ACTIONS = ROOT / "routes" / "orders" / "customer_actions.py"
ORDER_PAYMENTS = ROOT / "routes" / "orders" / "payments.py"
ORDER_HISTORY = ROOT / "routes" / "orders" / "history_tracking.py"
STORE_ORDERS = ROOT / "routes" / "store" / "orders.py"
CUSTOMER_ORDERS_TEMPLATE = ROOT / "templates" / "orders.html"


def function_source(path: Path, name: str) -> str:
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(text, node) or ""
    raise AssertionError(f"Function {name} not found in {path}")


@pytest.mark.static
def test_customer_cancellation_keeps_stock_refund_and_payout_contracts():
    src = function_source(ORDER_CUSTOMER_ACTIONS, "order_cancel")
    assert "_release_order_stock_items(order_items)" in src
    assert '"status": "CANCELLED"' in src
    assert '"cancelled_by": "customer"' in src
    assert 'refund_status = "READY_FOR_REFUND"' in src
    assert 'refund_status = "NOT_REQUIRED"' in src
    assert '"store_payout_status": "NOT_REQUIRED"' in src
    assert 'new_payment_status = "REFUNDED"' not in src


@pytest.mark.static
@pytest.mark.finance
def test_razorpay_verification_keeps_ownership_reconciliation_and_idempotency_guards():
    src = function_source(ORDER_PAYMENTS, "api_verify_razorpay_payment")
    mismatch = src.index("saved_razorpay_order_id != razorpay_order_id")
    verify = src.index("verify_razorpay_payment_signature")
    assert mismatch < verify
    assert 'payment_status in ["PAID", "ONLINE_PAID", "SUCCESS"]' in src
    assert 'order_doc["payment_received_by"] = "ADMIN_PLATFORM"' in src
    assert 'order_doc["payment_reconciliation_status"] = "VERIFIED"' in src
    assert 'order_doc["platform_fee_status"] = "RECEIVED"' in src
    assert 'order_doc["rider_cash_settlement_status"] = "NOT_REQUIRED"' in src
    assert 'order_doc["order_settlement_status"] = "PENDING_STORE_PAYOUT"' in src
    assert "except DuplicateKeyError:" in src
    assert "_release_order_stock_items(order_items)" in src
    gateway_src = (ROOT / "services" / "payment_gateway.py").read_text(encoding="utf-8")
    assert "client.utility.verify_payment_signature" in gateway_src


@pytest.mark.static
def test_store_cancellation_records_store_as_customer_visible_source():
    store_text = STORE_ORDERS.read_text(encoding="utf-8")
    customer_template = CUSTOMER_ORDERS_TEMPLATE.read_text(encoding="utf-8")
    assert '"cancelled_by": "store"' in store_text
    assert '"cancelled_by_role": "store"' in store_text
    assert "cancelled_by_role or o.cancelled_by" in customer_template
    assert "Cancelled by Store" in customer_template


@pytest.mark.static
def test_store_tracking_is_role_contained_and_customer_tracker_has_defensive_redirect():
    store_src = function_source(STORE_ORDERS, "store_order_track_page")
    customer_src = function_source(ORDER_HISTORY, "order_track")
    assert 'render_template(\n        "store_order_track.html"' in store_src or '"store_order_track.html"' in store_src
    assert 'url_for("store_order_track", oid=oid)' in customer_src
    assert '(u.get("role") or "").strip().lower() == "store"' in customer_src
