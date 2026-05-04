
from app.extensions import db
from app.models import TimestampMixin

class Category(TimestampMixin, db.Model):
    __tablename__="categories"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    slug = db.Column(db.String(140), unique=True, nullable=False, index=True)
    is_active = db.Column(db.Boolean, default=True)

class Product(TimestampMixin, db.Model):
    __tablename__="products"
    id = db.Column(db.Integer, primary_key=True)
    shop_id = db.Column(db.Integer, db.ForeignKey("shops.id", ondelete="CASCADE"), index=True, nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey("categories.id"), index=True, nullable=False)
    name = db.Column(db.String(160), nullable=False)
    slug = db.Column(db.String(180), unique=True, nullable=False, index=True)
    description = db.Column(db.Text)
    price_mrp = db.Column(db.Integer, nullable=False)
    price_sale = db.Column(db.Integer, nullable=False)
    stock_qty = db.Column(db.Integer, nullable=False, default=0)
    unit = db.Column(db.String(8), nullable=False)   # kg|g|pc
    min_order_qty = db.Column(db.Integer, nullable=False, default=1)
    image_url = db.Column(db.String(300))
    is_active = db.Column(db.Boolean, default=True)
    approved_by_admin = db.Column(db.Boolean, default=False)
