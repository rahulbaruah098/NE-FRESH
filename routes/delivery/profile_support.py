"""Delivery profile support route section.

Route decorators, endpoint names, URL paths, HTTP methods and function bodies are
preserved from the Step 6 source. Shared legacy helpers are imported from
``routes.delivery.shared`` during this transitional decomposition.
"""

from routes.delivery.shared import *

@app.route('/delivery/profile', methods=['GET', 'POST'])
@login_required(role='delivery')
def delivery_profile():
    user = current_user()
    availability = _get_delivery_availability(user["id"])

    if request.method == 'POST':
        name = (request.form.get('name') or '').strip()
        phone = (request.form.get('phone') or '').strip()
        update_data = {}
        if name:
            update_data['name'] = name
        if phone:
            update_data['phone'] = normalize_phone(phone)
        if update_data:
            update_data['updated_at'] = datetime.utcnow().isoformat()
            mongo.users.update_one({'_id': ObjectId(user['id'])}, {'$set': update_data})
        flash('Delivery Partner profile updated.', 'success')
        return redirect(url_for('delivery_profile'))

    profile_stats = {
        'active_orders': mongo.orders.count_documents({
            '$and': [
                {'$or': [
                    {'delivery_partner_id': user['id']},
                    {'delivery_partner_id': str(user['id'])}
                ]},
                {'status': {'$in': DELIVERY_ASSIGNED_ACTIVE_STATUSES}}
            ]
        }),
        'completed_orders': mongo.orders.count_documents({
            '$and': [
                {'$or': [
                    {'delivery_partner_id': user['id']},
                    {'delivery_partner_id': str(user['id'])}
                ]},
                {'status': 'DELIVERED'}
            ]
        })
    }

    return render_template(
        "delivery_profile.html",
        user=user,
        delivery_active=bool(availability.get('active')),
        delivery_availability=availability,
        profile_stats=profile_stats,
        active_page="profile",
    )


@app.route('/delivery/support', methods=['GET', 'POST'])
@login_required(role='delivery')
def delivery_support():
    user = current_user()

    if request.method == "POST":
        category = (request.form.get("category") or "Delivery Support").strip()
        order_reference = (request.form.get("order_reference") or "").strip()
        requested_subject = (request.form.get("subject") or "").strip()
        email = (request.form.get("email") or user.get("email") or "").strip().lower()
        phone = (request.form.get("phone") or user.get("phone") or "").strip()
        message = (request.form.get("message") or "").strip()

        if not category or not message:
            flash("Please choose an issue category and describe the problem.", "warning")
            return redirect(url_for("delivery_support"))
        if len(message) < 10:
            flash("Please describe the issue in at least 10 characters.", "warning")
            return redirect(url_for("delivery_support"))
        if email and ("@" not in email or "." not in email):
            flash("Please enter a valid reply email address.", "warning")
            return redirect(url_for("delivery_support"))

        now = datetime.utcnow().isoformat()
        rider_name = (user.get("name") or user.get("full_name") or user.get("email") or "Delivery Partner").strip()
        subject_base = requested_subject or category
        subject = subject_base if not order_reference else f"{subject_base} · Order {order_reference}"
        contact_doc = {
            "name": rider_name,
            "email": email,
            "phone": phone,
            "subject": subject,
            "message": message,
            "source": "Delivery Partner Portal",
            "recipient_type": "admin",
            "page_context": "delivery_support",
            "order_reference": order_reference,
            "status": "NEW",
            "priority": "NORMAL",
            "user_id": str(user.get("_id") or user.get("id") or ""),
            "user_role": "delivery",
            "ip_address": request.headers.get("X-Forwarded-For", request.remote_addr),
            "user_agent": request.headers.get("User-Agent", ""),
            "created_at": now,
            "updated_at": now,
            "read_at": "",
            "resolved_at": "",
            "admin_note": "",
        }
        mongo.contact_messages.insert_one(contact_doc)
        flash("Your support request has been sent to NE LOCALS Admin.", "success")
        return redirect(url_for("delivery_support"))

    recent_requests = list(
        mongo.contact_messages.find({
            "user_id": str(user.get("_id") or user.get("id") or ""),
            "user_role": "delivery",
            "source": "Delivery Partner Portal",
        }).sort("created_at", -1).limit(8)
    )
    for row in recent_requests:
        row["id"] = str(row.get("_id") or "")

    return render_template(
        "delivery_support.html",
        user=user,
        support_requests=recent_requests,
        delivery_active=bool(_get_delivery_availability(user['id']).get('active')),
        delivery_availability=_get_delivery_availability(user['id']),
        active_page="support",
    )
