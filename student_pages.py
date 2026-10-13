"""
P5 – student_pages.py
Student-facing blueprint.

Security rules enforced here:
  • Every route is decorated with @student_required.
  • Issue listings are ALWAYS filtered by session['student_id'] –
    never by any value from the URL or form (IDOR prevention).
  • Renewal requests use the session student_id, not a form field.
  • Input is validated before calling logic functions.
"""

from flask import (
    Blueprint, flash, g, redirect, render_template,
    request, session, url_for
)

from auth import student_required
from database import get_db
from karma import karma_score
from logic_return_renewal import request_renewal
from time_utils import get_today

student_bp = Blueprint("student", __name__, url_prefix="/student")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _my_student_id():
    """Return the session's student_id as an int (always trusted)."""
    return int(session["student_id"])


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@student_bp.route("/")
@student_required
def dashboard():
    db = get_db()
    sid = _my_student_id()
    today = get_today()
    score, badge, message = karma_score(sid, today)

    # Open issues for this student only
    open_issues = db.execute(
        """
        SELECT i.*, it.name AS item_name, it.kind,
               a.serial_no
        FROM issues i
        JOIN items it ON it.item_id = i.item_id
        LEFT JOIN assets a ON a.asset_id = i.asset_id
        WHERE i.student_id = ? AND i.status = 'open'
        ORDER BY i.due_date ASC
        """,
        (sid,)
    ).fetchall()

    # Pending renewals for this student
    pending_renewals = db.execute(
        """
        SELECT r.*, i.item_id, it.name AS item_name, i.due_date
        FROM renewals r
        JOIN issues i ON i.issue_id = r.issue_id
        JOIN items it ON it.item_id = i.item_id
        WHERE i.student_id = ? AND r.status = 'pending'
        """,
        (sid,)
    ).fetchall()

    return render_template(
        "student/dashboard.html",
        open_issues=open_issues,
        pending_renewals=pending_renewals,
        today=today,
        karma_score=score,
        karma_badge=badge,
        karma_message=message,
    )


# ---------------------------------------------------------------------------
# Issue history (closed issues)
# ---------------------------------------------------------------------------

@student_bp.route("/history")
@student_required
def history():
    db = get_db()
    sid = _my_student_id()

    issues = db.execute(
        """
        SELECT i.*, it.name AS item_name, it.kind, a.serial_no
        FROM issues i
        JOIN items it ON it.item_id = i.item_id
        LEFT JOIN assets a ON a.asset_id = i.asset_id
        WHERE i.student_id = ?
        ORDER BY i.issue_date DESC
        """,
        (sid,)
    ).fetchall()

    return render_template("student/history.html", issues=issues)


# ---------------------------------------------------------------------------
# Issue detail
# ---------------------------------------------------------------------------

@student_bp.route("/issue/<int:issue_id>")
@student_required
def issue_detail(issue_id):
    db  = get_db()
    sid = _my_student_id()

    # IDOR guard: enforce student_id = session
    issue = db.execute(
        """
        SELECT i.*, it.name AS item_name, it.kind,
               a.serial_no, a.condition AS asset_condition
        FROM issues i
        JOIN items it ON it.item_id = i.item_id
        LEFT JOIN assets a ON a.asset_id = i.asset_id
        WHERE i.issue_id = ? AND i.student_id = ?
        """,
        (issue_id, sid)
    ).fetchone()
    if issue is None:
        flash("Issue not found.", "danger")
        return redirect(url_for("student.dashboard"))

    renewals = db.execute(
        "SELECT * FROM renewals WHERE issue_id = ? ORDER BY request_date DESC",
        (issue_id,)
    ).fetchall()

    return render_template(
        "student/issue_detail.html",
        issue=issue,
        renewals=renewals,
    )


# ---------------------------------------------------------------------------
# Request renewal
# ---------------------------------------------------------------------------

@student_bp.route("/renew/<int:issue_id>", methods=["POST"])
@student_required
def renew(issue_id):
    sid   = _my_student_id()
    today = get_today()

    # issue_id comes from URL; student_id from session → safe against IDOR
    ok, msg = request_renewal(issue_id, sid, today)
    flash(msg, "success" if ok else "danger")
    return redirect(url_for("student.issue_detail", issue_id=issue_id))


# ---------------------------------------------------------------------------
# Student profile (read-only)
# ---------------------------------------------------------------------------

@student_bp.route("/profile")
@student_required
def profile():
    db  = get_db()
    sid = _my_student_id()

    student = db.execute(
        "SELECT * FROM students WHERE student_id = ?", (sid,)
    ).fetchone()
    if student is None:
        flash("Profile not found.", "danger")
        return redirect(url_for("student.dashboard"))

    return render_template("student/profile.html", student=student)
