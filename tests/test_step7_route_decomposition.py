from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

import pytest

from tests.support.source_contracts import project_root, route_contracts

ROOT = project_root()
ROLES = ("admin", "store", "orders", "delivery")
EXPECTED_ROUTE_COUNTS = {"admin": 73, "store": 60, "orders": 16, "delivery": 21}
EXPECTED_DOMAIN_MODULES = {
    "admin": {
        "settings.py", "refunds.py", "settlements.py", "dashboard.py", "notifications.py",
        "stores.py", "delivery_management.py", "user_exports.py", "complaints.py", "users.py",
        "contact_messages.py", "profile.py",
    },
    "store": {
        "public_storefront.py", "dashboard_settings.py", "products.py", "orders.py",
        "delivery_management.py", "returns.py", "inventory.py", "categories.py", "reviews.py",
        "complaints.py", "profile.py", "notifications.py", "transactions.py",
    },
    "orders": {"customer_actions.py", "checkout.py", "payments.py", "history_tracking.py", "api.py"},
    "delivery": {"profile_support.py", "dashboard.py", "orders.py", "earnings.py", "actions.py", "tracking.py"},
}


def _route_functions(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    rows = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if any(isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr == "route" for d in node.decorator_list):
            rows.append(node)
    return rows


def _function_hash(node) -> str:
    return hashlib.sha256(ast.dump(node, include_attributes=False).encode("utf-8")).hexdigest()


@pytest.mark.static
def test_step7_registry_modules_are_compact_and_decorator_free():
    for role in ROLES:
        path = ROOT / "routes" / role / "routes.py"
        assert len(path.read_text(encoding="utf-8").splitlines()) < 100
        assert _route_functions(path) == []
        text = path.read_text(encoding="utf-8")
        assert f"from . import shared as _shared" in text


@pytest.mark.static
def test_step7_shared_modules_hold_helpers_not_live_routes():
    for role in ROLES:
        path = ROOT / "routes" / role / "shared.py"
        assert path.is_file()
        assert _route_functions(path) == []
        text = path.read_text(encoding="utf-8")
        assert "__all__ = [name for name in globals() if not name.startswith('__')]" in text


@pytest.mark.static
def test_step7_domain_module_inventory_is_exact():
    for role, expected in EXPECTED_DOMAIN_MODULES.items():
        folder = ROOT / "routes" / role
        actual = {p.name for p in folder.glob("*.py")} - {"__init__.py", "routes.py", "shared.py"}
        assert actual == expected


@pytest.mark.static
def test_step7_route_counts_are_preserved_by_role():
    for role, expected in EXPECTED_ROUTE_COUNTS.items():
        folder = ROOT / "routes" / role
        total = sum(len(_route_functions(p)) for p in folder.glob("*.py"))
        assert total == expected
    assert sum(EXPECTED_ROUTE_COUNTS.values()) == 170


@pytest.mark.static
def test_step7_moved_route_function_ast_matches_step6_exactly():
    expected = json.loads((ROOT / "tests" / "baselines" / "step7_route_function_ast_hashes.json").read_text(encoding="utf-8"))
    actual = {}
    for role in ROLES:
        folder = ROOT / "routes" / role
        for path in folder.glob("*.py"):
            if path.name in {"__init__.py", "routes.py", "shared.py"}:
                continue
            for node in _route_functions(path):
                actual[f"{role}:{node.name}"] = _function_hash(node)
    assert actual == expected


@pytest.mark.static
def test_step7_domain_modules_use_shared_namespace_not_app_core_directly():
    for role in ROLES:
        folder = ROOT / "routes" / role
        for path in folder.glob("*.py"):
            if path.name in {"__init__.py", "routes.py", "shared.py"}:
                continue
            text = path.read_text(encoding="utf-8")
            assert f"from routes.{role}.shared import *" in text
            assert "from app_core import" not in text
            assert "import app_core" not in text


@pytest.mark.static
def test_step7_does_not_introduce_blueprint_namespacing():
    for role in ROLES:
        text = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "routes" / role).glob("*.py"))
        assert "Blueprint(" not in text
        assert ".register_blueprint(" not in text


@pytest.mark.static
def test_step7_app_bootstrap_contract_stays_on_four_registries():
    # Step 8 moves central bootstrap into app_factory.py; Step 7's four compact
    # registries must still be the only role-level modules imported there.
    factory_text = (ROOT / "app_factory.py").read_text(encoding="utf-8")
    app_text = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "from app_factory import create_app" in app_text
    for role in ROLES:
        assert f"import routes.{role}.routes" in factory_text
    for role, modules in EXPECTED_DOMAIN_MODULES.items():
        for module in modules:
            modname = module[:-3]
            assert f"import routes.{role}.{modname}" not in factory_text
