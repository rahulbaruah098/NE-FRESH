
from flask import Blueprint, render_template, request, abort
from app.models.product import Product, Category
bp = Blueprint("public", __name__)

@bp.get("/")
def landing(): return render_template("landing.html")

@bp.get("/catalog")
def catalog():
    q = request.args.get("q","").strip()
    category = request.args.get("category")
    query = Product.query.filter_by(is_active=True, approved_by_admin=True)
    if q: query = query.filter(Product.name.ilike(f"%{q}%"))
    if category: query = query.join(Category).filter(Category.slug==category)
    products = query.order_by(Product.created_at.desc()).limit(48).all()
    return render_template("catalog.html", products=products)

@bp.get("/p/<slug>")
def product(slug):
    p = Product.query.filter_by(slug=slug, is_active=True, approved_by_admin=True).first()
    if not p: abort(404)
    return render_template("product.html", p=p)

@bp.get("/cart")
def cart():
    return render_template("cart.html")
