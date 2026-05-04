from app.extensions import db
from app.models import TimestampMixin


class Cart(TimestampMixin, db.Model):
    __tablename__ = "carts"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )


class CartItem(TimestampMixin, db.Model):
    __tablename__ = "cart_items"
    id = db.Column(db.Integer, primary_key=True)
    cart_id = db.Column(
        db.Integer,
        db.ForeignKey("carts.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    product_id = db.Column(
        db.Integer,
        db.ForeignKey("products.id"),
        index=True,
        nullable=False,
    )
    qty = db.Column(db.Integer, nullable=False)


class Order(TimestampMixin, db.Model):
    __tablename__ = "orders"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        index=True,
        nullable=False,
    )
    shop_id = db.Column(
        db.Integer,
        db.ForeignKey("shops.id"),
        index=True,
        nullable=False,
    )
    address_id = db.Column(
        db.Integer,
        db.ForeignKey("addresses.id"),
        nullable=False,
    )
    payment_method = db.Column(db.String(20), nullable=False)  # COD|UPI|NETBANKING
    payment_status = db.Column(db.String(20), default="PENDING")
    order_status = db.Column(db.String(30), default="PLACED", index=True)
    subtotal = db.Column(db.Integer, nullable=False)
    delivery_fee = db.Column(db.Integer, default=0)
    discount = db.Column(db.Integer, default=0)
    total_amount = db.Column(db.Integer, nullable=False)


class OrderItem(TimestampMixin, db.Model):
    __tablename__ = "order_items"
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(
        db.Integer,
        db.ForeignKey("orders.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    product_id = db.Column(
        db.Integer,
        db.ForeignKey("products.id"),
        index=True,
        nullable=False,
    )
    product_name = db.Column(db.String(160), nullable=False)
    unit_price = db.Column(db.Integer, nullable=False)
    qty = db.Column(db.Integer, nullable=False)
    line_total = db.Column(db.Integer, nullable=False)


class Payment(TimestampMixin, db.Model):
    __tablename__ = "payments"
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(
        db.Integer,
        db.ForeignKey("orders.id"),
        index=True,
        nullable=False,
    )
    reference = db.Column(db.String(120), index=True)
    provider = db.Column(db.String(30))  # MOCK|RAZORPAY|PAYTM
    method = db.Column(db.String(20))    # COD|UPI|NETBANKING
    amount = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(20), default="CREATED")


class Notification(TimestampMixin, db.Model):
    __tablename__ = "notifications"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        index=True,
        nullable=False,
    )
    type = db.Column(db.String(20))  # ORDER|ADMIN|INVENTORY
    title = db.Column(db.String(200))
    message = db.Column(db.Text)
    is_read = db.Column(db.Boolean, default=False)


class FeatureFlag(TimestampMixin, db.Model):
    __tablename__ = "feature_flags"
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(120), unique=True, nullable=False)
    value = db.Column(db.Text)


class DeliveryAssignment(TimestampMixin, db.Model):
    __tablename__ = "delivery_assignments"
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(
        db.Integer,
        db.ForeignKey("orders.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    delivery_user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        index=True,
        nullable=False,
    )
    status = db.Column(
        db.String(30),
        default="ASSIGNED"
    )  # ASSIGNED|ACCEPTED|PICKED_UP|OUT_FOR_DELIVERY|DELIVERED|FAILED
