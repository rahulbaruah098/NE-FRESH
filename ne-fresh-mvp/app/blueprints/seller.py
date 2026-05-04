
from flask import Blueprint, render_template
from flask_login import login_required
from app.security import role_required
from app.models import Role

bp = Blueprint("seller", __name__)

@bp.get("/seller")
@login_required
@role_required(Role.SELLER, Role.ADMIN)
def dashboard():
    return render_template("seller_dashboard.html")
