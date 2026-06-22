# NE FRESH Delivery Operation Logic Final Update

## Updated logic

### 1. In-house Delivery is standalone
- Admin can activate In-house Delivery only by selecting **In-house Delivery Only**.
- When this is active, External Local and Shiprocket/Courier channels are forced OFF.
- Existing delivery-boy assignment, tracking, rider collection and settlement logic remains unchanged.

### 2. Connected External Delivery is separate
- Admin can select **Connected External Delivery**.
- When this is active, In-house Delivery is forced OFF.
- External Local Delivery and Shiprocket/Courier Shipping can be enabled together.
- Checkout routes orders only between External Local and Shiprocket.

### 3. External Local Delivery remains simple
- No Rapido/Ola/Uber API booking is stored.
- No external rider payment/remittance ledger is maintained.
- Customer receives the NE FRESH order reference.
- Delivery charge is calculated from Admin hard-coded external local fare rules.

### 4. Shiprocket/Courier is for outside-local/inter-city orders
- Shiprocket/Courier shipping is selected after External Local is not serviceable.
- Shiprocket orders are forced to Online Payment before shipment creation.
- Shiprocket quote/booking uses real credentials when configured.
- If live quote fails, fallback courier charges are used.

### 5. Pay Online on Delivery wording
- Customer-facing wording is now **Pay Online on Delivery**.
- Internal backend value remains `COD` for compatibility with existing orders and settlement pages.
- External Local means customer pays Store/NE FRESH by UPI/online at handover, not cash to Rapido/Ola/Uber rider.

### 6. Product-wise package dimensions
- Store Add Product and Edit Product now include:
  - Shipping Weight per Unit (KG)
  - Package Length (CM)
  - Package Breadth (CM)
  - Package Height (CM)
- Shiprocket quote and booking use product-wise dimensions where available.
- Admin fallback package dimensions are used only when product dimensions are missing.

## Money/platform fee
- Platform fee calculation was not changed.
- Existing formula remains:
  Customer Payable = Items Subtotal + Delivery Fee + Platform Fee + Tip
- Online payments are still collected by platform/Razorpay first.
- In-house delivery settlements remain unchanged.
- External Local delivery avoids rider settlement complexity by not tracking Rapido/Ola/Uber payment inside NE FRESH.
- Shiprocket COD/remittance is avoided by forcing Online Payment for Shiprocket orders.

## Changed files
- app_core.py
- routes/admin/routes.py
- routes/orders/routes.py
- routes/store/routes.py
- services/delivery_integrations/base.py
- templates/admin_delivery_routing_settings.html
- templates/admin_delivery_channel_settings.html
- templates/checkout.html
- templates/order_track.html
- templates/store_orders.html
- templates/store_add_product.html
- templates/store_product_edit.html
