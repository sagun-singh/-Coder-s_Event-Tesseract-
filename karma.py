"""
karma.py  –  P6
Student karma / trustworthiness score.

karma_score(student_id, today) -> (score: int, badge: str, message: str)

Scoring rules (start at 100):
  +5   per on-time return (return_date <= due_date at the time of return)
  +5   per usable return  (condition = 'usable')
  -2   per day each open-overdue issue has been overdue
         capped at -30 per issue (= 15 days overdue max deduction)
  -20  per damaged return (condition = 'damaged')
  -50  per lost item      (condition = 'lost')

Clamp to [0, 150].

Tiers and messages:
  Gold   score >= 90  "Super reliable! Thank you!"
  Silver 70 <= score < 90  "Nice work. Keep it up."
  (else) "Needs a Reminder"  –  hint message: "Please return on time - others are waiting."

NOTE: karma is a hint only and NEVER blocks an issue.
"""

from datetime import date
from database import get_db


# Caps & weights
_OVERDUE_PER_DAY   = 2      # points deducted per overdue calendar day
_OVERDUE_CAP       = 30     # max deduction per overdue issue (= 15 days)
_DAMAGED_PENALTY   = 20
_LOST_PENALTY      = 50
_ONTIME_BONUS      = 5
_USABLE_BONUS      = 5
_CLAMP_MIN         = 0
_CLAMP_MAX         = 150


def karma_score(student_id: int, today: str):
    """
    Compute a karma score for a student.

    Parameters
    ----------
    student_id : int
    today      : str  'YYYY-MM-DD'  (never calls date.today() directly)

    Returns
    -------
    (score: int, badge: str, message: str)
      badge   is 'Gold' | 'Silver' | 'Needs a Reminder'
      message is the hint string
    """
    if not isinstance(student_id, int) or student_id <= 0:
        return 0, "Needs a Reminder", "Please return on time - others are waiting."

    today_date = date.fromisoformat(today)
    db = get_db()
    score = 100

    # ── On-time returns (+5 each) ─────────────────────────────────────────
    ontime_count = db.execute(
        """
        SELECT COUNT(*)
        FROM   returns_log rl
        JOIN   issues i ON i.issue_id = rl.issue_id
        WHERE  i.student_id   = ?
          AND  rl.return_date <= i.due_date
        """,
        (student_id,)
    ).fetchone()[0]
    score += ontime_count * _ONTIME_BONUS

    # ── Usable returns (+5 each) ──────────────────────────────────────────
    usable_count = db.execute(
        """
        SELECT COUNT(*)
        FROM   returns_log rl
        JOIN   issues i ON i.issue_id = rl.issue_id
        WHERE  i.student_id  = ?
          AND  rl.condition   = 'usable'
        """,
        (student_id,)
    ).fetchone()[0]
    score += usable_count * _USABLE_BONUS

    # ── Currently open-overdue issues (-2/day, capped at -30 per issue) ───
    # Fetch each overdue issue's due_date individually so we can cap per-issue.
    overdue_rows = db.execute(
        """
        SELECT due_date
        FROM   issues
        WHERE  student_id = ?
          AND  status     = 'open'
          AND  due_date   <  ?
          AND  (qty - qty_returned) > 0
        """,
        (student_id, today)
    ).fetchall()
    for row in overdue_rows:
        days_over  = (today_date - date.fromisoformat(row[0])).days
        deduction  = min(days_over * _OVERDUE_PER_DAY, _OVERDUE_CAP)
        score     -= deduction

    # ── Damaged returns (-20 each) ────────────────────────────────────────
    damaged_count = db.execute(
        """
        SELECT COUNT(*)
        FROM   returns_log rl
        JOIN   issues i ON i.issue_id = rl.issue_id
        WHERE  i.student_id  = ?
          AND  rl.condition   = 'damaged'
        """,
        (student_id,)
    ).fetchone()[0]
    score -= damaged_count * _DAMAGED_PENALTY

    # ── Lost items (-50 each) ─────────────────────────────────────────────
    lost_count = db.execute(
        """
        SELECT COUNT(*)
        FROM   returns_log rl
        JOIN   issues i ON i.issue_id = rl.issue_id
        WHERE  i.student_id  = ?
          AND  rl.condition   = 'lost'
        """,
        (student_id,)
    ).fetchone()[0]
    score -= lost_count * _LOST_PENALTY

    # ── Clamp ─────────────────────────────────────────────────────────────
    score = max(_CLAMP_MIN, min(_CLAMP_MAX, score))

    # ── Badge & message ───────────────────────────────────────────────────
    if score >= 90:
        badge   = "Gold"
        message = "Super reliable! Thank you!"
    elif score >= 70:
        badge   = "Silver"
        message = "Nice work. Keep it up."
    else:
        badge   = "Needs a Reminder"
        message = "Please return on time - others are waiting."

    return score, badge, message
