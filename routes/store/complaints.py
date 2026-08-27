"""Store complaints route section.

Route decorators, endpoint names, URL paths, HTTP methods and function bodies are
preserved from the Step 6 source. Shared legacy helpers are imported from
``routes.store.shared`` during this transitional decomposition.
"""

from routes.store.shared import *

@app.route('/store/complaints', methods=['GET'], endpoint='store_complaints')
@login_required(role='store')
def store_complaints_page():
    u, store = _get_current_store_or_redirect()

    if not store:
        return redirect(url_for("store_dashboard"))

    page_context = _build_store_split_page_context(store) or {}

    store_id = store["_id"]
    store_id_str = str(store_id)

    complaints = list(
        mongo.customer_complaints.find({
            "$and": [
                {
                    "$or": [
                        {"store_id": store_id},
                        {"store_id": store_id_str},
                        {"store_id_str": store_id_str}
                    ]
                },
                {
                    "$or": [
                        {"is_active": 1},
                        {"is_active": True},
                        {"is_active": {"$exists": False}}
                    ]
                }
            ]
        }).sort("created_at", -1)
    )

    # Resolve complaint order references to the same public NEO-* order number
    # used on the customer/store Orders pages. This is display-only and uses
    # one batched lookup to avoid per-row database queries.
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
                if public_number:
                    order_lookup[str(order_row.get("_id") or "")] = public_number
                    order_lookup[public_number] = public_number
        except Exception:
            order_lookup = {}

    for c in complaints:
        raw_order_ref = str(c.get("order_id") or "").strip()
        c["display_order_number"] = order_lookup.get(raw_order_ref, raw_order_ref)
        c["id"] = str(c["_id"])
        c["complaint_image_path"] = c.get("complaint_image_path") or c.get("image_path") or ""

        status = str(c.get("status") or "open").strip().lower()
        progress_status = str(c.get("progress_status") or "received").strip().lower()
        admin_takeover_status = str(c.get("admin_takeover_status") or "").strip().upper()

        c["status"] = status
        c["progress_status"] = progress_status
        c["status_label"] = status.replace("_", " ").title()
        c["progress_status_label"] = progress_status.replace("_", " ").title()

        c["admin_takeover_status"] = admin_takeover_status
        c["is_admin_taken_over"] = admin_takeover_status == "TAKEN_OVER"
        c["admin_takeover_reason"] = c.get("admin_takeover_reason") or ""
        c["admin_takeover_by_name"] = c.get("admin_takeover_by_name") or "NE FRESH Admin"
        c["admin_takeover_at"] = c.get("admin_takeover_at") or ""

        created_at = c.get("created_at") or ""
        updated_at = c.get("updated_at") or ""

        c["created_at_display"] = created_at
        c["updated_at_display"] = updated_at

        try:
            if isinstance(created_at, str) and created_at:
                clean_dt = created_at.replace("Z", "")
                dt_obj = datetime.fromisoformat(clean_dt)
                c["created_at_display"] = dt_obj.strftime("%d %b %Y, %I:%M %p")
        except Exception:
            pass

        try:
            if isinstance(updated_at, str) and updated_at:
                clean_dt = updated_at.replace("Z", "")
                dt_obj = datetime.fromisoformat(clean_dt)
                c["updated_at_display"] = dt_obj.strftime("%d %b %Y, %I:%M %p")
        except Exception:
            pass

    complaint_metrics = {
        "total": len(complaints),
        "open": sum(1 for c in complaints if c.get("status") == "open"),
        "in_progress": sum(1 for c in complaints if c.get("status") == "in_progress"),
        "resolved": sum(1 for c in complaints if c.get("status") == "resolved")
    }

    return render_template(
        "store_complaints.html",
        user=u,
        store=store,
        complaints=complaints,
        complaint_metrics=complaint_metrics,
        **page_context
    )


@app.route('/store/complaints/<cid>/update', methods=['POST'], endpoint='store_complaint_update')
@login_required(role='store')
def store_complaint_update(cid):
    u, store = _get_current_store_or_redirect()

    if not store:
        return redirect(url_for("store_dashboard"))

    try:
        cid_obj = ObjectId(cid)
    except Exception:
        flash("Invalid complaint.", "danger")
        return redirect(url_for("store_complaints"))

    store_id = store["_id"]
    store_id_str = str(store_id)

    complaint = mongo.customer_complaints.find_one({
        "_id": cid_obj,
        "$or": [
            {"store_id": store_id},
            {"store_id": store_id_str},
            {"store_id_str": store_id_str}
        ]
    })

    if not complaint:
        flash("Complaint not found for your store.", "danger")
        return redirect(url_for("store_complaints"))
    
    admin_takeover_status = str(
        complaint.get("admin_takeover_status") or ""
    ).strip().upper()

    if admin_takeover_status == "TAKEN_OVER":
        flash("This complaint has been taken over by NE FRESH Admin. Store updates are disabled.", "warning")
        return redirect(url_for("store_complaints"))

    progress_status = (request.form.get("progress_status") or "").strip().lower()
    store_reply = (request.form.get("store_reply") or "").strip()
    store_progress_note = (request.form.get("store_progress_note") or "").strip()

    allowed_progress = {
        "received",
        "in_progress",
        "resolved"
    }

    if progress_status not in allowed_progress:
        flash("Please select a valid progress status.", "warning")
        return redirect(url_for("store_complaints"))

    if len(store_reply) > 1000:
        flash("Store reply is too long. Please keep it within 1000 characters.", "warning")
        return redirect(url_for("store_complaints"))

    if len(store_progress_note) > 1000:
        flash("Progress note is too long. Please keep it within 1000 characters.", "warning")
        return redirect(url_for("store_complaints"))

    if progress_status == "resolved":
        final_status = "resolved"
    elif progress_status == "in_progress":
        final_status = "in_progress"
    else:
        final_status = "open"

    now = datetime.utcnow().isoformat()

    

    mongo.customer_complaints.update_one(
        {"_id": cid_obj},
        {
            "$set": {
                "progress_status": progress_status,
                "status": final_status,
                "store_reply": store_reply,
                "store_progress_note": store_progress_note,
                "store_updated_by": str(u["_id"]),
                "store_updated_by_name": u.get("name", "Store User"),
                "updated_at": now
            }
        }
    )

    flash("Complaint progress updated successfully.", "success")
    return redirect(url_for("store_complaints"))
