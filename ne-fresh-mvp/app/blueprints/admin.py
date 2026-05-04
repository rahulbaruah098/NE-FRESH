from flask import Blueprint, render_template, jsonify, request, redirect, url_for, flash, current_app, Response
from flask_login import login_required
from sqlalchemy import func
from datetime import datetime, timedelta

from app.security import role_required
from app.extensions import db, bcrypt
from app.models import Role, User
from app.models.shop import Shop
from app.models.product import Product, Category
from app.models.order import Order, OrderItem, Payment, FeatureFlag, DeliveryAssignment

bp = Blueprint("admin", __name__)

# ------- Dashboard page -------
@bp.get("/admin")
@login_required
@role_required(Role.ADMIN)
def dashboard():
    # Pending products (need approval)
    pending_products = Product.query.filter_by(approved_by_admin=False, is_active=True).order_by(Product.created_at.asc()).all()

    # Recent orders (latest 20)
    recent_orders = Order.query.order_by(Order.id.desc()).limit(20).all()

    # Delivery users list for assignment
    delivery_users = User.query.filter_by(role=Role.DELIVERY).order_by(User.id.desc()).all()

    # Current flag value
    delivery_enabled = bool(current_app.config.get("DELIVERY_ENABLED", False))

    return render_template(
        "admin_dashboard.html",
        pending_products=pending_products,
        recent_orders=recent_orders,
        delivery_users=delivery_users,
        delivery_enabled=delivery_enabled
    )

# ------- Stats JSON for charts -------
@bp.get("/admin/stats.json")
@login_required
@role_required(Role.ADMIN)
def stats_json():
    since = datetime.utcnow() - timedelta(days=30)

    # Orders by day
    orders_q = (
        db.session.query(func.date(Order.created_at).label("d"), func.count(Order.id))
        .filter(Order.created_at >= since)
        .group_by("d").order_by("d")
    ).all()
    ordersByDay = [[str(d), int(c)] for d, c in orders_q]

    # GMV by day
    gmv_q = (
        db.session.query(func.date(Order.created_at).label("d"), func.coalesce(func.sum(Order.total_amount), 0))
        .filter(Order.created_at >= since)
        .group_by("d").order_by("d")
    ).all()
    gmvByDay = [[str(d), int(v or 0)] for d, v in gmv_q]

    # AOV by day = GMV / Orders (same date index)
    aov_map = {}
    count_map = {str(d): c for d, c in orders_q}
    for d, g in gmv_q:
        sd = str(d)
        orders = count_map.get(sd, 0)
        aov_map[sd] = (int(g or 0) // orders) if orders else 0
    aovByDay = [[d, aov_map[d]] for d in sorted(aov_map.keys())]

    # Category mix (by order items quantity in last 30 days)
    cat_q = (
        db.session.query(Category.name, func.coalesce(func.sum(OrderItem.qty), 0))
        .join(Product, Product.id == OrderItem.product_id)
        .join(Category, Category.id == Product.category_id)
        .join(Order, Order.id == OrderItem.order_id)
        .filter(Order.created_at >= since)
        .group_by(Category.name)
        .order_by(Category.name.asc())
    ).all()
    categoryMix = [[name, int(val or 0)] for name, val in cat_q]

    # Seller performance (orders per shop)
    seller_q = (
        db.session.query(Shop.name, func.count(Order.id))
        .join(Order, Order.shop_id == Shop.id)
        .filter(Order.created_at >= since)
        .group_by(Shop.name)
        .order_by(func.count(Order.id).desc())
    ).all()
    sellerPerf = [[name or f"Shop {i+1}", int(n)] for i, (name, n) in enumerate(seller_q)]

    # Active sellers
    active_sellers = db.session.query(Shop).filter(Shop.status == "ACTIVE").count()

    # On-time % (approx: delivered / (delivered+failed) where assignment exists)
    delivered = db.session.query(DeliveryAssignment).filter(DeliveryAssignment.status == "DELIVERED").count()
    failed = db.session.query(DeliveryAssignment).filter(DeliveryAssignment.status == "FAILED").count()
    on_time_pct = int((delivered * 100) / (delivered + failed)) if (delivered + failed) else 100

    return jsonify({
        "ordersByDay": ordersByDay,
        "gmvByDay": gmvByDay,
        "aovByDay": aovByDay,
        "categoryMix": categoryMix,
        "sellerPerf": sellerPerf,
        "activeSellers": active_sellers,
        "onTimePct": on_time_pct
    })

# ------- Approve product -------
@bp.post("/admin/product/approve/<int:pid>")
@login_required
@role_required(Role.ADMIN)
def approve_product(pid):
    p = Product.query.get_or_404(pid)
    p.approved_by_admin = True
    db.session.commit()
    flash(f"Approved product: {p.name}", "success")
    return redirect(url_for("admin.dashboard"))

# ------- Create SELLER + Shop -------
@bp.post("/admin/seller/new")
@login_required
@role_required(Role.ADMIN)
def create_seller():
    name = request.form.get("name","").strip()
    email = request.form.get("email","").strip().lower()
    password = request.form.get("password","").strip()
    shop_name = request.form.get("shop_name","").strip()

    if not name or not email or not password or not shop_name:
        flash("All fields are required.", "error")
        return redirect(url_for("admin.dashboard"))

    if User.query.filter_by(email=email).first():
        flash("Email already exists.", "error")
        return redirect(url_for("admin.dashboard"))

    u = User(name=name, email=email, role=Role.SELLER, password_hash=bcrypt.generate_password_hash(password).decode())
    db.session.add(u); db.session.commit()

    shop = Shop(owner_user_id=u.id, name=shop_name, is_verified=True, status="ACTIVE")
    db.session.add(shop); db.session.commit()

    flash(f"Seller {name} & shop '{shop_name}' created.", "success")
    return redirect(url_for("admin.dashboard"))

# ------- Create DELIVERY user -------
@bp.post("/admin/delivery/new")
@login_required
@role_required(Role.ADMIN)
def create_delivery():
    name = request.form.get("name","").strip()
    email = request.form.get("email","").strip().lower()
    password = request.form.get("password","").strip()

    if not name or not email or not password:
        flash("All fields are required.", "error")
        return redirect(url_for("admin.dashboard"))

    if User.query.filter_by(email=email).first():
        flash("Email already exists.", "error")
        return redirect(url_for("admin.dashboard"))

    u = User(name=name, email=email, role=Role.DELIVERY, password_hash=bcrypt.generate_password_hash(password).decode())
    db.session.add(u); db.session.commit()

    flash(f"Delivery user {name} created.", "success")
    return redirect(url_for("admin.dashboard"))

# ------- Toggle Delivery Feature Flag -------
@bp.post("/admin/feature/delivery_enabled")
@login_required
@role_required(Role.ADMIN)
def toggle_delivery_flag():
    enabled = request.form.get("enabled", "0") == "1"
    # persist in FeatureFlag
    ff = FeatureFlag.query.filter_by(key="DELIVERY_ENABLED").first()
    if not ff:
        ff = FeatureFlag(key="DELIVERY_ENABLED", value="1" if enabled else "0")
        db.session.add(ff)
    else:
        ff.value = "1" if enabled else "0"
    db.session.commit()
    # also update runtime config
    current_app.config["DELIVERY_ENABLED"] = enabled
    flash(f"Delivery network {'ENABLED' if enabled else 'DISABLED'}.", "success")
    return redirect(url_for("admin.dashboard"))

# ------- Assign order to a delivery user -------
@bp.post("/admin/orders/<int:oid>/assign_delivery")
@login_required
@role_required(Role.ADMIN)
def assign_delivery(oid):
    order = Order.query.get_or_404(oid)
    delivery_user_id = int(request.form.get("delivery_user_id","0"))
    if not delivery_user_id:
        flash("Select a delivery user.", "error")
        return redirect(url_for("admin.dashboard"))

    assign = DeliveryAssignment.query.filter_by(order_id=order.id).first()
    if not assign:
        assign = DeliveryAssignment(order_id=order.id, delivery_user_id=delivery_user_id, status="ASSIGNED")
        db.session.add(assign)
    else:
        assign.delivery_user_id = delivery_user_id
        assign.status = "ASSIGNED"
    order.order_status = "ASSIGNED"
    db.session.commit()
    flash(f"Order #{order.id} assigned.", "success")
    return redirect(url_for("admin.dashboard"))

# ------- CSV Exports -------
@bp.get("/admin/export/orders.csv")
@login_required
@role_required(Role.ADMIN)
def export_orders_csv():
    rows = db.session.query(
        Order.id, Order.user_id, Order.shop_id, Order.total_amount, Order.payment_status, Order.order_status, Order.created_at
    ).order_by(Order.id.desc()).all()
    def gen():
        yield "id,user_id,shop_id,total_amount,payment_status,order_status,created_at\n"
        for r in rows:
            created = r.created_at.isoformat() if r.created_at else ""
            yield f"{r.id},{r.user_id},{r.shop_id},{r.total_amount},{r.payment_status},{r.order_status},{created}\n"
    return Response(gen(), mimetype="text/csv")

@bp.get("/admin/export/users.csv")
@login_required
@role_required(Role.ADMIN)
def export_users_csv():
    rows = db.session.query(User.id, User.name, User.email, User.role, User.is_active, User.created_at).order_by(User.id.desc()).all()
    def gen():
        yield "id,name,email,role,is_active,created_at\n"
        for r in rows:
            created = r.created_at.isoformat() if r.created_at else ""
            yield f"{r.id},{r.name},{r.email},{r.role},{int(bool(r.is_active))},{created}\n"
    return Response(gen(), mimetype="text/csv")
