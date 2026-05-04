from flask import Flask, jsonify
from flask_wtf.csrf import CSRFError, generate_csrf
from app.config import Config
from app.extensions import init_extensions, db
from app.seed import run_seed

def create_app():
    app = Flask(__name__, static_folder="app/static", template_folder="app/templates")
    app.config.from_object(Config)
    init_extensions(app)

    # Secure cookies
    if app.config.get("SECURE_COOKIES"):
        app.config.update(
            SESSION_COOKIE_HTTPONLY=True,
            SESSION_COOKIE_SAMESITE="Lax",
        )

    # Make {{ csrf_token() }} available in all templates
    @app.context_processor
    def inject_csrf_token():
        return dict(csrf_token=generate_csrf)

    # Ensure a CSRF cookie is set on every response (helps manual-form flows)
    @app.after_request
    def set_csrf_cookie(response):
        try:
            # Don't HttpOnly this one; Flask-WTF reads it server-side, but some UIs use it for headers too
            response.set_cookie(
                "csrf_token",
                generate_csrf(),
                samesite="Lax",
                secure=False,   # set True when you move to HTTPS
                httponly=False
            )
        except Exception:
            pass
        return response

    # Blueprints
    from app.blueprints import public, auth, customer, seller, delivery, admin, api
    app.register_blueprint(public.bp)
    app.register_blueprint(auth.bp, url_prefix="")
    app.register_blueprint(customer.bp)
    app.register_blueprint(seller.bp)
    app.register_blueprint(delivery.bp)
    app.register_blueprint(admin.bp)
    app.register_blueprint(api.bp, url_prefix="/api")

    # Error handlers
    @app.errorhandler(CSRFError)
    def handle_csrf_error(e):
        # If you prefer an HTML page, render a template here instead of JSON
        return (jsonify(error="CSRF Failed", reason=e.description), 400)

    @app.errorhandler(403)
    def forbidden(e): return (jsonify(error="Forbidden"), 403)

    @app.errorhandler(404)
    def not_found(e): return (jsonify(error="Not found"), 404)

    @app.errorhandler(422)
    def unproc(e): return (jsonify(error=str(e)), 422)

    @app.errorhandler(429)
    def ratelimited(e): return (jsonify(error="Too Many Requests"), 429)

    @app.get("/health")
    def health(): return {"status": "ok"}

    return app

app = create_app()

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        run_seed(app)
    app.run(host="0.0.0.0", port=5000, debug=True)
