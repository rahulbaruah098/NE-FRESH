"""Application entry point for the refactored NE FRESH Flask app.

The original single app.py has been split into route-wise folders.
All route decorators still register on the same Flask app from app_core.py,
so existing url_for(...) endpoint names, templates, sessions, MongoDB usage and logic remain unchanged.
"""

import os
import time
from pathlib import Path

from flask import jsonify, request

from app_core import app

# Import route modules so their @app.route decorators register on the same Flask app.
import routes.public.routes  # noqa: F401
import routes.location.routes  # noqa: F401
import routes.auth.routes  # noqa: F401
import routes.customer.routes  # noqa: F401
import routes.products.routes  # noqa: F401
import routes.cart.routes  # noqa: F401
import routes.orders.routes  # noqa: F401
import routes.admin.routes  # noqa: F401
import routes.store.routes  # noqa: F401
import routes.delivery.routes  # noqa: F401
import routes.external_delivery.routes  # noqa: F401
import routes.api.routes  # noqa: F401


BASE_DIR = Path(__file__).resolve().parent

WATCH_DIRS = ["routes", "templates", "static"]
WATCH_FILES = ["app.py", "app_core.py"]
WATCH_EXTENSIONS = {".py", ".html", ".jinja", ".jinja2", ".css", ".js", ".json"}
IGNORE_DIRS = {
    "__pycache__",
    ".git",
    ".idea",
    ".vscode",
    "venv",
    "env",
    ".venv",
    "node_modules",
    "dist",
    "build",
    ".pytest_cache",
}


def _env_flag(name, default=False):
    """Read a boolean environment flag."""
    value = os.getenv(name)
    if value is None:
        return default

    return value.strip().lower() in {"1", "true", "yes", "on", "y"}


def _collect_watch_files():
    """Collect route, template, css and js files for auto reload."""
    files_to_watch = set()

    for filename in WATCH_FILES:
        path = BASE_DIR / filename
        if path.exists():
            files_to_watch.add(str(path))

    for watch_dir in WATCH_DIRS:
        root_dir = BASE_DIR / watch_dir
        if not root_dir.exists():
            continue

        for root, dirs, files in os.walk(root_dir):
            dirs[:] = [
                d for d in dirs
                if d not in IGNORE_DIRS and not d.startswith(".")
            ]

            for filename in files:
                path = Path(root) / filename
                if path.suffix.lower() in WATCH_EXTENSIONS:
                    files_to_watch.add(str(path))

    return sorted(files_to_watch)


def _apply_dev_reload_config(enabled=True):
    """Force templates/static changes to reflect immediately in development."""
    app.debug = enabled
    app.config["DEBUG"] = enabled
    app.config["TEMPLATES_AUTO_RELOAD"] = enabled
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0 if enabled else 31536000
    app.config["EXPLAIN_TEMPLATE_LOADING"] = False

    # This is the important part for HTML/Jinja template edits.
    app.jinja_env.auto_reload = enabled

    if enabled:
        # Disable Jinja in-memory cache so template changes are read on every request.
        app.jinja_env.cache = None


@app.after_request
def _disable_cache_in_dev(response):
    """Prevent browser cache from hiding edited HTML/CSS/JS during development."""
    if _env_flag("NEFRESH_AUTO_RELOAD", False):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


def _enable_browser_live_reload():
    """
    Refresh already-open browser tabs when route/template/static files change.

    Flask's server reloader restarts the Python process after file changes.
    This helper also tells the browser tab to refresh automatically after the
    server comes back up or after static/template mtimes change.
    """
    version_cache = {"checked_at": 0.0, "version": "0"}

    def current_reload_version():
        now = time.time()

        # Avoid scanning the whole project on every browser poll.
        if now - version_cache["checked_at"] < 0.6:
            return version_cache["version"]

        latest_mtime_ns = 0
        file_count = 0

        for file_path in _collect_watch_files():
            try:
                stat = os.stat(file_path)
            except OSError:
                continue

            file_count += 1
            latest_mtime_ns = max(
                latest_mtime_ns,
                getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000)),
            )

        version_cache["checked_at"] = now
        version_cache["version"] = f"{latest_mtime_ns}:{file_count}"
        return version_cache["version"]

    def live_reload_version():
        return jsonify({"version": current_reload_version()})

    if "__nefresh_dev_reload_version" not in app.view_functions:
        app.add_url_rule(
            "/__nefresh_dev_reload_version",
            "__nefresh_dev_reload_version",
            live_reload_version,
            methods=["GET"],
        )

    live_reload_script = """
<script id="nefresh-live-reload">
(function () {
  if (window.__NEFRESH_LIVE_RELOAD_ACTIVE__) return;
  window.__NEFRESH_LIVE_RELOAD_ACTIVE__ = true;

  var lastVersion = null;
  var checking = false;
  var serverWasDown = false;

  function checkForChanges() {
    if (checking) return;
    checking = true;

    fetch("/__nefresh_dev_reload_version?_=" + Date.now(), {
      cache: "no-store",
      headers: { "X-Requested-With": "XMLHttpRequest" }
    })
      .then(function (response) {
        if (!response.ok) throw new Error("reload-check-failed");
        return response.json();
      })
      .then(function (data) {
        var currentVersion = data && data.version ? String(data.version) : "";

        if (!lastVersion) {
          lastVersion = currentVersion;
          serverWasDown = false;
          return;
        }

        if (serverWasDown || (currentVersion && currentVersion !== lastVersion)) {
          window.location.reload();
          return;
        }

        serverWasDown = false;
      })
      .catch(function () {
        serverWasDown = true;
      })
      .finally(function () {
        checking = false;
      });
  }

  window.setInterval(checkForChanges, 900);
  window.setTimeout(checkForChanges, 450);
})();
</script>
""".strip()

    @app.after_request
    def _inject_live_reload_script(response):
        content_type = response.headers.get("Content-Type", "")

        if (
            request.method == "GET"
            and not request.path.startswith("/__nefresh_dev_reload_version")
            and "text/html" in content_type
            and not response.is_streamed
        ):
            try:
                html = response.get_data(as_text=True)
                if "</body>" in html and "nefresh-live-reload" not in html:
                    html = html.replace("</body>", live_reload_script + "\n</body>", 1)
                    response.set_data(html)
                    response.headers["Content-Length"] = str(len(response.get_data()))
            except Exception:
                pass

        return response


if os.getenv("NEFRESH_DEBUG_LOGS", "0").strip().lower() in ["1", "true", "yes", "on"]:
    print("\n=== ROUTES LOADED FROM app.py ===")
    print(app.url_map)
    print("=================================\n")


if __name__ == "__main__":
    # Auto reload is OFF by default. Enable it explicitly for local development.
    # Production therefore stays cache-safe without extra flags.
    # For local development enable with:
    #   set NEFRESH_AUTO_RELOAD=1
    #   set FLASK_DEBUG=1
    auto_reload_enabled = _env_flag("NEFRESH_AUTO_RELOAD", False)
    debug_enabled = _env_flag("FLASK_DEBUG", auto_reload_enabled)

    _apply_dev_reload_config(auto_reload_enabled)

    extra_files = _collect_watch_files() if auto_reload_enabled else None

    if auto_reload_enabled:
        _enable_browser_live_reload()
        print("\n=== NE FRESH DEV AUTO RELOAD ENABLED ===")
        print(f"Watching {len(extra_files or [])} files inside: routes, templates, static")
        print("Template cache disabled: HTML/Jinja edits should reflect immediately.")
        print("Open browser tabs will auto-refresh after detected changes.")
        print("Enabled with: NEFRESH_AUTO_RELOAD=1 FLASK_DEBUG=1 python app.py")
        print("========================================\n")

    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "5000")),
        debug=debug_enabled,
        use_reloader=auto_reload_enabled,
        reloader_type=os.getenv("FLASK_RELOADER_TYPE", "auto"),
        extra_files=extra_files,
    )
