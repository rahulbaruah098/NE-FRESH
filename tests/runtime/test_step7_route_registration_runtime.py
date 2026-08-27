import pytest

from tests.support.source_contracts import route_contracts


@pytest.mark.runtime
@pytest.mark.smoke
def test_step7_all_frozen_live_routes_are_registered(runtime_app):
    app = runtime_app["app"]
    actual = set()
    for rule in app.url_map.iter_rules():
        for method in sorted(set(rule.methods or ()) - {"HEAD", "OPTIONS"}):
            actual.add((rule.rule, method, rule.endpoint))

    expected = {(row["path"], row["method"], row["endpoint"]) for row in route_contracts(runtime_app["root"])}
    missing = sorted(expected - actual)
    assert missing == []


@pytest.mark.runtime
@pytest.mark.smoke
def test_step7_representative_endpoints_are_served_from_domain_modules(runtime_app):
    app = runtime_app["app"]
    expected_modules = {
        "admin_dashboard": "routes.admin.dashboard",
        "store_order_status": "routes.store.orders",
        "checkout": "routes.orders.checkout",
        "delivery_status": "routes.delivery.actions",
    }
    for endpoint, module_name in expected_modules.items():
        assert endpoint in app.view_functions
        assert app.view_functions[endpoint].__module__ == module_name
