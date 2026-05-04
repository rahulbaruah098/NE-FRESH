
from datetime import datetime
from enum import Enum
from app.extensions import db, login_manager
from flask_login import UserMixin

class Role(str, Enum):
    ADMIN="ADMIN"; SELLER="SELLER"; CUSTOMER="CUSTOMER"; DELIVERY="DELIVERY"

class TimestampMixin:
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class User(UserMixin, TimestampMixin, db.Model):
    __tablename__="users"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    phone = db.Column(db.String(20), unique=True, nullable=True, index=True)
    role = db.Column(db.Enum(Role), nullable=False, default=Role.CUSTOMER)
    password_hash = db.Column(db.String(200), nullable=False)
    is_active = db.Column(db.Boolean, default=True)

    def get_id(self): return str(self.id)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))
