import ast
import re
from pathlib import Path
import pytest
from tests.support.source_contracts import project_root, route_contracts, build_only_aliases, literal_url_for_endpoints

ROOT = project_root()

@pytest.mark.static
def test_no_import_time_database_initialization_returns():
    core = ROOT / "app_core.py"
    tree = ast.parse(core.read_text(encoding="utf-8"))
    forbidden = {"ensure_mongo_indexes", "ensure_admin_seed_password", "_seed_pincodes_if_empty"}
    offenders = []
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            fn = node.value.func
            name = fn.id if isinstance(fn, ast.Name) else None
            if name in forbidden:
                offenders.append(name)
    assert offenders == []

@pytest.mark.static
def test_literal_url_for_endpoints_resolve():
    live = {r["endpoint"] for r in route_contracts()}
    live.update(a["endpoint"] for a in build_only_aliases())
    # Flask built-ins / special endpoints can be referenced without an explicit app route.
    allowed = {"static"}
    missing = sorted(set(literal_url_for_endpoints()) - live - allowed)
    assert missing == []

@pytest.mark.static
def test_removed_promotions_implementation_does_not_return():
    forbidden = [
        ROOT / "routes" / "store" / "promotions.py",
        ROOT / "services" / "commerce_promotions.py",
        ROOT / "templates" / "store_promotions.html",
        ROOT / "static" / "css" / "store" / "store-promotions.css",
    ]
    assert [str(p.relative_to(ROOT)) for p in forbidden if p.exists()] == []

@pytest.mark.static
def test_no_current_self_deducted_write_or_status():
    hits = []
    for path in list(ROOT.rglob("*.py")) + list((ROOT / "templates").rglob("*.html")):
        if "tests" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if "SELF_DEDUCTED" in text:
            hits.append(str(path.relative_to(ROOT)))
    assert hits == []

@pytest.mark.static
def test_live_role_routes_stay_role_scoped():
    rows = route_contracts()
    public_store_endpoints = {"store_profile_image"}
    for row in rows:
        if row["path"].startswith("/admin/") or row["path"] == "/admin":
            assert row["role"] == "admin", row
        if row["path"].startswith("/store/") or row["path"] == "/store":
            if row["endpoint"] not in public_store_endpoints:
                assert row["role"] == "store", row
        if row["path"].startswith("/delivery"):
            assert row["role"] in {"delivery", "api_authenticated"}, row


@pytest.mark.static
def test_step95_repository_cleanup_does_not_regress():
    """Keep proven-dead migration/UI/history artifacts out of the active source tree."""
    forbidden = [
        ROOT / "scripts" / "migrate_sqlite_to_mongo.py",
        ROOT / "services" / "README.md",
        ROOT / "templates" / "admin_approvals.html",
        ROOT / "templates" / "admin_delivery_mode_settings.html",
        ROOT / "templates" / "admin_external_delivery_orders.html",
        ROOT / "templates" / "admin_external_delivery_settings.html",
        ROOT / "templates" / "admin_users.html",
        ROOT / "templates" / "store_external_delivery.html",
        ROOT / "templates" / "verify_otp.html",
        ROOT / "static" / "css" / "store" / "store-external-delivery.css",
    ]
    assert [str(p.relative_to(ROOT)) for p in forbidden if p.exists()] == []
    assert list(ROOT.glob("STEP_*")) == []
