"""Production WSGI entry point for Gunicorn/systemd."""
from app_factory import create_app

app = create_app()
application = app

__all__ = ["app", "application"]
