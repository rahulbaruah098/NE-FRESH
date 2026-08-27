"""Admin complaints route section.

Route decorators, endpoint names, URL paths, HTTP methods and function bodies are
preserved from the Step 6 source. Shared legacy helpers are imported from
``routes.admin.shared`` during this transitional decomposition.
"""

from routes.admin.shared import *

@app.route('/admin/complaints')
@login_required(role='admin')
def admin_complaints():
    complaints = list(
        mongo.customer_complaints.find({
            "$or": [
                {"is_active": 1},
                {"is_active": True},
                {"is_active": {"$exists": False}}
            ]
        }).sort("created_at", -1).limit(300)
    )

    for c in complaints:
        _admin_prepare_complaint_row(c)

    # Resolve linked orders in one query so complaint cards use the same public NEO-*
    # reference shown on the customer Orders page without adding per-row database calls.
    complaint_order_refs = {str(c.get("order_id") or "").strip() for c in complaints if str(c.get("order_id") or "").strip()}
    complaint_order_object_ids = []
    complaint_public_refs = []
    for ref in complaint_order_refs:
        if ref.upper().startswith("NEO-"):
            complaint_public_refs.append(ref)
        else:
            try:
                complaint_order_object_ids.append(ObjectId(ref))
            except Exception:
                complaint_public_refs.append(ref)

    order_lookup = {}
    order_matchers = []
    if complaint_order_object_ids:
        order_matchers.append({"_id": {"$in": complaint_order_object_ids}})
    if complaint_public_refs:
        order_matchers.append({"order_number": {"$in": complaint_public_refs}})

    if order_matchers:
        try:
            for order_row in mongo.orders.find({"$or": order_matchers}, {"_id": 1, "order_number": 1}):
                public_number = str(order_row.get("order_number") or "").strip()
                if not public_number:
                    continue
                order_lookup[str(order_row.get("_id") or "")] = public_number
                order_lookup[public_number] = public_number
        except Exception:
            order_lookup = {}

    for c in complaints:
        raw_ref = str(c.get("order_id") or "").strip()
        c["display_order_number"] = order_lookup.get(raw_ref, raw_ref)

    complaint_metrics = {
        "total": len(complaints),
        "admin": sum(1 for c in complaints if c.get("assigned_to") == "admin" or c.get("target_type") == "admin"),
        "store": sum(1 for c in complaints if c.get("assigned_to") == "store" or c.get("target_type") == "store"),
        "open": sum(1 for c in complaints if c.get("status") == "open"),
        "in_progress": sum(1 for c in complaints if c.get("status") == "in_progress"),
        "resolved": sum(1 for c in complaints if c.get("status") == "resolved")
    }

    return render_template(
        'admin_complaints.html',
        user=current_user(),
        complaints=complaints,
        complaint_metrics=complaint_metrics,
        active_page="complaints",
        active_group="operations"
    )


@app.route('/admin/complaints/<cid>/status', methods=['POST'])
@login_required(role='admin')
def admin_complaint_set_status(cid):
    try:
        cid_obj = ObjectId(cid)
    except Exception:
        flash("Invalid complaint.", "danger")
        return redirect(url_for("admin_complaints"))

    complaint = mongo.customer_complaints.find_one({"_id": cid_obj})

    if not complaint:
        flash("Complaint not found.", "danger")
        return redirect(url_for("admin_complaints"))

    if _admin_is_store_complaint_doc(complaint):
        flash("This is a store complaint. Admin can only view it unless it is taken over.", "warning")
        return redirect(url_for("admin_complaints"))

    status = (request.form.get("status") or "open").strip().lower()
    progress_status = (request.form.get("progress_status") or status).strip().lower()
    admin_reply = (request.form.get("admin_reply") or "").strip()
    admin_progress_note = (request.form.get("admin_progress_note") or "").strip()

    allowed_status = {
        "open",
        "in_progress",
        "resolved",
        "rejected"
    }

    allowed_progress = {
        "received",
        "in_progress",
        "resolved",
        "rejected"
    }

    if status not in allowed_status:
        flash("Please select a valid complaint status.", "warning")
        return redirect(url_for("admin_complaints"))

    if progress_status not in allowed_progress:
        flash("Please select a valid progress status.", "warning")
        return redirect(url_for("admin_complaints"))

    if status == "resolved":
        progress_status = "resolved"
    elif status == "in_progress":
        progress_status = "in_progress"
    elif status == "rejected":
        progress_status = "rejected"

    if len(admin_reply) > 1000:
        admin_reply = admin_reply[:1000]

    if len(admin_progress_note) > 1000:
        admin_progress_note = admin_progress_note[:1000]

    now = datetime.utcnow().isoformat()
    admin_user = current_user() or {}

    mongo.customer_complaints.update_one(
        {"_id": cid_obj},
        {
            "$set": {
                "status": status,
                "progress_status": progress_status,
                "admin_reply": admin_reply,
                "admin_progress_note": admin_progress_note,
                "admin_updated_at": now,
                "admin_updated_by": str(admin_user.get("id") or admin_user.get("_id") or ""),
                "admin_updated_by_name": admin_user.get("name") or admin_user.get("email") or "Admin",
                "updated_at": now
            },
            "$push": {
                "complaint_history": {
                    "action": "ADMIN_COMPLAINT_STATUS_UPDATED",
                    "status": status,
                    "progress_status": progress_status,
                    "admin_reply": admin_reply,
                    "admin_progress_note": admin_progress_note,
                    "created_at": now,
                    "created_by": str(admin_user.get("id") or admin_user.get("_id") or ""),
                    "created_by_name": admin_user.get("name") or admin_user.get("email") or "Admin"
                }
            }
        }
    )

    flash("Complaint status updated.", "success")
    return redirect(url_for("admin_complaints"))


@app.route('/admin/complaints/<cid>/takeover', methods=['POST'], endpoint='admin_complaint_takeover')
@login_required(role='admin')
def admin_complaint_takeover(cid):
    try:
        cid_obj = ObjectId(cid)
    except Exception:
        flash("Invalid complaint.", "danger")
        return redirect(url_for("admin_complaints"))

    complaint = mongo.customer_complaints.find_one({"_id": cid_obj})

    if not complaint:
        flash("Complaint not found.", "danger")
        return redirect(url_for("admin_complaints"))

    admin_takeover_status = str(
        complaint.get("admin_takeover_status") or ""
    ).strip().upper()

    if admin_takeover_status == "TAKEN_OVER":
        flash("This complaint is already taken over by Admin.", "warning")
        return redirect(url_for("admin_complaints"))

    if not _admin_is_store_complaint_doc(complaint):
        flash("Only store complaints can be taken over.", "warning")
        return redirect(url_for("admin_complaints"))

    assigned_to = str(complaint.get("assigned_to") or "").strip().lower()
    target_type = str(
        complaint.get("target_type")
        or complaint.get("target_kind")
        or ""
    ).strip().lower()

    takeover_reason = (request.form.get("takeover_reason") or "").strip()

    if len(takeover_reason) > 700:
        takeover_reason = takeover_reason[:700]

    now = datetime.utcnow().isoformat()
    admin_user = current_user() or {}

    original_store_id = complaint.get("store_id")
    original_store_id_str = complaint.get("store_id_str") or str(original_store_id or "")
    original_store_name = complaint.get("store_name") or ""

    takeover_event = {
        "action": "ADMIN_TAKEOVER_STORE_COMPLAINT",
        "old_assigned_to": assigned_to,
        "old_target_type": target_type,
        "new_assigned_to": "admin",
        "new_target_type": "admin",
        "original_store_id": original_store_id_str,
        "original_store_name": original_store_name,
        "takeover_reason": takeover_reason,
        "created_at": now,
        "created_by": str(admin_user.get("id") or admin_user.get("_id") or ""),
        "created_by_name": admin_user.get("name") or admin_user.get("email") or "Admin"
    }

    mongo.customer_complaints.update_one(
        {"_id": cid_obj},
        {
            "$set": {
                "assigned_to": "admin",
                "target_type": "admin",
                "admin_takeover_status": "TAKEN_OVER",
                "admin_takeover_at": now,
                "admin_takeover_by": str(admin_user.get("id") or admin_user.get("_id") or ""),
                "admin_takeover_by_name": admin_user.get("name") or admin_user.get("email") or "Admin",
                "admin_takeover_reason": takeover_reason,

                "original_assigned_to": assigned_to,
                "original_target_type": target_type,
                "original_store_id": original_store_id,
                "original_store_id_str": original_store_id_str,
                "original_store_name": original_store_name,

                "progress_status": "in_progress",
                "status": "in_progress",
                "updated_at": now
            },
            "$push": {
                "complaint_history": takeover_event
            }
        }
    )

    flash("Store complaint has been taken over by Admin.", "success")
    return redirect(url_for("admin_complaints"))
