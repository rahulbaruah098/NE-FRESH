import pytest
from tests.runtime.conftest import login_as

@pytest.mark.runtime
def test_cod_customer_cancel_restores_stock_and_voids_finance(client, runtime_app, bson_object_id):
    db = runtime_app["db"]
    user = login_as(client, db, bson_object_id, "customer")
    oid, pid = bson_object_id(), bson_object_id()
    db.products.insert_one({"_id":pid,"is_active":1,"stock_quantity":3.0,"name":"Rice"})
    db.orders.insert_one({
        "_id":oid,"user_id":str(user["_id"]),"status":"PLACED","payment_method":"COD","payment_status":"PENDING",
        "items_subtotal":100.0,"delivery_fee":30.0,"platform_fee":20.0,"tip_amount":0.0,"total_payable":150.0,
        "store_payout_status":"PENDING_AFTER_DELIVERY"
    })
    db.order_items.insert_one({"_id":bson_object_id(),"order_id":oid,"product_id":pid,"quantity":2,"product_name":"Rice"})
    response = client.post(f"/orders/{oid}/cancel", follow_redirects=False)
    assert response.status_code in {301,302,303}
    product = db.products.find_one({"_id":pid})
    order = db.orders.find_one({"_id":oid})
    assert product["stock_quantity"] == 5.0
    assert order["status"] == "CANCELLED"
    assert order["cancelled_by"] == "customer"
    assert order["payment_status"] == "VOID"
    assert order["refund_status"] == "NOT_REQUIRED"
    assert order["store_payout_status"] == "NOT_REQUIRED"

@pytest.mark.runtime
@pytest.mark.finance
def test_paid_online_customer_cancel_moves_to_admin_refund_queue(client, runtime_app, bson_object_id):
    db = runtime_app["db"]
    user = login_as(client, db, bson_object_id, "customer")
    oid, pid = bson_object_id(), bson_object_id()
    db.products.insert_one({"_id":pid,"is_active":1,"stock_quantity":3.0,"name":"Rice"})
    db.orders.insert_one({
        "_id":oid,"user_id":str(user["_id"]),"status":"PLACED","payment_method":"ONLINE","payment_status":"PAID",
        "items_subtotal":100.0,"delivery_fee":30.0,"platform_fee":20.0,"tip_amount":5.0,"total_payable":155.0,
        "store_payout_status":"PENDING_AFTER_DELIVERY"
    })
    db.order_items.insert_one({"_id":bson_object_id(),"order_id":oid,"product_id":pid,"quantity":1,"product_name":"Rice"})
    response = client.post(f"/orders/{oid}/cancel", follow_redirects=False)
    assert response.status_code in {301,302,303}
    order = db.orders.find_one({"_id":oid})
    assert order["refund_status"] == "READY_FOR_REFUND"
    assert order["refund_amount"] == 155.0
    assert order["payment_status"] == "PAID"
    assert order["platform_fee_status"] == "REFUND_PENDING"
    assert order["order_settlement_status"] == "REFUND_PENDING"
