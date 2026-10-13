"""
app.py  –  Flask application factory.

Security posture
----------------
* SECRET_KEY from env; random fallback + loud WARNING (never silently weak).
* flask_wtf.CSRFProtect covers every POST automatically via WTForms tokens
  injected by the `csrf_token()` context processor.
* Cookies: HttpOnly=True, SameSite=Lax, Secure controlled by COOKIE_SECURE env.
* Sessions: permanent=True, 30-minute lifetime.
* after_request injects:
    Content-Security-Policy  default-src 'self'  (no inline scripts/styles)
    X-Content-Type-Options   nosniff
    X-Frame-Options          DENY
    Referrer-Policy          no-referrer
    Cache-Control            no-store
* MAX_CONTENT_LENGTH = 1 MB  (rejects oversized uploads / form data).
* debug=False always (FLASK_DEBUG env var is intentionally ignored here;
  use `flask --debug run` at the CLI if you need it).
* Custom 403 / 404 / 500 pages (no internal details).
* Time Machine route: coordinator-only, date validated by time_utils.

CLI commands (added by database.init_app):
  flask init-db   – create tables
  flask seed-db   – insert demo data
"""

import os
import secrets

from flask import Flask, g, redirect, request, session, url_for, flash, render_template
from flask_wtf import CSRFProtect

from database import init_app as db_init_app
from auth import auth_bp, load_logged_in_user
from student_pages import student_bp
from coordinator_pages import coordinator_bp
from reports_pages import reports_bp
from time_utils import get_today, validate_fake_date


csrf = CSRFProtect()


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------

def create_app():
    app = Flask(__name__, template_folder="templates",
                static_folder="static", static_url_path="/static")

    # ── Secret key ────────────────────────────────────────────────────────
    secret = os.environ.get("SECRET_KEY", "")
    if not secret:
        secret = secrets.token_hex(32)
        import warnings
        warnings.warn(
            "[app.py] SECRET_KEY env var is not set -- using a random key. "
            "Sessions will be lost on every restart. Set SECRET_KEY in .env!",
            stacklevel=1,
        )
    app.config["SECRET_KEY"] = secret

    # ── Database path ────────────────────────────────────────────────────
    app.config["DATABASE"] = os.environ.get(
        "DATABASE",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "inventory.db"),
    )

    # ── Flask-WTF CSRF ───────────────────────────────────────────────────
    # WTF_CSRF_TIME_LIMIT=None → tokens never expire mid-session (30-min
    # session lifetime governs expiry instead).
    app.config["WTF_CSRF_TIME_LIMIT"] = None

    # ── Cookie / session security ────────────────────────────────────────
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    # Set COOKIE_SECURE=1 in .env when running behind HTTPS
    app.config["SESSION_COOKIE_SECURE"]   = os.environ.get("COOKIE_SECURE", "0") == "1"
    # 30-minute idle timeout
    from datetime import timedelta
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(minutes=30)

    # ── Upload / form size limit ─────────────────────────────────────────
    app.config["MAX_CONTENT_LENGTH"] = 1 * 1024 * 1024   # 1 MB

    # ── Jinja autoescape (already on for .html; explicit for safety) ─────
    app.jinja_env.autoescape = True

    # debug is NEVER hard-coded True
    app.debug = False

    # ── Extensions ───────────────────────────────────────────────────────
    csrf.init_app(app)
    db_init_app(app)

    # ── Before-request: load user ────────────────────────────────────────
    app.before_request(load_logged_in_user)

    # ── After-request: security headers ─────────────────────────────────
    @app.after_request
    def set_security_headers(response):
        # No inline scripts or styles – all JS/CSS must be served from /static
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "img-src 'self' data:; "
            "object-src 'none'; "
            "base-uri 'none'; "
            "form-action 'self'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"]        = "DENY"
        response.headers["Referrer-Policy"]        = "no-referrer"
        response.headers["Cache-Control"]          = "no-store"
        return response

    # ── Context processors ───────────────────────────────────────────────
    @app.context_processor
    def inject_globals():
        """Inject 'today' and current user into every template."""
        today = None
        try:
            today = get_today()
        except Exception:
            pass
        return {"today": today}

    # ── Blueprints ───────────────────────────────────────────────────────
    app.register_blueprint(auth_bp)
    app.register_blueprint(student_bp)
    app.register_blueprint(coordinator_bp)
    app.register_blueprint(reports_bp)

    # ── Root redirect ─────────────────────────────────────────────────────
    @app.route("/")
    def index():
        if g.user is None:
            return redirect(url_for("auth.login"))
        if g.user["role"] == "coordinator":
            return redirect(url_for("coordinator.dashboard"))
        return redirect(url_for("student.dashboard"))

    # ── Time Machine ─────────────────────────────────────────────────────
    from auth import coordinator_required

    @app.route("/time-machine", methods=["GET", "POST"])
    @coordinator_required
    def time_machine():
        if request.method == "POST":
            raw = request.form.get("fake_date", "").strip()
            if raw == "":
                # Empty submission → reset to real date
                session.pop("fake_date", None)
                flash("Time Machine reset to the real date.", "info")
                return redirect(url_for("coordinator.dashboard"))
            ok, result = validate_fake_date(raw)
            if not ok:
                flash(result, "danger")
            else:
                session["fake_date"] = result
                flash(f"Time Machine active: date is now {result}.", "warning")
                return redirect(url_for("coordinator.dashboard"))
        return render_template("time_machine.html")

    # ── Custom error pages ───────────────────────────────────────────────
    @app.errorhandler(403)
    def forbidden(_e):
        return render_template("errors/403.html"), 403

    @app.errorhandler(404)
    def not_found(_e):
        return render_template("errors/404.html"), 404

    @app.errorhandler(500)
    def server_error(e):
        app.logger.exception(e)
        return render_template("errors/500.html"), 500

    @app.errorhandler(413)
    def too_large(_e):
        flash("Request too large (max 1 MB).", "danger")
        return redirect(request.referrer or url_for("coordinator.dashboard")), 413

    return app
