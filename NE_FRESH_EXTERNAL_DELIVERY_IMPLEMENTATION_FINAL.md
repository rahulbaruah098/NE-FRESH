# NE FRESH External Delivery Implementation Final

Implemented without removing existing in-house delivery logic.

## Included
- Unified checkout quote for IN_HOUSE, EXTERNAL_LOCAL_DELIVERY and THIRD_PARTY_SHIPPING.
- External delivery quote fallback settings for Hyperlocal/Rapido/Zomato type and Shiprocket/courier type.
- Checkout delivery fee now comes from the active delivery mode. Product subtotal remains unchanged.
- COD/Online availability is returned by backend and reflected in checkout UI.
- External order snapshots store delivery mode, provider, quote source/status/message, delivery charge and COD amount.
- External booking dashboards and booking routes remain compatible with existing external delivery screens.

## Payment rules
- IN_HOUSE: COD and Online allowed.
- EXTERNAL + ONLINE_ONLY: COD disabled, online required.
- EXTERNAL + COD_STORE_COLLECTION: COD allowed and store/admin settlement fields are saved.
- EXTERNAL + COD_PARTNER_COLLECTION: COD amount is saved for partner remittance tracking.

## Important
Live Shiprocket/Rapido/Zomato APIs still require real account credentials and provider-specific API URLs. Until credentials are configured, checkout uses safe manual fallback delivery quotes configured by Admin.
