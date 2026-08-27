"""Admin contact messages route section.

Route decorators, endpoint names, URL paths, HTTP methods and function bodies are
preserved from the Step 6 source. Shared legacy helpers are imported from
``routes.admin.shared`` during this transitional decomposition.
"""

from routes.admin.shared import *

@app.route("/admin/contact-messages", methods=["GET"], endpoint="admin_contact_messages")
@login_required(role="admin")
def admin_contact_messages():
    q = (request.args.get("q") or "").strip()

    query = {}

    if q:
        query["$or"] = [
            {"name": {"$regex": q, "$options": "i"}},
            {"email": {"$regex": q, "$options": "i"}},
            {"phone": {"$regex": q, "$options": "i"}},
            {"subject": {"$regex": q, "$options": "i"}},
            {"message": {"$regex": q, "$options": "i"}}
        ]

    messages = list(
        mongo.contact_messages.find(query).sort("created_at", -1)
    )

    for m in messages:
        m["id"] = str(m["_id"])

    auto_reply_settings = get_contact_auto_reply_settings()

    stats = {
        "total": mongo.contact_messages.count_documents({}),
        "pending_reply": mongo.contact_messages.count_documents({
            "$and": [
                {"auto_reply_sent": {"$ne": True}},
                {"manual_reply_sent": {"$ne": True}}
            ]
        }),
        "auto_sent": mongo.contact_messages.count_documents({"auto_reply_sent": True}),
        "manual_sent": mongo.contact_messages.count_documents({"manual_reply_sent": True})
    }

    return render_template(
        "admin_contact_messages.html",
        user=current_user(),
        messages=messages,
        stats=stats,
        q=q,
        auto_reply_enabled=bool(auto_reply_settings.get("enabled")),
        auto_reply_settings=auto_reply_settings,
        active_page="contact_messages",
        active_group="operations"
    )


@app.route("/admin/contact-messages/<mid>/status", methods=["POST"], endpoint="admin_contact_message_status")
@login_required(role="admin")
def admin_contact_message_status(mid):
    status = (request.form.get("status") or "NEW").strip().upper()
    admin_note = (request.form.get("admin_note") or "").strip()

    if status not in ["NEW", "READ", "RESOLVED"]:
        status = "NEW"

    try:
        mid_obj = ObjectId(mid)
    except Exception:
        flash("Invalid contact message.", "danger")
        return redirect(url_for("admin_contact_messages"))

    now = datetime.utcnow().isoformat()

    update_doc = {
        "status": status,
        "updated_at": now
    }

    if admin_note:
        update_doc["admin_note"] = admin_note

    if status == "READ":
        update_doc["read_at"] = now

    if status == "RESOLVED":
        update_doc["resolved_at"] = now

    mongo.contact_messages.update_one(
        {"_id": mid_obj},
        {
            "$set": update_doc
        }
    )

    flash("Contact message updated successfully.", "success")
    return redirect(request.referrer or url_for("admin_contact_messages"))


@app.route("/admin/contact-messages/auto-reply/toggle", methods=["POST"], endpoint="admin_contact_auto_reply_toggle")
@login_required(role="admin")
def admin_contact_auto_reply_toggle():
    enabled = str(request.form.get("enabled") or "0").strip() == "1"
    admin_user = current_user() or {}
    now = datetime.utcnow().isoformat()

    existing = mongo.platform_settings.find_one({
        "key": CONTACT_AUTO_REPLY_SETTINGS_KEY
    }) or {}

    mongo.platform_settings.update_one(
        {"key": CONTACT_AUTO_REPLY_SETTINGS_KEY},
        {
            "$set": {
                "key": CONTACT_AUTO_REPLY_SETTINGS_KEY,
                "enabled": bool(enabled),
                "subject": existing.get("subject") or "We received your message - NELOCALS",
                "body": existing.get("body") or (
                    "Dear {name},\n\n"
                    "Thank you for contacting NELOCALS.\n\n"
                    "We have received your message regarding: {subject}.\n\n"
                    "Our admin/contact team will review your message and contact you as soon as possible.\n\n"
                    "Thank you,\n"
                    "NELOCALS Admin Team"
                ),
                "updated_at": now,
                "updated_by": str(admin_user.get("_id") or admin_user.get("id") or ""),
                "updated_by_name": admin_user.get("name") or admin_user.get("email") or "Admin"
            },
            "$setOnInsert": {
                "created_at": now
            }
        },
        upsert=True
    )

    flash(
        "Automatic contact acknowledgement email enabled."
        if enabled else
        "Automatic contact acknowledgement email disabled.",
        "success"
    )

    return redirect(request.referrer or url_for("admin_contact_messages"))


@app.route("/admin/contact-messages/auto-reply/settings", methods=["POST"], endpoint="admin_contact_auto_reply_settings_update")
@login_required(role="admin")
def admin_contact_auto_reply_settings_update():
    # Automatic message editing has intentionally been removed from the Admin UI.
    # Keep this route harmless for old cached forms/bookmarks, but do not update
    # the automatic acknowledgement subject/body from POST data anymore.
    flash("Automatic acknowledgement message editing is disabled. Use the ON/OFF switch and manual reply box instead.", "info")
    return redirect(request.referrer or url_for("admin_contact_messages"))


@app.route("/admin/contact-messages/<mid>/auto-reply/send", methods=["POST"], endpoint="admin_contact_auto_reply_send")
@login_required(role="admin")
def admin_contact_auto_reply_send(mid):
    try:
        mid_obj = ObjectId(mid)
    except Exception:
        flash("Invalid contact message.", "danger")
        return redirect(url_for("admin_contact_messages"))

    contact_doc = mongo.contact_messages.find_one({"_id": mid_obj})

    if not contact_doc:
        flash("Contact message not found.", "danger")
        return redirect(url_for("admin_contact_messages"))

    result = send_contact_auto_reply(contact_doc)
    now = datetime.utcnow().isoformat()

    mongo.contact_messages.update_one(
        {"_id": mid_obj},
        {
            "$set": {
                "auto_reply_sent": bool(result.get("sent")),
                "auto_reply_error": result.get("error") or "",
                "auto_reply_sent_at": now if result.get("sent") else contact_doc.get("auto_reply_sent_at", ""),
                "updated_at": now
            },
            "$push": {
                "reply_logs": {
                    "type": "AUTO_ACKNOWLEDGEMENT",
                    "sent": bool(result.get("sent")),
                    "error": result.get("error") or "",
                    "subject": result.get("subject") or "We received your message - NELOCALS",
                    "message": "Automatic acknowledgement email sent to user." if result.get("sent") else "Automatic acknowledgement email was not sent.",
                    "created_at": now,
                    "created_by": str((current_user() or {}).get("_id") or (current_user() or {}).get("id") or ""),
                    "created_by_name": (current_user() or {}).get("name") or (current_user() or {}).get("email") or "Admin"
                }
            }
        }
    )

    if result.get("sent"):
        flash("Automatic acknowledgement email sent to user.", "success")
    elif not result.get("enabled", True):
        flash("Automatic acknowledgement is OFF. Use the manual reply box to email this user.", "warning")
    else:
        flash(result.get("error") or "Could not send automatic email.", "danger")

    return redirect(request.referrer or url_for("admin_contact_messages"))


@app.route("/admin/contact-messages/<mid>/reply", methods=["POST"], endpoint="admin_contact_message_reply")
@login_required(role="admin")
def admin_contact_message_reply(mid):
    try:
        mid_obj = ObjectId(mid)
    except Exception:
        flash("Invalid contact message.", "danger")
        return redirect(url_for("admin_contact_messages"))

    contact_doc = mongo.contact_messages.find_one({"_id": mid_obj})

    if not contact_doc:
        flash("Contact message not found.", "danger")
        return redirect(url_for("admin_contact_messages"))

    to_email = (contact_doc.get("email") or "").strip()
    reply_subject = (request.form.get("reply_subject") or "").strip()
    reply_message = (request.form.get("reply_message") or "").strip()

    if not to_email:
        flash("This contact message has no email address.", "warning")
        return redirect(request.referrer or url_for("admin_contact_messages"))

    if not reply_subject:
        reply_subject = "Reply from NELOCALS Admin"

    if not reply_message:
        flash("Please write a reply message before sending.", "warning")
        return redirect(request.referrer or url_for("admin_contact_messages"))

    safe_name = html.escape(contact_doc.get("name") or "there")
    safe_message = html.escape(reply_message).replace("\n", "<br>")

    email_body = f"""
    <div style="font-family:Arial,sans-serif;line-height:1.6;color:#1F332A;">
      <h2 style="color:#00A859;margin-bottom:8px;">Message from NELOCALS Admin</h2>

      <p>Dear {safe_name},</p>

      <div style="margin:16px 0;padding:14px;border-left:4px solid #00A859;background:#F3FFF8;">
        {safe_message}
      </div>

      <p>
        Thank you,<br>
        <strong>NELOCALS Admin Team</strong>
      </p>
    </div>
    """

    now = datetime.utcnow().isoformat()
    admin_user = current_user() or {}

    try:
        send_email(to_email, reply_subject, email_body)
    except Exception as exc:
        mongo.contact_messages.update_one(
            {"_id": mid_obj},
            {
                "$set": {
                    "manual_reply_error": str(exc),
                    "updated_at": now
                }
            }
        )

        flash(f"Could not send manual email: {exc}", "danger")
        return redirect(request.referrer or url_for("admin_contact_messages"))

    mongo.contact_messages.update_one(
        {"_id": mid_obj},
        {
            "$set": {
                "manual_reply_sent": True,
                "manual_reply_sent_at": now,
                "manual_reply_error": "",
                "last_manual_reply_subject": reply_subject,
                "last_manual_reply_message": reply_message,
                "updated_at": now
            },
            "$push": {
                "reply_logs": {
                    "type": "MANUAL_REPLY",
                    "sent": True,
                    "error": "",
                    "subject": reply_subject,
                    "message": reply_message,
                    "created_at": now,
                    "created_by": str(admin_user.get("_id") or admin_user.get("id") or ""),
                    "created_by_name": admin_user.get("name") or admin_user.get("email") or "Admin"
                }
            }
        }
    )

    flash("Manual email reply sent to user.", "success")
    return redirect(request.referrer or url_for("admin_contact_messages"))
