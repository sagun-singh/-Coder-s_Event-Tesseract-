"""
auth.py  –  P5
Authentication blueprint: login, logout, helpers.

Security notes:
  * Passwords hashed with werkzeug.security (pbkdf2-sha256).
  * Session stores only user_id + role + student_id (never raw password).
  * Role checked server-side on every protected route via decorators.
  * CSRF handled globally by Flask-WTF CSRFProtect (no manual token logic).
  * Generic error messages; no detail about which field failed.
"""

from functools import wraps

from flask import (
    Blueprint, flash, g, redirect, render_template,
    request, session, url_for, abort
)
from werkzeug.security import check_password_hash, generate_password_hash

from database import get_db

auth_bp = Blueprint("auth", __name__)


# ---------------------------------------------------------------------------
# current_user(): populate g.user on every request
# ---------------------------------------------------------------------------

def load_logged_in_user():
    """
    Flask before_request hook: load the user row into g.user from the session.
    Called via app.py  →  app.before_request(load_logged_in_user).
    """
    user_id = session.get("user_id")
    if user_id is None:
        g.user = None
    else:
        g.user = get_db().execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()


def current_user():
    """Return the current g.user row (may be None if not logged in)."""
    return g.get("user")


# ---------------------------------------------------------------------------
# Decorators
# ---------------------------------------------------------------------------

def login_required(view):
    """Redirect to login if user is not authenticated."""
    @wraps(view)
    def wrapped(**kwargs):
        if g.user is None:
            return redirect(url_for("auth.login"))
        return view(**kwargs)
    return wrapped


def coordinator_required(view):
    """Allow only coordinators; 403 for everyone else."""
    @wraps(view)
    @login_required
    def wrapped(**kwargs):
        if g.user["role"] != "coordinator":
            abort(403)
        return view(**kwargs)
    return wrapped


def student_required(view):
    """Allow only students; 403 for everyone else."""
    @wraps(view)
    @login_required
    def wrapped(**kwargs):
        if g.user["role"] != "student":
            abort(403)
        return view(**kwargs)
    return wrapped


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if g.user is not None:
        # Already logged in → redirect to appropriate dashboard
        if g.user["role"] == "coordinator":
            return redirect(url_for("coordinator.dashboard"))
        return redirect(url_for("student.dashboard"))

    error = None
    if request.method == "POST":
        # CSRF is validated globally in app.py before_request for all POSTs.
        username = request.form.get("username", "").strip()[:150]
        password = request.form.get("password", "")

        if not username or not password:
            error = "Username and password are required."
        else:
            db = get_db()
            user = db.execute(
                "SELECT * FROM users WHERE username = ?", (username,)
            ).fetchone()

            # Generic message – don't reveal whether username or password is wrong
            if user is None or not check_password_hash(user["password"], password):
                error = "Invalid credentials."

        if error is None:
            # Regenerate session to prevent session fixation
            session.clear()
            session["user_id"]    = user["user_id"]
            session["role"]       = user["role"]
            session["student_id"] = user["student_id"]  # None for coordinators
            session.permanent = True

            if user["role"] == "coordinator":
                return redirect(url_for("coordinator.dashboard"))
            return redirect(url_for("student.dashboard"))

        flash(error, "danger")

    return render_template("login.html")


@auth_bp.route("/logout", methods=["POST"])
@login_required
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))


