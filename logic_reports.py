"""
logic_reports.py  –  P6
Pure read-only data-access functions.  No side-effects.

Public API
----------
get_overdue_list(today)
    Open issues with outstanding qty > 0 and today > due_date.
    Each row carries:
      days_overdue  int        – calendar days since due_date
      sentence      str        – human-readable summary sentence (exact spec)

get_upcoming_due(today, days=3)
    Open issues due within `days` calendar days from today (inclusive).
    Each row carries:
      days_left     int        – calendar days until due_date

get_stock_summary(item_id=None)
    Per-item totals: total, available, issued, overdue, damaged, lost, maintenance.

report_by_student(today, limit=200)
report_by_item(today, limit=200)
report_by_programme(today, limit=200)
report_by_lab(today, limit=200)
    Aggregated drill-down reports.
"""

from datetime import date
from database import get_db


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse(d: str) -> date:
    """Parse an ISO date string to a date object."""
    return date.fromisoformat(d)


def _format_date(d: date) -> str:
    """
    Return 'D Month YYYY', e.g. '16 October 2026' (no leading zero on day).
    Cross-platform: avoids %-d (Linux-only) and %#d (Windows-only) by
    converting the day to an integer and composing manually.
    """
    return f"{d.day} {d.strftime('%B')} {d.year}"


def _build_sentence(row: dict, days_overdue: int) -> str:
    """
    Produce the exact required sentence:
      "Development Board DB-014 issued to student enrolment number XXXXX
       was due on 16 October 2026 and is overdue by 3 days."
    Uses "1 day" (singular) / "N days" (plural).
    For quantity items (no serial) the serial_no part is omitted.
    """
    item_part = row["item_name"]
    if row.get("serial_no"):
        item_part = f"{row['item_name']} {row['serial_no']}"

    day_word = "day" if days_overdue == 1 else "days"
    due_str  = _format_date(_parse(row["due_date"]))

    return (
        f"{item_part} issued to student enrolment number "
        f"{row['enrolment_no']} was due on {due_str} "
        f"and is overdue by {days_overdue} {day_word}."
    )


# ---------------------------------------------------------------------------
# get_overdue_list
# ---------------------------------------------------------------------------

def get_overdue_list(today: str) -> list:
    """
    Return open issues with outstanding qty > 0 whose due_date < today.

    Each returned item is a plain dict enriched with:
      days_overdue : int
      sentence     : str  (the exact spec sentence)
      outstanding  : int  (qty - qty_returned)
    """
    today_date = _parse(today)
    db = get_db()

    rows = db.execute(
        """
        SELECT  i.issue_id,
                i.due_date,
                i.issue_date,
                i.qty,
                i.qty_returned,
                i.purpose,
                i.issued_by,
                i.student_id,
                s.name          AS student_name,
                s.enrolment_no,
                it.item_id,
                it.code         AS item_code,
                it.name         AS item_name,
                it.kind,
                a.serial_no
        FROM    issues   i
        JOIN    students s  ON s.student_id = i.student_id
        JOIN    items    it ON it.item_id   = i.item_id
        LEFT JOIN assets a  ON a.asset_id  = i.asset_id
        WHERE   i.status      = 'open'
          AND   (i.qty - i.qty_returned) > 0
          AND   i.due_date    <  ?
        ORDER BY i.due_date ASC
        """,
        (today,)
    ).fetchall()

    result = []
    for r in rows:
        d          = dict(r)
        due_date   = _parse(d["due_date"])
        days_over  = (today_date - due_date).days      # always >= 1 here
        d["days_overdue"] = days_over
        d["outstanding"]  = d["qty"] - d["qty_returned"]
        d["sentence"]     = _build_sentence(d, days_over)
        result.append(d)
    return result


# ---------------------------------------------------------------------------
# get_upcoming_due
# ---------------------------------------------------------------------------

def get_upcoming_due(today: str, days: int = 3) -> list:
    """
    Return open issues with outstanding qty > 0 due within `days` days
    (today <= due_date <= today + days).

    Each row is enriched with:
      days_left   : int
      outstanding : int
    """
    if not isinstance(days, int) or days < 0:
        days = 3
    from datetime import timedelta
    today_date = _parse(today)
    end_date   = (today_date + timedelta(days=days)).isoformat()

    db = get_db()
    rows = db.execute(
        """
        SELECT  i.issue_id,
                i.due_date,
                i.issue_date,
                i.qty,
                i.qty_returned,
                i.purpose,
                i.issued_by,
                s.name          AS student_name,
                s.enrolment_no,
                it.code         AS item_code,
                it.name         AS item_name,
                it.kind,
                a.serial_no
        FROM    issues   i
        JOIN    students s  ON s.student_id = i.student_id
        JOIN    items    it ON it.item_id   = i.item_id
        LEFT JOIN assets a  ON a.asset_id  = i.asset_id
        WHERE   i.status      = 'open'
          AND   (i.qty - i.qty_returned) > 0
          AND   i.due_date   >= ?
          AND   i.due_date   <= ?
        ORDER BY i.due_date ASC
        """,
        (today, end_date)
    ).fetchall()

    result = []
    for r in rows:
        d = dict(r)
        d["days_left"]   = (_parse(d["due_date"]) - today_date).days
        d["outstanding"] = d["qty"] - d["qty_returned"]
        result.append(d)
    return result


# ---------------------------------------------------------------------------
# get_stock_summary
# ---------------------------------------------------------------------------

def get_stock_summary(item_id: int = None) -> list:
    """
    Per-item stock summary.

    Columns returned (as list of dicts):
      item_id, code, name, category, kind,
      total, available, issued, overdue, damaged, lost, maintenance

    'issued'   = count of open issue rows for this item
    'overdue'  = count of open issues where due_date < today (uses DB date)
    """
    db = get_db()

    # We pass today so SQLite can compute overdue inline
    today = date.today().isoformat()    # safe: only for display aggregation
    # (get_today() would need Flask context which may not be available in tests)
    try:
        from time_utils import get_today
        today = get_today()
    except Exception:
        pass

    where  = "WHERE it.item_id = ?" if (item_id is not None and isinstance(item_id, int)) else ""
    params = (item_id,) if where else ()

    sql = f"""
        SELECT  it.item_id,
                it.code,
                it.name,
                it.category,
                it.kind,
                it.total_qty                                        AS total,
                it.qty_available                                    AS available,
                it.qty_damaged                                      AS damaged,
                it.qty_lost                                         AS lost,
                it.qty_maintenance                                  AS maintenance,
                COUNT(CASE WHEN iss.status = 'open' THEN 1 END)    AS issued,
                COUNT(CASE WHEN iss.status = 'open'
                            AND iss.due_date < '{today}'
                            AND (iss.qty - iss.qty_returned) > 0
                            THEN 1 END)                             AS overdue
        FROM    items it
        LEFT JOIN issues iss ON iss.item_id = it.item_id
        {where}
        GROUP BY it.item_id
        ORDER BY it.category, it.name
    """
    rows = db.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Drill-down reports
# ---------------------------------------------------------------------------

def report_by_student(today: str, limit: int = 200) -> list:
    """Issues grouped by student: name, enrolment, open count, overdue count."""
    db = get_db()
    rows = db.execute(
        """
        SELECT  s.student_id,
                s.name              AS student_name,
                s.enrolment_no,
                s.programme,
                s.semester,
                COUNT(i.issue_id)   AS total_issues,
                SUM(CASE WHEN i.status = 'open' THEN 1 ELSE 0 END)  AS open_issues,
                SUM(CASE WHEN i.status = 'open'
                          AND i.due_date < ?
                          AND (i.qty - i.qty_returned) > 0
                     THEN 1 ELSE 0 END)                              AS overdue_issues,
                SUM(CASE WHEN i.status = 'closed' THEN 1 ELSE 0 END) AS closed_issues
        FROM    students s
        LEFT JOIN issues i ON i.student_id = s.student_id
        GROUP BY s.student_id
        ORDER BY overdue_issues DESC, open_issues DESC, s.name
        LIMIT ?
        """,
        (today, limit)
    ).fetchall()
    return [dict(r) for r in rows]


def report_by_item(today: str, limit: int = 200) -> list:
    """Issues grouped by item: utilisation, overdue count."""
    db = get_db()
    rows = db.execute(
        """
        SELECT  it.item_id,
                it.code,
                it.name             AS item_name,
                it.category,
                it.kind,
                it.total_qty,
                it.qty_available,
                COUNT(i.issue_id)   AS total_issues,
                SUM(CASE WHEN i.status = 'open' THEN 1 ELSE 0 END)  AS open_issues,
                SUM(CASE WHEN i.status = 'open'
                          AND i.due_date < ?
                          AND (i.qty - i.qty_returned) > 0
                     THEN 1 ELSE 0 END)                              AS overdue_issues
        FROM    items it
        LEFT JOIN issues i ON i.item_id = it.item_id
        GROUP BY it.item_id
        ORDER BY overdue_issues DESC, open_issues DESC, it.name
        LIMIT ?
        """,
        (today, limit)
    ).fetchall()
    return [dict(r) for r in rows]


def report_by_programme(today: str, limit: int = 200) -> list:
    """Issues grouped by programme + semester."""
    db = get_db()
    rows = db.execute(
        """
        SELECT  s.programme,
                s.semester,
                COUNT(DISTINCT s.student_id)                         AS student_count,
                COUNT(i.issue_id)                                    AS total_issues,
                SUM(CASE WHEN i.status = 'open' THEN 1 ELSE 0 END)  AS open_issues,
                SUM(CASE WHEN i.status = 'open'
                          AND i.due_date < ?
                          AND (i.qty - i.qty_returned) > 0
                     THEN 1 ELSE 0 END)                              AS overdue_issues
        FROM    students s
        LEFT JOIN issues i ON i.student_id = s.student_id
        GROUP BY s.programme, s.semester
        ORDER BY s.programme, s.semester
        LIMIT ?
        """,
        (today, limit)
    ).fetchall()
    return [dict(r) for r in rows]


def report_by_lab(today: str, limit: int = 200) -> list:
    """Issues grouped by location (lab), joining via items.location_id."""
    db = get_db()
    rows = db.execute(
        """
        SELECT  COALESCE(l.name, 'Unassigned')  AS lab_name,
                COUNT(DISTINCT it.item_id)       AS item_count,
                COUNT(i.issue_id)                AS total_issues,
                SUM(CASE WHEN i.status = 'open' THEN 1 ELSE 0 END)  AS open_issues,
                SUM(CASE WHEN i.status = 'open'
                          AND i.due_date < ?
                          AND (i.qty - i.qty_returned) > 0
                     THEN 1 ELSE 0 END)                              AS overdue_issues
        FROM    items it
        LEFT JOIN locations l ON l.location_id = it.location_id
        LEFT JOIN issues    i ON i.item_id     = it.item_id
        GROUP BY it.location_id
        ORDER BY overdue_issues DESC, open_issues DESC, lab_name
        LIMIT ?
        """,
        (today, limit)
    ).fetchall()
    return [dict(r) for r in rows]
