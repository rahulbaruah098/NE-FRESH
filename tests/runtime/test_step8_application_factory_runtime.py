from __future__ import annotations

import importlib

import pytest

from tests.support.source_contracts import route_contracts


def _registered_contracts(app):
    rows = set()
    for rule in app.url_map.iter_rules():
        for method in sorted(set(rule.methods or ()) - {"HEAD", "OPTIONS"}):
            rows.add((rule.rule, method, rule.endpoint))
    return rows


def _hook_counts(app):
    return {
        "before": sum(len(v) for v in app.before_request_funcs.values()),
        "after": sum(len(v) for v in app.after_request_funcs.values()),
        "context": sum(len(v) for v in app.template_context_processors.values()),
    }


@pytest.mark.runtime
@pytest.mark.smoke
def test_step8_factory_repeated_calls_are_idempotent(runtime_app):
    factory = importlib.import_module("app_factory")
    app = runtime_app["app"]
    before_routes = _registered_contracts(app)

    first = factory.create_app()
    second = factory.create_app()

    assert first is app
    assert second is app
    assert _registered_contracts(app) == before_routes


@pytest.mark.runtime
@pytest.mark.smoke
def test_step8_wsgi_exports_factory_application_with_all_frozen_routes(runtime_app):
    wsgi = importlib.import_module("wsgi")
    app = runtime_app["app"]
    assert wsgi.app is app
    assert wsgi.application is app

    actual = _registered_contracts(app)
    expected = {(r["path"], r["method"], r["endpoint"]) for r in route_contracts(runtime_app["root"])}
    assert expected - actual == set()


@pytest.mark.runtime
@pytest.mark.smoke
def test_step8_repeated_factory_calls_do_not_duplicate_hooks(runtime_app):
    factory = importlib.import_module("app_factory")
    app = runtime_app["app"]
    before = _hook_counts(app)
    factory.create_app()
    factory.create_app()
    after = _hook_counts(app)
    assert after == before
