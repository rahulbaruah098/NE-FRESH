"""Gunicorn configuration for the NE FRESH EC2 deployment."""
from __future__ import annotations

import os

bind = os.getenv("GUNICORN_BIND", "127.0.0.1:8000")
workers = int(os.getenv("GUNICORN_WORKERS", "2"))
worker_class = "sync"
threads = 1
timeout = int(os.getenv("GUNICORN_TIMEOUT", "120"))
graceful_timeout = int(os.getenv("GUNICORN_GRACEFUL_TIMEOUT", "30"))
keepalive = int(os.getenv("GUNICORN_KEEPALIVE", "5"))
max_requests = int(os.getenv("GUNICORN_MAX_REQUESTS", "1000"))
max_requests_jitter = int(os.getenv("GUNICORN_MAX_REQUESTS_JITTER", "100"))

# Do not preload this app. PyMongo connections and process-local Flask state
# must be initialized independently inside each Gunicorn worker after fork.
preload_app = False

accesslog = "-"
errorlog = "-"
loglevel = os.getenv("GUNICORN_LOG_LEVEL", "info").lower()
capture_output = True
forwarded_allow_ips = os.getenv("GUNICORN_FORWARDED_ALLOW_IPS", "127.0.0.1")

# A bounded header policy avoids pathological requests while remaining well
# above the application's normal usage.
limit_request_line = 8190
limit_request_fields = 100
limit_request_field_size = 8190
