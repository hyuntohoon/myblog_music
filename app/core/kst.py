# app/core/kst.py
"""KST day-boundary helper — the single definition of "today" for this service.

Site convention: the database stores UTC, but a *day* is KST wall-clock, and the
boundary is computed in Python — never with Postgres' `current_date` or a bare
`date.today()`.

Both halves matter. `current_date`, and `date.today()` in a Lambda, resolve
against the session/host timezone, which is UTC in every environment we run:
Neon, the Lambda runtime, and CI. A KST day therefore starts 9 hours late, so
anything decided between 00:00 and 09:00 KST uses the previous day.

Cross-repo twin: myblog_backend/app/core/kst.py — same contract, same reason.
Keep them in sync (the backend copy documents A-4, the today's-pick bug that
made this rule explicit).
"""
from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


def kst_today() -> date:
    """Today's calendar date on the KST wall clock."""
    return datetime.now(KST).date()
