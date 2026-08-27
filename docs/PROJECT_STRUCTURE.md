# NE FRESH Project Structure

This document explains why the repository now contains more files even though the website did not gain dozens of new pages. The refactor split large Python files into smaller backend responsibilities while preserving the same route/form contracts.

## Application entry points

| File | Purpose |
|---|---|
| `app.py` | Local development entry point. |
| `wsgi.py` | Production Gunicorn/WSGI entry point. |
| `app_factory.py` | Creates/configures the Flask application and registers route registries. |
| `development.py` | Local auto-reload/development-server helpers only. |

## Infrastructure

| File | Purpose |
|---|---|
| `config.py` | Environment/session/production configuration. |
| `security.py` | CSRF, CORS, ProxyFix and security response hooks. |
| `extensions.py` | Shared Mongo resource compatibility layer. |
| `mongo_db.py` | Mongo client/index definitions still used by the compatibility architecture. |
| `database_init.py` | Explicit one-time database/index/admin initialization. |
| `uploads.py` | Upload path/type configuration. |
| `logging_config.py` | Shared logging setup/compatibility. |
| `template_context.py` | Global Jinja context processors. |

## Compatibility core

`app_core.py` is **not the final desired architecture**. It began as a ~9,156-line monolith and has already been substantially reduced. It now remains because several route/shared modules still depend on legacy wildcard imports and shared helpers. Removing it safely requires another dedicated refactor after staging or as a separate pre-production architecture phase.

## Routes

`routes/` contains Python endpoint handlers, not extra UI pages. Route files were split by role/domain so Admin, Store, Orders and Delivery code are no longer stored in a few enormous files.

## Services

`services/` contains business logic such as product pricing, inventory, order lifecycle, delivery, payments, payouts, refunds and reconciliation. These files were extracted from `app_core.py`; they do not represent new screens.

## Helpers

`helpers/` contains small reusable formatting/ID/number helpers.

## UI

- `templates/` contains the Jinja HTML pages/components.
- `static/` contains CSS, JavaScript and other frontend assets.

## Tests

`tests/` is intentionally kept in the repository. It is not production application code, but it protects route contracts, forms, finance, inventory, payments, refunds, delivery, WSGI startup and deployment behavior.

## Deployment

`deploy/` and the deployment-oriented scripts are required for the AWS EC2 workflow: Gunicorn, systemd, Nginx, environment validation, health checks, deploy/rollback and provisioning.

## Historical documentation

All `STEP_*` refactor/audit documents are stored under `docs/history/` so the project root stays readable while preserving the development history.

## Uploads

The existing files under `uploads/` were intentionally **not deleted** during repository cleanup. Source-code searches do not reference them directly, but existing MongoDB documents may store their filenames. Production deployment places uploads in persistent shared storage. Do not delete legacy upload files until Mongo references are checked.
