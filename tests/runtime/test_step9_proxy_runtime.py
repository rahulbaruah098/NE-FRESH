from __future__ import annotations

import pytest


@pytest.mark.runtime
@pytest.mark.smoke
def test_step9_proxyfix_trusts_exact_single_nginx_hop(monkeypatch):
    flask = pytest.importorskip("flask")
    from security import register_trusted_proxy

    monkeypatch.setenv("TRUST_PROXY_HEADERS", "1")
    monkeypatch.setenv("PROXY_FIX_X_FOR", "1")
    monkeypatch.setenv("PROXY_FIX_X_PROTO", "1")
    monkeypatch.setenv("PROXY_FIX_X_HOST", "1")
    monkeypatch.setenv("PROXY_FIX_X_PORT", "1")
    monkeypatch.setenv("PROXY_FIX_X_PREFIX", "0")

    app = flask.Flask("step9-proxy-enabled")
    register_trusted_proxy(app)

    @app.get("/")
    def probe():
        return flask.jsonify(
            scheme=flask.request.scheme,
            host=flask.request.host,
            remote_addr=flask.request.remote_addr,
        )

    response = app.test_client().get(
        "/",
        headers={
            "Host": "127.0.0.1:8000",
            "X-Forwarded-For": "203.0.113.42",
            "X-Forwarded-Proto": "https",
            "X-Forwarded-Host": "shop.example.test",
            "X-Forwarded-Port": "443",
        },
    )
    data = response.get_json()
    assert data["scheme"] == "https"
    assert data["host"].split(":", 1)[0] == "shop.example.test"
    assert data["remote_addr"] == "203.0.113.42"


@pytest.mark.runtime
@pytest.mark.smoke
def test_step9_proxyfix_ignores_forwarded_headers_when_not_trusted(monkeypatch):
    flask = pytest.importorskip("flask")
    from security import register_trusted_proxy

    monkeypatch.setenv("TRUST_PROXY_HEADERS", "0")
    app = flask.Flask("step9-proxy-disabled")
    register_trusted_proxy(app)

    @app.get("/")
    def probe():
        return flask.jsonify(scheme=flask.request.scheme, host=flask.request.host)

    response = app.test_client().get(
        "/",
        headers={
            "Host": "127.0.0.1:8000",
            "X-Forwarded-Proto": "https",
            "X-Forwarded-Host": "spoofed.example.test",
        },
    )
    data = response.get_json()
    assert data["scheme"] == "http"
    assert data["host"] == "127.0.0.1:8000"
