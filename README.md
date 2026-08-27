# NE FRESH / NE LOCALS

Production-oriented Flask application for the NE FRESH / NE LOCALS platform.

## Start here

- Local development: `python app.py`
- Production WSGI target: `wsgi:app`
- Application factory: `app_factory.py`
- Production deployment assets: `deploy/`
- Database initialization: `python scripts/init_db.py`
- Full regression suite: `python -m pytest`
- Project structure guide: `docs/PROJECT_STRUCTURE.md`
- Historical refactor/audit notes: `docs/history/`

## Main folders

- `routes/` — HTTP endpoints, split by role/domain
- `services/` — business logic and external-provider logic
- `helpers/` — small shared utilities
- `templates/` — Jinja HTML templates
- `static/` — CSS, JavaScript, images and other frontend assets
- `tests/` — regression and runtime safety net
- `scripts/` — explicit maintenance/deployment commands
- `deploy/` — Gunicorn, Nginx, systemd and EC2 deployment files
- `uploads/` — current local upload data; production uses persistent shared storage

`app_core.py` remains a temporary compatibility bridge. It is intentionally still present because some route modules continue to import legacy shared names from it. It can be reduced further in a later controlled refactor, but its removal is not required for the current EC2 staging deployment.
