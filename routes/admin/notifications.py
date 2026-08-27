"""Admin notifications route section.

Route decorators, endpoint names, URL paths, HTTP methods and function bodies are
preserved from the Step 6 source. Shared legacy helpers are imported from
``routes.admin.shared`` during this transitional decomposition.
"""

from routes.admin.shared import *

@app.route('/admin/notifications', methods=['GET', 'POST'], endpoint='admin_notifications')
@login_required(role='admin')
def admin_notifications():
    if request.method == 'POST':
        title = _admin_notification_text("title", 120)
        message = _admin_notification_text("message", 500)
        priority = _admin_notification_priority(request.form.get("priority"))
        link_url = _admin_notification_text("link_url", 300)
        display_location = (request.form.get("display_location") or "homepage").strip().lower()

        if display_location not in ["homepage", "all"]:
            display_location = "homepage"

        is_active = _admin_bool_from_form("is_active", True)
        show_ticker = _admin_bool_from_form("show_ticker", True)
        show_popup = _admin_bool_from_form("show_popup", False)

        if not title:
            flash("Notification title is required.", "warning")
            return redirect(url_for("admin_notifications"))

        if not message:
            flash("Notification message is required.", "warning")
            return redirect(url_for("admin_notifications"))

        now = datetime.utcnow().isoformat()

        mongo.homepage_notifications.insert_one({
            "title": title,
            "message": message,
            "priority": priority,
            "priority_rank": _admin_notification_priority_rank(priority),
            "link_url": link_url,
            "display_location": display_location,
            "is_active": 1 if is_active else 0,
            "show_ticker": 1 if show_ticker else 0,
            "show_popup": 1 if show_popup else 0,
            "created_at": now,
            "updated_at": now,
            "created_by": str((current_user() or {}).get("_id") or (current_user() or {}).get("id") or "")
        })

        flash("Homepage notification created successfully.", "success")
        return redirect(url_for("admin_notifications"))

    notifications = list(
        mongo.homepage_notifications.find({})
        .sort([
            ("priority_rank", 1),
            ("created_at", -1)
        ])
    )

    for n in notifications:
        n["id"] = str(n["_id"])
        n["priority"] = _admin_notification_priority(n.get("priority"))
        n["priority_rank"] = _admin_notification_priority_rank(n.get("priority"))

    stats = {
        "total": mongo.homepage_notifications.count_documents({}),
        "active": mongo.homepage_notifications.count_documents({"is_active": 1}),
        "high": mongo.homepage_notifications.count_documents({"priority": "high"}),
        "medium": mongo.homepage_notifications.count_documents({"priority": "medium"}),
        "low": mongo.homepage_notifications.count_documents({"priority": "low"}),
    }

    return render_template(
        "admin_notifications.html",
        user=current_user(),
        notifications=notifications,
        stats=stats,
        active_page="notifications"
    )


@app.route('/admin/notifications/<nid>/toggle', methods=['POST'], endpoint='admin_notification_toggle')
@login_required(role='admin')
def admin_notification_toggle(nid):
    try:
        notification_id = ObjectId(nid)
    except Exception:
        flash("Invalid notification.", "danger")
        return redirect(url_for("admin_notifications"))

    notification = mongo.homepage_notifications.find_one({"_id": notification_id})

    if not notification:
        flash("Notification not found.", "danger")
        return redirect(url_for("admin_notifications"))

    next_status = 0 if int(notification.get("is_active", 1) or 0) == 1 else 1

    mongo.homepage_notifications.update_one(
        {"_id": notification_id},
        {
            "$set": {
                "is_active": next_status,
                "updated_at": datetime.utcnow().isoformat()
            }
        }
    )

    flash("Notification status updated.", "success")
    return redirect(url_for("admin_notifications"))


@app.route('/admin/notifications/<nid>/delete', methods=['POST'], endpoint='admin_notification_delete')
@login_required(role='admin')
def admin_notification_delete(nid):
    try:
        notification_id = ObjectId(nid)
    except Exception:
        flash("Invalid notification.", "danger")
        return redirect(url_for("admin_notifications"))

    mongo.homepage_notifications.delete_one({"_id": notification_id})

    flash("Notification deleted successfully.", "success")
    return redirect(url_for("admin_notifications"))


@app.route('/admin/notifications/<nid>/priority', methods=['POST'], endpoint='admin_notification_update_priority')
@login_required(role='admin')
def admin_notification_update_priority(nid):
    try:
        notification_id = ObjectId(nid)
    except Exception:
        flash("Invalid notification.", "danger")
        return redirect(url_for("admin_notifications"))

    priority = _admin_notification_priority(request.form.get("priority"))

    mongo.homepage_notifications.update_one(
        {"_id": notification_id},
        {
            "$set": {
                "priority": priority,
                "priority_rank": _admin_notification_priority_rank(priority),
                "updated_at": datetime.utcnow().isoformat()
            }
        }
    )

    flash("Notification priority updated.", "success")
    return redirect(url_for("admin_notifications"))
