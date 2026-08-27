from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from tests.support.source_contracts import (
    build_only_aliases,
    form_contracts,
    project_root,
    route_contracts,
)

ROOT = project_root()

EXPECTED_ROUTE_REGISTRIES = {
    "routes.public.routes",
    "routes.location.routes",
    "routes.auth.routes",
    "routes.customer.routes",
    "routes.products.routes",
    "routes.cart.routes",
    "routes.orders.routes",
    "routes.admin.routes",
    "routes.store.routes",
    "routes.delivery.routes",
    "routes.external_delivery.routes",
    "routes.api.routes",
    "routes.health.routes",
}


def _source(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def _tree(name: str):
    return ast.parse(_source(name), filename=name)


@pytest.mark.static
def test_step8_app_py_is_thin_factory_entrypoint():
    text = _source("app.py")
    assert len(text.splitlines()) < 40
    assert "from app_factory import create_app" in text
    assert "app = create_app()" in text
    assert "import routes." not in text
    assert "Flask(" not in text
    assert "app.run(" not in text


@pytest.mark.static
def test_step8_wsgi_is_production_factory_entrypoint():
    text = _source("wsgi.py")
    assert "from app_factory import create_app" in text
    assert "app = create_app()" in text
    assert "application = app" in text
    assert "app.run(" not in text
    assert "development" not in text


@pytest.mark.static
def test_step8_app_core_no_longer_constructs_or_runs_flask():
    text = _source("app_core.py")
    assert "Flask(" not in text
    assert "app.run(" not in text
    assert "from app_factory import get_base_app" in text
    assert "app = get_base_app()" in text


@pytest.mark.static
def test_step8_factory_centralizes_exact_route_registry_inventory():
    tree = _tree("app_factory.py")
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
        if alias.name.startswith("routes.")
    }
    assert imports == EXPECTED_ROUTE_REGISTRIES


@pytest.mark.static
def test_step8_factory_has_no_database_bootstrap_side_effect():
    text = _source("app_factory.py")
    assert "initialize_database" not in text
    assert "ensure_mongo_indexes" not in text
    assert "ensure_admin_seed_password" not in text
    assert "database_init" not in text


@pytest.mark.static
def test_step8_factory_configures_base_app_before_route_registration():
    text = _source("app_factory.py")
    for required in (
        "configure_application(flask_app",
        "register_security(flask_app)",
        "configure_uploads(flask_app)",
        "warn_missing_production_sender_settings",
        "_register_application_routes()",
    ):
        assert required in text
    assert "_routes_registered" in text
    assert "RLock" in text


@pytest.mark.static
def test_step8_dev_server_helpers_are_not_in_production_bootstrap():
    app_text = _source("app.py")
    wsgi_text = _source("wsgi.py")
    dev_text = _source("development.py")
    assert "run_development_server" in app_text
    assert "if __name__ == \"__main__\"" in app_text
    assert "run_development_server" not in wsgi_text
    assert "NEFRESH_AUTO_RELOAD" in dev_text
    assert "app.run(" in dev_text


@pytest.mark.static
def test_step8_route_form_and_alias_baselines_remain_exact():
    expected_routes = json.loads((ROOT / "tests" / "baselines" / "route_contracts.json").read_text(encoding="utf-8"))
    expected_forms = json.loads((ROOT / "tests" / "baselines" / "form_contracts.json").read_text(encoding="utf-8"))
    expected_aliases = json.loads((ROOT / "tests" / "baselines" / "build_only_aliases.json").read_text(encoding="utf-8"))
    assert route_contracts() == expected_routes
    assert form_contracts() == expected_forms
    assert build_only_aliases() == expected_aliases
    assert len(expected_routes) == 282
    assert len(expected_forms) == 182
    assert len(expected_aliases) == 2


@pytest.mark.static
def test_step8_legacy_app_core_compatibility_exports_remain():
    text = _source("app_core.py")
    assert "app = get_base_app()" in text
    assert "__all__ = [name for name in globals() if not name.startswith('__')]" in text
