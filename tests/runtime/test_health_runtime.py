import pytest

@pytest.mark.runtime
@pytest.mark.smoke
def test_liveness_endpoint(client):
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.get_json()["status"] == "alive"

@pytest.mark.runtime
@pytest.mark.smoke
def test_readiness_endpoint_checks_mongo_and_uploads(client, runtime_app, monkeypatch):
    import routes.health.routes as health_routes
    class ReadyMongo:
        def command(self, name):
            assert name == "ping"
            return {"ok": 1}
    monkeypatch.setattr(health_routes, "mongo", ReadyMongo())
    response = client.get("/health/ready")
    assert response.status_code == 200
    body = response.get_json()
    assert body["ok"] is True
    assert body["checks"] == {"mongo":"ok", "uploads":"ok"}
