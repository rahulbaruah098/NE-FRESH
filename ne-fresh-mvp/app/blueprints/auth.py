
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_user, logout_user, current_user
from app.extensions import db, bcrypt
from app.models import User, Role

bp = Blueprint("auth", __name__)

@bp.route("/login", methods=["GET","POST"])
def login():
    if request.method=="POST":
        email = request.form.get("email","").strip().lower()
        password = request.form.get("password","")
        u = User.query.filter_by(email=email).first()
        if u and bcrypt.check_password_hash(u.password_hash, password):
            login_user(u)
            return redirect(url_for("public.landing"))
        flash("Invalid credentials","error")
    return render_template("auth_login.html")

@bp.route("/register", methods=["GET","POST"])
def register():
    if request.method=="POST":
        name = request.form.get("name","").strip()
        email = request.form.get("email","").strip().lower()
        password = request.form.get("password","")
        if User.query.filter_by(email=email).first():
            flash("Email already in use","error")
            return render_template("auth_register.html")
        u = User(name=name, email=email, role=Role.CUSTOMER, password_hash=bcrypt.generate_password_hash(password).decode())
        db.session.add(u); db.session.commit()
        login_user(u)
        return redirect(url_for("public.landing"))
    return render_template("auth_register.html")

@bp.post("/logout")
def logout():
    if current_user.is_authenticated:
        logout_user()
    return redirect(url_for("public.landing"))
