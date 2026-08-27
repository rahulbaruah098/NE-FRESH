"""Orders checkout route section.

Route decorators, endpoint names, URL paths, HTTP methods and function bodies are
preserved from the Step 6 source. Shared legacy helpers are imported from
``routes.orders.shared`` during this transitional decomposition.
"""

from routes.orders.shared import *

@app.route('/checkout', methods=['GET', 'POST'])
@login_required()
def checkout():
    u = current_user()
    cid = get_or_create_cart(u["id"])

    store_lat = None
    store_lng = None

    payment_settings = get_checkout_payment_gateway_settings()
    online_payment_enabled = bool(
        payment_settings.get("enabled")
        and payment_settings.get("gateway") == "RAZORPAY"
        and payment_settings.get("razorpay_key_id")
    )

    cart_items = list(mongo.cart_items.find({"cart_id": cid}))

    items = []

    for ci in cart_items:
        item = _checkout_hydrate_cart_item(ci)

        if not item:
            continue

        if item.get("invalid_bundle"):
            flash(item.get("bundle_error") or "One product bundle is no longer available. Please update your cart.", "danger")
            return redirect(url_for("cart_page"))

        items.append(item)

    store_ids = sorted(set([str(it["store_id"]) for it in items if it.get("store_id")]))
    cart_store_count = len(store_ids)

    if cart_store_count > 1:
        flash("Your cart contains items from multiple stores. Please clear the cart and order from one store at a time.", "danger")
        return redirect(url_for("cart_page"))

    for it in items:
        if int(it["is_active"] or 0) != 1:
            flash("One or more items are sold out.", "danger")
            return redirect(url_for("cart_page"))

        if float(it["stock_quantity"] or 0) <= 0:
            flash("One or more items are sold out.", "danger")
            return redirect(url_for("cart_page"))

        quantity_min = float(it.get("quantity_min") or 1)

        if float(it["quantity"] or 0) < quantity_min:
            flash(
                f"{it.get('name', 'One item')} requires minimum order quantity of {quantity_min:g} {it.get('unit_label', 'unit')}. Please update your cart.",
                "danger"
            )
            return redirect(url_for("cart_page"))

        if float(it["quantity"] or 0) > float(it["stock_quantity"] or 0):
            flash(
                "Requested amount is not available in stock. Please change the amount.",
                "danger"
            )
            return redirect(url_for("cart_page"))

    addresses = list(
        mongo.addresses.find({"user_id": u["id"]}).sort([
            ("is_default", -1),
            ("created_at", -1)
        ])
    )

    for a in addresses:
        a["id"] = str(a["_id"])

    if items:
        store = mongo.stores.find_one({"_id": items[0]["store_id"]})
        if store:
            store_lat = store.get("latitude")
            store_lng = store.get("longitude")

    if request.method == "POST":
        if not items:
            flash("Your cart is empty.", "warning")
            return redirect(url_for("cart_page"))

        if cart_store_count > 1:
            flash("Your cart contains items from multiple stores. Please order from one store at a time.", "danger")
            return redirect(url_for("cart_page"))

        if not addresses:
            flash("Please add delivery address before checkout.", "warning")
            return redirect(url_for("profile"))

        for it in items:
            if int(it["is_active"] or 0) != 1:
                flash("One or more items are sold out.", "danger")
                return redirect(url_for("cart_page"))

            if float(it["stock_quantity"] or 0) <= 0:
                flash("One or more items are sold out.", "danger")
                return redirect(url_for("cart_page"))

            quantity_min = float(it.get("quantity_min") or 1)

            if float(it["quantity"] or 0) < quantity_min:
                flash(
                    f"{it.get('name', 'One item')} requires minimum order quantity of {quantity_min:g} {it.get('unit_label', 'unit')}. Please update your cart.",
                    "danger"
                )
                return redirect(url_for("cart_page"))

            if float(it["quantity"] or 0) > float(it["stock_quantity"] or 0):
                flash(
                    "Requested amount is not available in stock. Please change the amount.",
                    "danger"
                )
                return redirect(url_for("cart_page"))

        addr_id = request.form.get("address_id")

        if not addr_id:
            flash("Please select a delivery address.", "warning")
            return redirect(url_for("checkout"))

        try:
            addr_obj_id = ObjectId(addr_id)
        except Exception:
            flash("Invalid address selected.", "danger")
            return redirect(url_for("checkout"))

        sel = mongo.addresses.find_one({
            "_id": addr_obj_id,
            "user_id": u["id"]
        })

        if not sel:
            flash("Invalid address selected.", "danger")
            return redirect(url_for("checkout"))

        sel_pin = (sel.get("pincode") or "").strip()

        if not is_serviceable_pincode(sel_pin):
            flash("Please enter a valid 6-digit pincode.", "danger")
            return redirect(url_for("checkout"))

        if not is_assam_state(sel.get("state")):
            flash("Delivery is currently available only within Assam.", "danger")
            return redirect(url_for("checkout"))

        items_total = sum([
            float(it.get("line_total") or 0)
            for it in items
        ])

        store_id = items[0]["store_id"]
        store = mongo.stores.find_one({"_id": store_id}) or {}

        store_lat = store.get("latitude")
        store_lng = store.get("longitude")

        def _safe_float(value):
            try:
                if value is None or str(value).strip() == "":
                    return None
                return float(value)
            except Exception:
                return None

        # Location priority:
        # 1. Fresh checkout GPS/current-location hidden fields
        # 2. Session location from navbar/checkout location API
        # 3. Saved address coordinates
        form_lat = _safe_float(request.form.get("resolved_lat"))
        form_lng = _safe_float(request.form.get("resolved_lng"))

        session_lat = _safe_float(session.get("location_lat"))
        session_lng = _safe_float(session.get("location_lng"))

        saved_lat = _safe_float(sel.get("latitude"))
        saved_lng = _safe_float(sel.get("longitude"))

        final_lat = form_lat if form_lat is not None else session_lat if session_lat is not None else saved_lat
        final_lng = form_lng if form_lng is not None else session_lng if session_lng is not None else saved_lng

        if form_lat is not None and form_lng is not None:
            location_source = "checkout_gps"
        elif session_lat is not None and session_lng is not None:
            location_source = session.get("location_source") or "session_location"
        elif saved_lat is not None and saved_lng is not None:
            location_source = "saved_address"
        else:
            location_source = "missing_coordinates"

        requested_payment_method = (request.form.get("payment_method") or "COD").strip().upper()

        if requested_payment_method not in ["COD", "ONLINE"]:
            requested_payment_method = "COD"

        serviceability = check_checkout_delivery_quote(
            store=store,
            customer_lat=final_lat,
            customer_lng=final_lng,
            customer_pincode=sel_pin,
            items_total=items_total,
            payment_method=requested_payment_method,
            cart_items=items
        )

        if not serviceability.get("serviceable"):
            flash(
                serviceability.get("message") or "Delivery is not available for your selected location.",
                "danger"
            )
            return redirect(url_for("checkout"))

        km = serviceability.get("distance_km")
        delivery_fee = serviceability.get("delivery_fee", 0)
        delivery_fee_source = serviceability.get("delivery_fee_source") or "unknown"
        delivery_fee_slab = serviceability.get("delivery_fee_slab")
        delivery_base_fee = float(serviceability.get("delivery_base_fee") or 0)
        delivery_fee_details = serviceability.get("delivery_fee_details") or {}

        free_delivery_above_applied = delivery_fee_source == "admin_free_delivery_above"
        free_delivery_above = float(
            delivery_fee_details.get("free_delivery_above")
            or 0
        )
        original_delivery_fee = float(
            delivery_fee_details.get("original_delivery_fee")
            or delivery_fee
            or 0
        )
        free_delivery_savings = float(
            delivery_fee_details.get("free_delivery_savings")
            or 0
        )

        tip_amount_raw = (
            request.form.get("tip_amount")
            or request.form.get("tip")
            or request.form.get("delivery_tip")
            or "0"
        )

        try:
            tip_amount = float(tip_amount_raw or 0)
        except (TypeError, ValueError):
            tip_amount = 0.0

        if tip_amount < 0:
            tip_amount = 0.0

        if tip_amount > 10000:
            tip_amount = 10000.0

        tip_amount = round(tip_amount, 2)

        now = datetime.utcnow().isoformat()

        delivery_mode_snapshot = get_order_delivery_mode_snapshot(serviceability.get("active_delivery_mode"))
        active_delivery_mode = delivery_mode_snapshot.get("active_delivery_mode") or DELIVERY_MODE_IN_HOUSE
        in_house_delivery_enabled_at_order = bool(delivery_mode_snapshot.get("in_house_delivery_enabled_at_order", True))
        external_delivery_enabled_at_order = bool(delivery_mode_snapshot.get("external_delivery_enabled_at_order", False))
        external_payment_rule = delivery_mode_snapshot.get("external_payment_rule") or EXTERNAL_PAYMENT_RULE_COD_STORE
        delivery_payment_methods = delivery_mode_snapshot.get("delivery_payment_methods") or DELIVERY_PAYMENT_ONLINE_AND_COD
        allow_online_payment_for_mode = bool(delivery_mode_snapshot.get("allow_online_payment", True))
        allow_cod_payment_for_mode = bool(delivery_mode_snapshot.get("allow_cod_payment", True))
        cod_collection_method = delivery_mode_snapshot.get("cod_collection_method") or COD_COLLECTION_DELIVERY_BOY
        delivery_type = delivery_mode_snapshot.get("delivery_type") or "OWN_DELIVERY"
        external_delivery_provider_type = delivery_mode_snapshot.get("external_delivery_provider_type") or "IN_HOUSE"
        external_delivery_status_initial = delivery_mode_snapshot.get("external_delivery_status") or "NOT_APPLICABLE"

        # Use the latest serviceability response for payment availability because it
        # also reflects the selected active delivery mode and external quote path.
        allow_online_payment_for_mode = bool(serviceability.get("online_allowed", allow_online_payment_for_mode))
        allow_cod_payment_for_mode = bool(serviceability.get("cod_allowed", allow_cod_payment_for_mode))

        if requested_payment_method == "COD" and not allow_cod_payment_for_mode:
            flash("Cash on Delivery (COD) is currently disabled for this delivery route. Please choose Online Payment.", "warning")
            return redirect(url_for("checkout"))

        if requested_payment_method == "ONLINE" and not allow_online_payment_for_mode:
            flash("Online payment is currently disabled for this delivery route. Please choose Cash on Delivery (COD).", "warning")
            return redirect(url_for("checkout"))

        store_allows_online = bool(store.get("allow_online_payment", True))

        if requested_payment_method == "ONLINE":
            if not online_payment_enabled:
                flash("Online payment is currently not enabled by Admin. Please use Cash on Delivery (COD).", "warning")
                return redirect(url_for("checkout"))

            if not store_allows_online:
                flash("Online payment is currently not enabled for this store. Please use Cash on Delivery (COD).", "warning")
                return redirect(url_for("checkout"))

            payment_method = "ONLINE"
        else:
            payment_method = "COD"

        money_breakdown = build_order_money_breakdown(
            items_total=items_total,
            delivery_fee=delivery_fee,
            tip_amount=tip_amount,
            payment_method=payment_method
        )

        total_payable = float(money_breakdown.get("total_payable") or 0)

        # ------------------------------------------------------------
        # Platform-controlled payment / settlement base fields
        # ------------------------------------------------------------
        # COD_RIDER_COLLECTION = customer pays rider as collection agent; the full customer cash is business money and is remitted to Admin.
        # ONLINE_PLATFORM = customer pays NE FRESH through platform gateway.
        # ------------------------------------------------------------
        is_online_order = payment_method == "ONLINE"

        # delivery_mode_snapshot was frozen before payment calculation.
        # It lets old orders keep their original delivery mode even if Admin changes the global mode later.

        if is_online_order:
            platform_payment_flow = "ONLINE_PLATFORM"
            initial_payment_status = "PENDING_PAYMENT"
            payment_collection_status = "ONLINE_PENDING"
            cod_collection_status = "NOT_REQUIRED"
            initial_payment_reconciliation_status = "PENDING_PAYMENT"
            external_cod_remittance_status = "NOT_REQUIRED"
            platform_fee_status = "PENDING_PAYMENT"
            rider_cash_settlement_status = "NOT_REQUIRED"
            initial_store_payout_status = "PENDING_AFTER_DELIVERY"
            initial_store_settlement_status = "PAYOUT_PENDING"
            initial_order_settlement_status = "PENDING_PAYMENT"
            expected_rider_cash_to_submit = 0.0
            rider_cash_to_submit = 0.0
            flash_message = "Order created. Please complete online payment in the next step."
        else:
            if in_house_delivery_enabled_at_order:
                platform_payment_flow = "COD_RIDER_COLLECTION"
                payment_collection_status = "PENDING"
                cod_collection_status = "PENDING"
                initial_payment_reconciliation_status = "PENDING_COLLECTION"
                external_cod_remittance_status = "NOT_REQUIRED"
                platform_fee_status = "PENDING_COLLECTION"
                rider_cash_settlement_status = "NOT_COLLECTED_YET"
                initial_store_payout_status = "PENDING_AFTER_DELIVERY"
                initial_store_settlement_status = "PAYOUT_PENDING"
                initial_order_settlement_status = "PENDING_COLLECTION"
            else:
                if external_payment_rule == EXTERNAL_PAYMENT_RULE_COD_PARTNER:
                    platform_payment_flow = "COD_PARTNER_COLLECTION"
                    payment_collection_status = "PENDING_PARTNER_COLLECTION"
                    cod_collection_status = "PENDING_PARTNER_COLLECTION"
                    initial_payment_reconciliation_status = "PENDING_PARTNER_COLLECTION"
                    external_cod_remittance_status = "PENDING_PARTNER_COLLECTION"
                    platform_fee_status = "PENDING_PARTNER_REMITTANCE"
                    initial_store_payout_status = "PENDING_AFTER_DELIVERY"
                    initial_store_settlement_status = "PAYOUT_PENDING"
                    initial_order_settlement_status = "PENDING_PARTNER_COLLECTION"
                else:
                    platform_payment_flow = (
                        "PAY_ON_DELIVERY_STORE_ONLINE"
                        if active_delivery_mode == DELIVERY_MODE_EXTERNAL_LOCAL
                        else "COD_STORE_COLLECTION"
                    )
                    payment_collection_status = "PENDING_STORE_COLLECTION"
                    cod_collection_status = "PENDING_STORE_COLLECTION"
                    initial_payment_reconciliation_status = "PENDING_STORE_COLLECTION"
                    external_cod_remittance_status = "NOT_REQUIRED"
                    platform_fee_status = "DUE_FROM_STORE"
                    # The Store will receive the customer money directly. Admin does not owe
                    # a separate Store payout for this order.
                    initial_store_payout_status = "NOT_REQUIRED"
                    initial_store_settlement_status = "DIRECT_COLLECTION_PENDING"
                    initial_order_settlement_status = "PENDING_STORE_COLLECTION"

                rider_cash_settlement_status = "NOT_APPLICABLE"

            initial_payment_status = "PENDING"
            flash_message = "Order placed! Cash on Delivery (COD) selected."

        items_subtotal_amount = round(float(money_breakdown.get("items_subtotal") or items_total or 0), 2)
        delivery_fee_amount_final = round(float(money_breakdown.get("delivery_fee") or delivery_fee or 0), 2)
        platform_fee_amount = round(float(money_breakdown.get("platform_fee") or 0), 2)
        tip_amount_final = round(float(money_breakdown.get("tip_amount") or tip_amount or 0), 2)

        store_earning_amount = round(float(money_breakdown.get("store_earning") or items_subtotal_amount or 0), 2)
        delivery_boy_earning_amount = (
            round(delivery_fee_amount_final + tip_amount_final, 2)
            if in_house_delivery_enabled_at_order
            else 0.0
        )

        external_delivery_fee_amount = (
            0.0
            if in_house_delivery_enabled_at_order
            else round(delivery_fee_amount_final + tip_amount_final, 2)
        )
        admin_platform_earning_amount = round(
            float(money_breakdown.get("admin_platform_earning") or platform_fee_amount or 0),
            2
        )

        if not is_online_order and in_house_delivery_enabled_at_order:
            # MONTHLY_V1 keeps customer money and delivery-partner pay completely separate.
            # The rider may physically collect COD cash, but must remit the FULL customer
            # payment. Delivery fee + tip are paid later through the monthly settlement.
            expected_rider_cash_to_submit = round(max(float(total_payable or 0), 0), 2)

            # Actual rider cash liability starts only after COD is physically collected.
            rider_cash_to_submit = 0.0
        elif not is_online_order:
            expected_rider_cash_to_submit = 0.0
            rider_cash_to_submit = 0.0

        order_items_docs = []

        for it in items:
            order_items_docs.append(_checkout_order_item_doc_from_item(it))

        order_result = mongo.orders.insert_one({
            "user_id": u["id"],
            "customer_name": u.get("name"),
            "customer_phone": u.get("phone"),
            "store_id": store_id,
            "store_name": store.get("store_name", ""),

            # Customer-facing public number.
            # COD receives it after stock reservation succeeds.
            # Online receives it only after Razorpay verification succeeds.
            "order_number": "",

            "items_subtotal": items_subtotal_amount,

            # Final payable amount including items + delivery fee + platform fee + tip.
            # Keep this same as total_payable so old pages using total_amount do not show only subtotal.
            "total_amount": float(total_payable),

            "status": "PENDING_PAYMENT" if is_online_order else "PLACED",
            "payment_status": initial_payment_status,
            "payment_method": payment_method,

            # ------------------------------------------------------------
            # New platform-controlled payment flow fields
            # ------------------------------------------------------------
            "payment_flow": platform_payment_flow,
            "official_payment_mode": platform_payment_flow,
            "delivery_type": delivery_type,
            "active_delivery_mode": active_delivery_mode,
            "in_house_delivery_enabled_at_order": bool(in_house_delivery_enabled_at_order),
            "external_delivery_enabled_at_order": bool(external_delivery_enabled_at_order),
            "external_payment_rule": external_payment_rule,
            "delivery_payment_methods": delivery_payment_methods,
            "allow_online_payment": bool(allow_online_payment_for_mode),
            "allow_cod_payment": bool(allow_cod_payment_for_mode),
            "cod_collection_method": cod_collection_method,

            "external_delivery_status": external_delivery_status_initial,
            "external_delivery_provider_type": external_delivery_provider_type,
            "external_delivery_partner_type": external_delivery_provider_type,
            "external_delivery_partner_name": "",
            "external_delivery_provider": (
                serviceability.get("external_delivery_provider")
                or delivery_mode_snapshot.get("external_delivery_provider")
                or (delivery_mode_snapshot.get("third_party_provider") if active_delivery_mode == DELIVERY_MODE_THIRD_PARTY else delivery_mode_snapshot.get("external_local_provider"))
            ) if external_delivery_enabled_at_order else "",
            "external_delivery_fee_amount": external_delivery_fee_amount,
            "external_delivery_charge": external_delivery_fee_amount,
            "external_delivery_booking_status": (
                "NOT_REQUIRED" if in_house_delivery_enabled_at_order
                else ("ORDER_REFERENCE_ONLY" if active_delivery_mode == DELIVERY_MODE_EXTERNAL_LOCAL else "PENDING_SHIPROCKET_BOOKING")
            ),
            "external_order_id": "",
            "external_shipment_id": "",
            "external_awb": "",
            "external_tracking_url": "",
            "external_label_url": "",
            "external_manifest_url": "",
            "external_cod_amount": float(total_payable) if (not is_online_order and external_delivery_enabled_at_order) else 0.0,
            "external_cod_remittance_status": external_cod_remittance_status,
            "external_delivery_payload": {},
            "external_delivery_response": {},
            "external_delivery_quote": serviceability.get("external_delivery_quote") or {},
            "external_package_snapshot": serviceability.get("external_package_snapshot") or (serviceability.get("external_delivery_quote") or {}).get("package") or {},
            "external_delivery_quote_status": (serviceability.get("external_delivery_quote") or {}).get("quote_status") or "",
            "external_delivery_quote_message": (serviceability.get("external_delivery_quote") or {}).get("message") or "",
            "external_delivery_quote_source": serviceability.get("delivery_fee_source") or "",
            "external_delivery_eta_minutes": serviceability.get("eta_minutes"),
            "external_status_history": [],

            # At order creation, COD / Pay-on-Delivery money is not collected yet.
            # At delivery, the assigned rider records either Cash or official UPI.
            "payment_received_by": None,
            "payment_collected_at": None,
            "payment_collection_channel": "RAZORPAY" if payment_method == "ONLINE" else "",
            "payment_reconciliation_status": initial_payment_reconciliation_status,
            "upi_delivery_reference": "",
            "upi_delivery_reconciliation_status": "NOT_APPLICABLE",
            "payment_collection_status": payment_collection_status,
            "cod_collection_status": cod_collection_status,

            "delivery_partner_id": None,

            "delivery_fee": float(money_breakdown.get("delivery_fee") or delivery_fee),
            "delivery_fee_amount": float(money_breakdown.get("delivery_fee_amount") or delivery_fee),
            "delivery_fee_source": delivery_fee_source,
            "delivery_fee_slab": delivery_fee_slab,
            "delivery_base_fee": delivery_base_fee,
            "delivery_fee_details": delivery_fee_details,
            "free_delivery_above_applied": bool(free_delivery_above_applied),
            "free_delivery_above": float(free_delivery_above or 0),
            "original_delivery_fee": float(original_delivery_fee or 0),
            "free_delivery_savings": float(free_delivery_savings or 0),
            "delivery_fee_settings_snapshot": {
                "delivery_fee_settings_source": delivery_fee_details.get("delivery_fee_settings_source") or "admin_platform_settings",
                "admin_delivery_base_fee": delivery_base_fee,
                "admin_free_delivery_above": float(free_delivery_above or 0),
                "admin_delivery_fee_source": delivery_fee_source,
                "admin_delivery_fee_slab": delivery_fee_slab,
                "admin_original_delivery_fee": float(original_delivery_fee or 0),
                "admin_free_delivery_savings": float(free_delivery_savings or 0),
            },

            "platform_fee": platform_fee_amount,
            "admin_platform_earning": admin_platform_earning_amount,
            "platform_fee_source": money_breakdown.get("platform_fee_source") or "disabled",
            "platform_fee_settings_snapshot": money_breakdown.get("platform_fee_settings_snapshot") or {},

            "store_earning": store_earning_amount,
            "delivery_tip_amount": tip_amount_final,

            # ------------------------------------------------------------
            # Existing settlement fields preserved for current pages
            # ------------------------------------------------------------
            "settlement_status": money_breakdown.get("settlement_status") or "PENDING",
            "store_settlement_status": money_breakdown.get("store_settlement_status") or "PENDING",
            "admin_platform_fee_status": money_breakdown.get("admin_platform_fee_status") or "DUE",
            "delivery_settlement_status": money_breakdown.get("delivery_settlement_status") or "PENDING",

            # ------------------------------------------------------------
            # New platform-controlled settlement fields
            # ------------------------------------------------------------
            "platform_fee_status": platform_fee_status,
            "platform_fee_received_at": None,

            "store_payout_amount": store_earning_amount,
            "store_payout_status": initial_store_payout_status,
            "store_payout_paid_at": None,
            "store_payout_marked_by": None,
            "store_payout_note": "",

            "delivery_boy_earning": delivery_boy_earning_amount,
            "delivery_boy_payout_amount": delivery_boy_earning_amount,
            "delivery_boy_payout_status": "PENDING_DELIVERY",
            "delivery_boy_payout_paid_at": None,
            "delivery_boy_payout_marked_by": None,
            "delivery_boy_payout_note": "",
            "delivery_payout_model": (
                DELIVERY_PAYOUT_MODEL_MONTHLY_V1
                if in_house_delivery_enabled_at_order
                else DELIVERY_PAYOUT_MODEL_NOT_REQUIRED
            ),
            "delivery_monthly_period": "",
            "delivery_monthly_settlement_status": (
                DELIVERY_MONTHLY_STATUS_PENDING_DELIVERY
                if in_house_delivery_enabled_at_order
                else "NOT_REQUIRED"
            ),
            "delivery_monthly_earning_amount": delivery_boy_earning_amount,
            "delivery_monthly_settlement_id": "",
            "delivery_monthly_paid_at": None,

            # No COD has been collected at order creation.
            # The delivered-status workflow writes the actual collected amount.
            "cod_collected_amount": 0.0,
            "expected_rider_cash_to_submit": expected_rider_cash_to_submit,
            "rider_cash_to_submit": rider_cash_to_submit,
            "rider_cash_settlement_status": rider_cash_settlement_status,
            "rider_cash_received_at": None,
            "rider_cash_received_by": None,
            "rider_cash_settlement_note": "",

            "order_settlement_status": initial_order_settlement_status,
            "store_settlement_status": initial_store_settlement_status,
            "settlement_audit_logs": [],

            "distance_km": float(km) if km is not None else None,
            "delivery_zone_matched": True,
            "delivery_serviceability_reason": serviceability.get("reason"),
            "delivery_serviceability_message": serviceability.get("message"),
            "delivery_routing_reason": serviceability.get("routing_reason") or serviceability.get("reason"),

            "store_latitude": store.get("latitude"),
            "store_longitude": store.get("longitude"),
            "store_online_at_order": int(store.get("is_online", store.get("is_open", 1)) or 0),
            "delivery_enabled_at_order": int(store.get("delivery_enabled", 1 if store.get("delivery_available", False) else 0) or 0),

            "tip_amount": tip_amount_final,
            "total_payable": float(total_payable),

    # Final checkout delivery location used for fee calculation.
            "delivery_latitude": final_lat,
            "delivery_longitude": final_lng,
            "delivery_location_source": location_source,

    # Session/global detected location info, if available.
            "delivery_location_address": session.get("location_address"),
            "delivery_location_pincode": session.get("location_pincode"),
            "delivery_location_city": session.get("location_city"),
            "delivery_location_state": session.get("location_state"),

            "created_at": now
        })

        oid = order_result.inserted_id

        if active_delivery_mode == DELIVERY_MODE_EXTERNAL_LOCAL:
            local_reference = f"NEF-{str(oid)[-8:].upper()}"
            mongo.orders.update_one(
                {"_id": oid},
                {
                    "$set": {
                        "external_order_id": local_reference,
                        "external_delivery_tracking_code": local_reference,
                        "customer_delivery_reference": local_reference,
                        "external_delivery_booking_status": "ORDER_REFERENCE_ONLY",
                        "external_delivery_status": "ORDER_PLACED_EXTERNAL_LOCAL",
                    },
                    "$push": {
                        "external_status_history": {
                            "status": "ORDER_REFERENCE_ONLY",
                            "note": "External local delivery uses the NE FRESH order reference only. No Rapido/Ola/Uber API booking record is created.",
                            "at": now,
                            "payload": {},
                        }
                    }
                }
            )

        stock_reserved, stock_message = _reserve_order_stock_items(order_items_docs)

        if not stock_reserved:
            mongo.orders.delete_one({"_id": oid})
            mongo.order_events.delete_many({"order_id": oid})
            flash(stock_message or "Requested stock is no longer available. Please update your cart.", "danger")
            return redirect(url_for("cart_page"))

        if not is_online_order:
            public_order_number = _next_public_order_number()
            mongo.orders.update_one(
                {"_id": oid},
                {"$set": {"order_number": public_order_number}}
            )

        for order_item in order_items_docs:
            order_item["order_id"] = oid
            mongo.order_items.insert_one(order_item)

        mongo.transactions.insert_one({
            "order_id": oid,
            "amount": float(total_payable),
            "items_subtotal": items_subtotal_amount,
            "delivery_fee": delivery_fee_amount_final,
            "delivery_fee_source": delivery_fee_source,
            "delivery_fee_slab": delivery_fee_slab,
            "delivery_base_fee": delivery_base_fee,
            "platform_fee": platform_fee_amount,
            "tip_amount": tip_amount_final,

            "payment_method": payment_method,
            "payment_flow": platform_payment_flow,
            "official_payment_mode": platform_payment_flow,

            "delivery_type": delivery_type,
            "active_delivery_mode": active_delivery_mode,
            "in_house_delivery_enabled_at_order": bool(in_house_delivery_enabled_at_order),
            "external_delivery_enabled_at_order": bool(external_delivery_enabled_at_order),
            "external_payment_rule": external_payment_rule,
            "delivery_payment_methods": delivery_payment_methods,
            "allow_online_payment": bool(allow_online_payment_for_mode),
            "allow_cod_payment": bool(allow_cod_payment_for_mode),
            "cod_collection_method": cod_collection_method,
            "external_delivery_provider_type": external_delivery_provider_type,
            "external_delivery_fee_amount": external_delivery_fee_amount,

            "payment_received_by": None,
            "payment_collection_status": payment_collection_status,

            "store_earning": store_earning_amount,
            "delivery_boy_earning": delivery_boy_earning_amount,
            "admin_platform_earning": admin_platform_earning_amount,

            "store_payout_amount": store_earning_amount,
            "store_payout_status": "PENDING_AFTER_DELIVERY",

            "delivery_boy_payout_amount": delivery_boy_earning_amount,
            "delivery_boy_payout_status": "PENDING_DELIVERY",
            "delivery_payout_model": (
                DELIVERY_PAYOUT_MODEL_MONTHLY_V1
                if in_house_delivery_enabled_at_order
                else DELIVERY_PAYOUT_MODEL_NOT_REQUIRED
            ),
            "delivery_monthly_period": "",
            "delivery_monthly_settlement_status": (
                DELIVERY_MONTHLY_STATUS_PENDING_DELIVERY
                if in_house_delivery_enabled_at_order
                else "NOT_REQUIRED"
            ),
            "delivery_monthly_earning_amount": delivery_boy_earning_amount,
            "delivery_monthly_settlement_id": "",
            "delivery_monthly_paid_at": None,

            "expected_rider_cash_to_submit": expected_rider_cash_to_submit,
            "rider_cash_to_submit": rider_cash_to_submit,
            "rider_cash_settlement_status": rider_cash_settlement_status,

            "platform_fee_status": platform_fee_status,

            "status": initial_payment_status,
            "settlement_status": "PENDING",
            "order_settlement_status": "NOT_STARTED",
            "admin_platform_fee_status": money_breakdown.get("admin_platform_fee_status") or "DUE",
            "created_at": now
        })

        mongo.order_addresses.insert_one({
            "order_id": oid,
            "line1": sel.get("line1"),
            "line2": sel.get("line2"),
            "city": sel.get("city"),
            "state": sel.get("state"),
            "pincode": sel.get("pincode"),

            # Final coordinates actually used at checkout.
            "latitude": final_lat,
            "longitude": final_lng,
            "location_source": location_source,

            # Original saved-address coordinates for reference.
            "saved_address_latitude": saved_lat,
            "saved_address_longitude": saved_lng,

            "created_at": now
        })

        mongo.order_events.insert_one({
            "order_id": oid,
            "status": "PENDING_PAYMENT" if is_online_order else "PLACED",
            "note": (
                f"{'Online payment attempt prepared' if is_online_order else 'Order placed'}. "
                f"Items: ₹{float(money_breakdown.get('items_subtotal') or items_total):.2f}, "
                f"Delivery: ₹{float(money_breakdown.get('delivery_fee') or delivery_fee):.2f}, "
                f"Platform fee: ₹{float(money_breakdown.get('platform_fee') or 0):.2f}, "
                f"Tip: ₹{float(money_breakdown.get('tip_amount') or tip_amount):.2f}, "
                f"Total: ₹{float(total_payable):.2f}."
            ),
            "created_at": now
        })

        # ------------------------------------------------------------
        # ONLINE PAYMENT FIX:
        # Online payment is NOT a real order until Razorpay success.
        #
        # We temporarily created the order above only to reuse the
        # existing checkout calculation/snapshot logic.
        # Now move that snapshot into payment_attempts and remove it
        # from real order collections.
        #
        # Result:
        # - No unpaid online order appears in My Orders.
        # - No unpaid online order appears in Store Orders.
        # - Stock is restored until payment succeeds.
        # - Cart is NOT cleared until payment succeeds.
        # ------------------------------------------------------------
        if is_online_order:
            order_doc_snapshot = mongo.orders.find_one({"_id": oid}) or {}
            order_items_snapshot = list(mongo.order_items.find({"order_id": oid}))
            transactions_snapshot = list(mongo.transactions.find({"order_id": oid}))
            order_address_snapshot = mongo.order_addresses.find_one({"order_id": oid}) or {}

            payment_attempt_doc = {
                "_id": oid,
                "attempt_id": str(oid),
                "user_id": u["id"],
                "customer_name": u.get("name"),
                "customer_phone": u.get("phone"),
                "customer_email": u.get("email") or "",
                "cart_id": cid,

                "status": "PENDING_PAYMENT",
                "payment_method": "ONLINE",
                "payment_gateway": "RAZORPAY",
                "payment_status": "PENDING_PAYMENT",
                "payment_collection_status": "ONLINE_PENDING",

                "order_doc": order_doc_snapshot,
                "order_items": order_items_snapshot,
                "transactions": transactions_snapshot,
                "order_address": order_address_snapshot,

                "total_payable": float(total_payable),
                "amount": float(total_payable),
                "currency": "INR",

                "created_at": now,
                "updated_at": now,
                "expires_at": (datetime.utcnow() + timedelta(hours=24)).isoformat()
            }

            mongo.payment_attempts.replace_one(
                {"_id": oid},
                payment_attempt_doc,
                upsert=True
            )

            # Restore stock because this is not a real order yet.
            _release_order_stock_items(order_items_snapshot)

            # Remove temporary real-order records.
            mongo.orders.delete_one({"_id": oid})
            mongo.order_items.delete_many({"order_id": oid})
            mongo.transactions.delete_many({"order_id": oid})
            mongo.order_addresses.delete_many({"order_id": oid})
            mongo.order_events.delete_many({"order_id": oid})

            flash("Please complete online payment to place your order.", "success")
            return redirect(url_for("order_payment", oid=str(oid)))

        mongo.cart_items.delete_many({"cart_id": cid})

        flash(flash_message, "success")

        if is_online_order:
            return redirect(url_for("order_payment", oid=str(oid)))

        return redirect(url_for("orders"))

    total = sum([
        float(it.get("line_total") or 0)
        for it in items
    ])

    return render_template(
        "checkout.html",
        user=u,
        addresses=addresses,
        items=items,
        total=total,
        base_fee=BASE_DELIVERY_FEE_INR,
        slabs=DELIVERY_SURCHARGE_SLABS,
        max_km=None,
        delivery_mode="STORE_POLYGON_ZONE",
        delivery_message="Delivery availability depends on the selected store delivery zone. Final fee is calculated after serviceability check.",
        store_lat=store_lat,
        store_lng=store_lng,
        cart_store_count=cart_store_count,
        online_payment_enabled=online_payment_enabled,
        payment_gateway_settings=payment_settings,
    )


@app.route("/api/checkout/serviceability", methods=["POST"])
@login_required()
def api_checkout_serviceability():
    u = current_user()
    cid = get_or_create_cart(u["id"])

    data = request.get_json(silent=True) or {}

    customer_lat = data.get("lat")
    customer_lng = data.get("lng")
    customer_pincode = (data.get("pincode") or "").strip()

    cart_items = list(mongo.cart_items.find({"cart_id": cid}))

    if not cart_items:
        return jsonify({
            "ok": False,
            "serviceable": False,
            "reason": "EMPTY_CART",
            "message": "Your cart is empty."
        }), 400

    store_ids = []
    items_total = 0.0
    quote_items = []

    for ci in cart_items:
        item = _checkout_hydrate_cart_item(ci)

        if not item or item.get("invalid_bundle"):
            continue

        store_id = item.get("store_id")

        if store_id:
            store_ids.append(str(store_id))

        quantity = item.get("quantity") or item.get("cart_quantity") or 1
        price_per_unit = float(item.get("price_per_unit") or 0)

        items_total += float(quantity or 0) * float(price_per_unit or 0)

        quote_items.append({
            "item_type": item.get("item_type") or "product",
            "is_bundle": bool(item.get("is_bundle")),
            "product_id": item.get("product_id"),
            "bundle_id": item.get("bundle_id"),
            "quantity": quantity,
            "cart_quantity": quantity,
            "shipping_weight_kg": item.get("shipping_weight_kg"),
            "shipping_length_cm": item.get("shipping_length_cm"),
            "shipping_breadth_cm": item.get("shipping_breadth_cm"),
            "shipping_height_cm": item.get("shipping_height_cm"),
        })

    unique_store_ids = sorted(set(store_ids))

    if not unique_store_ids:
        return jsonify({
            "ok": False,
            "serviceable": False,
            "reason": "STORE_MISSING",
            "message": "Store information is missing from cart items."
        }), 400

    if len(unique_store_ids) > 1:
        return jsonify({
            "ok": False,
            "serviceable": False,
            "reason": "MULTI_STORE_CART",
            "message": "Your cart contains items from multiple stores. Please order from one store at a time."
        }), 400

    store_id_raw = unique_store_ids[0]

    try:
        store_id = ObjectId(store_id_raw)
    except Exception:
        store_id = store_id_raw

    store = mongo.stores.find_one({
        "$or": [
            {"_id": store_id},
            {"_id": store_id_raw}
        ]
    })

    if not store:
        return jsonify({
            "ok": False,
            "serviceable": False,
            "reason": "STORE_NOT_FOUND",
            "message": "Store not found."
        }), 404

    serviceability = check_checkout_delivery_quote(
        store=store,
        customer_lat=customer_lat,
        customer_lng=customer_lng,
        customer_pincode=customer_pincode,
        items_total=items_total,
        payment_method=(data.get("payment_method") or ""),
        cart_items=quote_items
    )

    delivery_fee = float(serviceability.get("delivery_fee") or 0)
    platform_result = calculate_platform_fee(items_total)
    platform_settings = platform_result.get("platform_fee_settings") or {}
    platform_fee = float(platform_result.get("platform_fee") or 0)

    total_payable = round(
        float(items_total or 0)
        + float(delivery_fee or 0)
        + float(platform_fee or 0),
        2
    )

    return jsonify({
        "ok": True,
        "serviceable": bool(serviceability.get("serviceable")),
        "reason": serviceability.get("reason"),
        "message": serviceability.get("message"),
        "distance_km": serviceability.get("distance_km"),

        "items_total": round(float(items_total or 0), 2),
        "delivery_fee": round(float(delivery_fee or 0), 2),

        "delivery_fee_source": serviceability.get("delivery_fee_source") or "unknown",
        "delivery_fee_slab": serviceability.get("delivery_fee_slab"),
        "delivery_base_fee": float(serviceability.get("delivery_base_fee") or 0),
        "delivery_fee_details": serviceability.get("delivery_fee_details") or {},
        "free_delivery_above_applied": serviceability.get("delivery_fee_source") == "admin_free_delivery_above",
        "free_delivery_above": float((serviceability.get("delivery_fee_details") or {}).get("free_delivery_above") or 0),
        "original_delivery_fee": float((serviceability.get("delivery_fee_details") or {}).get("original_delivery_fee") or serviceability.get("delivery_fee") or 0),
        "free_delivery_savings": float((serviceability.get("delivery_fee_details") or {}).get("free_delivery_savings") or 0),

        "platform_fee": round(float(platform_fee or 0), 2),
        "platform_fee_label": platform_settings.get("display_name") or "Platform Fee",
        "platform_fee_description": platform_settings.get("description") or "",
        "platform_fee_source": platform_result.get("platform_fee_source") or "disabled",
        "total_payable": total_payable,

        "active_delivery_mode": serviceability.get("active_delivery_mode") or DELIVERY_MODE_IN_HOUSE,
        "delivery_type": serviceability.get("delivery_type") or "OWN_DELIVERY",
        "external_delivery_enabled": bool(serviceability.get("external_delivery_enabled", False)),
        "external_delivery_provider": serviceability.get("external_delivery_provider") or "IN_HOUSE",
        "external_delivery_provider_type": serviceability.get("external_delivery_provider_type") or "IN_HOUSE",
        "external_payment_rule": serviceability.get("external_payment_rule") or EXTERNAL_PAYMENT_RULE_COD_STORE,
        "cod_allowed": bool(serviceability.get("cod_allowed", True)),
        "online_allowed": bool(serviceability.get("online_allowed", True)),
        "eta_minutes": serviceability.get("eta_minutes"),
        "external_delivery_quote": serviceability.get("external_delivery_quote") or {},

        "store": {
            "id": str(store.get("_id")),
            "store_name": store.get("store_name", ""),
            "is_online": int(store.get("is_online", store.get("is_open", 1)) or 0),
            "delivery_enabled": int(store.get("delivery_enabled", 1 if store.get("delivery_available", False) else 0) or 0),
            "delivery_zone_configured": 1 if len(store.get("delivery_zone_polygon") or []) >= 3 else int(store.get("delivery_zone_configured", 0) or 0)
        }
    })
