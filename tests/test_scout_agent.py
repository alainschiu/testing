"""End-to-end scout pipeline with mocked LLM. Covers the four acceptance
scenarios in the build brief: happy path, malformed block, dedup on re-run,
and the web-search budget cap being passed through to the tool definition."""
from __future__ import annotations

from scout.agents.scout_agent import (
    _MAX_PAUSE_TURNS,
    latest_digest,
    run_scout,
)
from scout.config import get_settings
from scout.db import connection, run_migrations
from scout.llm.client import FakeLLMClient, LLMResponse, LLMUsage
from tests.fixtures.sample_scout_output import (
    HAPPY_TWO_BLOCKS,
    HAPPY_WITH_MALFORMED,
    VALID_BLOCK_A,
)


def _make_fake(text: str, *, stop_reason: str = "end_turn") -> FakeLLMClient:
    return FakeLLMClient(
        responses={
            "scout_agent": LLMResponse(
                text=text,
                usage=LLMUsage(
                    model="claude-opus-4-7",
                    input_tokens=12_000,
                    output_tokens=4_500,
                    cost_usd=0.1725,
                ),
                stop_reason=stop_reason,
            )
        }
    )


def test_happy_path_inserts_two_opportunities() -> None:
    run_migrations()
    fake = _make_fake(HAPPY_TWO_BLOCKS)
    summary = run_scout(client=fake, today_iso="2026-05-11")
    assert "added 2" in summary
    assert "quarantined 0" in summary

    with connection() as conn:
        rows = conn.execute(
            "SELECT title, type, deadline, fit_score, primary_angle, status FROM opportunities"
            " ORDER BY id"
        ).fetchall()
    assert len(rows) == 2
    assert rows[0]["title"].startswith("DAAD")
    assert rows[0]["status"] == "lead"
    assert rows[0]["fit_score"] == 5
    assert rows[0]["deadline"] == "2026-08-31"
    assert rows[1]["title"].startswith("Akademie")
    assert rows[1]["deadline"] is None  # rolling


def test_rerun_with_identical_output_dedups() -> None:
    run_migrations()
    fake = _make_fake(HAPPY_TWO_BLOCKS)
    run_scout(client=fake, today_iso="2026-05-11")
    summary2 = run_scout(client=fake, today_iso="2026-05-12")
    assert "added 0" in summary2
    assert "dedup-in-db 2" in summary2

    with connection() as conn:
        n = conn.execute("SELECT COUNT(*) AS n FROM opportunities").fetchone()["n"]
    assert n == 2


def test_malformed_block_quarantined_not_fatal() -> None:
    run_migrations()
    fake = _make_fake(HAPPY_WITH_MALFORMED)
    summary = run_scout(client=fake, today_iso="2026-05-11")
    assert "added 2" in summary
    assert "quarantined 1" in summary

    s = get_settings()
    qdirs = list(s.runs_dir.glob("*_quarantine"))
    assert len(qdirs) == 1
    assert any(qdirs[0].glob("block_*.txt"))


def test_web_search_budget_cap_is_passed_to_tool() -> None:
    run_migrations()
    fake = _make_fake(VALID_BLOCK_A)
    run_scout(client=fake, today_iso="2026-05-11")
    assert fake.calls, "expected at least one LLM call"
    tools = fake.calls[0]["tools"]
    assert tools, "scout agent must declare the web_search tool"
    web = next(t for t in tools if t.get("name") == "web_search")
    s = get_settings()
    assert web["max_uses"] == s.max_web_searches  # env-overridden to 5 in tests
    assert web["type"] == "web_search_20260209"


def test_pause_turn_then_end_turn_concatenates_text() -> None:
    """Server-side tool can hit its loop limit and emit pause_turn; we must
    resume by re-sending and concatenate parsed text from both turns."""
    run_migrations()
    paused = LLMResponse(
        text=VALID_BLOCK_A,
        usage=LLMUsage(model="claude-opus-4-7", input_tokens=8_000, output_tokens=2_000),
        stop_reason="pause_turn",
    )
    finished = LLMResponse(
        text=HAPPY_TWO_BLOCKS.replace(VALID_BLOCK_A, ""),
        usage=LLMUsage(model="claude-opus-4-7", input_tokens=6_000, output_tokens=2_000),
        stop_reason="end_turn",
    )
    queue = [paused, finished]

    class StepFake(FakeLLMClient):
        def create_message(self, **kw):  # type: ignore[override]
            self.calls.append(kw)
            return queue.pop(0)

    fake = StepFake()
    summary = run_scout(client=fake, today_iso="2026-05-11")
    assert len(fake.calls) == 2, "expected one resume call after pause_turn"
    assert "added 2" in summary


def test_first_run_then_weekly_user_brief_differs() -> None:
    run_migrations()
    fake1 = _make_fake(VALID_BLOCK_A)
    run_scout(client=fake1, today_iso="2026-05-11")
    first_user_msg = fake1.calls[0]["messages"][0]["content"]
    assert "first-run brief" in first_user_msg.lower()

    fake2 = _make_fake(HAPPY_TWO_BLOCKS)
    run_scout(client=fake2, today_iso="2026-05-18")
    second_user_msg = fake2.calls[0]["messages"][0]["content"]
    assert "weekly variant" in second_user_msg.lower()


def test_digest_reports_top_and_counts() -> None:
    run_migrations()
    fake = _make_fake(HAPPY_TWO_BLOCKS)
    run_scout(client=fake, today_iso="2026-05-11")
    out = latest_digest()
    assert "Latest scout run" in out
    assert "DAAD" in out
    assert "lead" in out
    # Top 3 must include the fit=5 entry
    assert "fit=5" in out


def test_pause_turn_cap_limits_resumes() -> None:
    """Defensive: an agent stuck on pause_turn must not loop forever."""
    run_migrations()
    paused = LLMResponse(
        text="",
        usage=LLMUsage(model="claude-opus-4-7", input_tokens=1, output_tokens=1),
        stop_reason="pause_turn",
    )
    fake = FakeLLMClient(responses={"scout_agent": paused})
    run_scout(client=fake, today_iso="2026-05-11")
    assert len(fake.calls) == _MAX_PAUSE_TURNS
