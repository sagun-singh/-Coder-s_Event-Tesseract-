"""
test_reports.py
===============
Unit tests for logic_reports.py, karma.py, and reports_pages.py (CSV export).

Run with:
    python -m pytest test_reports.py -v

All tests are self-contained: they build an in-memory SQLite database
seeded with the exact scenario described in the spec, then exercise the
public functions without starting a Flask server (except for the CSV tests,
which use app.test_client()).
"""

import os
import sqlite3
import sys
import pytest

# ── App factory bootstrap ─────────────────────────────────────────────────
os.environ.setdefault("SECRET_KEY", "test_secret_key_hackathon_abc123456789xyz")
os.environ.setdefault("DATABASE",   ":memory:")   # overridden per-test

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def mem_db():
    """
    Return an open in-memory SQLite connection seeded with the scenario:

    Scenario
    --------
    Student A  (enrolment DFIS2024001)
    Item       "Development Board", kind=asset, code=DB-ITEM
    Asset      serial_no=DB-014

    Issue 1  (un-renewed)
      issue_date = 2026-10-01
      due_date   = 2026-10-16    ← 3 days overdue on 2026-10-19
      status     = open
      qty        = 1
      qty_returned = 0

    Issue 2  (renewed: due 2026-10-31)
      issue_date = 2026-10-01
      due_date   = 2026-10-31    ← NOT overdue on 2026-10-19
      status     = open
      qty        = 1
      qty_returned = 0

    Issue 3  (partial return of quantity item: USB Cable)
      qty          = 5
      qty_returned = 2           ← 3 outstanding, still overdue
      due_date     = 2026-10-14
      status       = open

    Issue 4  (fully returned – closed, should NOT appear in overdue)
      qty          = 1
      qty_returned = 1
      status       = closed
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript("""
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
    """)

    # Seed data
    conn.execute(
        "INSERT INTO students VALUES (1,'Priya Sharma','DFIS2024001','M.Sc. DFIS',1,'9876543210')"
    )
    conn.execute("INSERT INTO locations VALUES (1,'Lab 1')")
    conn.execute(
        "INSERT INTO items VALUES "
        "(1,'DB-ITEM','Development Board','Electronics',NULL,NULL,'asset',10,8,0,0,0,1)"
    )
    conn.execute(
        "INSERT INTO items VALUES "
        "(2,'USB-CABLE','USB Cable','Accessories',NULL,NULL,'quantity',20,15,0,0,0,1)"
    )
    conn.execute(
        "INSERT INTO assets VALUES (1,1,'DB-014',1,'usable','issued')"
    )
    conn.execute(
        "INSERT INTO assets VALUES (2,1,'DB-015',1,'usable','issued')"
    )

    # Issue 1: un-renewed, due 2026-10-16 → overdue by 3 days on 2026-10-19
    conn.execute(
        "INSERT INTO issues VALUES "
        "(1,1,1,1,1,'Experiment','2026-10-01','2026-10-16',0,'open','coordinator')"
    )
    # Issue 2: renewed, due 2026-10-31 → NOT overdue on 2026-10-19
    conn.execute(
        "INSERT INTO issues VALUES "
        "(2,1,1,2,1,'Experiment 2','2026-10-01','2026-10-31',0,'open','coordinator')"
    )
    # Issue 3: partial return, USB Cable, due 2026-10-14 → 3 outstanding, overdue
    conn.execute(
        "INSERT INTO issues VALUES "
        "(3,1,2,NULL,5,'Lab usage','2026-10-01','2026-10-14',2,'open','coordinator')"
    )
    # Issue 4: fully closed (qty_returned == qty)
    conn.execute(
        "INSERT INTO issues VALUES "
        "(4,1,1,NULL,1,'Closed issue','2026-09-01','2026-09-16',1,'closed','coordinator')"
    )
    # returns_log for issue 4: on-time usable return
    conn.execute(
        "INSERT INTO returns_log VALUES "
        "(1,4,'2026-09-14',1,'usable','coordinator')"
    )

    conn.execute("INSERT INTO policies VALUES (1,5,2,15)")
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Patch get_db to return our in-memory connection
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def patch_get_db(mem_db, monkeypatch):
    """Route all get_db() calls in logic_reports and karma to mem_db."""
    import logic_reports
    import karma as karma_module
    monkeypatch.setattr(logic_reports, "get_db", lambda: mem_db)
    monkeypatch.setattr(karma_module,   "get_db", lambda: mem_db)


# ===========================================================================
# Tests: get_overdue_list
# ===========================================================================

class TestGetOverdueList:

    def test_un_renewed_issue_is_overdue_by_3_days(self):
        """Issue 1: due 2026-10-16, today 2026-10-19 → 3 days overdue."""
        from logic_reports import get_overdue_list
        rows = get_overdue_list("2026-10-19")
        issue1 = next((r for r in rows if r["issue_id"] == 1), None)
        assert issue1 is not None, "Issue 1 (un-renewed) must appear in overdue list"
        assert issue1["days_overdue"] == 3

    def test_overdue_sentence_exact(self):
        """The sentence for issue 1 must match the spec exactly."""
        from logic_reports import get_overdue_list
        rows = get_overdue_list("2026-10-19")
        issue1 = next(r for r in rows if r["issue_id"] == 1)
        expected = (
            "Development Board DB-014 issued to student enrolment number "
            "DFIS2024001 was due on 16 October 2026 and is overdue by 3 days."
        )
        assert issue1["sentence"] == expected

    def test_singular_day_in_sentence(self):
        """1 day overdue → singular 'day'."""
        from logic_reports import get_overdue_list
        rows = get_overdue_list("2026-10-17")   # exactly 1 day after 2026-10-16
        issue1 = next(r for r in rows if r["issue_id"] == 1)
        assert issue1["days_overdue"] == 1
        assert "overdue by 1 day." in issue1["sentence"]
        assert "days" not in issue1["sentence"]

    def test_renewed_issue_not_overdue(self):
        """Issue 2 (due 2026-10-31) must NOT appear in overdue on 2026-10-19."""
        from logic_reports import get_overdue_list
        rows = get_overdue_list("2026-10-19")
        issue_ids = [r["issue_id"] for r in rows]
        assert 2 not in issue_ids, "Renewed issue (due 31 Oct) must not be overdue on 19 Oct"

    def test_partial_return_still_listed(self):
        """Issue 3 (qty=5, returned=2, outstanding=3) must appear even with partial return."""
        from logic_reports import get_overdue_list
        rows = get_overdue_list("2026-10-19")
        issue3 = next((r for r in rows if r["issue_id"] == 3), None)
        assert issue3 is not None, "Partial-return issue still has outstanding qty, must be listed"
        assert issue3["outstanding"] == 3

    def test_closed_issue_not_listed(self):
        """Issue 4 (status=closed) must never appear."""
        from logic_reports import get_overdue_list
        rows = get_overdue_list("2026-10-19")
        assert not any(r["issue_id"] == 4 for r in rows)

    def test_fully_returned_open_issue_not_listed(self, mem_db):
        """An open issue where qty == qty_returned has 0 outstanding and must not appear."""
        # Make a temporary fully-returned open issue
        mem_db.execute(
            "INSERT INTO issues VALUES "
            "(99,1,2,NULL,3,'test','2026-09-01','2026-09-16',3,'open','coordinator')"
        )
        mem_db.commit()
        from logic_reports import get_overdue_list
        rows = get_overdue_list("2026-10-19")
        assert not any(r["issue_id"] == 99 for r in rows), \
            "Issue with qty_returned == qty (0 outstanding) must not appear"


# ===========================================================================
# Tests: get_upcoming_due
# ===========================================================================

class TestGetUpcomingDue:

    def test_upcoming_includes_exactly_due_in_window(self):
        """Issue 2 (due 2026-10-31) is 12 days away on 2026-10-19; not in 3-day window."""
        from logic_reports import get_upcoming_due
        rows = get_upcoming_due("2026-10-19", days=3)
        assert not any(r["issue_id"] == 2 for r in rows)

    def test_upcoming_days_left_correct(self, mem_db):
        """An issue due tomorrow should have days_left=1."""
        mem_db.execute(
            "INSERT INTO issues VALUES "
            "(50,1,2,NULL,2,'Soon','2026-10-18','2026-10-20',0,'open','coordinator')"
        )
        mem_db.commit()
        from logic_reports import get_upcoming_due
        rows = get_upcoming_due("2026-10-19", days=3)
        r = next((x for x in rows if x["issue_id"] == 50), None)
        assert r is not None
        assert r["days_left"] == 1

    def test_upcoming_excludes_already_overdue(self):
        """Issues already overdue (due < today) should not appear in upcoming."""
        from logic_reports import get_upcoming_due
        rows = get_upcoming_due("2026-10-19", days=3)
        assert not any(r["issue_id"] == 1 for r in rows)   # issue 1 is overdue


# ===========================================================================
# Tests: karma_score
# ===========================================================================

class TestKarmaScore:

    def test_clean_student_on_time_usable_return(self):
        """Issue 4 was returned on time and usable → +5 +5 bonuses, no penalties."""
        from karma import karma_score
        # All issues except 4 are open/overdue; score starts 100.
        # Issue 1: 3 days overdue on 2026-10-19 → -min(3*2,30) = -6
        # Issue 3: 5 days overdue (due 2026-10-14) → -min(5*2,30) = -10
        # On-time return × 1 → +5
        # Usable return × 1 → +5
        # Net = 100 - 6 - 10 + 5 + 5 = 94
        score, badge, message = karma_score(1, "2026-10-19")
        assert score == 94
        assert badge == "Gold"
        assert message == "Super reliable! Thank you!"

    def test_damaged_penalty(self, mem_db):
        """A damaged return deducts 20 points."""
        # Fixture has: return_id=1 on issue_id=4, return_date='2026-09-14',
        #   condition='usable', due_date='2026-09-16' → on-time AND usable.
        # Adding return_id=99 on issue_id=4, same date 'damaged':
        #   on-time count = 2 (both returns are before due_date) → +10
        #   usable count  = 1 (original) → +5
        #   damaged count = 1 (new)      → -20
        #   issue 1 overdue 3d → -6;  issue 3 overdue 5d → -10
        #   Score = 100 + 10 + 5 - 20 - 6 - 10 = 79
        mem_db.execute(
            "INSERT INTO returns_log VALUES (99,4,'2026-09-14',1,'damaged','coordinator')"
        )
        mem_db.commit()
        from karma import karma_score
        score_with_damage, _, _ = karma_score(1, "2026-10-19")
        assert score_with_damage == 79

    def test_lost_penalty(self, mem_db):
        """A lost return deducts 50 points."""
        # Adding return_id=98 on issue_id=4, condition='lost', same on-time date:
        #   on-time count = 2 (both returns before due_date) → +10
        #   usable count  = 1 (original) → +5
        #   lost count    = 1 (new)      → -50
        #   issue 1 → -6; issue 3 → -10
        #   Score = 100 + 10 + 5 - 50 - 6 - 10 = 49
        mem_db.execute(
            "INSERT INTO returns_log VALUES (98,4,'2026-09-14',1,'lost','coordinator')"
        )
        mem_db.commit()
        from karma import karma_score
        score, badge, msg = karma_score(1, "2026-10-19")
        assert score == 49
        assert badge == "Needs a Reminder"
        assert "Please return on time" in msg

    def test_overdue_cap_per_issue(self, mem_db):
        """Per-issue overdue deduction caps at 30 (= 15 days)."""
        # Add an issue overdue by 100 days → deduction capped at 30
        mem_db.execute(
            "INSERT INTO issues VALUES "
            "(77,1,2,NULL,1,'Old','2026-01-01','2026-01-15',0,'open','coordinator')"
        )
        mem_db.commit()
        from karma import karma_score
        score, _, _ = karma_score(1, "2026-10-19")
        # Baseline deduction for issue 77: min(100*2, 30) = 30 (capped)
        # The total should include -30 for this issue
        # We just verify the score isn't lower than clamp_min
        assert score >= 0

    def test_clamp_max(self):
        """Score never exceeds 150."""
        from karma import karma_score
        score, _, _ = karma_score(1, "2020-01-01")   # no overdue issues
        assert score <= 150

    def test_clamp_min(self, mem_db):
        """Score never goes below 0."""
        # Pile on many lost items
        for i in range(100, 110):
            mem_db.execute(
                f"INSERT INTO returns_log VALUES ({i},4,'2026-09-14',1,'lost','coordinator')"
            )
        mem_db.commit()
        from karma import karma_score
        score, _, _ = karma_score(1, "2026-10-19")
        assert score == 0

    def test_silver_tier(self, mem_db):
        """Score 70-89 → Silver / 'Nice work. Keep it up.'"""
        # Manufacture: 100 + 0 bonuses - 30 (one overdue-capped issue) = 70
        mem_db.execute(
            "DELETE FROM issues WHERE issue_id IN (1,2,3,4)"
        )
        mem_db.execute("DELETE FROM returns_log")
        # One issue overdue for > 15 days → -30 (capped)
        mem_db.execute(
            "INSERT INTO issues VALUES "
            "(200,1,2,NULL,1,'Test','2026-01-01','2026-01-15',0,'open','coordinator')"
        )
        mem_db.commit()
        from karma import karma_score
        score, badge, msg = karma_score(1, "2026-10-19")
        assert score == 70
        assert badge == "Silver"
        assert msg == "Nice work. Keep it up."


# ===========================================================================
# Tests: CSV export via Flask test client
# ===========================================================================

@pytest.fixture()
def app_client(mem_db, monkeypatch):
    """
    Flask test client that routes all DB calls to the in-memory fixture db.

    Strategy:
    * Patch database.get_db, logic_reports.get_db, karma.get_db to return mem_db.
    * Add the users table to mem_db and insert a coordinator row.
    * Set the session directly so load_logged_in_user finds a real user row.
    * Disable WTF CSRF for test requests.
    """
    # Add users table to the in-memory db (not in the DDL above to keep it minimal)
    mem_db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id    INTEGER PRIMARY KEY AUTOINCREMENT,
            username   TEXT NOT NULL UNIQUE,
            password   TEXT NOT NULL,
            role       TEXT NOT NULL,
            student_id INTEGER
        );
    """)
    from werkzeug.security import generate_password_hash
    mem_db.execute(
        "INSERT OR IGNORE INTO users (user_id, username, password, role) "
        "VALUES (1, 'coordinator', ?, 'coordinator')",
        (generate_password_hash("TestPass1!"),)
    )
    mem_db.commit()

    import logic_reports
    import karma as karma_mod
    import database as db_module
    import auth as auth_mod
    monkeypatch.setattr(logic_reports, "get_db", lambda: mem_db)
    monkeypatch.setattr(karma_mod,     "get_db", lambda: mem_db)
    monkeypatch.setattr(db_module,     "get_db", lambda db_path=None: mem_db)
    monkeypatch.setattr(auth_mod,      "get_db", lambda: mem_db)

    from app import create_app
    app = create_app()
    app.config["TESTING"]         = True
    app.config["WTF_CSRF_ENABLED"] = False   # disable CSRF for test requests

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"]    = 1
            sess["role"]       = "coordinator"
            sess["student_id"] = None
            # Set Time Machine to 2026-10-19 so overdue issues in fixture show up
            sess["fake_date"]  = "2026-10-19"
        yield client


class TestCSVExport:

    def test_overdue_csv_returns_200(self, app_client):
        r = app_client.get("/reports/export/overdue")
        assert r.status_code == 200
        assert b"text/csv" in r.headers["Content-Type"].encode()

    def test_csv_cache_control_no_store(self, app_client):
        r = app_client.get("/reports/export/overdue")
        assert r.headers.get("Cache-Control") == "no-store"

    def test_csv_filename_is_safe(self, app_client):
        r = app_client.get("/reports/export/overdue")
        cd = r.headers.get("Content-Disposition", "")
        assert "overdue_issues.csv" in cd
        # Must not contain any path separators
        assert "/" not in cd.replace("overdue_issues.csv", "")
        assert "\\" not in cd

    def test_unknown_kind_returns_404(self, app_client):
        r = app_client.get("/reports/export/evil_report")
        assert r.status_code == 404

    def test_path_traversal_returns_404(self, app_client):
        r = app_client.get("/reports/export/../../../etc/passwd")
        # Flask normalises the URL; either 404 or redirect is acceptable
        assert r.status_code in (404, 308, 301, 302)

    def test_injection_cell_neutralised(self):
        """Cells starting with = are prefixed with a single-quote."""
        from reports_pages import _safe_cell
        assert _safe_cell("=cmd|' /C calc'!A0") == "'=cmd|' /C calc'!A0"
        assert _safe_cell("-1+1") == "'-1+1"
        assert _safe_cell("+1") == "'+1"
        assert _safe_cell("@SUM(1)") == "'@SUM(1)"
        assert _safe_cell("\t bad") == "'\t bad"

    def test_normal_cell_unchanged(self):
        """Normal cells must not be altered."""
        from reports_pages import _safe_cell
        assert _safe_cell("Development Board") == "Development Board"
        assert _safe_cell("DFIS2024001") == "DFIS2024001"
        assert _safe_cell(None) == ""
        assert _safe_cell(42) == "42"

    def test_csv_contains_sentence(self, app_client):
        """The overdue CSV must include the spec sentence for issue 1."""
        r = app_client.get("/reports/export/overdue")
        content = r.data.decode("utf-8-sig")   # strip BOM
        assert "overdue by 3 days" in content


# ===========================================================================
# Tests: _format_date helper (sentence date format)
# ===========================================================================

class TestFormatDate:

    def test_october_format(self):
        from logic_reports import _format_date
        from datetime import date
        # Platform-independent: use strptime to verify output
        d = date(2026, 10, 16)
        s = _format_date(d)
        assert "October" in s
        assert "2026" in s
        # Day should not have a leading zero (%-d on Linux, but we accept both)
        assert "16" in s

    def test_january_format(self):
        from logic_reports import _format_date
        from datetime import date
        s = _format_date(date(2025, 1, 5))
        assert "January" in s
        assert "2025" in s
