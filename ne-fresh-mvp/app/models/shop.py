
from app.extensions import db
from app.models import TimestampMixin, User

class Address(TimestampMixin, db.Model):
    __tablename__="addresses"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    line1 = db.Column(db.String(200), nullable=False)
    line2 = db.Column(db.String(200))
    city = db.Column(db.String(100), nullable=False)
    state = db.Column(db.String(100), nullable=False)
    pincode = db.Column(db.String(6), nullable=False, index=True)
    is_default = db.Column(db.Boolean, default=False)

class Shop(TimestampMixin, db.Model):
    __tablename__="shops"
    id = db.Column(db.Integer, primary_key=True)
    owner_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), index=True, nullable=False)
    name = db.Column(db.String(160), nullable=False)
    gstin = db.Column(db.String(20))
    is_verified = db.Column(db.Boolean, default=False)
    status = db.Column(db.String(20), default="PENDING", index=True)
