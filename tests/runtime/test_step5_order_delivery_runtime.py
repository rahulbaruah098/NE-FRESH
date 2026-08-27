import pytest
from tests.runtime.conftest import login_as


@pytest.mark.runtime
def test_repeated_customer_cancel_does_not_restore_stock_twice(client, runtime_app, bson_object_id):
    db = runtime_app["db"]
    user = login_as(client, db, bson_object_id, "customer")
    oid, pid = bson_object_id(), bson_object_id()
    db.products.insert_one({"_id": pid, "is_active": 1, "stock_quantity": 3.0, "name": "Rice"})
    db.orders.insert_one({
        "_id": oid, "user_id": str(user["_id"]), "status": "PLACED",
        "payment_method": "COD", "payment_status": "PENDING",
        "items_subtotal": 100.0, "delivery_fee": 0.0, "platform_fee": 0.0,
        "tip_amount": 0.0, "total_payable": 100.0,
    })
    db.order_items.insert_one({"_id": bson_object_id(), "order_id": oid, "product_id": pid, "quantity": 2, "product_name": "Rice"})

    first = client.post(f"/orders/{oid}/cancel", follow_redirects=False)
    second = client.post(f"/orders/{oid}/cancel", follow_redirects=False)
    assert first.status_code in {301, 302, 303}
    assert second.status_code in {301, 302, 303}
    assert db.products.find_one({"_id": pid})["stock_quantity"] == 5.0
    assert db.order_events.count_documents({"order_id": oid, "status": "CANCELLED"}) == 1


@pytest.mark.runtime
def test_store_cancel_restores_bundle_children_once(client, runtime_app, bson_object_id):
    db = runtime_app["db"]
    user = login_as(client, db, bson_object_id, "store")
    sid, oid = bson_object_id(), bson_object_id()
    p1, p2 = bson_object_id(), bson_object_id()
    db.stores.insert_one({"_id": sid, "user_id": str(user["_id"]), "store_name": "Test Store"})
    db.products.insert_many([
        {"_id": p1, "is_active": 1, "stock_quantity": 8.0, "name": "Rice"},
        {"_id": p2, "is_active": 1, "stock_quantity": 14.0, "name": "Oil"},
    ])
    db.orders.insert_one({
        "_id": oid, "store_id": sid, "status": "PLACED", "payment_method": "COD", "payment_status": "PENDING"
    })
    db.order_items.insert_one({
        "_id": bson_object_id(), "order_id": oid, "item_type": "bundle", "bundle_id": str(bson_object_id()),
        "quantity": 2,
        "bundle_items_snapshot": [
            {"product_id": p1, "quantity": 1, "product_name_snapshot": "Rice"},
            {"product_id": p2, "quantity": 3, "product_name_snapshot": "Oil"},
        ],
    })

    first = client.post(f"/store/orders/{oid}/status", data={"status": "CANCELLED"}, follow_redirects=False)
    second = client.post(f"/store/orders/{oid}/status", data={"status": "CANCELLED"}, follow_redirects=False)
    assert first.status_code in {301, 302, 303}
    assert second.status_code in {301, 302, 303}
    assert db.products.find_one({"_id": p1})["stock_quantity"] == 10.0
    assert db.products.find_one({"_id": p2})["stock_quantity"] == 20.0
    order = db.orders.find_one({"_id": oid})
    assert order["status"] == "CANCELLED"
    assert order["cancelled_by"] == "store"


@pytest.mark.runtime
def test_delivery_assignment_is_conflict_safe_and_idempotent(runtime_app, bson_object_id):
    db = runtime_app["db"]
    from services import delivery_operations as ops

    sid, oid = bson_object_id(), bson_object_id()
    r1, r2 = bson_object_id(), bson_object_id()
    db.stores.insert_one({"_id": sid, "store_name": "Pickup", "latitude": 26.1, "longitude": 91.7})
    db.users.insert_many([
        {"_id": r1, "role": "delivery", "name": "Rider One", "is_active": 1},
        {"_id": r2, "role": "delivery", "name": "Rider Two", "is_active": 1},
    ])
    db.delivery_availability.insert_many([
        {"user_id": str(r1), "active": True, "latitude": 26.1, "longitude": 91.7},
        {"user_id": str(r2), "active": True, "latitude": 26.1, "longitude": 91.7},
    ])
    db.orders.insert_one({"_id": oid, "store_id": sid, "status": "SHIPMENT_READY"})

    first = ops.assign_delivery_partner_to_order(oid, str(r1), actor={"id": "store1", "role": "store", "name": "Store"})
    repeat = ops.assign_delivery_partner_to_order(oid, str(r1), actor={"id": "store1", "role": "store", "name": "Store"})
    conflict = ops.assign_delivery_partner_to_order(oid, str(r2), actor={"id": "store1", "role": "store", "name": "Store"})

    assert first["ok"] is True
    assert repeat["ok"] is True
    assert conflict["ok"] is False
    order = db.orders.find_one({"_id": oid})
    assert order["delivery_partner_id"] == str(r1)
    assert order["status"] == "ASSIGNED_TO_DELIVERY"
    assert db.order_events.count_documents({"order_id": oid, "status": "ASSIGNED_TO_DELIVERY"}) == 1


@pytest.mark.runtime
def test_clear_delivery_assignment_returns_order_to_shipment_ready(runtime_app, bson_object_id):
    db = runtime_app["db"]
    from services import delivery_operations as ops

    oid, rid = bson_object_id(), bson_object_id()
    db.orders.insert_one({
        "_id": oid, "status": "ASSIGNED_TO_DELIVERY", "delivery_partner_id": str(rid),
        "delivery_partner_name": "Rider"
    })
    db.delivery_availability.insert_one({"user_id": str(rid), "active": True, "current_order_id": str(oid)})
    result = ops.clear_delivery_assignment(oid, actor={"id": "store1", "role": "store"})
    assert result["ok"] is True
    order = db.orders.find_one({"_id": oid})
    assert order["status"] == "SHIPMENT_READY"
    assert order["delivery_partner_id"] is None
    availability = db.delivery_availability.find_one({"user_id": str(rid)})
    assert availability["current_order_id"] is None


@pytest.mark.runtime
def test_order_tracking_hydration_normalizes_swapped_coordinates(runtime_app, bson_object_id):
    db = runtime_app["db"]
    from services import order_tracking

    oid, sid = bson_object_id(), bson_object_id()
    db.orders.insert_one({"_id": oid, "store_id": sid, "user_id": "customer1", "status": "PLACED"})
    db.stores.insert_one({"_id": sid, "store_name": "Store", "latitude": 26.2, "longitude": 91.8})
    db.order_addresses.insert_one({
        "_id": bson_object_id(), "order_id": oid, "line1": "Test", "latitude": 92.0, "longitude": 26.0
    })
    result = order_tracking.get_order_full(str(oid))
    assert result is not None
    assert result["address"]["latitude"] == 26.0
    assert result["address"]["longitude"] == 92.0
    assert result["tracking_map"]["store"]["latitude"] == 26.2
