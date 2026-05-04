
from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app
from flask_login import login_required, current_user
from app.extensions import db
from app.models.product import Product
from app.models.order import Order, OrderItem, Payment, Notification
from app.models.shop import Address
from app.payments import MockPaymentProvider
from app.security import assert_pincode
from app.models import User, Role

bp = Blueprint("customer", __name__)

@bp.get("/checkout")
@login_required
def checkout():
    return render_template("checkout.html")

@bp.post("/order/create")
@login_required
def order_create():
    product_id = int(request.form.get("product_id", "0"))
    qty = int(request.form.get("qty","1"))
    line = Product.query.get_or_404(product_id)

    line1 = request.form.get("addr_line1","").strip()
    city = request.form.get("city","").strip()
    state = request.form.get("state","").strip()
    pincode = request.form.get("pincode","").strip()
    allowed = current_app.config["PINCODE_ALLOWED"]
    assert_pincode(pincode, allowed)

    addr = Address(user_id=current_user.id, line1=line1, city=city, state=state, pincode=pincode, is_default=True)
    db.session.add(addr); db.session.commit()

    subtotal = line.price_sale * qty
    total = subtotal
    order = Order(user_id=current_user.id, shop_id=line.shop_id, address_id=addr.id,
                  payment_method=request.form.get("payment_method","COD"),
                  subtotal=subtotal, total_amount=total, delivery_fee=0, discount=0)
    db.session.add(order); db.session.commit()

    oi = OrderItem(order_id=order.id, product_id=line.id, product_name=line.name,
                   unit_price=line.price_sale, qty=qty, line_total=subtotal)
    db.session.add(oi); db.session.commit()

    provider = MockPaymentProvider(sandbox=True)
    pay_ref = provider.create_payment(order.id, order.total_amount, order.payment_method)["reference"]
    if order.payment_method in ("UPI","NETBANKING"):
        status = provider.confirm_payment(pay_ref)["status"]
        order.payment_status = "PAID" if status=="PAID" else "FAILED"
    else:
        order.payment_status = "PENDING"
    db.session.add(order); db.session.commit()

    pay = Payment(order_id=order.id, reference=pay_ref, provider="MOCK",
                  method=order.payment_method, amount=order.total_amount, status="PAID" if order.payment_status=="PAID" else "CREATED")
    db.session.add(pay); db.session.commit()

    admin = User.query.filter_by(role=Role.ADMIN).first()
    if admin:
        db.session.add(Notification(user_id=admin.id, type="ORDER", title="New Order", message=f"Order #{order.id} placed"))
        db.session.commit()

    flash(f"Order #{order.id} placed successfully!", "success")
    return redirect(url_for("customer.order_detail", id=order.id))

@bp.get("/orders")
@login_required
def orders_list():
    orders = Order.query.filter_by(user_id=current_user.id).order_by(Order.id.desc()).all()
    return render_template("customer_orders.html", orders=orders)

@bp.get("/order/<int:id>")
@login_required
def order_detail(id):
    order = Order.query.get_or_404(id)
    return render_template("customer_order_detail.html", order=order)
