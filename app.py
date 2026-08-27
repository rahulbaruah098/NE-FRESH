"""NE FRESH local-development entry point.

Production servers should load ``wsgi:app``.  Keeping ``app`` exported here
preserves the existing ``from app import app`` development/test contract.
"""
from app_factory import create_app

app = create_app()


if __name__ == "__main__":
    from development import run_development_server

    run_development_server(app)


__all__ = ["app", "create_app"]
