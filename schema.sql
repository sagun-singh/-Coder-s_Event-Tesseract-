-- schema.sql  –  run once via `flask init-db`
-- All tables use IF NOT EXISTS so re-runs are safe.

PRAGMA foreign_keys = ON;

-- ── Students ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS students (
    student_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT    NOT NULL,
    enrolment_no  TEXT    NOT NULL UNIQUE,
    programme     TEXT    NOT NULL,
    semester      INTEGER NOT NULL,
    contact       TEXT    NOT NULL
);

-- ── Users (auth) ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    user_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    username   TEXT    NOT NULL UNIQUE,
    password   TEXT    NOT NULL,              -- werkzeug hashed
    role       TEXT    NOT NULL CHECK(role IN ('student','coordinator')),
    student_id INTEGER REFERENCES students(student_id)
);

-- ── Locations ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS locations (
    location_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL UNIQUE
);

-- ── Items (catalogue) ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS items (
    item_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    code             TEXT    NOT NULL UNIQUE,
    name             TEXT    NOT NULL,
    category         TEXT    NOT NULL,
    description      TEXT,
    make_model       TEXT,
    kind             TEXT    NOT NULL CHECK(kind IN ('asset','quantity')),
    total_qty        INTEGER NOT NULL DEFAULT 0,
    qty_available    INTEGER NOT NULL DEFAULT 0,
    qty_damaged      INTEGER NOT NULL DEFAULT 0,
    qty_lost         INTEGER NOT NULL DEFAULT 0,
    qty_maintenance  INTEGER NOT NULL DEFAULT 0,
    location_id      INTEGER REFERENCES locations(location_id)
);

-- ── Assets (individual tracked units) ─────────────────────────────────────
CREATE TABLE IF NOT EXISTS assets (
    asset_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id     INTEGER NOT NULL REFERENCES items(item_id),
    serial_no   TEXT    NOT NULL UNIQUE,
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
    asset_id     INTEGER REFERENCES assets(asset_id),    -- NULL for quantity items
    qty          INTEGER NOT NULL DEFAULT 1,
    purpose      TEXT    NOT NULL,
    issue_date   TEXT    NOT NULL,   -- YYYY-MM-DD
    due_date     TEXT    NOT NULL,   -- YYYY-MM-DD
    qty_returned INTEGER NOT NULL DEFAULT 0,
    status       TEXT    NOT NULL DEFAULT 'open'
                          CHECK(status IN ('open','closed')),
    issued_by    TEXT    NOT NULL
);

-- ── Returns log ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS returns_log (
    return_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id     INTEGER NOT NULL REFERENCES issues(issue_id),
    return_date  TEXT    NOT NULL,
    qty_returned INTEGER NOT NULL,
    condition    TEXT    NOT NULL CHECK(condition IN ('usable','damaged','lost','maintenance')),
    received_by  TEXT    NOT NULL
);

-- ── Renewals ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS renewals (
    renewal_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id        INTEGER NOT NULL REFERENCES issues(issue_id),
    request_date    TEXT    NOT NULL,
    status          TEXT    NOT NULL DEFAULT 'pending'
                             CHECK(status IN ('pending','approved','rejected')),
    revised_due_date TEXT,           -- set when approved
    decided_by      TEXT,
    remarks         TEXT
);

-- ── Stock log (audit trail) ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS stock_log (
    log_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id  INTEGER NOT NULL REFERENCES items(item_id),
    action   TEXT    NOT NULL,
    qty      INTEGER NOT NULL DEFAULT 0,
    reason   TEXT,
    log_date TEXT    NOT NULL,
    done_by  TEXT    NOT NULL
);

-- ── Policies ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS policies (
    id                    INTEGER PRIMARY KEY CHECK(id = 1),  -- singleton row
    max_items_per_student INTEGER NOT NULL DEFAULT 3,
    max_renewals          INTEGER NOT NULL DEFAULT 2,
    issue_days            INTEGER NOT NULL DEFAULT 15
);

-- Seed the singleton policy row if it doesn't exist yet
INSERT OR IGNORE INTO policies (id, max_items_per_student, max_renewals, issue_days)
VALUES (1, 3, 2, 15);
