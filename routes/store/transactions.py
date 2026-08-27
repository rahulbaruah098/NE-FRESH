"""Store transactions route section.

Route decorators, endpoint names, URL paths, HTTP methods and function bodies are
preserved from the Step 6 source. Shared legacy helpers are imported from
``routes.store.shared`` during this transitional decomposition.
"""

from routes.store.shared import *

@app.route('/store/transactions.csv')
@login_required(role='store')
def store_txn_csv():
    """
    Download transactions for this store as CSV.
    Supported presets via ?range=day|week|month.
    You can also pass explicit ?start=YYYY-MM-DD&end=YYYY-MM-DD.
    Only PAID transactions are included.
    """
    u = current_user()

    store = mongo.stores.find_one({"user_id": u["id"]})

    if not store:
        flash("Store not found.", "danger")
        return redirect(url_for("store_dashboard"))

    preset = (request.args.get("range") or "").lower()
    start_str = request.args.get("start")
    end_str = request.args.get("end")

    if start_str and end_str:
        try:
            start_date = date.fromisoformat(start_str)
            end_date = date.fromisoformat(end_str)
        except Exception:
            flash("Invalid start/end date. Use YYYY-MM-DD.", "warning")
            return redirect(url_for("store_dashboard"))
    else:
        today = datetime.utcnow().date()

        if preset == "week":
            start_date = today - timedelta(days=today.weekday())
            end_date = start_date + timedelta(days=7)
        elif preset == "month":
            start_date = date(today.year, today.month, 1)

            if today.month == 12:
                end_date = date(today.year + 1, 1, 1)
            else:
                end_date = date(today.year, today.month + 1, 1)
        else:
            start_date = today
            end_date = today + timedelta(days=1)

    start_iso = f"{start_date.isoformat()}T00:00:00"
    end_iso = f"{end_date.isoformat()}T00:00:00"

    txns = list(
        mongo.transactions.find({
            "status": "PAID",
            "created_at": {
                "$gte": start_iso,
                "$lt": end_iso
            }
        }).sort("created_at", -1)
    )

    csv_lines = [
        "txn_id,txn_created_at,order_id,items_total,delivery_fee,tip_amount,paid_amount,txn_status"
    ]

    for t in txns:
        order_id = t.get("order_id")
        order = None

        if order_id:
            order = mongo.orders.find_one({
                "_id": order_id,
                "store_id": store["_id"]
            })

        if not order:
            continue

        csv_lines.append(",".join([
            str(t.get("_id", "")),
            str(t.get("created_at", "")),
            str(order.get("_id", "")),
            str(float(order.get("total_amount") or 0)),
            str(float(order.get("delivery_fee") or 0)),
            str(float(order.get("tip_amount") or 0)),
            str(float(t.get("amount") or 0)),
            str(t.get("status", "")),
        ]))

    data = "\n".join(csv_lines).encode("utf-8")

    return send_file(
        io.BytesIO(data),
        mimetype="text/csv",
        as_attachment=True,
        download_name="store_transactions.csv"
    )
