from pathlib import Path
import ast

ROOT = Path(__file__).resolve().parents[1]


def _module(path):
    return ast.parse((ROOT / path).read_text(encoding="utf-8"))


def _top_level_function_names(path):
    tree = _module(path)
    return {node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}


def test_infrastructure_modules_exist():
    expected = [
        "config.py",
        "logging_config.py",
        "security.py",
        "template_context.py",
        "uploads.py",
        "extensions.py",
        "database_init.py",
    ]
    assert [name for name in expected if not (ROOT / name).is_file()] == []


def test_app_core_no_longer_defines_extracted_infrastructure_helpers():
    names = _top_level_function_names("app_core.py")
    extracted = {
        "_env_bool", "_is_production_env", "is_debug_logging_enabled",
        "log_debug", "log_warning", "_get_csrf_token", "_inject_csrf_helpers",
        "_protect_html_form_posts", "inject_globals", "inject_cart_count",
        "inject_footer_links", "inject_site_brand_settings", "allowed_file",
        "ensure_admin_seed_password", "add_no_cache_headers",
    }
    assert sorted(names & extracted) == []


def test_database_init_has_no_import_time_database_calls():
    for rel in ["app_core.py", "database_init.py", "mongo_db.py"]:
        tree = _module(rel)
        forbidden = {"ensure_mongo_indexes", "ensure_admin_seed_password", "initialize_database"}
        calls = []
        for node in tree.body:
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                fn = node.value.func
                name = fn.id if isinstance(fn, ast.Name) else None
                if name in forbidden:
                    calls.append(name)
        assert calls == [], f"{rel} has import-time DB mutation calls: {calls}"


def test_init_db_script_no_longer_imports_app_core():
    source = (ROOT / "scripts" / "init_db.py").read_text(encoding="utf-8")
    assert "from app_core import" not in source
    assert "import app_core" not in source
    assert "from database_init import initialize_database" in source


def test_app_core_compatibility_exports_remain_enabled():
    source = (ROOT / "app_core.py").read_text(encoding="utf-8")
    assert "__all__ = [name for name in globals() if not name.startswith('__')]" in source
