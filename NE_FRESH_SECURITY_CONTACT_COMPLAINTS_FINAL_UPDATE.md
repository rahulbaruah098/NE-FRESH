# NE FRESH Security + Contact Toggle + Admin Complaints Final Update

Implemented updates:

- Removed hardcoded Flask debug=True from app.py and app_core.py. Debug now requires FLASK_DEBUG=1.
- Removed hardcoded app secret fallback. APP_SECRET_KEY/SECRET_KEY is now required for stable production sessions; missing env uses a temporary runtime key only.
- Removed hardcoded admin@chhimphei.local / admin123 seeding. Optional admin seed now requires ADMIN_SEED_EMAIL and ADMIN_SEED_PASSWORD.
- Added CSRF protection for same-origin HTML form POSTs, with automatic form token injection in base/admin/store/delivery layouts. API routes remain exempt for mobile/API compatibility.
- Converted requirements.txt from UTF-16 to UTF-8.
- External delivery webhook token is now mandatory before live Shiprocket/hyperlocal provider mode can be enabled, and webhook endpoint rejects requests if provider token is not configured.
- Disabled duplicate legacy /complaints POST route; current complaint flow remains handled by customer_complaints.
- Restored visible Contact Messages auto acknowledgement ON/OFF switch while keeping automatic message editing removed.
- Redesigned Admin Complaints page into a responsive card-based admin UI with search, filters, pagination and existing update/takeover forms preserved.

Not changed as requested:

- .env file presence in ZIP.
- Live external partner credential/API mapping.
