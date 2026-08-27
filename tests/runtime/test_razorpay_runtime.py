import pytest
from tests.runtime.conftest import login_as

@pytest.mark.runtime
@pytest.mark.finance
def test_razorpay_verification_rejects_order_id_mismatch_before_gateway_call(client, runtime_app, bson_object_id):
    db = runtime_app["db"]
    user = login_as(client, db, bson_object_id, "customer")
    attempt_id = bson_object_id()
    db.payment_attempts.insert_one({
        "_id":attempt_id,"user_id":str(user["_id"]),"status":"PENDING_PAYMENT",
        "razorpay_order_id":"order_expected"
    })
    response = client.post(f"/api/payment/verify-razorpay-payment/{attempt_id}", json={
        "razorpay_order_id":"order_wrong","razorpay_payment_id":"pay_1","razorpay_signature":"sig_1"
    })
    assert response.status_code == 400
    assert "mismatch" in response.get_json()["message"].lower()

@pytest.mark.runtime
@pytest.mark.finance
def test_razorpay_paid_order_verification_is_idempotent(client, runtime_app, bson_object_id):
    db = runtime_app["db"]
    user = login_as(client, db, bson_object_id, "customer")
    oid = bson_object_id()
    db.orders.insert_one({"_id":oid,"user_id":str(user["_id"]),"payment_status":"PAID","status":"PLACED"})
    response = client.post(f"/api/payment/verify-razorpay-payment/{oid}", json={})
    assert response.status_code == 200
    assert "already verified" in response.get_json()["message"].lower()
    assert db.orders.count_documents({"_id":oid}) == 1
