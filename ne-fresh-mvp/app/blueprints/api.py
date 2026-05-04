
from flask import Blueprint, request, jsonify, current_app
bp = Blueprint("api", __name__)

@bp.get("/zipcode/validate")
def zipcode_validate():
    pin = request.args.get("pin","")
    valid = pin == current_app.config.get("PINCODE_ALLOWED","796009")
    return jsonify({"valid": valid})

@bp.post("/payment/create")
def payment_create():
    data = request.get_json(silent=True) or {}
    order_id = data.get("order_id"); amount = data.get("amount",0); method = data.get("method","COD")
    return jsonify({"reference": f"MOCK-{order_id}", "status": "CREATED", "provider":"MOCK"})

@bp.post("/payment/confirm")
def payment_confirm():
    data = request.get_json(silent=True) or {}
    ref = data.get("reference","")
    return jsonify({"reference": ref, "status": "PAID"})
