
from flask import Blueprint, render_template, abort, current_app
from flask_login import login_required
from app.security import role_required, delivery_enabled_required
from app.models import Role

bp = Blueprint("delivery", __name__)

@bp.get("/delivery")
@login_required
@role_required(Role.DELIVERY, Role.ADMIN)
@delivery_enabled_required
def queue():
    if not current_app.config.get("DELIVERY_ENABLED"):
        abort(404)
    return render_template("delivery_queue.html")
