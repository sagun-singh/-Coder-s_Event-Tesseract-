"""
P1 – database.py
================
Provides:
  get_db()         – per-request SQLite connection (Flask g, Row factory,
                     foreign_keys ON, 5-second busy timeout)
  init_db()        – create every table idempotently (inline DDL, no .sql file)
  seed_data()      – idempotent demo data (policies, locations, items, assets,
                     students, users with HASHED passwords from env vars)
  log_action()     – append one audit row to stock_log
  add_student()    – validated helper; raises ValueError on bad input
  add_item()       – validated helper
  add_asset()      – validated helper
  add_location()   – validated helper
  init_app(app)    – register Flask teardown + 'flask init-db' CLI command

Running this file directly:
  python database.py
  → creates inventory.db next to the file, creates tables, seeds demo data.

⚠  Add inventory.db to your .gitignore!
"""

import os
import sys
import sqlite3
import warnings

# werkzeug is available because Flask depends on it
from werkzeug.security import generate_password_hash


# ---------------------------------------------------------------------------
# Path to the database file (always next to this source file)
# ---------------------------------------------------------------------------

_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "inventory.db")


# ---------------------------------------------------------------------------
# Enum constants (mirror CHECK constraints – single source of truth)
# ---------------------------------------------------------------------------

ROLES       = ("student", "coordinator")
KINDS       = ("asset", "quantity")
CONDITIONS  = ("usable", "damaged", "lost", "maintenance")
ASSET_STATUS = ("available", "issued")
ISSUE_STATUS = ("open", "closed")
RENEWAL_STATUS = ("pending", "approved", "rejected")


# ============================================================================
# get_db()  –  two modes:
#   1. Inside a Flask request context  → stored on Flask g (one per request)
#   2. Outside Flask (seed script, tests) → plain connection, caller manages it
# ============================================================================

def get_db(db_path: str | None = None):
    """
    Return an open sqlite3 connection.

    • Inside a Flask request context the connection is cached on g and closed
      automatically at request teardown.
    • Outside Flask (e.g. direct execution) a fresh connection is returned;
      the caller is responsible for closing it.

    Both modes:
      - row_factory = sqlite3.Row  (row["col"] syntax)
      - PRAGMA foreign_keys = ON
      - busy_timeout = 5000 ms    (waits up to 5 s on a locked database)
    """
    # ── Flask path ────────────────────────────────────────────────────────
    try:
        from flask import g, current_app
        if "db" not in g:
            path = db_path or current_app.config.get("DATABASE", _DB_PATH)
            conn = sqlite3.connect(path, detect_types=sqlite3.PARSE_DECLTYPES)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA busy_timeout = 5000")
            g.db = conn
        return g.db
    except RuntimeError:
        pass  # No Flask application context → fall through

    # ── Standalone path ───────────────────────────────────────────────────
    path = db_path or _DB_PATH
    conn = sqlite3.connect(path, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def close_db(e=None):
    """Flask teardown: close the per-request connection."""
    try:
        from flask import g
        db = g.pop("db", None)
        if db is not None:
            db.close()
    except RuntimeError:
        pass  # Outside request context – nothing to close


# ============================================================================
# init_db()  –  create all tables (IF NOT EXISTS, so safe to call repeatedly)
# ============================================================================

_DDL = """
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout  = 5000;

-- ── Students ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS students (
    student_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT    NOT NULL CHECK(length(trim(name))    > 0),
    enrolment_no  TEXT    NOT NULL UNIQUE CHECK(length(trim(enrolment_no)) > 0),
    programme     TEXT    NOT NULL CHECK(length(trim(programme)) > 0),
    semester      INTEGER NOT NULL CHECK(semester BETWEEN 1 AND 12),
    contact       TEXT    NOT NULL CHECK(length(trim(contact))  > 0)
);

-- ── Users ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    user_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    username   TEXT    NOT NULL UNIQUE CHECK(length(trim(username)) > 0),
    password   TEXT    NOT NULL,          -- werkzeug pbkdf2 hash; never plain-text
    role       TEXT    NOT NULL CHECK(role IN ('student','coordinator')),
    student_id INTEGER REFERENCES students(student_id) ON DELETE SET NULL
);

-- ── Locations ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS locations (
    location_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL UNIQUE CHECK(length(trim(name)) > 0)
);

-- ── Items ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS items (
    item_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    code            TEXT    NOT NULL UNIQUE CHECK(length(trim(code)) > 0),
    name            TEXT    NOT NULL CHECK(length(trim(name))        > 0),
    category        TEXT    NOT NULL CHECK(length(trim(category))    > 0),
    description     TEXT,
    make_model      TEXT,
    kind            TEXT    NOT NULL CHECK(kind IN ('asset','quantity')),
    total_qty       INTEGER NOT NULL DEFAULT 0  CHECK(total_qty       >= 0),
    qty_available   INTEGER NOT NULL DEFAULT 0  CHECK(qty_available   >= 0),
    qty_damaged     INTEGER NOT NULL DEFAULT 0  CHECK(qty_damaged     >= 0),
    qty_lost        INTEGER NOT NULL DEFAULT 0  CHECK(qty_lost        >= 0),
    qty_maintenance INTEGER NOT NULL DEFAULT 0  CHECK(qty_maintenance >= 0),
    location_id     INTEGER REFERENCES locations(location_id)
);

-- ── Assets ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS assets (
    asset_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id     INTEGER NOT NULL REFERENCES items(item_id) ON DELETE CASCADE,
    serial_no   TEXT    NOT NULL UNIQUE CHECK(length(trim(serial_no)) > 0),
    location_id INTEGER REFERENCES locations(location_id),
    condition   TEXT    NOT NULL DEFAULT 'usable'
                         CHECK(condition IN ('usable','damaged','lost','maintenance')),
    status      TEXT    NOT NULL DEFAULT 'available'
                         CHECK(status IN ('available','issued'))
);

-- ── Issues ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS issues (
    issue_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id   INTEGER NOT NULL REFERENCES students(student_id),
    item_id      INTEGER NOT NULL REFERENCES items(item_id),
    asset_id     INTEGER          REFERENCES assets(asset_id),
    qty          INTEGER NOT NULL DEFAULT 1 CHECK(qty >= 1),
    purpose      TEXT    NOT NULL CHECK(length(trim(purpose)) > 0),
    issue_date   TEXT    NOT NULL,
    due_date     TEXT    NOT NULL,
    qty_returned INTEGER NOT NULL DEFAULT 0
                          CHECK(qty_returned BETWEEN 0 AND qty),
    status       TEXT    NOT NULL DEFAULT 'open'
                          CHECK(status IN ('open','closed')),
    issued_by    TEXT    NOT NULL CHECK(length(trim(issued_by)) > 0)
);

CREATE INDEX IF NOT EXISTS idx_issues_student
    ON issues(student_id);

CREATE INDEX IF NOT EXISTS idx_issues_status_due
    ON issues(status, due_date);

-- ── Returns log ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS returns_log (
    return_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id     INTEGER NOT NULL REFERENCES issues(issue_id),
    return_date  TEXT    NOT NULL,
    qty_returned INTEGER NOT NULL CHECK(qty_returned >= 1),
    condition    TEXT    NOT NULL CHECK(condition IN ('usable','damaged','lost','maintenance')),
    received_by  TEXT    NOT NULL CHECK(length(trim(received_by)) > 0)
);

-- ── Renewals ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS renewals (
    renewal_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id         INTEGER NOT NULL REFERENCES issues(issue_id),
    request_date     TEXT    NOT NULL,
    status           TEXT    NOT NULL DEFAULT 'pending'
                              CHECK(status IN ('pending','approved','rejected')),
    revised_due_date TEXT,
    decided_by       TEXT,
    remarks          TEXT
);

-- ── Stock log (audit trail) ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS stock_log (
    log_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id  INTEGER NOT NULL REFERENCES items(item_id),
    action   TEXT    NOT NULL CHECK(length(trim(action)) > 0),
    qty      INTEGER NOT NULL DEFAULT 0,
    reason   TEXT,
    log_date TEXT    NOT NULL,
    done_by  TEXT    NOT NULL CHECK(length(trim(done_by)) > 0)
);

-- ── Policies (singleton row, id must equal 1) ─────────────────────────────
CREATE TABLE IF NOT EXISTS policies (
    id                    INTEGER PRIMARY KEY CHECK(id = 1),
    max_items_per_student INTEGER NOT NULL DEFAULT 5  CHECK(max_items_per_student >= 1),
    max_renewals          INTEGER NOT NULL DEFAULT 2  CHECK(max_renewals          >= 0),
    issue_days            INTEGER NOT NULL DEFAULT 15 CHECK(issue_days            >= 1)
);
"""


def init_db(db_path: str | None = None):
    """
    Create all tables and indexes idempotently.
    Safe to call on an existing database – uses IF NOT EXISTS throughout.

    Works both inside a Flask app context (uses get_db()) and standalone.
    """
    _own_conn = False
    try:
        from flask import current_app  # noqa: F401
        conn = get_db(db_path)
    except RuntimeError:
        conn = get_db(db_path)
        _own_conn = True

    # executescript() issues an implicit COMMIT first, then runs each statement.
    conn.executescript(_DDL)

    if _own_conn:
        conn.commit()
        conn.close()


# ============================================================================
# seed_data()  –  idempotent demo data
# ============================================================================

# Fallback credentials (printed as a WARNING; never printed as hashes)
_DEFAULT_COORD_PW   = "CoordPass123!"   # CHANGE via env var DEMO_COORD_PASSWORD
_DEFAULT_STUDENT_PW = "StudentPass1!"   # CHANGE via env var DEMO_STUDENT_PASSWORD


def seed_data(db_path: str | None = None):
    """
    Populate the database with repeatable demo data.

    Idempotent: uses INSERT OR IGNORE so repeated calls are safe.

    Passwords are read from environment variables:
      DEMO_COORD_PASSWORD   – coordinator account
      DEMO_STUDENT_PASSWORD – student accounts
    Falls back to hard-coded defaults and prints a WARNING (but never the hash).
    """
    _own_conn = False
    try:
        from flask import current_app  # noqa: F401
        conn = get_db(db_path)
    except RuntimeError:
        conn = get_db(db_path)
        _own_conn = True

    # ── Read passwords from env ────────────────────────────────────────────
    coord_pw   = os.environ.get("DEMO_COORD_PASSWORD",   "")
    student_pw = os.environ.get("DEMO_STUDENT_PASSWORD", "")

    if not coord_pw:
        coord_pw = _DEFAULT_COORD_PW
        warnings.warn(
            "[database.py] DEMO_COORD_PASSWORD env var not set -- "
            "using insecure default password. Set the env var before deployment.",
            stacklevel=2,
        )
    if not student_pw:
        student_pw = _DEFAULT_STUDENT_PW
        warnings.warn(
            "[database.py] DEMO_STUDENT_PASSWORD env var not set -- "
            "using insecure default password. Set the env var before deployment.",
            stacklevel=2,
        )

    coord_hash   = generate_password_hash(coord_pw)
    student_hash = generate_password_hash(student_pw)
    # Passwords are never printed – hashes go straight into the DB.

    try:
        conn.execute("BEGIN IMMEDIATE")

        # ── 1. Policies (singleton) ────────────────────────────────────────
        conn.execute(
            """
            INSERT OR IGNORE INTO policies
                (id, max_items_per_student, max_renewals, issue_days)
            VALUES (1, 5, 2, 15)
            """
        )

        # ── 2. Locations ──────────────────────────────────────────────────
        for loc_name in ("Lab 1", "Cupboard A"):
            conn.execute(
                "INSERT OR IGNORE INTO locations (name) VALUES (?)",
                (loc_name,)
            )

        # Fetch location IDs (created just above or pre-existing)
        loc = {
            row["name"]: row["location_id"]
            for row in conn.execute("SELECT location_id, name FROM locations")
        }
        lab1      = loc["Lab 1"]
        cupboard  = loc["Cupboard A"]

        # ── 3. Items ──────────────────────────────────────────────────────
        # Development Board – asset, 10 units
        conn.execute(
            """
            INSERT OR IGNORE INTO items
                (code, name, category, description, kind,
                 total_qty, qty_available, location_id)
            VALUES ('DB-ITEM', 'Development Board', 'Electronics',
                    'Microcontroller development board', 'asset',
                    10, 10, ?)
            """,
            (lab1,)
        )
        # USB Cable – quantity, 20 units
        conn.execute(
            """
            INSERT OR IGNORE INTO items
                (code, name, category, description, kind,
                 total_qty, qty_available, location_id)
            VALUES ('USB-CABLE', 'USB Cable', 'Accessories',
                    'USB Type-A to Type-B cable', 'quantity',
                    20, 20, ?)
            """,
            (cupboard,)
        )
        # Adapter – quantity, 3 units
        conn.execute(
            """
            INSERT OR IGNORE INTO items
                (code, name, category, description, kind,
                 total_qty, qty_available, location_id)
            VALUES ('ADAPTER', 'Adapter', 'Accessories',
                    'Power adapter 5V/2A', 'quantity',
                    3, 3, ?)
            """,
            (cupboard,)
        )

        # Fetch the Development Board item_id
        db_item = conn.execute(
            "SELECT item_id FROM items WHERE code = 'DB-ITEM'"
        ).fetchone()
        if db_item is None:
            raise RuntimeError("Development Board item not found after insert.")
        db_item_id = db_item["item_id"]

        # ── 4. Assets (DB-001 … DB-010) ───────────────────────────────────
        for n in range(1, 11):
            serial = f"DB-{n:03d}"
            conn.execute(
                """
                INSERT OR IGNORE INTO assets
                    (item_id, serial_no, location_id, condition, status)
                VALUES (?, ?, ?, 'usable', 'available')
                """,
                (db_item_id, serial, lab1)
            )

        # ── 5. Students (M.Sc. DFIS, Semester I) ─────────────────────────
        students = [
            ("Priya Sharma",   "DFIS2024001", "M.Sc. DFIS", 1, "9876543210"),
            ("Arjun Mehta",    "DFIS2024002", "M.Sc. DFIS", 1, "9876543211"),
            ("Kavya Reddy",    "DFIS2024003", "M.Sc. DFIS", 1, "9876543212"),
        ]
        for name, enrol, prog, sem, contact in students:
            conn.execute(
                """
                INSERT OR IGNORE INTO students
                    (name, enrolment_no, programme, semester, contact)
                VALUES (?, ?, ?, ?, ?)
                """,
                (name, enrol, prog, sem, contact)
            )

        # ── 6. Users ──────────────────────────────────────────────────────
        # Coordinator
        conn.execute(
            """
            INSERT OR IGNORE INTO users (username, password, role, student_id)
            VALUES ('coordinator', ?, 'coordinator', NULL)
            """,
            (coord_hash,)
        )

        # One student user linked to the first student (Priya Sharma)
        priya = conn.execute(
            "SELECT student_id FROM students WHERE enrolment_no = 'DFIS2024001'"
        ).fetchone()
        if priya:
            conn.execute(
                """
                INSERT OR IGNORE INTO users (username, password, role, student_id)
                VALUES ('student1', ?, 'student', ?)
                """,
                (student_hash, priya["student_id"])
            )

        conn.execute("COMMIT")

    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        if _own_conn:
            conn.close()


# ============================================================================
# log_action()
# ============================================================================

def log_action(conn, item_id, action, qty, reason, log_date, done_by):
    """
    Append one audit row to stock_log.

    Must be called while a transaction is open (caller owns BEGIN/COMMIT).

    Parameters
    ----------
    conn     : sqlite3.Connection
    item_id  : int   – FK to items.item_id
    action   : str   – e.g. 'issue', 'return', 'renewal_approved'
    qty      : int   – 0 for asset-level events where qty is N/A
    reason   : str   – free-text description
    log_date : str   – 'YYYY-MM-DD'
    done_by  : str   – username of actor
    """
    if not action or not str(action).strip():
        raise ValueError("log_action: action must not be empty.")
    if not done_by or not str(done_by).strip():
        raise ValueError("log_action: done_by must not be empty.")
    conn.execute(
        """
        INSERT INTO stock_log (item_id, action, qty, reason, log_date, done_by)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (item_id, str(action).strip(), qty, reason, log_date, str(done_by).strip()),
    )


# ============================================================================
# Validated helpers
# ============================================================================

def add_location(conn, name: str) -> int:
    """
    Insert a new location and return its location_id.
    Raises ValueError with a plain message on invalid input or duplicate name.
    """
    name = (name or "").strip()
    if not name:
        raise ValueError("Location name must not be empty.")
    if len(name) > 200:
        raise ValueError("Location name must be 200 characters or fewer.")
    try:
        cur = conn.execute(
            "INSERT INTO locations (name) VALUES (?)", (name,)
        )
        return cur.lastrowid
    except sqlite3.IntegrityError:
        raise ValueError(f"A location named '{name}' already exists.")


def add_student(conn, name: str, enrolment_no: str, programme: str,
                semester: int, contact: str) -> int:
    """
    Insert a new student and return their student_id.
    Raises ValueError with a plain message on invalid input or duplicate enrolment_no.
    """
    name         = (name         or "").strip()
    enrolment_no = (enrolment_no or "").strip()
    programme    = (programme    or "").strip()
    contact      = (contact      or "").strip()

    if not name:
        raise ValueError("Student name must not be empty.")
    if len(name) > 200:
        raise ValueError("Student name must be 200 characters or fewer.")
    if not enrolment_no:
        raise ValueError("Enrolment number must not be empty.")
    if len(enrolment_no) > 50:
        raise ValueError("Enrolment number must be 50 characters or fewer.")
    if not programme:
        raise ValueError("Programme must not be empty.")
    if not isinstance(semester, int) or not (1 <= semester <= 12):
        raise ValueError("Semester must be an integer between 1 and 12.")
    if not contact:
        raise ValueError("Contact must not be empty.")

    try:
        cur = conn.execute(
            """
            INSERT INTO students (name, enrolment_no, programme, semester, contact)
            VALUES (?, ?, ?, ?, ?)
            """,
            (name, enrolment_no, programme, semester, contact)
        )
        return cur.lastrowid
    except sqlite3.IntegrityError:
        raise ValueError(
            f"A student with enrolment number '{enrolment_no}' already exists."
        )


def add_item(conn, code: str, name: str, category: str, kind: str,
             total_qty: int, location_id: int | None = None,
             description: str = "", make_model: str = "") -> int:
    """
    Insert a new item and return its item_id.
    Raises ValueError with a plain message on invalid input.
    """
    code     = (code     or "").strip().upper()
    name     = (name     or "").strip()
    category = (category or "").strip()
    kind     = (kind     or "").strip().lower()

    if not code:
        raise ValueError("Item code must not be empty.")
    if len(code) > 50:
        raise ValueError("Item code must be 50 characters or fewer.")
    if not name:
        raise ValueError("Item name must not be empty.")
    if not category:
        raise ValueError("Item category must not be empty.")
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}.")
    if not isinstance(total_qty, int) or total_qty < 0:
        raise ValueError("total_qty must be a non-negative integer.")

    try:
        cur = conn.execute(
            """
            INSERT INTO items
                (code, name, category, description, make_model, kind,
                 total_qty, qty_available, location_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (code, name, category, description or "", make_model or "",
             kind, total_qty, total_qty, location_id)
        )
        return cur.lastrowid
    except sqlite3.IntegrityError:
        raise ValueError(f"An item with code '{code}' already exists.")


def add_asset(conn, item_id: int, serial_no: str,
              location_id: int | None = None,
              condition: str = "usable") -> int:
    """
    Insert a new asset row and return its asset_id.
    Also decrements nothing – the item's qty_available must already be set
    at item creation time (batch inserts via seed_data handle counts directly).
    Raises ValueError on invalid input.
    """
    serial_no = (serial_no or "").strip()
    condition = (condition or "usable").strip().lower()

    if not isinstance(item_id, int) or item_id <= 0:
        raise ValueError("item_id must be a positive integer.")
    if not serial_no:
        raise ValueError("serial_no must not be empty.")
    if len(serial_no) > 100:
        raise ValueError("serial_no must be 100 characters or fewer.")
    if condition not in CONDITIONS:
        raise ValueError(f"condition must be one of {CONDITIONS}.")

    # Verify the parent item exists and is of kind 'asset'
    row = conn.execute(
        "SELECT kind FROM items WHERE item_id = ?", (item_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"No item with item_id={item_id} found.")
    if row["kind"] != "asset":
        raise ValueError(
            f"item_id={item_id} is a 'quantity' item; assets can only belong to 'asset' items."
        )

    try:
        cur = conn.execute(
            """
            INSERT INTO assets (item_id, serial_no, location_id, condition, status)
            VALUES (?, ?, ?, ?, 'available')
            """,
            (item_id, serial_no, location_id, condition)
        )
        return cur.lastrowid
    except sqlite3.IntegrityError:
        raise ValueError(f"An asset with serial_no '{serial_no}' already exists.")


# ============================================================================
# Flask app factory hook
# ============================================================================

def init_app(app):
    """
    Register close_db teardown and the 'flask init-db' CLI command.
    Call this from your Flask application factory.
    """
    app.teardown_appcontext(close_db)

    @app.cli.command("init-db")
    def _init_db_command():
        """Create all tables (idempotent).  flask init-db"""
        init_db()
        print("Database initialised.")

    @app.cli.command("seed-db")
    def _seed_db_command():
        """Seed demo data (idempotent).  flask seed-db"""
        seed_data()
        print("Seed data inserted.")


# ============================================================================
# Direct execution: python database.py
# ============================================================================

if __name__ == "__main__":
    print("[database.py] Target database: " + _DB_PATH)

    # Warn loudly if .gitignore does not exclude inventory.db
    gitignore_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".gitignore")
    if os.path.exists(gitignore_path):
        with open(gitignore_path) as _f:
            _gi = _f.read()
        if "inventory.db" not in _gi:
            print(
                "\n*** WARNING: inventory.db is NOT in your .gitignore "
                "-- add it now to avoid committing the database! ***\n"
            )
    else:
        print(
            "\n*** WARNING: No .gitignore found. "
            "Create one and add 'inventory.db' to it. ***\n"
        )

    print("Creating tables ...")
    init_db()
    print("Tables created (or already existed).")

    print("Seeding demo data ...")
    seed_data()
    print("Done.  Demo credentials:")
    print("  Coordinator -> username: coordinator  (password from DEMO_COORD_PASSWORD env var)")
    print("  Student     -> username: student1     (password from DEMO_STUDENT_PASSWORD env var)")
    print("")
    print("*** Add 'inventory.db' to your .gitignore! ***")
