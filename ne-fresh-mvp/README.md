
# NE-FRESH by Sayanant Group — MVP

Flask + SQLite e-commerce MVP for fresh meat, **hard-restricted to PIN 796009**. Roles: **ADMIN, SELLER, CUSTOMER, DELIVERY**.
Only **ADMIN** can onboard **SELLER** and **DELIVERY**. Includes storytelling landing, cart, checkout with mock payments, and seed data.

## Quick Start
```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate
pip install -r requirements.txt

# Copy .env
cp .env.example .env  # or create manually on Windows

# Run
python wsgi.py
# Open http://127.0.0.1:5000
```
Admin login: `admin@nefresh.local / Admin@123`

## Notes
- Support: **ites@sdsindia.in**
- Delivery routes gated by `DELIVERY_ENABLED=1` in `.env`.
- PIN code validation enforced on address and checkout (`796009`).
