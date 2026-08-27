import pytest
from tests.runtime.conftest import login_as


@pytest.mark.runtime
@pytest.mark.finance
def test_admin_upi_verification_is_idempotent(client, runtime_app, bson_object_id):
    db = runtime_app["db"]
    login_as(client, db, bson_object_id, "admin")
    oid = bson_object_id()
    db.orders.insert_one({
        "_id": oid,
        "status": "DELIVERED",
        "payment_method": "COD",
        "payment_collection_channel": "UPI",
        "upi_delivery_reference": "UPI123456",
        "upi_delivery_reconciliation_status": "PENDING_ADMIN_VERIFICATION",
        "cod_collected_amount": 120.0,
        "total_payable": 120.0,
        "platform_fee": 10.0,
        "store_payout_amount": 100.0,
        "store_payout_status": "PENDING_PAYMENT_RECONCILIATION",
    })
    first = client.post(f"/admin/settlements/{oid}/upi-delivery-verified", data={"note": "verified"}, follow_redirects=False)
    second = client.post(f"/admin/settlements/{oid}/upi-delivery-verified", data={"note": "again"}, follow_redirects=False)
    assert first.status_code in {301, 302, 303}
    assert second.status_code in {301, 302, 303}
    order = db.orders.find_one({"_id": oid})
    assert order["upi_delivery_reconciliation_status"] == "VERIFIED"
    assert order["rider_cash_settlement_status"] == "NOT_REQUIRED"
    logs = [x for x in order.get("settlement_audit_logs", []) if x.get("action") == "UPI_AT_DELIVERY_VERIFIED_BY_ADMIN"]
    assert len(logs) == 1
    assert db.order_events.count_documents({"order_id": oid, "status": "UPI_AT_DELIVERY_VERIFIED"}) == 1


@pytest.mark.runtime
@pytest.mark.finance
def test_admin_rider_cash_receipt_is_idempotent(client, runtime_app, bson_object_id):
    db = runtime_app["db"]
    login_as(client, db, bson_object_id, "admin")
    oid = bson_object_id()
    db.orders.insert_one({
        "_id": oid,
        "status": "DELIVERED",
        "payment_method": "COD",
        "payment_collection_channel": "CASH",
        "payment_status": "COLLECTED_BY_RIDER",
        "rider_cash_settlement_status": "PENDING",
        "rider_cash_to_submit": 150.0,
        "platform_fee": 10.0,
        "store_payout_amount": 120.0,
    })
    first = client.post(f"/admin/settlements/{oid}/rider-cash-received", data={"note": "received"}, follow_redirects=False)
    second = client.post(f"/admin/settlements/{oid}/rider-cash-received", data={"note": "again"}, follow_redirects=False)
    assert first.status_code in {301, 302, 303}
    assert second.status_code in {301, 302, 303}
    order = db.orders.find_one({"_id": oid})
    assert order["rider_cash_settlement_status"] == "RECEIVED"
    logs = [x for x in order.get("settlement_audit_logs", []) if x.get("action") == "RIDER_CASH_RECEIVED_BY_ADMIN"]
    assert len(logs) == 1
    assert db.order_events.count_documents({"order_id": oid, "status": "RIDER_CASH_RECEIVED_BY_ADMIN"}) == 1


@pytest.mark.runtime
@pytest.mark.finance
def test_refund_after_paid_store_payout_creates_one_carry_forward_adjustment(client, runtime_app, bson_object_id):
    db = runtime_app["db"]
    login_as(client, db, bson_object_id, "admin")
    oid, sid = bson_object_id(), bson_object_id()
    db.orders.insert_one({
        "_id": oid,
        "store_id": sid,
        "store_name": "Test Store",
        "status": "DELIVERED",
        "return_status": "STORE_APPROVED",
        "refund_status": "READY_FOR_REFUND",
        "payment_method": "ONLINE",
        "payment_status": "PAID",
        "payment_reconciliation_status": "VERIFIED",
        "platform_fee_status": "RECEIVED",
        "items_subtotal": 100.0,
        "store_earning": 100.0,
        "store_payout_amount": 100.0,
        "store_payout_status": "PAID",
        "platform_fee": 10.0,
        "delivery_fee": 10.0,
        "tip_amount": 0.0,
        "total_payable": 120.0,
        "refund_items_amount": 25.0,
    })
    data = {
        "refund_items_amount": "25",
        "refund_delivery_fee": "0",
        "refund_platform_fee": "0",
        "refund_tip_amount": "0",
        "refund_method": "MANUAL",
        "refund_reference": "REF-1",
    }
    first = client.post(f"/admin/refund-processing/{oid}/process", data=data, follow_redirects=False)
    second = client.post(f"/admin/refund-processing/{oid}/process", data=data, follow_redirects=False)
    assert first.status_code in {301, 302, 303}
    assert second.status_code in {301, 302, 303}
    order = db.orders.find_one({"_id": oid})
    assert order["refund_status"] == "PROCESSED"
    assert order["store_adjustment_due"] == 25.0
    assert db.store_finance_adjustments.count_documents({"source_order_id": str(oid)}) == 1
    refund_logs = [x for x in order.get("refund_audit_logs", []) if x.get("action") == "REFUND_PROCESSED_BY_ADMIN"]
    assert len(refund_logs) == 1


@pytest.mark.runtime
@pytest.mark.finance
def test_store_adjustment_apply_and_rollback_preserve_ledger(runtime_app, bson_object_id):
    db = runtime_app["db"]
    from services import store_finance_adjustments as adj

    sid, source_order, payout_order = bson_object_id(), bson_object_id(), bson_object_id()
    db.orders.insert_one({"_id": source_order, "store_id": sid, "store_name": "Store"})
    source = db.orders.find_one({"_id": source_order})
    doc = adj.finance_create_store_adjustment(source, 40.0, actor={"id": "admin1", "name": "Admin"})
    assert doc["remaining_amount"] == 40.0

    applied, apps = adj.finance_apply_store_adjustments(sid, payout_order, 25.0, actor={"id": "admin1"})
    assert applied == 25.0
    assert len(apps) == 1
    after = db.store_finance_adjustments.find_one({"_id": doc["_id"]})
    assert after["remaining_amount"] == 15.0
    assert after["status"] == "PARTIAL"

    rolled = adj.finance_rollback_store_adjustments(apps, payout_order)
    assert rolled == 25.0
    restored = db.store_finance_adjustments.find_one({"_id": doc["_id"]})
    assert restored["remaining_amount"] == 40.0
    assert restored["applied_amount"] == 0.0
    assert restored["status"] == "OPEN"


@pytest.mark.runtime
@pytest.mark.finance
def test_monthly_delivery_settlement_is_paid_once(client, runtime_app, bson_object_id):
    db = runtime_app["db"]
    login_as(client, db, bson_object_id, "admin")
    rider_id = bson_object_id()
    oid = bson_object_id()
    db.orders.insert_one({
        "_id": oid,
        "status": "DELIVERED",
        "delivery_partner_id": str(rider_id),
        "delivery_partner_name": "Rider One",
        "delivery_payout_model": "MONTHLY_V1",
        "delivery_monthly_period": "2026-07",
        "delivery_monthly_settlement_status": "MONTHLY_ACCRUED",
        "delivery_boy_payout_amount": 35.0,
        "delivery_fee": 30.0,
        "tip_amount": 5.0,
        "payment_method": "ONLINE",
        "payment_status": "PAID",
        "payment_reconciliation_status": "VERIFIED",
        "platform_fee_status": "RECEIVED",
        "platform_fee": 10.0,
        "store_payout_status": "PAID",
        "delivered_at": "2026-07-20T10:00:00",
    })
    path = f"/admin/settlements/delivery-partner/{rider_id}/2026-07/paid"
    first = client.post(path, data={"payout_mode": "CASH", "note": "month paid"}, follow_redirects=False)
    second = client.post(path, data={"payout_mode": "CASH", "note": "again"}, follow_redirects=False)
    assert first.status_code in {301, 302, 303}
    assert second.status_code in {301, 302, 303}
    assert db.delivery_partner_monthly_settlements.count_documents({"delivery_partner_id_str": str(rider_id), "period": "2026-07"}) == 1
    batch = db.delivery_partner_monthly_settlements.find_one({"delivery_partner_id_str": str(rider_id), "period": "2026-07"})
    assert batch["status"] == "PAID"
    assert batch["amount_paid"] == 35.0
    order = db.orders.find_one({"_id": oid})
    assert order["delivery_monthly_settlement_status"] == "PAID_MONTHLY"


@pytest.mark.runtime
@pytest.mark.finance
def test_store_platform_fee_received_is_idempotent(client, runtime_app, bson_object_id):
    db = runtime_app["db"]
    login_as(client, db, bson_object_id, "admin")
    oid = bson_object_id()
    db.orders.insert_one({
        "_id": oid,
        "status": "DELIVERED",
        "payment_method": "COD",
        "cod_collection_method": "STORE",
        "payment_received_by": "STORE",
        "payment_status": "PAID",
        "payment_reconciliation_status": "VERIFIED_AT_STORE",
        "platform_fee": 10.0,
        "platform_fee_status": "DUE_FROM_STORE",
        "store_payout_status": "NOT_REQUIRED",
    })
    path = f"/admin/settlements/{oid}/store-platform-fee-received"
    first = client.post(path, data={"payment_mode": "CASH"}, follow_redirects=False)
    second = client.post(path, data={"payment_mode": "CASH"}, follow_redirects=False)
    assert first.status_code in {301, 302, 303}
    assert second.status_code in {301, 302, 303}
    order = db.orders.find_one({"_id": oid})
    assert order["platform_fee_status"] == "RECEIVED"
    logs = [x for x in order.get("settlement_audit_logs", []) if x.get("action") == "STORE_PLATFORM_FEE_RECEIVED_BY_ADMIN"]
    assert len(logs) == 1
