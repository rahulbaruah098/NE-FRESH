"""Delivery earnings route section.

Route decorators, endpoint names, URL paths, HTTP methods and function bodies are
preserved from the Step 6 source. Shared legacy helpers are imported from
``routes.delivery.shared`` during this transitional decomposition.
"""

from routes.delivery.shared import *

@app.route('/delivery/earnings')
@login_required(role='delivery')
def delivery_earnings():
    u = current_user()

    availability = _get_delivery_availability(u["id"])
    delivery_active = bool(availability.get("active"))

    q = (request.args.get("q") or "").strip()
    date_from = (request.args.get("from") or "").strip()
    date_to = (request.args.get("to") or "").strip()

    query_filter = {
        "$and": [
            {
                "$or": [
                    {"delivery_partner_id": u["id"]},
                    {"delivery_partner_id": str(u["id"])}
                ]
            },
            {
                "status": "DELIVERED"
            }
        ]
    }

    raw_orders = list(
        mongo.orders.find(query_filter).sort("delivered_at", -1)
    )

    orders = []

    for o in raw_orders:
        o = _decorate_delivery_financials(_hydrate_delivery_order(o))

        delivered_at = str(o.get("delivered_at") or o.get("updated_at") or "")

        if date_from and delivered_at and delivered_at[:10] < date_from:
            continue

        if date_to and delivered_at and delivered_at[:10] > date_to:
            continue

        if q:
            haystack = " ".join([
                str(o.get("id") or ""),
                str(o.get("store_name") or ""),
                str(o.get("customer_name") or ""),
                str(o.get("customer_phone") or ""),
                str(o.get("payment_method") or ""),
                str(o.get("payment_status") or "")
            ]).lower()

            if q.lower() not in haystack:
                continue

        orders.append(o)

    total_cod_collected = 0
    total_delivery_fee = 0
    total_tip = 0
    total_payable = 0
    total_platform_fee = 0
    total_expected_earning = 0

    for o in orders:
        total_payable += float(o.get("total_payable") or 0)
        total_delivery_fee += float(o.get("delivery_fee") or 0)
        total_tip += float(o.get("tip_amount") or 0)
        total_platform_fee += float(o.get("platform_fee") or 0)
        total_expected_earning += float(o.get("delivery_boy_expected_earning") or 0)
        total_cod_collected += float(o.get("cod_collected_amount") or 0)

    current_month = delivery_monthly_current_period()
    monthly_groups = {}

    for o in raw_orders:
        if not delivery_order_uses_monthly_payout(o):
            continue

        period = (o.get("delivery_monthly_period") or "").strip()
        if not period:
            period = delivery_monthly_period_from_utc(o.get("delivered_at") or o.get("updated_at"))

        group = monthly_groups.setdefault(period, {
            "period": period,
            "period_label": delivery_monthly_period_label(period),
            "order_count": 0,
            "delivery_fee": 0.0,
            "tips": 0.0,
            "gross_earning": 0.0,
            "paid_order_count": 0,
            "unreconciled_count": 0,
        })

        fee = _delivery_money_float(
            o.get("delivery_fee_amount") if o.get("delivery_fee_amount") is not None else o.get("delivery_fee"),
            0.0
        )
        tip = _delivery_money_float(
            o.get("tip_amount") if o.get("tip_amount") is not None else o.get("delivery_tip_amount"),
            0.0
        )
        earning = _delivery_money_float(
            o.get("delivery_boy_payout_amount") if o.get("delivery_boy_payout_amount") is not None else o.get("delivery_boy_earning"),
            fee + tip
        )

        group["order_count"] += 1
        group["delivery_fee"] += fee
        group["tips"] += tip
        group["gross_earning"] += earning
        if (o.get("delivery_boy_payout_status") or "").strip().upper() == DELIVERY_MONTHLY_STATUS_PAID:
            group["paid_order_count"] += 1
        elif not delivery_monthly_payment_is_reconciled(o):
            group["unreconciled_count"] += 1

    paid_batches = {
        str(doc.get("period") or ""): doc
        for doc in mongo.delivery_partner_monthly_settlements.find({
            "delivery_partner_id_str": str(u["id"]),
            "status": DELIVERY_MONTHLY_BATCH_STATUS_PAID
        })
    }

    monthly_rows = []
    for period, group in monthly_groups.items():
        batch = paid_batches.get(period) or {}
        if (batch.get("status") or "").upper() == DELIVERY_MONTHLY_BATCH_STATUS_PAID:
            status = "PAID"
            paid_at = batch.get("paid_at") or ""
            payment_mode = batch.get("payment_mode") or ""
            reference_no = batch.get("reference_no") or ""
        elif period == current_month:
            status = "ACCRUING"
            paid_at = ""
            payment_mode = ""
            reference_no = ""
        elif group.get("unreconciled_count", 0) > 0:
            status = "RECONCILIATION PENDING"
            paid_at = ""
            payment_mode = ""
            reference_no = ""
        else:
            status = "READY"
            paid_at = ""
            payment_mode = ""
            reference_no = ""

        group["delivery_fee"] = round(group["delivery_fee"], 2)
        group["tips"] = round(group["tips"], 2)
        group["gross_earning"] = round(group["gross_earning"], 2)
        group["status"] = status
        group["paid_at"] = paid_at
        group["payment_mode"] = payment_mode
        group["reference_no"] = reference_no
        monthly_rows.append(group)

    monthly_rows.sort(key=lambda row: row.get("period") or "", reverse=True)
    current_month_summary = next((row for row in monthly_rows if row.get("period") == current_month), {
        "period": current_month,
        "period_label": delivery_monthly_period_label(current_month),
        "order_count": 0,
        "delivery_fee": 0.0,
        "tips": 0.0,
        "gross_earning": 0.0,
        "status": "ACCRUING"
    })
    total_monthly_paid = round(sum(
        float((paid_batches.get(row.get("period")) or {}).get("amount_paid") or 0)
        for row in monthly_rows
        if row.get("status") == "PAID"
    ), 2)

    return render_template(
        "delivery_earnings.html",
        user=u,
        orders=orders,
        delivery_active=delivery_active,
        delivery_availability=availability,
        q=q,
        date_from=date_from,
        date_to=date_to,
        total_cod_collected=total_cod_collected,
        total_delivery_fee=total_delivery_fee,
        total_tip=total_tip,
        total_payable=total_payable,
        total_platform_fee=total_platform_fee,
        total_expected_earning=total_expected_earning,
        monthly_rows=monthly_rows,
        current_month=current_month,
        current_month_summary=current_month_summary,
        total_monthly_paid=total_monthly_paid
    )
