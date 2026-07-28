from calendar import monthrange
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from app.core.cache import SEARCH_CACHE_CONTROL
from app.core.db import get_db
from app.core.kst import kst_today
from app.domain.schemas import ReleaseCalendarResult
from app.services.release_calendar_service import ReleaseCalendarService

router = APIRouter()

# The month-grid UI (Step 7) shows ~3 months max (RFC OQ2: real content sits
# <=90 d out); 93 = three max-length months back-to-back.
MAX_WINDOW_DAYS = 93


# FEAT-release-calendar Track B Step 6: public DB-only read over
# artist_release_events (edge_guard; no JWT / no apigateway route — GET rides
# the ANY /api/music/{proxy+} catch-all). No Spotify/external call on this
# path (hard rule #9). Window defaults (RFC leaves them open): from = first
# day of the current month, to = last day of from's month; max span 93 days.
@router.get(
    "/calendar",
    response_model=ReleaseCalendarResult,
    summary="발매 캘린더 (관측 소스별 발매 이벤트, 표시용 소프트 그룹핑)",
)
def release_calendar(
    response: Response,
    date_from: Optional[date] = Query(
        None, alias="from", description="윈도우 시작 (YYYY-MM-DD, 기본: 이번 달 1일)"
    ),
    date_to: Optional[date] = Query(
        None, alias="to", description="윈도우 끝 (YYYY-MM-DD, 기본: from이 속한 달의 말일)"
    ),
    db: Session = Depends(get_db),
):
    if date_from is None:
        # "이번 달" is the KST month — on the 1st, a UTC `date.today()` would
        # still be last month until 09:00 KST and default the grid to the wrong
        # month (A-4 twin; see app/core/kst.py).
        date_from = kst_today().replace(day=1)
    if date_to is None:
        date_to = date_from.replace(
            day=monthrange(date_from.year, date_from.month)[1]
        )
    if date_from > date_to:
        raise HTTPException(status_code=422, detail="`from` must be <= `to`")
    if (date_to - date_from).days > MAX_WINDOW_DAYS:
        raise HTTPException(
            status_code=422, detail=f"window exceeds {MAX_WINDOW_DAYS} days"
        )

    result = ReleaseCalendarService(db).get_calendar(
        date_from=date_from, date_to=date_to
    )
    # announced rows churn with poller ticks — short cache like search/feed.
    response.headers["Cache-Control"] = SEARCH_CACHE_CONTROL
    return result
