import pytest
from tests.runtime.conftest import login_as

@pytest.mark.runtime
@pytest.mark.smoke
def test_store_user_customer_track_url_redirects_to_store_tracker(client, runtime_app, bson_object_id):
    db = runtime_app["db"]
    user = login_as(client, db, bson_object_id, "store")
    oid = bson_object_id()
    response = client.get(f"/orders/{oid}", follow_redirects=False)
    assert response.status_code in {301,302,303,307,308}
    assert f"/store/orders/{oid}/track" in response.headers["Location"]

@pytest.mark.runtime
@pytest.mark.smoke
@pytest.mark.parametrize("role,path", [
    ("admin", "/admin/dashboard"),
    ("delivery", "/delivery"),
])
def test_admin_and_delivery_dashboards_do_not_500_with_empty_fixture(client, runtime_app, bson_object_id, role, path):
    db = runtime_app["db"]
    login_as(client, db, bson_object_id, role)
    response = client.get(path, follow_redirects=False)
    assert response.status_code < 500

@pytest.mark.runtime
@pytest.mark.smoke
def test_store_dashboard_does_not_500_with_minimal_store_fixture(client, runtime_app, bson_object_id):
    db = runtime_app["db"]
    user = login_as(client, db, bson_object_id, "store")
    db.stores.insert_one({"_id":bson_object_id(), "user_id":str(user["_id"]), "name":"Fixture Store", "is_active":1, "is_online":1, "created_at":"2026-08-26T00:00:00"})
    response = client.get("/store/dashboard", follow_redirects=False)
    assert response.status_code < 500
