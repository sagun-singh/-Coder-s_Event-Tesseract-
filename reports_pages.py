"""
reports_pages.py  –  P6
Coordinator-only reports blueprint.

Routes
------
GET  /reports/overdue           overdue list with sentence
GET  /reports/upcoming          upcoming due in 3 days
GET  /reports/stock             per-item stock summary (6 columns)
GET  /reports/by-student        drill-down by student
GET  /reports/by-item           drill-down by item
GET  /reports/by-programme      drill-down by programme + semester
GET  /reports/by-lab            drill-down by lab / location
GET  /reports/export/<kind>     CSV download  (kind in ALLOWED_CSV_EXPORTS)
     - kind not in whitelist → 404
     - CSV-injection protection: cells starting with = - + @ TAB CR prefixed with '
     - Content-Disposition attachment; filename is fixed/safe (no user input)
     - Cache-Control no-store on response
"""

import csv
import io
from flask import Blueprint, abort, make_response, render_template

from auth import coordinator_required
from logic_reports import (
    get_overdue_list,
    get_upcoming_due,
    get_stock_summary,
    report_by_student,
    report_by_item,
    report_by_programme,
    report_by_lab,
)
from time_utils import get_today

reports_bp = Blueprint("reports", __name__, url_prefix="/reports")

# Whitelist of allowed CSV export kinds  →  (label, generator_callable, headers)
# Callables receive today: str and return list[dict].
# Only these exact string values are accepted in the URL; anything else → 404.
_ALLOWED_CSV_EXPORTS = {
    "overdue": {
        "label":    "overdue",
        "filename": "overdue_issues.csv",
        "func":     lambda today: get_overdue_list(today),
        "headers":  [
            "issue_id", "student_name", "enrolment_no",
            "item_name", "serial_no", "due_date", "days_overdue",
            "outstanding", "purpose", "sentence",
        ],
    },
    "stock": {
        "label":    "stock",
        "filename": "stock_summary.csv",
        "func":     lambda today: get_stock_summary(),
        "headers":  [
            "item_id", "code", "name", "category", "kind",
            "total", "available", "issued", "overdue",
            "damaged", "lost", "maintenance",
        ],
    },
    "by-student": {
        "label":    "by-student",
        "filename": "report_by_student.csv",
        "func":     lambda today: report_by_student(today),
        "headers":  [
            "student_id", "student_name", "enrolment_no",
            "programme", "semester",
            "total_issues", "open_issues", "overdue_issues", "closed_issues",
        ],
    },
    "by-item": {
        "label":    "by-item",
        "filename": "report_by_item.csv",
        "func":     lambda today: report_by_item(today),
        "headers":  [
            "item_id", "code", "item_name", "category", "kind",
            "total_qty", "qty_available",
            "total_issues", "open_issues", "overdue_issues",
        ],
    },
}


# ---------------------------------------------------------------------------
# CSV injection protection
# ---------------------------------------------------------------------------

_INJECTION_PREFIXES = ("=", "-", "+", "@", "\t", "\r")


def _safe_cell(value) -> str:
    """
    Convert value to string and prefix with a single-quote if the cell
    starts with any CSV-injection trigger character.
    Ref: OWASP CSV Injection.
    """
    s = "" if value is None else str(value)
    if s and s[0] in _INJECTION_PREFIXES:
        s = "'" + s
    return s


def _build_csv(headers: list, rows: list) -> str:
    """Return a UTF-8 CSV string (with BOM for Excel compatibility)."""
    buf = io.StringIO()
    writer = csv.writer(buf, quoting=csv.QUOTE_ALL)
    writer.writerow([_safe_cell(h) for h in headers])
    for row in rows:
        writer.writerow([_safe_cell(row.get(h, "")) for h in headers])
    # Prepend UTF-8 BOM so Excel opens it correctly
    return "\ufeff" + buf.getvalue()


def _csv_response(content: str, filename: str):
    """Wrap a CSV string in a Flask response with the correct headers."""
    resp = make_response(content)
    resp.headers["Content-Type"]        = "text/csv; charset=utf-8"
    resp.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    resp.headers["Cache-Control"]       = "no-store"
    return resp


# ---------------------------------------------------------------------------
# HTML report pages
# ---------------------------------------------------------------------------

@reports_bp.route("/overdue")
@coordinator_required
def overdue():
    today = get_today()
    rows  = get_overdue_list(today)
    return render_template("reports/overdue.html", rows=rows, today=today)


@reports_bp.route("/upcoming")
@coordinator_required
def upcoming():
    today = get_today()
    rows  = get_upcoming_due(today, days=3)
    return render_template("reports/upcoming.html", rows=rows, today=today)


@reports_bp.route("/stock")
@coordinator_required
def stock():
    today = get_today()
    rows  = get_stock_summary()
    return render_template("reports/stock.html", rows=rows, today=today)


@reports_bp.route("/by-student")
@coordinator_required
def by_student():
    today = get_today()
    rows  = report_by_student(today)
    return render_template("reports/by_student.html", rows=rows, today=today)


@reports_bp.route("/by-item")
@coordinator_required
def by_item():
    today = get_today()
    rows  = report_by_item(today)
    return render_template("reports/by_item.html", rows=rows, today=today)


@reports_bp.route("/by-programme")
@coordinator_required
def by_programme():
    today = get_today()
    rows  = report_by_programme(today)
    return render_template("reports/by_programme.html", rows=rows, today=today)


@reports_bp.route("/by-lab")
@coordinator_required
def by_lab():
    today = get_today()
    rows  = report_by_lab(today)
    return render_template("reports/by_lab.html", rows=rows, today=today)


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------

@reports_bp.route("/export/<kind>")
@coordinator_required
def export_csv(kind: str):
    """
    Download a report as CSV.

    `kind` must be in _ALLOWED_CSV_EXPORTS; anything else returns 404.
    Filename is taken from the whitelist – never constructed from user input.
    """
    spec = _ALLOWED_CSV_EXPORTS.get(kind)
    if spec is None:
        abort(404)

    today   = get_today()
    rows    = spec["func"](today)
    content = _build_csv(spec["headers"], rows)
    return _csv_response(content, spec["filename"])
