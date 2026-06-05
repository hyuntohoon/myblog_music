"""FEAT-music-search-recall Step 2 (E2) — Hit@K recall gate.

Loads `tests/fixtures/search_cases.yaml`, runs every query through the real
`SearchService.unified_search`, and asserts:

    Hit@5 >= 0.90   (the intended entity surfaces in its bucket's top 5)
    Hit@1 >= 0.60   (the intended entity is its bucket's rank-1)

This is the **measurement harness** for the RFC. At Step 2 the gate is expected
to FAIL on the current ILIKE-only matcher — that failure is the baseline diff
that Steps 4 (pg_trgm) / 5 (aliases) / 6 (decomposition) close. The per-query
breakdown printed on failure is the artifact to record in the RFC Decisions log.

Read-only: it never seeds, so it points at whatever catalog the gate DB holds.
Target DB resolution: $RECALL_GATE_DB_URL, then $TEST_DB_URL. When neither is
set the test skips at collection ([[feedback-local-db-smoke-fallback]]). Because
the fixture carries real prod ids, a clean baseline measurement wants the gate
pointed at the prod catalog (read-only) via RECALL_GATE_DB_URL — a smaller
test branch will report most cases as data-misses rather than recall results.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# app.* modules read DATABASE_URL at import — give them a parseable placeholder
# before importing the service (mirrors test_unified_search_expansion.py).
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://x:x@localhost/x")

from app.services.search_service import SearchService  # noqa: E402

_GATE_DB_URL = os.environ.get("RECALL_GATE_DB_URL") or os.environ.get("TEST_DB_URL")

_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "search_cases.yaml"

# Targets per the RFC Goal.
HIT5_TARGET = 0.90
HIT1_TARGET = 0.60

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _GATE_DB_URL,
        reason="recall gate needs RECALL_GATE_DB_URL (or TEST_DB_URL) — Neon catalog",
    ),
]


def _load_cases() -> list[dict]:
    data = yaml.safe_load(_FIXTURE.read_text(encoding="utf-8"))
    return data["cases"]


@pytest.fixture(scope="module")
def session():
    eng = create_engine(_GATE_DB_URL, pool_pre_ping=True, future=True)
    Session = sessionmaker(bind=eng, autoflush=False, future=True)
    s = Session()
    try:
        yield s
    finally:
        s.close()
        eng.dispose()


def _bucket(res, type_: str) -> list:
    return {"artist": res.artists, "album": res.albums, "track": res.tracks}[type_]


def _evaluate(session, case: dict) -> dict:
    """Return per-case result dict: {hit1, hit5, data_miss, ids_in_order}."""
    svc = SearchService(session)
    res = svc.unified_search(q=case["query"], limit=20, offset=0)

    if case["type"] == "empty":
        empty = not (res.artists or res.albums or res.tracks)
        return {"hit1": empty, "hit5": empty, "data_miss": False, "rank": None}

    expected = [str(e) for e in case["expected_ids"]]
    ids_in_order = [str(item.id) for item in _bucket(res, case["type"])]
    top5 = ids_in_order[:5]
    top1 = ids_in_order[0] if ids_in_order else None

    hit5 = any(eid in top5 for eid in expected)
    hit1 = bool(expected) and expected[0] == top1
    # rank of the first expected id within the bucket (1-based), if present
    rank = next((i + 1 for i, x in enumerate(ids_in_order) if x in expected), None)
    return {"hit1": hit1, "hit5": hit5, "data_miss": False, "rank": rank}


def test_search_recall_gate(session):
    cases = _load_cases()
    assert cases, "fixture has no cases"

    rows = []
    hit5 = hit1 = 0
    for c in cases:
        r = _evaluate(session, c)
        rows.append((c, r))
        hit5 += int(r["hit5"])
        hit1 += int(r["hit1"])

    n = len(cases)
    hit5_rate = hit5 / n
    hit1_rate = hit1 / n

    # Per-query breakdown — the baseline artifact. Printed always so a passing
    # run still shows the distribution.
    lines = ["", f"=== recall gate: {n} cases ==="]
    for c, r in rows:
        mark5 = "✓" if r["hit5"] else "✗"
        mark1 = "①" if r["hit1"] else " "
        rank = f"@{r['rank']}" if r["rank"] else "—"
        lines.append(
            f"  [{c['category']:<11}] {mark5}5 {mark1}1 {rank:<4} {c['query']!r}"
        )
    lines.append(f"--- Hit@5 = {hit5}/{n} = {hit5_rate:.3f} (target ≥ {HIT5_TARGET})")
    lines.append(f"--- Hit@1 = {hit1}/{n} = {hit1_rate:.3f} (target ≥ {HIT1_TARGET})")
    report = "\n".join(lines)
    print(report)

    assert hit5_rate >= HIT5_TARGET and hit1_rate >= HIT1_TARGET, (
        f"recall gate below target:\n{report}"
    )
