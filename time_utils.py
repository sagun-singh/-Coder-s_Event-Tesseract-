"""
time_utils.py  –  P4
Single source of truth for "today".

get_today() is the ONLY function any other module should call.
It NEVER calls date.today() directly in logic – it returns the
session-overridden fake date when the Time Machine is active.

validate_fake_date(s) is used by the Time Machine form handler to
reject syntactically or semantically invalid dates before storing.
"""

import re
from datetime import date

_DATE_RE  = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_YEAR_MIN = 2020
_YEAR_MAX = 2100


def get_today() -> str:
    """
    Return today's date as 'YYYY-MM-DD'.

    Inside a Flask request context: checks session['fake_date'] first.
    Outside a Flask request context (tests, CLI): returns real date.
    """
    try:
        from flask import session
        fake = session.get("fake_date")
        if fake:
            return fake
    except RuntimeError:
        pass   # no app context – fall through to real date

    return date.today().isoformat()


def validate_fake_date(s: str):
    """
    Validate a candidate fake-date string.

    Returns (True, cleaned_str) on success, (False, error_message) on failure.
    Accepts only YYYY-MM-DD in the range 2020-01-01 .. 2100-12-31.
    """
    s = (s or "").strip()
    if not _DATE_RE.match(s):
        return False, "Date must be in YYYY-MM-DD format."
    try:
        d = date.fromisoformat(s)
    except ValueError:
        return False, "That is not a real calendar date."
    if d.year < _YEAR_MIN or d.year > _YEAR_MAX:
        return False, f"Year must be between {_YEAR_MIN} and {_YEAR_MAX}."
    return True, s
