# NE FRESH Delivery Mode Payment UI Cleanup

This update cleans the Admin Delivery Mode Settings page and removes the confusing provider-selection section from that page.

## Updated behavior

### Delivery Mode Settings now controls only:
1. Active delivery system
   - In-house Delivery
   - External Local Delivery
   - Third-party Shipping

2. Payment methods
   - Online Payment
   - Cash on Delivery
   - Both Online + COD can be active together

3. COD collection method
   - In-house mode automatically uses NE FRESH delivery boy collection
   - External modes can use Store collection or External Partner collection

4. Return / Refund
   - Admin can turn return/refund on or off from the same page

## Removed from Delivery Mode Settings
Provider selection was removed from this page to avoid confusion. Provider setup belongs to:

Admin -> External Provider Settings

That page is for Shiprocket, Rapido, manual courier, manual hyperlocal, and future API credentials/settings.

## Compatibility
The old `external_payment_rule` field is still saved internally for existing external-delivery code compatibility.
New clearer fields are also saved:

- `delivery_payment_methods`
- `allow_online_payment`
- `allow_cod_payment`
- `cod_collection_method`

No unrelated modules or existing in-house delivery logic were intentionally changed.
