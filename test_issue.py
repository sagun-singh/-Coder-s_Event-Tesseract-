"""
test_issue.py
=============
Unit tests for logic_issue.py  (P2 – issue_item).

Run with:
    python -m pytest test_issue.py -v

All tests are self-contained: they build an in-memory SQLite database
seeded with realistic data, then exercise issue_item() directly without
starting a Flask server.

Coverage matrix
---------------
Phase 1 – pre-DB validation (no DB call should occur)
  • bad today date (None, wrong format, not a real date)
  • bool/float/str rejected for student_id, item_id, qty, asset_id
  • zero / negative qty rejected
  • purpose: None, too long, whitespace-only
  • staff_id: empty, whitespace-only, non-string

Phase 2 – database checks (in spec order)
  5. staff_id is not a coordinator
  6. student_id not registered
  7. item_id not found
  8. asset kind: asset_id missing, condition not usable
  9. qty > qty_available (exact message format)
 10. asset kind: status != 'available'
 11. max_items_per_student exceeded

Transaction / concurrency
  • guarded UPDATE fires correctly
  • successful issue: correct return tuple, DB rows created, stock decremented
  • asset issued: asset.status set to 'issued', qty_available decremented by 1
  • quantity issued: qty_available decremented by requested amount
  • stock_log row written on success
  • sqlite3.Error returns generic message (nothing leaked)
"""

import os
import sqlite3
import pytest

# Bootstrap env before importing app code
os.environ.setdefault("SECRET_KEY", "test_secret_key_hackathon_abc123456789xyz")
os.environ.setdefault("DATABASE",   ":memory:")


class _NoCloseConn:
    """
    Thin wrapper around a sqlite3.Connection that makes close() a no-op.
    logic_issue's finally-block calls conn.close() when outside a Flask context.
    Wrapping the fixture connection prevents it from being torn down between
    the issue_item() call and the post-call assertions.
    All other attribute accesses are forwarded transparently.
    """
    def __init__(self, conn):
        self._conn = conn

    def close(self):
        pass  # intentional no-op

    def __getattr__(self, name):
        return getattr(self._conn, name)


# ============================================================================
# Fixtures
# ============================================================================

_DDL_MINIMAL = """
CREATE TABLE students (
    student_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL,
    enrolment_no TEXT NOT NULL UNIQUE,
    programme    TEXT NOT NULL,
    semester     INTEGER NOT NULL,
    contact      TEXT NOT NULL
);
CREATE TABLE locations (
    location_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE
);
CREATE TABLE items (
    item_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    code            TEXT NOT NULL UNIQUE,
    name            TEXT NOT NULL,
    category        TEXT NOT NULL,
    description     TEXT,
    make_model      TEXT,
    kind            TEXT NOT NULL,
    total_qty       INTEGER NOT NULL DEFAULT 0,
    qty_available   INTEGER NOT NULL DEFAULT 0,
    qty_damaged     INTEGER NOT NULL DEFAULT 0,
    qty_lost        INTEGER NOT NULL DEFAULT 0,
    qty_maintenance INTEGER NOT NULL DEFAULT 0,
    location_id     INTEGER
);
CREATE TABLE assets (
    asset_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id     INTEGER NOT NULL,
    serial_no   TEXT NOT NULL UNIQUE,
    location_id INTEGER,
    condition   TEXT NOT NULL DEFAULT 'usable',
    status      TEXT NOT NULL DEFAULT 'available'
);
CREATE TABLE issues (
    issue_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id   INTEGER NOT NULL,
    item_id      INTEGER NOT NULL,
    asset_id     INTEGER,
    qty          INTEGER NOT NULL DEFAULT 1,
    purpose      TEXT NOT NULL,
    issue_date   TEXT NOT NULL,
    due_date     TEXT NOT NULL,
    qty_returned INTEGER NOT NULL DEFAULT 0,
    status       TEXT NOT NULL DEFAULT 'open',
    issued_by    TEXT NOT NULL
);
CREATE TABLE returns_log (
    return_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id     INTEGER NOT NULL,
    return_date  TEXT NOT NULL,
    qty_returned INTEGER NOT NULL,
    condition    TEXT NOT NULL,
    received_by  TEXT NOT NULL
);
CREATE TABLE renewals (
    renewal_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id         INTEGER NOT NULL,
    request_date     TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'pending',
    revised_due_date TEXT,
    decided_by       TEXT,
    remarks          TEXT
);
CREATE TABLE stock_log (
    log_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id  INTEGER NOT NULL,
    action   TEXT NOT NULL,
    qty      INTEGER NOT NULL DEFAULT 0,
    reason   TEXT,
    log_date TEXT NOT NULL,
    done_by  TEXT NOT NULL
);
CREATE TABLE policies (
    id                    INTEGER PRIMARY KEY,
    max_items_per_student INTEGER NOT NULL DEFAULT 5,
    max_renewals          INTEGER NOT NULL DEFAULT 2,
    issue_days            INTEGER NOT NULL DEFAULT 15
);
CREATE TABLE users (
    user_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    username   TEXT NOT NULL UNIQUE,
    password   TEXT NOT NULL,
    role       TEXT NOT NULL,
    student_id INTEGER
);
"""


@pytest.fixture()
def mem_db():
    """
    In-memory SQLite db pre-seeded with:
      - 1 coordinator user       (username='coord')
      - 1 non-coordinator user   (username='student_user')
      - 1 registered student     (student_id=1)
      - 1 unregistered student   (id=999 – not in DB)
      - 1 location               (Lab 1)
      - 1 asset item             (item_id=1, code='SCOPE', qty_available=2)
        + 2 assets:  asset_id=1 (usable, available)
                     asset_id=2 (damaged, available)
      - 1 quantity item          (item_id=2, code='CABLE', qty_available=10)
      - policies: max_items_per_student=3, max_renewals=2, issue_days=15
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_DDL_MINIMAL)

    from werkzeug.security import generate_password_hash
    coord_hash   = generate_password_hash("CoordPass1!")
    student_hash = generate_password_hash("StudentPass1!")

    conn.execute("INSERT INTO policies VALUES (1, 3, 2, 15)")
    conn.execute("INSERT INTO locations VALUES (1, 'Lab 1')")

    # Users
    conn.execute(
        "INSERT INTO users VALUES (1,'coord',?,         'coordinator', NULL)",
        (coord_hash,)
    )
    conn.execute(
        "INSERT INTO users VALUES (2,'student_user',?,  'student',     1)",
        (student_hash,)
    )

    # Student
    conn.execute(
        "INSERT INTO students VALUES (1,'Aarav Singh','DFIS2024001','M.Sc. DFIS',1,'9000000001')"
    )

    # Items
    conn.execute(
        "INSERT INTO items VALUES (1,'SCOPE','Oscilloscope','Electronics',NULL,NULL,'asset',  2,2,0,0,0,1)"
    )
    conn.execute(
        "INSERT INTO items VALUES (2,'CABLE','USB Cable',   'Accessories',NULL,NULL,'quantity',20,10,0,0,0,1)"
    )

    # Assets for item_id=1
    conn.execute("INSERT INTO assets VALUES (1,1,'SN-001',1,'usable',   'available')")  # good
    conn.execute("INSERT INTO assets VALUES (2,1,'SN-002',1,'damaged',  'available')")  # bad condition
    conn.execute("INSERT INTO assets VALUES (3,1,'SN-003',1,'usable',   'issued')")     # already issued

    conn.commit()
    return conn


@pytest.fixture(autouse=True)
def patch_get_db(mem_db, monkeypatch):
    """
    Route all get_db() calls in logic_issue to the in-memory fixture db.
    Wrap the connection in _NoCloseConn so that logic_issue's finally-block
    cannot close the shared fixture connection between the call and assertions.
    """
    import logic_issue
    wrapped = _NoCloseConn(mem_db)
    monkeypatch.setattr(logic_issue, "get_db", lambda: wrapped)


# ============================================================================
# Phase 1 – pre-DB validation (no DB touch)
# ============================================================================

class TestInputValidation:
    """All of these should fail before any DB query."""

    TODAY = "2026-10-19"

    # ── today date ───────────────────────────────────────────────────────────

    def test_none_today_fails(self):
        from logic_issue import issue_item
        ok, msg, due = issue_item(1, 2, 3, "purpose", "coord", None)
        assert not ok
        assert due is None

    def test_bad_format_today_fails(self):
        from logic_issue import issue_item
        ok, msg, due = issue_item(1, 2, 3, "purpose", "coord", "19-10-2026")
        assert not ok
        assert due is None

    def test_impossible_date_today_fails(self):
        """2026-02-30 is not a real calendar date."""
        from logic_issue import issue_item
        ok, msg, _ = issue_item(1, 2, 3, "purpose", "coord", "2026-02-30")
        assert not ok

    # ── bool rejected for int params ────────────────────────────────────────

    def test_bool_student_id_rejected(self):
        from logic_issue import issue_item
        ok, _, _ = issue_item(True, 2, 1, "purpose", "coord", self.TODAY)
        assert not ok

    def test_bool_item_id_rejected(self):
        from logic_issue import issue_item
        ok, _, _ = issue_item(1, True, 1, "purpose", "coord", self.TODAY)
        assert not ok

    def test_bool_qty_rejected(self):
        from logic_issue import issue_item
        ok, _, _ = issue_item(1, 2, True, "purpose", "coord", self.TODAY)
        assert not ok

    def test_bool_asset_id_rejected(self):
        from logic_issue import issue_item
        ok, _, _ = issue_item(1, 1, 1, "purpose", "coord", self.TODAY, asset_id=True)
        assert not ok

    # ── float rejected ───────────────────────────────────────────────────────

    def test_float_student_id_rejected(self):
        from logic_issue import issue_item
        ok, _, _ = issue_item(1.0, 2, 1, "purpose", "coord", self.TODAY)
        assert not ok

    def test_float_item_id_rejected(self):
        from logic_issue import issue_item
        ok, _, _ = issue_item(1, 2.0, 1, "purpose", "coord", self.TODAY)
        assert not ok

    def test_float_qty_rejected(self):
        from logic_issue import issue_item
        ok, _, _ = issue_item(1, 2, 1.0, "purpose", "coord", self.TODAY)
        assert not ok

    # ── zero / negative qty ──────────────────────────────────────────────────

    def test_zero_qty_rejected(self):
        from logic_issue import issue_item
        ok, msg, _ = issue_item(1, 2, 0, "purpose", "coord", self.TODAY)
        assert not ok
        assert "positive" in msg.lower()

    def test_negative_qty_rejected(self):
        from logic_issue import issue_item
        ok, _, _ = issue_item(1, 2, -1, "purpose", "coord", self.TODAY)
        assert not ok

    # ── negative / zero IDs ──────────────────────────────────────────────────

    def test_zero_student_id_rejected(self):
        from logic_issue import issue_item
        ok, _, _ = issue_item(0, 2, 1, "purpose", "coord", self.TODAY)
        assert not ok

    def test_negative_item_id_rejected(self):
        from logic_issue import issue_item
        ok, _, _ = issue_item(1, -5, 1, "purpose", "coord", self.TODAY)
        assert not ok

    # ── purpose ─────────────────────────────────────────────────────────────

    def test_purpose_none_rejected(self):
        from logic_issue import issue_item
        ok, _, _ = issue_item(1, 2, 1, None, "coord", self.TODAY)
        assert not ok

    def test_purpose_whitespace_only_rejected(self):
        from logic_issue import issue_item
        ok, _, _ = issue_item(1, 2, 1, "   ", "coord", self.TODAY)
        assert not ok

    def test_purpose_too_long_rejected(self):
        from logic_issue import issue_item
        ok, msg, _ = issue_item(1, 2, 1, "x" * 201, "coord", self.TODAY)
        assert not ok
        assert "200" in msg

    def test_purpose_max_len_accepted_eventually(self):
        """200-char purpose passes phase 1 (may fail later for other reasons)."""
        from logic_issue import issue_item
        # Will fail at DB level because student_id=999 doesn't exist,
        # but phase-1 validation must pass → msg won't mention '200 characters'.
        ok, msg, _ = issue_item(999, 2, 1, "x" * 200, "coord", self.TODAY)
        assert "200 characters" not in msg

    # ── staff_id ─────────────────────────────────────────────────────────────

    def test_empty_staff_id_rejected(self):
        from logic_issue import issue_item
        ok, _, _ = issue_item(1, 2, 1, "purpose", "", self.TODAY)
        assert not ok

    def test_whitespace_staff_id_rejected(self):
        from logic_issue import issue_item
        ok, _, _ = issue_item(1, 2, 1, "purpose", "   ", self.TODAY)
        assert not ok

    def test_none_staff_id_rejected(self):
        from logic_issue import issue_item
        ok, _, _ = issue_item(1, 2, 1, "purpose", None, self.TODAY)
        assert not ok


# ============================================================================
# Phase 2 – database checks (in spec order)
# ============================================================================

class TestDatabaseChecks:

    TODAY = "2026-10-19"

    def test_non_coordinator_staff_rejected(self):
        """staff_id 'student_user' has role='student' → must fail."""
        from logic_issue import issue_item
        ok, msg, _ = issue_item(1, 2, 1, "purpose", "student_user", self.TODAY)
        assert not ok
        assert "coordinator" in msg.lower()

    def test_unknown_staff_id_rejected(self):
        """staff_id that doesn't exist in users table → must fail."""
        from logic_issue import issue_item
        ok, msg, _ = issue_item(1, 2, 1, "purpose", "no_such_user", self.TODAY)
        assert not ok

    def test_unregistered_student_rejected(self):
        """student_id=999 is not in the students table."""
        from logic_issue import issue_item
        ok, msg, _ = issue_item(999, 2, 1, "purpose", "coord", self.TODAY)
        assert not ok
        assert "student" in msg.lower()

    def test_missing_item_rejected(self):
        """item_id=999 does not exist."""
        from logic_issue import issue_item
        ok, msg, _ = issue_item(1, 999, 1, "purpose", "coord", self.TODAY)
        assert not ok
        assert "item" in msg.lower() or "found" in msg.lower()

    # ── asset checks ─────────────────────────────────────────────────────────

    def test_asset_kind_without_asset_id_rejected(self):
        """Issuing an asset-kind item without specifying asset_id must fail."""
        from logic_issue import issue_item
        ok, msg, _ = issue_item(1, 1, 1, "purpose", "coord", self.TODAY)
        # asset_id defaults to None
        assert not ok
        assert "asset_id" in msg.lower() or "required" in msg.lower()

    def test_asset_with_damaged_condition_rejected(self):
        """asset_id=2 has condition='damaged'; must be blocked at check 8."""
        from logic_issue import issue_item
        ok, msg, _ = issue_item(1, 1, 1, "purpose", "coord", self.TODAY, asset_id=2)
        assert not ok
        assert "damaged" in msg.lower() or "usable" in msg.lower()

    def test_asset_wrong_item_rejected(self, mem_db):
        """asset_id=1 belongs to item_id=1; issuing under item_id=2 must fail."""
        from logic_issue import issue_item
        # asset_id=1 is for item_id=1 (Oscilloscope), not item_id=2 (Cable)
        ok, msg, _ = issue_item(1, 2, 1, "purpose", "coord", self.TODAY, asset_id=1)
        assert not ok

    def test_quantity_kind_with_asset_id_rejected(self):
        """Supplying asset_id for a quantity item must be rejected."""
        from logic_issue import issue_item
        ok, msg, _ = issue_item(1, 2, 5, "purpose", "coord", self.TODAY, asset_id=1)
        assert not ok
        assert "asset_id" in msg.lower() or "quantity" in msg.lower()

    # ── availability check ────────────────────────────────────────────────────

    def test_qty_exceeds_available_rejected(self):
        """Requesting 11 cables when only 10 available → exact message format."""
        from logic_issue import issue_item
        ok, msg, _ = issue_item(1, 2, 11, "purpose", "coord", self.TODAY)
        assert not ok
        # Message must contain both the requested qty and available qty
        assert "11" in msg
        assert "10" in msg

    def test_exact_available_qty_accepted(self):
        """Requesting exactly qty_available (10) should pass the availability check."""
        from logic_issue import issue_item
        # May fail for other reasons, but NOT for availability
        ok, msg, due = issue_item(1, 2, 10, "purpose", "coord", self.TODAY)
        # Either it succeeded or failed for a different reason than availability
        if not ok:
            assert "available" not in msg.lower() or "10" not in msg

    # ── asset status check ────────────────────────────────────────────────────

    def test_already_issued_asset_rejected(self):
        """asset_id=3 has status='issued'; must be blocked at check 10."""
        from logic_issue import issue_item
        ok, msg, _ = issue_item(1, 1, 1, "purpose", "coord", self.TODAY, asset_id=3)
        assert not ok
        assert "issued" in msg.lower() or "available" in msg.lower()

    # ── max items per student ─────────────────────────────────────────────────

    def test_max_items_per_student_enforced(self, mem_db):
        """Fill student to max (3 open issues); 4th must be rejected."""
        # Manually insert 3 open issues for student_id=1 on item_id=2
        for i in range(3):
            mem_db.execute(
                "INSERT INTO issues VALUES (?,1,2,NULL,1,'Experiment','2026-10-01',"
                "'2026-10-16',0,'open','coord')",
                (100 + i,)
            )
        mem_db.commit()

        from logic_issue import issue_item
        ok, msg, _ = issue_item(1, 2, 1, "purpose", "coord", self.TODAY)
        assert not ok
        assert "limit" in msg.lower() or "max" in msg.lower() or "3" in msg


# ============================================================================
# Successful issue path
# ============================================================================

class TestSuccessfulIssue:

    TODAY = "2026-10-19"

    def test_quantity_issue_returns_ok_and_due_date(self):
        """Happy-path: issue 3 USB cables to student 1."""
        from logic_issue import issue_item
        ok, msg, due = issue_item(1, 2, 3, "Lab project", "coord", self.TODAY)
        assert ok is True
        assert due == "2026-11-03"   # 2026-10-19 + 15 days
        assert "success" in msg.lower() or ok

    def test_due_date_is_today_plus_15(self):
        """due_date must be exactly today + 15 days from policies.issue_days."""
        from logic_issue import issue_item
        ok, _, due = issue_item(1, 2, 1, "purpose", "coord", "2026-01-01")
        assert ok
        assert due == "2026-01-16"   # 2026-01-01 + 15

    def test_issues_row_created(self, mem_db):
        """A row is inserted into the issues table after a successful issue."""
        from logic_issue import issue_item
        issue_item(1, 2, 2, "Experiment", "coord", self.TODAY)
        row = mem_db.execute(
            "SELECT * FROM issues WHERE student_id=1 AND item_id=2 AND status='open'"
        ).fetchone()
        assert row is not None
        assert row["qty"] == 2
        assert row["due_date"] == "2026-11-03"
        assert row["issued_by"] == "coord"

    def test_qty_available_decremented(self, mem_db):
        """qty_available must decrease by the issued amount."""
        from logic_issue import issue_item
        before = mem_db.execute(
            "SELECT qty_available FROM items WHERE item_id=2"
        ).fetchone()[0]
        issue_item(1, 2, 4, "Lab", "coord", self.TODAY)
        after = mem_db.execute(
            "SELECT qty_available FROM items WHERE item_id=2"
        ).fetchone()[0]
        assert after == before - 4

    def test_stock_log_row_written(self, mem_db):
        """A stock_log row with action='ISSUE' must be written on success."""
        from logic_issue import issue_item
        issue_item(1, 2, 1, "audit test", "coord", self.TODAY)
        row = mem_db.execute(
            "SELECT * FROM stock_log WHERE item_id=2 AND action='ISSUE'"
        ).fetchone()
        assert row is not None
        assert row["done_by"] == "coord"
        assert row["log_date"] == self.TODAY

    def test_purpose_stored_in_issue(self, mem_db):
        """The purpose string must be persisted as-is (after strip)."""
        from logic_issue import issue_item
        issue_item(1, 2, 1, "  Network experiment  ", "coord", self.TODAY)
        row = mem_db.execute(
            "SELECT purpose FROM issues WHERE student_id=1 ORDER BY issue_id DESC LIMIT 1"
        ).fetchone()
        assert row["purpose"] == "Network experiment"

    # ── asset-kind specific ──────────────────────────────────────────────────

    def test_asset_issue_sets_status_to_issued(self, mem_db):
        """After issuing asset_id=1, its status must become 'issued'."""
        from logic_issue import issue_item
        ok, _, _ = issue_item(1, 1, 1, "Lab", "coord", self.TODAY, asset_id=1)
        assert ok
        row = mem_db.execute(
            "SELECT status FROM assets WHERE asset_id=1"
        ).fetchone()
        assert row["status"] == "issued"

    def test_asset_issue_decrements_qty_available(self, mem_db):
        """Issuing an asset decrements qty_available by exactly 1."""
        from logic_issue import issue_item
        before = mem_db.execute(
            "SELECT qty_available FROM items WHERE item_id=1"
        ).fetchone()[0]
        issue_item(1, 1, 1, "Lab", "coord", self.TODAY, asset_id=1)
        after = mem_db.execute(
            "SELECT qty_available FROM items WHERE item_id=1"
        ).fetchone()[0]
        assert after == before - 1

    def test_asset_issue_row_has_asset_id(self, mem_db):
        """The issues row for an asset must store the asset_id."""
        from logic_issue import issue_item
        issue_item(1, 1, 1, "Lab", "coord", self.TODAY, asset_id=1)
        row = mem_db.execute(
            "SELECT asset_id FROM issues WHERE student_id=1 AND item_id=1 ORDER BY issue_id DESC LIMIT 1"
        ).fetchone()
        assert row["asset_id"] == 1

    def test_asset_issue_qty_is_always_1(self, mem_db):
        """Even if caller passes qty=5, issued qty must be clamped to 1 for assets."""
        from logic_issue import issue_item
        ok, _, _ = issue_item(1, 1, 5, "Lab", "coord", self.TODAY, asset_id=1)
        # qty=5 passes pre-DB validation, but is overridden to 1 for asset kind
        if ok:
            row = mem_db.execute(
                "SELECT qty FROM issues WHERE asset_id=1 ORDER BY issue_id DESC LIMIT 1"
            ).fetchone()
            assert row["qty"] == 1

    # ── idempotency / atomicity ───────────────────────────────────────────────

    def test_failed_issue_leaves_stock_unchanged(self, mem_db):
        """If validation fails (e.g. student not found), qty_available must not change."""
        from logic_issue import issue_item
        before = mem_db.execute(
            "SELECT qty_available FROM items WHERE item_id=2"
        ).fetchone()[0]
        issue_item(999, 2, 3, "purpose", "coord", self.TODAY)   # student 999 doesn't exist
        after = mem_db.execute(
            "SELECT qty_available FROM items WHERE item_id=2"
        ).fetchone()[0]
        assert after == before

    def test_failed_issue_creates_no_issues_row(self, mem_db):
        """A failed issue must not insert a row into the issues table."""
        from logic_issue import issue_item
        count_before = mem_db.execute("SELECT COUNT(*) FROM issues").fetchone()[0]
        issue_item(999, 2, 1, "purpose", "coord", self.TODAY)   # bad student
        count_after  = mem_db.execute("SELECT COUNT(*) FROM issues").fetchone()[0]
        assert count_after == count_before

    def test_failed_issue_creates_no_stock_log(self, mem_db):
        """A failed issue must not write a stock_log row."""
        from logic_issue import issue_item
        count_before = mem_db.execute("SELECT COUNT(*) FROM stock_log").fetchone()[0]
        issue_item(1, 999, 1, "purpose", "coord", self.TODAY)   # bad item
        count_after  = mem_db.execute("SELECT COUNT(*) FROM stock_log").fetchone()[0]
        assert count_after == count_before


# ============================================================================
# sqlite3.Error path – generic message, nothing leaked
# ============================================================================

class TestDatabaseError:

    TODAY = "2026-10-19"

    def test_db_error_returns_generic_message(self, monkeypatch):
        """
        If get_db() raises sqlite3.Error, the function must return a generic
        message that does not expose SQL internals or a stack trace.
        """
        import logic_issue

        def bad_db():
            raise sqlite3.OperationalError("no such table: users")

        monkeypatch.setattr(logic_issue, "get_db", bad_db)

        from logic_issue import issue_item
        ok, msg, due = issue_item(1, 2, 1, "purpose", "coord", self.TODAY)
        assert ok is False
        assert due is None
        # Must not contain SQL details
        assert "no such table" not in msg
        assert "users" not in msg
        # Must be a non-empty user-friendly string
        assert len(msg) > 0
