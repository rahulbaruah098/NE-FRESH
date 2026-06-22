# NE FRESH Admin User Buttons Functional Fix

## Purpose
The Admin menu contained two user-management buttons that were unclear and one of them did not have a working data page:

- Store Login Users
- In-house Delivery Boy Users

This update makes both buttons useful, clear, and functional.

## What changed

### 1. Store Login Users renamed and made functional
Renamed to:

- Store Login Accounts

Added the missing template:

- `templates/admin_store_users.html`

This page now shows:

- Store login owner name
- Store account email and phone
- Linked store profile status
- Store address
- Total products
- Active products
- Total orders
- Delivered orders
- Delivered order value
- Store rating count and average
- Login account status
- Store profile status
- CSV export
- ZIP export
- Enable/disable login
- Safe delete action

### 2. In-house Delivery Boy Users renamed and clarified
Renamed to:

- In-house Delivery Boy Accounts

Updated:

- `templates/admin_delivery_users.html`

The page now explains that it manages in-house delivery-boy login accounts, not external Rapido/Ola/Uber riders.

### 3. In-house delivery-boy account page remains accessible
Previously, the Admin guard treated `admin_delivery_users` as an operational in-house delivery page. That made the account page unavailable when in-house delivery was disabled.

Now the account page remains accessible for Admin account management even when in-house delivery mode is OFF. Operational pages like delivery overview, assignment, history and rider cash collection still remain protected by the in-house delivery switch.

### 4. Admin menu wording improved
Updated:

- `templates/admin_base.html`

The menu now uses clearer wording:

- Store Login Accounts
- In-house Delivery Boy Accounts

### 5. Users Overview wording improved
Updated:

- `templates/admin_users_overview.html`

The overview now uses clearer labels for store login accounts and in-house delivery-boy accounts.

## Logic preserved
No delivery calculation, payment calculation, order placement, store operations, customer tracking, or settlement logic was changed.

