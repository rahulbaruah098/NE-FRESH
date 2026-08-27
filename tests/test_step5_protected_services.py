from pathlib import Path
import ast
import pytest

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "app_core.py"


def _top_level_function_names(path):
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    return {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def test_step5_protected_service_modules_exist():
    expected = [
        "helpers/numbers.py",
        "services/order_inventory.py",
        "services/order_lifecycle.py",
        "services/order_tracking.py",
        "services/delivery_operations.py",
    ]
    assert [rel for rel in expected if not (ROOT / rel).is_file()] == []


def test_app_core_no_longer_defines_extracted_step5_functions():
    names = _top_level_function_names(CORE)
    extracted = {
        "haversine_km", "_delivery_now", "_get_delivery_availability", "_is_delivery_active",
        "_get_float_or_none", "_driver_distance_to_store_km", "_hydrate_delivery_order",
        "calculate_delivery_fee_by_distance", "_delivery_int", "_delivery_float_or_none",
        "_delivery_float_or_default", "_delivery_user_id", "_delivery_actor_snapshot",
        "add_order_event", "get_delivery_partner_snapshot", "get_online_delivery_people_near_store",
        "assign_delivery_partner_to_order", "clear_delivery_assignment", "is_cancellable", "get_order_full",
    }
    assert sorted(names & extracted) == []


def test_step5_modules_do_not_import_app_core_back():
    for rel in [
        "helpers/numbers.py", "services/order_inventory.py", "services/order_lifecycle.py",
        "services/order_tracking.py", "services/delivery_operations.py",
    ]:
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "from app_core import" not in text
        assert "import app_core" not in text


def test_app_core_keeps_step5_compatibility_exports():
    text = CORE.read_text(encoding="utf-8")
    for required in [
        "from helpers.numbers import (",
        "from services.delivery_operations import (",
        "from services.order_lifecycle import CANCELLABLE_STATUSES, is_cancellable",
        "from services.order_tracking import get_order_full",
    ]:
        assert required in text
    assert "__all__ = [name for name in globals() if not name.startswith('__')]" in text


def test_store_order_visibility_and_active_rules_are_preserved():
    from services.order_lifecycle import store_order_visible_to_store, is_store_order_active
    assert store_order_visible_to_store({"status": "PENDING_PAYMENT", "payment_method": "ONLINE"}) is False
    assert store_order_visible_to_store({"status": "PLACED", "payment_method": "COD", "payment_status": "PENDING"}) is True
    assert store_order_visible_to_store({"status": "PLACED", "payment_method": "ONLINE", "payment_status": "PAID"}) is True
    assert is_store_order_active({"status": "PLACED", "payment_method": "COD"}) is True
    assert is_store_order_active({"status": "DELIVERED", "payment_method": "COD"}) is False
    assert is_store_order_active({"status": "CANCELLED", "payment_method": "COD"}) is False


def test_delivery_transition_policy_is_preserved():
    from services.order_lifecycle import is_delivery_transition_allowed
    assert is_delivery_transition_allowed("ASSIGNED_TO_DELIVERY", "REACHED_STORE") is True
    assert is_delivery_transition_allowed("ASSIGNED_TO_DELIVERY", "OUT_FOR_DELIVERY") is True
    assert is_delivery_transition_allowed("PICKED_UP", "DELIVERED") is False
    assert is_delivery_transition_allowed("OUT_FOR_DELIVERY", "DELIVERED") is True
    assert is_delivery_transition_allowed("DELIVERED", "DELIVERY_FAILED") is False
    assert is_delivery_transition_allowed("PLACED", "INVALID") is False


def test_customer_cancellation_window_rule_is_preserved():
    from services.order_lifecycle import is_cancellable
    assert is_cancellable("placed") is True
    assert is_cancellable("PACKAGING") is True
    assert is_cancellable("SHIPMENT_READY") is False
    assert is_cancellable("DELIVERED") is False


def test_store_cancellation_uses_shared_stock_restoration_service():
    shared_text = (ROOT / "routes" / "store" / "shared.py").read_text(encoding="utf-8")
    route_text = (ROOT / "routes" / "store" / "orders.py").read_text(encoding="utf-8")
    assert "from services.order_inventory import _release_order_stock_items" in shared_text
    assert "_release_order_stock_items(order_items)" in route_text


def test_orders_route_uses_extracted_stock_service():
    text = (ROOT / "routes" / "orders" / "shared.py").read_text(encoding="utf-8")
    assert "from services.order_inventory import _release_order_stock_items, _reserve_order_stock_items" in text
    tree = ast.parse(text)
    names = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    assert "_reserve_order_stock_items" not in names
    assert "_release_order_stock_items" not in names
    assert "_order_item_reserved_products" not in names
