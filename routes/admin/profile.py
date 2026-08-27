"""Admin profile route section.

Route decorators, endpoint names, URL paths, HTTP methods and function bodies are
preserved from the Step 6 source. Shared legacy helpers are imported from
``routes.admin.shared`` during this transitional decomposition.
"""

from routes.admin.shared import *

@app.route('/admin/profile', methods=['GET', 'POST'])
@login_required(role='admin')
def admin_profile():
    admin_user = current_user() or {}
    if not admin_user:
        flash("Please log in first.", "warning")
        return redirect(url_for("login"))

    action = (request.form.get("action") or "").strip()
    now = datetime.utcnow().isoformat()

    if request.method == 'POST':
        user_oid = ObjectId(str(admin_user["id"]))

        if action == "profile_details":
            name = (request.form.get("name") or "").strip()
            phone_raw = request.form.get("phone") or ""
            phone = normalize_phone(phone_raw)

            if not name:
                flash("Admin name is required.", "warning")
                return redirect(url_for("admin_profile"))

            if len(name) > 120:
                flash("Admin name cannot be more than 120 characters.", "warning")
                return redirect(url_for("admin_profile"))

            if phone_raw.strip() and (len(phone) < 7 or len(phone) > 15):
                flash("Please enter a valid phone number.", "warning")
                return redirect(url_for("admin_profile"))

            update_data = {
                "name": name,
                "updated_at": now,
                "profile_updated_at": now,
            }

            if phone:
                update_data["phone"] = phone

            mongo.users.update_one({"_id": user_oid, "role": "admin"}, {"$set": update_data})
            flash("Admin profile updated successfully.", "success")
            return redirect(url_for("admin_profile"))

        if action == "change_email":
            current_password = request.form.get("current_password") or ""
            new_email = _admin_profile_clean_email(request.form.get("new_email") or "")
            current_email = _admin_profile_clean_email(admin_user.get("email") or "")

            if not current_password:
                flash("Current password is required to change email.", "warning")
                return redirect(url_for("admin_profile"))

            if not check_password_hash(admin_user.get("password_hash", ""), current_password):
                flash("Current password is incorrect. Email was not changed.", "danger")
                return redirect(url_for("admin_profile"))

            if not _admin_profile_email_is_valid(new_email):
                flash("Please enter a valid new email address.", "warning")
                return redirect(url_for("admin_profile"))

            if new_email == current_email:
                flash("Current email cannot be used as the new email.", "warning")
                return redirect(url_for("admin_profile"))

            if _admin_profile_email_exists_for_other_user(new_email, user_oid):
                flash("This email is already used by another account.", "danger")
                return redirect(url_for("admin_profile"))

            mongo.users.update_one(
                {"_id": user_oid, "role": "admin"},
                {"$set": {
                    "email": new_email,
                    "email_verified": 1,
                    "previous_email": admin_user.get("email", ""),
                    "email_changed_at": now,
                    "updated_at": now,
                }}
            )
            flash("Admin email changed successfully. Use the new email on your next login.", "success")
            return redirect(url_for("admin_profile"))

        if action == "change_password":
            current_password = request.form.get("current_password") or ""
            new_password = request.form.get("new_password") or ""
            confirm_password = request.form.get("confirm_password") or ""
            password_hash = admin_user.get("password_hash", "")

            if not current_password:
                flash("Current password is required to change password.", "warning")
                return redirect(url_for("admin_profile"))

            if not check_password_hash(password_hash, current_password):
                flash("Current password is incorrect. Password was not changed.", "danger")
                return redirect(url_for("admin_profile"))

            if not new_password or not confirm_password:
                flash("Please enter and confirm the new password.", "warning")
                return redirect(url_for("admin_profile"))

            if new_password != confirm_password:
                flash("New password and confirm password do not match.", "warning")
                return redirect(url_for("admin_profile"))

            if check_password_hash(password_hash, new_password):
                flash("New password cannot be the same as your current password.", "warning")
                return redirect(url_for("admin_profile"))

            password_error = _admin_profile_password_error(new_password, admin_user)
            if password_error:
                flash(password_error, "warning")
                return redirect(url_for("admin_profile"))

            mongo.users.update_one(
                {"_id": user_oid, "role": "admin"},
                {"$set": {
                    "password_hash": generate_password_hash(new_password),
                    "password_changed_at": now,
                    "updated_at": now,
                }}
            )
            flash("Admin password changed successfully.", "success")
            return redirect(url_for("admin_profile"))

        flash("Invalid profile action.", "warning")
        return redirect(url_for("admin_profile"))

    return render_template(
        "admin_profile.html",
        user=admin_user,
        active_group="account",
        active_page="admin_profile",
    )
