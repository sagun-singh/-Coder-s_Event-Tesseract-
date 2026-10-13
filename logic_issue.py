"""
logic_issue.py
==============
Single public function: issue_item().

Security / correctness guarantees
----------------------------------
* All integer parameters validated with  type(x) is int  so that bool (True/False)
  and float (1.0) are rejected – both are common Web-form coercion accidents.
* qty  must be a strict positive int (>0); 0 and negatives are rejected.
* purpose  stripped, non-empty, max 200 characters.
* staff_id  verified as a coordinator in the users table before any mutation.
* All SQL uses ? placeholders only – no string interpolation.
* State changes happen inside a single  BEGIN IMMEDIATE  transaction.
  The guarded UPDATE (qty_available >= ?) acts as the final concurrency
  safety-net; rowcount == 0 means the stock changed under us → rollback.
* Every early-return path inside the transaction calls ROLLBACK first.
* sqlite3.Error is caught separately and returns a generic message so
  database internals are never exposed to the caller.
* The connection (or cursor) is released in a  finally  block.
* This module never calls  date.today()  – today is always injected by the caller.
* Never raises; always returns (ok, msg, due_date).
"""

import re
import sqlite3
from datetime import date, timedelta

from database import get_db, log_action

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

_DATE_RE           = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DEFAULT_ISSUE_DAYS = 15   # fallback if policies row is missing
_PURPOSE_MAX_LEN    = 200


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _is_strict_int(value) -> bool:
    """
    Return True only for genuine int values.
    Rejects bool (subclass of int), float, str, None, etc.
    """
    return type(value) is int   # intentional identity check, not isinstance


def _parse_date(s: str) -> date:
    """Parse 'YYYY-MM-DD' → date, or raise ValueError."""
    if not isinstance(s, str) or not _DATE_RE.match(s):
        raise ValueError(f"Bad date: {s!r}")
    return date.fromisoformat(s)


def _get_policies(conn) -> tuple:
    """
    Return (max_items_per_student, issue_days) from the policies singleton row.
    Falls back to sensible defaults if the table/row is missing.
    """
    try:
        row = conn.execute("SELECT * FROM policies WHERE id = 1").fetchone()
        if row:
            return int(row["max_items_per_student"]), int(row["issue_days"])
    except sqlite3.Error:
        pass
    return 5, _DEFAULT_ISSUE_DAYS


def _log_exc(exc: Exception) -> None:
    """Log to Flask app logger when available, otherwise stderr."""
    try:
        from flask import current_app
        current_app.logger.exception("logic_issue: %s", exc)
    except RuntimeError:
        import sys
        print(f"[logic_issue ERROR] {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def issue_item(
    student_id,
    item_id,
    qty,
    purpose,
    staff_id,
    today,
    asset_id=None,
):
    """
    Lend an item to a student.

    Parameters
    ----------
    student_id : int    – must be a registered student
    item_id    : int    – catalogue item
    qty        : int    – units requested; must equal 1 for asset-kind items
    purpose    : str    – reason for loan; non-empty, max 200 chars
    staff_id   : str    – username of the coordinator performing the action
    today      : str    – 'YYYY-MM-DD'; injected by Time Machine, never date.today()
    asset_id   : int | None – required for 'asset' kind, must be None for 'quantity'

    Checks (in this order, stop at first failure)
    ----------------------------------------------
    1. today is a valid YYYY-MM-DD date.
    2. All integer args are genuine ints (not bool/float/str); positive.
    3. purpose is a non-empty string, ≤ 200 chars.
    4. staff_id is a non-empty string.
    5. staff_id belongs to a coordinator in the users table.
    6. student_id is a registered student.
    7. item_id exists.
    8. For 'asset' kind: asset condition is 'usable'.
    9. qty ≤ qty_available (message: "N items requested, only M available.").
    10. For 'asset' kind: asset status is 'available' (not already issued).
    11. Student has not hit max_items_per_student open issues.

    Transaction
    -----------
    BEGIN IMMEDIATE → guarded UPDATE qty_available → UPDATE asset status → INSERT issue → log_action → COMMIT
    Any failure → ROLLBACK → return (False, generic_msg, None)

    Returns
    -------
    (ok: bool, msg: str, due_date: str | None)
    """

    # ── Phase 1: pre-DB validation (no connection needed) ─────────────────

    # today
    try:
        today_date = _parse_date(today)
    except (ValueError, TypeError):
        return False, "Invalid date supplied.", None

    # Integer args – reject bool, float, str, 0, negatives
    if not _is_strict_int(student_id) or student_id <= 0:
        return False, "Invalid student.", None
    if not _is_strict_int(item_id) or item_id <= 0:
        return False, "Invalid item.", None
    if not _is_strict_int(qty) or qty <= 0:
        return False, "Quantity must be a positive integer.", None
    if asset_id is not None and (not _is_strict_int(asset_id) or asset_id <= 0):
        return False, "Invalid asset_id.", None

    # purpose
    if not isinstance(purpose, str):
        return False, "Purpose must be a string.", None
    purpose = purpose.strip()
    if not purpose:
        return False, "Purpose is required.", None
    if len(purpose) > _PURPOSE_MAX_LEN:
        return False, f"Purpose must be {_PURPOSE_MAX_LEN} characters or fewer.", None

    # staff_id – type check only here; coordinator check happens in DB
    if not isinstance(staff_id, str) or not staff_id.strip():
        return False, "Staff ID is required.", None
    staff_id = staff_id.strip()

    # ── Phase 2: database work ─────────────────────────────────────────────

    conn = None
    try:
        conn = get_db()

        # ── Coordinator check ──────────────────────────────────────────────
        staff = conn.execute(
            "SELECT role FROM users WHERE username = ?",
            (staff_id,)
        ).fetchone()
        if staff is None or staff["role"] != "coordinator":
            return False, "Action requires a coordinator account.", None

        # Open transaction AFTER the read-only coordinator check so we hold
        # the write lock for the shortest possible time.
        conn.execute("BEGIN IMMEDIATE")

        max_items, issue_days = _get_policies(conn)

        # ── 1. Student registered ──────────────────────────────────────────
        student = conn.execute(
            "SELECT student_id FROM students WHERE student_id = ?",
            (student_id,)
        ).fetchone()
        if student is None:
            conn.execute("ROLLBACK")
            return False, "Student not found.", None

        # ── 2. Item exists ─────────────────────────────────────────────────
        item = conn.execute(
            "SELECT item_id, name, kind, qty_available FROM items WHERE item_id = ?",
            (item_id,)
        ).fetchone()
        if item is None:
            conn.execute("ROLLBACK")
            return False, "Item not found.", None

        kind         = item["kind"]
        item_name    = item["name"]
        qty_avail    = int(item["qty_available"])

        # ── 3. Asset condition 'usable' (asset kind only) ─────────────────
        if kind == "asset":
            if asset_id is None:
                conn.execute("ROLLBACK")
                return False, "asset_id is required for asset items.", None

            asset = conn.execute(
                "SELECT asset_id, condition, status FROM assets "
                "WHERE asset_id = ? AND item_id = ?",
                (asset_id, item_id)
            ).fetchone()
            if asset is None:
                conn.execute("ROLLBACK")
                return False, "Asset not found or does not belong to this item.", None

            if asset["condition"] != "usable":
                conn.execute("ROLLBACK")
                return (
                    False,
                    f"Asset condition is '{asset['condition']}'; "
                    "only usable assets can be issued.",
                    None,
                )

            qty = 1  # assets are single units

        elif kind == "quantity":
            if asset_id is not None:
                conn.execute("ROLLBACK")
                return False, "asset_id must not be supplied for quantity items.", None

        else:
            conn.execute("ROLLBACK")
            return False, f"Unknown item kind '{kind}'.", None

        # ── 4. qty ≤ qty_available ─────────────────────────────────────────
        if qty > qty_avail:
            conn.execute("ROLLBACK")
            unit = "unit" if qty == 1 else "units"
            return (
                False,
                f"{qty} {item_name} {unit} requested, only {qty_avail} available.",
                None,
            )

        # ── 5. Asset status 'available' (asset kind only) ─────────────────
        if kind == "asset":
            if asset["status"] != "available":
                conn.execute("ROLLBACK")
                return False, "Asset is already issued.", None

        # ── 6. max_items_per_student ───────────────────────────────────────
        open_count = conn.execute(
            "SELECT COUNT(*) FROM issues WHERE student_id = ? AND status = 'open'",
            (student_id,)
        ).fetchone()[0]
        if open_count >= max_items:
            conn.execute("ROLLBACK")
            return (
                False,
                f"Student already has {open_count} open issue(s); "
                f"limit is {max_items}.",
                None,
            )

        # ── Mutations ──────────────────────────────────────────────────────

        # Guarded decrement – rowcount 0 means stock changed under us
        updated = conn.execute(
            "UPDATE items "
            "SET qty_available = qty_available - ? "
            "WHERE item_id = ? AND qty_available >= ?",
            (qty, item_id, qty)
        ).rowcount
        if updated == 0:
            conn.execute("ROLLBACK")
            return False, "Stock changed during the request; please try again.", None

        # Mark asset as issued (asset kind only)
        if kind == "asset":
            conn.execute(
                "UPDATE assets SET status = 'issued' WHERE asset_id = ?",
                (asset_id,)
            )

        # due_date = today + issue_days
        due_date = (today_date + timedelta(days=issue_days)).isoformat()

        # Insert issue row
        conn.execute(
            """
            INSERT INTO issues
                (student_id, item_id, asset_id, qty, purpose,
                 issue_date, due_date, qty_returned, status, issued_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0, 'open', ?)
            """,
            (student_id, item_id, asset_id, qty, purpose, today, due_date, staff_id)
        )

        # Audit log (ISSUE action)
        log_action(
            conn,
            item_id,
            action="ISSUE",
            qty=qty,
            reason=f"Issued to student_id={student_id}; purpose={purpose}",
            log_date=today,
            done_by=staff_id,
        )

        conn.execute("COMMIT")
        return True, "Item issued successfully.", due_date

    except sqlite3.Error as db_exc:
        # SQLite-specific errors: rollback and return a generic message
        _log_exc(db_exc)
        try:
            if conn is not None:
                conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        return False, "A database error occurred. Please try again.", None

    except Exception as exc:
        # Catch-all for unexpected Python errors (programming mistakes, etc.)
        _log_exc(exc)
        try:
            if conn is not None:
                conn.execute("ROLLBACK")
        except Exception:
            pass
        return False, "An unexpected error occurred. Please try again.", None

    finally:
        # Release the connection back to the pool (Flask g) or close it.
        # get_db() in Flask mode caches on g and is closed at request teardown,
        # so calling close() here is safe (g.pop handles double-close).
        # Outside Flask (tests, CLI) we close explicitly.
        if conn is not None:
            try:
                from flask import g
                if g.get("db") is not conn:
                    conn.close()
                # else: Flask will close it via teardown_appcontext
            except RuntimeError:
                # No Flask application context – close directly
                conn.close()
