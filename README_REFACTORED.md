# NE FRESH Refactored Routes - Updated Version

This package was generated from the latest uploaded `app.py` text file.

## What changed

- The large single `app.py` was split into separate route folders.
- Existing `@app.route(...)` decorators were kept, not converted to Blueprints, so existing endpoint names continue to work.
- Existing route logic, MongoDB calls, sessions, flashes, templates, and URL paths were preserved.
- Shared config/helpers remain in `app_core.py`.
- A full backup of the latest original file is included as `app_original_backup.py`.

## Route split

- routes/public/routes.py: 12 route function(s)
- routes/location/routes.py: 5 route function(s)
- routes/auth/routes.py: 8 route function(s)
- routes/customer/routes.py: 13 route function(s)
- routes/products/routes.py: 9 route function(s)
- routes/cart/routes.py: 5 route function(s)
- routes/orders/routes.py: 8 route function(s)
- routes/admin/routes.py: 33 route function(s)
- routes/store/routes.py: 35 route function(s)
- routes/delivery/routes.py: 6 route function(s)
- routes/api/routes.py: 6 route function(s)

## How to use

Place these files/folders beside your existing project files:

- `mongo_db.py`
- `templates/`
- `static/`
- `uploads/`
- `.env`
- any other current project files

Then run:

```bash
python app.py
```

## Important

Do not delete `app_original_backup.py` until you test all major pages. It is included only for safety/reference.
