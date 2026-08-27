from pathlib import Path
import ast
import math
import re

from tests.support.extract_source import load_source_definitions

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "app_core.py"


def _top_level_function_names(path):
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    return {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def test_step4_modules_exist():
    expected = [
        "helpers/__init__.py",
        "helpers/formatting.py",
        "helpers/identifiers.py",
        "services/product_pricing.py",
        "services/product_units.py",
        "services/product_bundles.py",
        "services/store_categories.py",
        "services/store_notifications.py",
        "services/store_catalog.py",
        "services/store_profile.py",
    ]
    assert [p for p in expected if not (ROOT / p).is_file()] == []


def test_app_core_no_longer_defines_extracted_low_risk_helpers():
    names = _top_level_function_names(CORE)
    extracted = {
        "_safe_float", "_calculate_product_pricing_from_form",
        "normalize_bundle_discount_type", "build_bundle_item_snapshots",
        "calculate_bundle_pricing", "calculate_bundle_stock", "build_live_product_bundle",
        "build_product_bundle_document", "validate_product_bundle_for_cart", "build_bundle_cart_snapshot",
        "normalize_unit_type", "normalize_unit_label", "unit_quantity_rules",
        "normalize_quantity_by_unit", "hydrate_product_unit_fields", "build_unit_product_update_from_form",
        "_clean_pin", "_clean_state", "is_assam_state", "_norm_status", "_norm_role",
        "normalize_phone", "order_status_label", "_store_identity_values", "_order_identity_values",
        "_get_store_products", "_category_slug", "_ensure_store_categories", "_get_store_categories",
        "_get_store_category_by_id", "_get_store_category_by_name", "_get_category_product_count",
        "_build_store_profile_context", "_store_notification_stats", "_create_store_notification",
        "_hydrate_store_notification", "_sync_store_order_notifications",
    }
    assert sorted(names & extracted) == []


def test_app_core_keeps_compatibility_import_layer():
    text = CORE.read_text(encoding="utf-8")
    for required in [
        "from services.product_pricing import",
        "from services.product_units import",
        "from services.product_bundles import",
        "from services.store_categories import",
        "from services.store_notifications import",
        "from services.store_catalog import _get_store_products",
        "from services.store_profile import _build_store_profile_context",
        "from helpers.formatting import",
        "from helpers.identifiers import",
    ]:
        assert required in text
    assert "__all__ = [name for name in globals() if not name.startswith('__')]" in text


def test_extracted_modules_do_not_import_app_core_back():
    for rel in [
        "helpers/formatting.py", "helpers/identifiers.py",
        "services/product_pricing.py", "services/product_units.py", "services/product_bundles.py",
        "services/store_categories.py", "services/store_notifications.py",
        "services/store_catalog.py", "services/store_profile.py",
    ]:
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "import app_core" not in text
        assert "from app_core import" not in text


def test_product_percent_discount_behavior_is_preserved():
    from services.product_pricing import _calculate_product_pricing_from_form
    result = _calculate_product_pricing_from_form({
        "original_price_per_unit": "200",
        "discount_enabled": "on",
        "discount_type": "percent",
        "discount_value": "25",
    })
    assert result["original_price_per_unit"] == 200.0
    assert result["price_per_unit"] == 150.0
    assert result["discount_amount_per_unit"] == 50.0
    assert result["discount_percent"] == 25.0


def test_product_fixed_discount_clamps_to_base_price():
    from services.product_pricing import _calculate_product_pricing_from_form
    result = _calculate_product_pricing_from_form({
        "original_price_per_unit": "100",
        "discount_enabled": "yes",
        "discount_type": "amount",
        "discount_value": "140",
    })
    assert result["price_per_unit"] == 0.0
    assert result["discount_value"] == 100.0
    assert result["discount_percent"] == 100.0


def test_unit_normalization_and_default_labels_are_preserved():
    from services.product_units import normalize_unit_type, normalize_unit_label
    assert normalize_unit_type("volume") == "VOLUME"
    assert normalize_unit_type("unknown") == "WEIGHT"
    assert normalize_unit_label("VOLUME", "") == "liter"
    assert normalize_unit_label("COUNT", "custom", "pack") == "pack"


def test_unit_quantity_rules_are_preserved():
    from services.product_units import normalize_quantity_by_unit
    assert normalize_quantity_by_unit(0.5, "WEIGHT", "kg") == (0.5, None)
    qty, error = normalize_quantity_by_unit(0.1, "WEIGHT", "kg")
    assert qty is None and "0.25 kg" in error
    assert normalize_quantity_by_unit(2.4, "COUNT", "piece") == (2, None)


def test_product_hydration_keeps_existing_shape():
    from services.product_units import hydrate_product_unit_fields
    product = {"unit_type": "count", "unit_label": "packet", "price_per_unit": "12.5", "stock_quantity": "8"}
    result = hydrate_product_unit_fields(product)
    assert result is product
    assert product["unit_type"] == "COUNT"
    assert product["unit_label"] == "packet"
    assert product["price_per_unit"] == 12.5
    assert product["stock_quantity"] == 8.0
    assert product["quantity_min"] == 1


def test_bundle_percent_pricing_is_preserved():
    path = ROOT / "services/product_bundles.py"
    ns = load_source_definitions(
        path,
        function_names={"_bundle_money_float", "normalize_bundle_discount_type", "calculate_bundle_pricing"},
        assignment_names={"BUNDLE_DISCOUNT_TYPES"},
        namespace={},
    )
    items = [{"line_total_snapshot": 100}, {"line_total_snapshot": 50}]
    result = ns["calculate_bundle_pricing"](items, "percent", 10)
    assert result["items_total"] == 150.0
    assert result["bundle_price"] == 135.0
    assert result["savings_amount"] == 15.0


def test_bundle_stock_calculation_is_preserved():
    path = ROOT / "services/product_bundles.py"
    ns = load_source_definitions(
        path,
        function_names={"_bundle_money_float", "_bundle_quantity_float", "calculate_bundle_stock"},
        namespace={"math": math},
    )
    result = ns["calculate_bundle_stock"]([
        {"quantity": 2, "stock_quantity_snapshot": 9},
        {"quantity": 3, "stock_quantity_snapshot": 8},
    ])
    assert result["max_bundle_stock"] == 2
    assert result["stock_status"] == "LOW_STOCK"
    assert result["stock_blockers"] == []


def test_category_slug_behavior_is_preserved():
    ns = load_source_definitions(
        ROOT / "services/store_categories.py",
        function_names={"_category_slug"},
        namespace={"re": re},
    )
    assert ns["_category_slug"](" Fresh Fish & Meat ") == "fresh-fish-meat"


def test_shared_formatting_behavior_is_preserved():
    from helpers.formatting import _clean_pin, is_assam_state, normalize_phone, order_status_label
    assert _clean_pin(" 781 001 ") == "781001"
    assert is_assam_state("AS") is True
    assert normalize_phone("98765-43210") == "+919876543210"
    assert order_status_label("shipment_ready") == "Shipment Ready"


def test_identifier_helpers_keep_legacy_and_new_shapes():
    class FakeObjectId(str):
        def __new__(cls, value):
            return str.__new__(cls, str(value))
    ns = load_source_definitions(
        ROOT / "helpers/identifiers.py",
        function_names={"_store_identity_values", "_order_identity_values"},
        namespace={"ObjectId": FakeObjectId},
    )
    values = ns["_store_identity_values"]("abc")
    assert "abc" in values
    order_values = ns["_order_identity_values"](["o1", "o2"])
    assert "o1" in order_values and "o2" in order_values


def test_store_service_modules_keep_database_dependency_outside_routes():
    for rel in [
        "services/product_bundles.py", "services/store_categories.py",
        "services/store_notifications.py", "services/store_catalog.py", "services/store_profile.py",
    ]:
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "from extensions import mongo" in text
        assert "@app.route" not in text
