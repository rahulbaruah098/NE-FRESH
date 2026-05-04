
from app.extensions import db, bcrypt
from app.models import User, Role
from app.models.shop import Shop
from app.models.product import Category, Product

def run_seed(app):
    with app.app_context():
        db.create_all()

        if not User.query.filter_by(email="admin@nefresh.local").first():
            admin = User(
                name="NE-FRESH Admin",
                email="admin@nefresh.local",
                role=Role.ADMIN,
                password_hash=bcrypt.generate_password_hash("Admin@123").decode()
            )
            db.session.add(admin); db.session.commit()

        seller = User.query.filter_by(email="seller@nefresh.local").first()
        if not seller:
            seller = User(
                name="Sample Seller",
                email="seller@nefresh.local",
                role=Role.SELLER,
                password_hash=bcrypt.generate_password_hash("Seller@123").decode()
            )
            db.session.add(seller); db.session.commit()

        shop = Shop.query.filter_by(owner_user_id=seller.id).first()
        if not shop:
            shop = Shop(owner_user_id=seller.id, name="Local Meat Hub", is_verified=True, status="ACTIVE")
            db.session.add(shop); db.session.commit()

        cat = Category.query.filter_by(slug="meat").first()
        if not cat:
            cat = Category(name="Fresh Meat", slug="meat"); db.session.add(cat); db.session.commit()

        if not Product.query.first():
            p = [
                Product(shop_id=shop.id, category_id=cat.id, name="Chicken (Dressed)", slug="chicken-dressed",
                        description="Locally sourced, same-day dressed.",
                        price_mrp=300, price_sale=260, stock_qty=50, unit="kg", min_order_qty=1,
                        image_url="/static/img/chicken.jpg", approved_by_admin=True),
                Product(shop_id=shop.id, category_id=cat.id, name="Pork Cuts", slug="pork-cuts",
                        description="Fresh pork cuts.", price_mrp=450, price_sale=420, stock_qty=30,
                        unit="kg", min_order_qty=1, image_url="/static/img/pork.jpg", approved_by_admin=True),
                Product(shop_id=shop.id, category_id=cat.id, name="Mutton (Bone-in)", slug="mutton-bone-in",
                        description="Premium mutton.", price_mrp=850, price_sale=799, stock_qty=15,
                        unit="kg", min_order_qty=1, image_url="/static/img/mutton.jpg", approved_by_admin=True),
            ]
            db.session.add_all(p); db.session.commit()
