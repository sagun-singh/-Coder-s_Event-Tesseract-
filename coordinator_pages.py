"""
coordinator_pages.py
Coordinator blueprint: all inventory-management routes.

Security:
  * Every route decorated @coordinator_required.
  * All state-changing routes are POST-only (GET only shows forms).
  * Inputs validated (type, range, enum, length) before calling logic.
  * Flask-WTF CSRFProtect is active globally – no manual token checks needed.
  * Student_id for issue is resolved from the DB, never trusted from forms.
  * flash(msg) from logic functions; generic extra text avoided.
"""

import re

from flask import (
    Blueprint, flash, g, jsonify, redirect, render_template, request, url_for
)
from werkzeug.security import generate_password_hash

from auth import coordinator_required
from database import get_db, log_action, add_item, add_asset, add_location
from logic_issue import issue_item
from logic_return_renewal import return_item, decide_renewal, update_condition
from time_utils import get_today

coordinator_bp = Blueprint("coordinator", __name__, url_prefix="/coordinator")

_VALID_CONDITIONS = {"usable", "damaged", "lost", "maintenance"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _int_or_none(val):
    try:
        v = int(val)
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def _staff():
    return g.user["username"]


def _stock_summary(db):
    """
    Aggregate the 7 stock numbers shown on the dashboard.
    Returns a simple namespace-like dict.
    """
    row = db.execute(
        """
        SELECT
            SUM(total_qty)                                   AS total_qty,
            SUM(qty_available)                               AS qty_available,
            SUM(qty_damaged)                                 AS qty_damaged,
            SUM(qty_lost)                                    AS qty_lost,
            SUM(qty_maintenance)                             AS qty_maintenance,
            (SELECT COUNT(*) FROM assets)                    AS total_assets,
            (SELECT COUNT(*) FROM issues WHERE status='open') AS open_issues
        FROM items
        """
    ).fetchone()
    # Convert Row to plain dict so the template can use .attr notation
    return {
        "total_qty":      row["total_qty"]      or 0,
        "qty_available":  row["qty_available"]  or 0,
        "qty_damaged":    row["qty_damaged"]     or 0,
        "qty_lost":       row["qty_lost"]        or 0,
        "qty_maintenance":row["qty_maintenance"] or 0,
        "total_assets":   row["total_assets"]    or 0,
        "open_issues":    row["open_issues"]     or 0,
    }


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@coordinator_bp.route("/")
@coordinator_required
def dashboard():
    from logic_reports import get_overdue_list, get_upcoming_due
    today   = get_today()
    db      = get_db()
    overdue = get_overdue_list(today)
    upcoming= get_upcoming_due(today, days=3)
    stock   = _stock_summary(db)

    pending_renewals = db.execute(
        """
        SELECT r.renewal_id, r.request_date,
               i.issue_id, i.due_date,
               it.name  AS item_name,
               s.name   AS student_name,
               s.enrolment_no
        FROM   renewals r
        JOIN   issues   i  ON i.issue_id   = r.issue_id
        JOIN   items    it ON it.item_id   = i.item_id
        JOIN   students s  ON s.student_id = i.student_id
        WHERE  r.status = 'pending'
        ORDER BY r.request_date
        """
    ).fetchall()

    return render_template(
        "coordinator/dashboard.html",
        overdue=overdue,
        upcoming=upcoming,
        pending_renewals=pending_renewals,
        stock=stock,
        today=today,
    )


# ---------------------------------------------------------------------------
# Items list
# ---------------------------------------------------------------------------

@coordinator_bp.route("/items")
@coordinator_required
def items():
    db   = get_db()
    rows = db.execute("SELECT * FROM items ORDER BY category, name").fetchall()
    return render_template("coordinator/items.html", items=rows)


# ---------------------------------------------------------------------------
# Add Item
# ---------------------------------------------------------------------------

@coordinator_bp.route("/items/add", methods=["GET", "POST"])
@coordinator_required
def add_item_route():
    db = get_db()
    locations = db.execute("SELECT * FROM locations ORDER BY name").fetchall()

    if request.method == "POST":
        code        = request.form.get("code",        "").strip()[:50]
        name        = request.form.get("name",        "").strip()[:200]
        category    = request.form.get("category",    "").strip()[:100]
        kind        = request.form.get("kind",        "").strip()
        description = request.form.get("description", "").strip()[:500]
        make_model  = request.form.get("make_model",  "").strip()[:200]
        total_qty   = _int_or_none(request.form.get("total_qty"))
        location_id = _int_or_none(request.form.get("location_id"))

        if total_qty is None:
            total_qty = 0
            if not request.form.get("total_qty", "").strip() == "0":
                # allow explicit 0
                pass

        # Server-side validation
        if not code:
            flash("Item code is required.", "danger")
        elif not name:
            flash("Item name is required.", "danger")
        elif not category:
            flash("Category is required.", "danger")
        elif kind not in ("asset", "quantity"):
            flash("Kind must be 'asset' or 'quantity'.", "danger")
        elif total_qty is None or total_qty < 0:
            flash("Total qty must be 0 or greater.", "danger")
        else:
            db.execute("BEGIN IMMEDIATE")
            try:
                add_item(db, code, name, category, kind, total_qty,
                         location_id, description, make_model)
                db.execute("COMMIT")
                flash(f"Item '{name}' added.", "success")
                return redirect(url_for("coordinator.items"))
            except ValueError as e:
                db.execute("ROLLBACK")
                flash(str(e), "danger")
            except Exception:
                db.execute("ROLLBACK")
                flash("Could not add item.", "danger")

    return render_template("coordinator/add_item.html", locations=locations)


# ---------------------------------------------------------------------------
# Add Asset
# ---------------------------------------------------------------------------

@coordinator_bp.route("/assets/add", methods=["GET", "POST"])
@coordinator_required
def add_asset_route():
    db = get_db()
    asset_items = db.execute(
        "SELECT item_id, code, name FROM items WHERE kind='asset' ORDER BY name"
    ).fetchall()
    locations = db.execute("SELECT * FROM locations ORDER BY name").fetchall()

    if request.method == "POST":
        item_id     = _int_or_none(request.form.get("item_id"))
        serial_no   = request.form.get("serial_no", "").strip()[:100]
        location_id = _int_or_none(request.form.get("location_id"))
        condition   = request.form.get("condition", "usable").strip()

        if not item_id:
            flash("Parent item is required.", "danger")
        elif not serial_no:
            flash("Serial number is required.", "danger")
        elif condition not in _VALID_CONDITIONS:
            flash("Invalid condition.", "danger")
        else:
            db.execute("BEGIN IMMEDIATE")
            try:
                new_id = add_asset(db, item_id, serial_no, location_id, condition)
                # Bump total_qty on parent item to keep counts consistent
                db.execute(
                    "UPDATE items SET total_qty = total_qty + 1 WHERE item_id = ?",
                    (item_id,)
                )
                db.execute("COMMIT")
                flash(f"Asset '{serial_no}' added (ID {new_id}).", "success")
                return redirect(url_for("coordinator.items"))
            except ValueError as e:
                db.execute("ROLLBACK")
                flash(str(e), "danger")
            except Exception:
                db.execute("ROLLBACK")
                flash("Could not add asset.", "danger")

    return render_template("coordinator/add_asset.html",
                           asset_items=asset_items, locations=locations)


# ---------------------------------------------------------------------------
# Add Location
# ---------------------------------------------------------------------------

@coordinator_bp.route("/locations/add", methods=["GET", "POST"])
@coordinator_required
def add_location_route():
    if request.method == "POST":
        name = request.form.get("name", "").strip()[:200]
        if not name:
            flash("Location name is required.", "danger")
        else:
            db = get_db()
            db.execute("BEGIN IMMEDIATE")
            try:
                add_location(db, name)
                db.execute("COMMIT")
                flash(f"Location '{name}' added.", "success")
                return redirect(url_for("coordinator.dashboard"))
            except ValueError as e:
                db.execute("ROLLBACK")
                flash(str(e), "danger")
            except Exception:
                db.execute("ROLLBACK")
                flash("Could not add location.", "danger")

    return render_template("coordinator/add_location.html")


# ---------------------------------------------------------------------------
# Issue item  (GET = form, POST = action → redirect to slip on success)
# ---------------------------------------------------------------------------

@coordinator_bp.route("/issue", methods=["GET", "POST"])
@coordinator_required
def issue():
    db    = get_db()
    today = get_today()

    if request.method == "POST":
        student_id = _int_or_none(request.form.get("student_id"))
        item_id    = _int_or_none(request.form.get("item_id"))
        qty_raw    = request.form.get("qty", "1").strip()
        purpose    = request.form.get("purpose", "").strip()[:255]
        asset_id   = _int_or_none(request.form.get("asset_id"))

        qty = _int_or_none(qty_raw) if qty_raw else None

        # Server-side guard before entering logic
        errors = []
        if not student_id:
            errors.append("Student is required.")
        if not item_id:
            errors.append("Item is required.")
        if qty is None or qty < 1:
            errors.append("Quantity must be at least 1.")
        if not purpose:
            errors.append("Purpose is required.")

        if errors:
            for e in errors:
                flash(e, "danger")
        else:
            ok, msg, due_date = issue_item(
                student_id=student_id,
                item_id=item_id,
                qty=qty,
                purpose=purpose,
                staff_id=_staff(),
                today=today,
                asset_id=asset_id,
            )
            flash(msg, "success" if ok else "danger")
            if ok:
                # Find the new issue_id and redirect to the slip
                new_issue = db.execute(
                    """
                    SELECT i.issue_id, i.qty, i.issue_date, i.due_date,
                           i.purpose, i.issued_by, i.asset_id,
                           it.name AS item_name, it.code AS item_code,
                           a.serial_no
                    FROM   issues i
                    JOIN   items  it ON it.item_id = i.item_id
                    LEFT JOIN assets a ON a.asset_id = i.asset_id
                    WHERE  i.student_id = ? AND i.item_id = ?
                    ORDER BY i.issue_id DESC LIMIT 1
                    """,
                    (student_id, item_id)
                ).fetchone()
                if new_issue:
                    return redirect(
                        url_for("coordinator.issue_slip", issue_id=new_issue["issue_id"])
                    )
                return redirect(url_for("coordinator.dashboard"))

    students = db.execute(
        "SELECT student_id, name, enrolment_no FROM students ORDER BY name"
    ).fetchall()
    all_items = db.execute("SELECT * FROM items ORDER BY category, name").fetchall()
    return render_template(
        "coordinator/issue_form.html",
        students=students,
        items=all_items,
        today=today,
    )


# ---------------------------------------------------------------------------
# Issue slip (printable)
# ---------------------------------------------------------------------------

@coordinator_bp.route("/issue/<int:issue_id>/slip")
@coordinator_required
def issue_slip(issue_id):
    db = get_db()
    issue = db.execute(
        """
        SELECT i.issue_id, i.qty, i.issue_date, i.due_date,
               i.purpose, i.issued_by,
               it.name AS item_name, it.code AS item_code,
               a.serial_no
        FROM   issues i
        JOIN   items  it ON it.item_id  = i.item_id
        LEFT JOIN assets a ON a.asset_id = i.asset_id
        WHERE  i.issue_id = ?
        """,
        (issue_id,)
    ).fetchone()
    if issue is None:
        flash("Issue not found.", "danger")
        return redirect(url_for("coordinator.dashboard"))

    student = db.execute(
        """
        SELECT s.* FROM students s
        JOIN issues i ON i.student_id = s.student_id
        WHERE i.issue_id = ?
        """,
        (issue_id,)
    ).fetchone()

    return render_template(
        "coordinator/issue_slip.html",
        issue=issue,
        student=student,
    )


# ---------------------------------------------------------------------------
# Assets for item (fetch endpoint for issue form JS)
# ---------------------------------------------------------------------------

@coordinator_bp.route("/assets_for_item/<int:item_id>")
@coordinator_required
def assets_for_item(item_id):
    db = get_db()
    rows = db.execute(
        """
        SELECT asset_id, serial_no, condition, status
        FROM   assets
        WHERE  item_id = ? AND status = 'available' AND condition = 'usable'
        ORDER BY serial_no
        """,
        (item_id,)
    ).fetchall()
    return jsonify([dict(r) for r in rows])


# ---------------------------------------------------------------------------
# Return item
# ---------------------------------------------------------------------------

@coordinator_bp.route("/return", methods=["GET", "POST"])
@coordinator_required
def record_return():
    db    = get_db()
    today = get_today()

    if request.method == "POST":
        issue_id     = _int_or_none(request.form.get("issue_id"))
        qty_returned = _int_or_none(request.form.get("qty_returned"))
        condition    = request.form.get("condition", "").strip()

        errors = []
        if not issue_id:
            errors.append("Select an open issue.")
        if not qty_returned or qty_returned < 1:
            errors.append("Quantity must be at least 1.")
        if condition not in _VALID_CONDITIONS:
            errors.append("Select a valid condition.")

        if errors:
            for e in errors:
                flash(e, "danger")
        else:
            ok, msg = return_item(issue_id, qty_returned, condition, _staff(), today)
            flash(msg, "success" if ok else "danger")
            if ok:
                return redirect(url_for("coordinator.dashboard"))

    open_issues = db.execute(
        """
        SELECT i.issue_id, i.qty, i.qty_returned, i.due_date,
               s.name AS student_name, s.enrolment_no,
               it.name AS item_name, it.kind
        FROM   issues i
        JOIN   students s  ON s.student_id = i.student_id
        JOIN   items    it ON it.item_id   = i.item_id
        WHERE  i.status = 'open'
        ORDER BY i.due_date
        """
    ).fetchall()

    return render_template(
        "coordinator/return_form.html",
        open_issues=open_issues,
        today=today,
    )


# ---------------------------------------------------------------------------
# Decide renewal  (remarks REQUIRED by both form and server)
# ---------------------------------------------------------------------------

@coordinator_bp.route("/renewal/<int:renewal_id>/decide", methods=["POST"])
@coordinator_required
def decide_renewal_route(renewal_id):
    decision = request.form.get("decision", "").strip()
    remarks  = request.form.get("remarks", "").strip()[:500]
    today    = get_today()

    # Server-side validation: remarks required regardless of decision
    if not remarks:
        flash("Remarks are required when approving or rejecting a renewal.", "danger")
        return redirect(url_for("coordinator.dashboard"))
    if decision not in ("approve", "reject"):
        flash("Invalid decision.", "danger")
        return redirect(url_for("coordinator.dashboard"))

    approve = (decision == "approve")
    ok, msg, new_due = decide_renewal(renewal_id, approve, remarks, _staff(), today)
    flash(msg, "success" if ok else "danger")
    return redirect(url_for("coordinator.dashboard"))


# ---------------------------------------------------------------------------
# Update condition / status
# ---------------------------------------------------------------------------

@coordinator_bp.route("/condition", methods=["GET", "POST"])
@coordinator_required
def condition():
    db    = get_db()
    today = get_today()

    if request.method == "POST":
        item_id       = _int_or_none(request.form.get("item_id"))
        asset_id_raw  = request.form.get("asset_id", "").strip()
        asset_id      = _int_or_none(asset_id_raw) if asset_id_raw else None
        new_condition = request.form.get("new_condition", "").strip()
        qty_raw       = request.form.get("qty", "1").strip()
        qty           = _int_or_none(qty_raw) or 1

        if not item_id:
            flash("Item is required.", "danger")
        elif new_condition not in _VALID_CONDITIONS:
            flash("Select a valid condition.", "danger")
        else:
            ok, msg = update_condition(
                item_id, asset_id, new_condition, qty, _staff(), today
            )
            flash(msg, "success" if ok else "danger")
            if ok:
                return redirect(url_for("coordinator.items"))

    all_items = db.execute(
        "SELECT item_id, code, name, kind FROM items ORDER BY name"
    ).fetchall()
    return render_template("coordinator/condition_form.html", items=all_items, today=today)


# ---------------------------------------------------------------------------
# Students list + karma
# ---------------------------------------------------------------------------

@coordinator_bp.route("/students")
@coordinator_required
def students():
    from karma import karma_score
    today = get_today()
    db    = get_db()
    rows  = db.execute("SELECT * FROM students ORDER BY name").fetchall()
    scored = [
        {"student": s, **dict(zip(("score","badge","message"), karma_score(s["student_id"], today)))}
        for s in rows
    ]
    return render_template("coordinator/students.html", students=scored, today=today)


@coordinator_bp.route("/students/add", methods=["GET", "POST"])
@coordinator_required
def add_student():
    db = get_db()
    if request.method == "POST":
        name         = request.form.get("name", "").strip()[:200]
        enrolment_no = request.form.get("enrolment_no", "").strip()[:50]
        programme    = request.form.get("programme", "").strip()[:100]
        semester_raw = request.form.get("semester", "")
        contact      = request.form.get("contact", "").strip()[:100]
        username     = request.form.get("username", "").strip()[:150]
        password     = request.form.get("password", "")

        semester = _int_or_none(semester_raw)

        errors = []
        if not all([name, enrolment_no, programme, contact, username, password]):
            errors.append("All fields are required.")
        if semester is None or not (1 <= semester <= 12):
            errors.append("Semester must be between 1 and 12.")
        if len(password) < 8:
            errors.append("Password must be at least 8 characters.")

        if errors:
            for e in errors:
                flash(e, "danger")
        else:
            try:
                db.execute("BEGIN IMMEDIATE")
                cur = db.execute(
                    """
                    INSERT INTO students (name, enrolment_no, programme, semester, contact)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (name, enrolment_no, programme, semester, contact)
                )
                sid = cur.lastrowid
                db.execute(
                    "INSERT INTO users (username, password, role, student_id) "
                    "VALUES (?, ?, 'student', ?)",
                    (username, generate_password_hash(password), sid)
                )
                db.execute("COMMIT")
                flash("Student and login created.", "success")
                return redirect(url_for("coordinator.students"))
            except Exception:
                try: db.execute("ROLLBACK")
                except Exception: pass
                flash("Could not create student. Enrolment number or username may already exist.", "danger")

    return render_template("coordinator/add_student.html")


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

@coordinator_bp.route("/stock-log")
@coordinator_required
def stock_log():
    db   = get_db()
    rows = db.execute(
        """
        SELECT sl.*, it.name AS item_name, it.code
        FROM   stock_log sl
        JOIN   items it ON it.item_id = sl.item_id
        ORDER BY sl.log_id DESC LIMIT 500
        """
    ).fetchall()
    return render_template("coordinator/stock_log.html", rows=rows)
