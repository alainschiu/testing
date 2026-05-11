"""Drafting agent: generate, regenerate, revise-with-critique, exemplar
injection, hallucination flagging. All LLM calls mocked.
"""
from __future__ import annotations

import json

import pytest

from scout.agents.artist_profile import refresh_artist_profile
from scout.agents.drafting_agent import generate_or_revise_draft
from scout.db import connection, run_migrations
from scout.llm.client import FakeLLMClient, LLMResponse, LLMUsage
from scout.models import utc_now_iso


def _seed_opp_and_app(*, title="DAAD Berliner Künstlerprogramm", type_="residency") -> tuple[int, int]:
    now = utc_now_iso()
    with connection() as conn:
        cur = conn.execute(
            "INSERT INTO opportunities (title, type, url, url_hash, deadline, fit_score,"
            " primary_angle, why_fits, status, discovered_at, updated_at)"
            " VALUES (?, ?, ?, ?, '2026-08-31', 5, 'A',"
            " 'Funds practice-led research with no production deliverable.',"
            " 'drafting', ?, ?)",
            (title, type_, f"https://example.org/{title}", title, now, now),
        )
        opp_id = int(cur.lastrowid)
        app_cur = conn.execute(
            "INSERT INTO applications (opportunity_id, status, created_at, updated_at)"
            " VALUES (?, 'drafting', ?, ?)",
            (opp_id, now, now),
        )
        return opp_id, int(app_cur.lastrowid)


def _fake_with(text: str, *, prompt_template: str | None = None) -> FakeLLMClient:
    response = LLMResponse(
        text=text,
        usage=LLMUsage(
            model="claude-opus-4-7",
            input_tokens=2_000,
            output_tokens=500,
            cost_usd=0.0225,
        ),
        stop_reason="end_turn",
    )
    return FakeLLMClient(default=response)


def test_generate_artist_statement_persists_a_draft() -> None:
    run_migrations()
    refresh_artist_profile()
    _, app_id = _seed_opp_and_app()
    fake = _fake_with(
        "Alain Chiu's practice centres on listening as a situated act. "
        "Drawing Room Project extends this through nocturne and miniature forms."
    )
    result = generate_or_revise_draft(
        application_id=app_id,
        kind="artist_statement",
        variant="500w",
        client=fake,
    )
    assert result.id > 0
    assert result.word_count > 0
    assert result.parent_draft_id is None
    with connection() as conn:
        row = conn.execute("SELECT * FROM drafts WHERE id = ?", (result.id,)).fetchone()
    assert row["kind"] == "artist_statement"
    assert row["variant"] == "500w"
    assert row["application_id"] == app_id
    assert row["word_count"] > 0


def test_revise_with_critique_creates_two_calls_and_linked_revision() -> None:
    run_migrations()
    refresh_artist_profile()
    _, app_id = _seed_opp_and_app()

    # First, plant an initial draft via the agent
    initial = _fake_with("Initial draft about Drawing Room Project at DAAD Berliner Künstlerprogramm.")
    first = generate_or_revise_draft(
        application_id=app_id, kind="project_description", client=initial
    )

    # Now critique + revise. Two LLM calls expected.
    queue = [
        LLMResponse(
            text="SCORES:\n- Clarity: 3/5 — quote: 'centres on listening'\n- Fit-to-funder: 2/5 — quote: '...'\n"
            "REVISE:\n- Echo DAAD's contemplative-listening phrase.\n",
            usage=LLMUsage(model="claude-opus-4-7", input_tokens=800, output_tokens=200, cost_usd=0.0050),
            stop_reason="end_turn",
        ),
        LLMResponse(
            text="Revised draft echoing DAAD Berliner Künstlerprogramm's contemplative-listening priority.",
            usage=LLMUsage(model="claude-opus-4-7", input_tokens=2_100, output_tokens=520, cost_usd=0.0235),
            stop_reason="end_turn",
        ),
    ]

    class TwoStep(FakeLLMClient):
        def create_message(self, **kw):  # type: ignore[override]
            self.calls.append(kw)
            return queue.pop(0)

    fake = TwoStep()
    revised = generate_or_revise_draft(
        application_id=app_id,
        kind="project_description",
        parent_id=first.id,
        with_critique=True,
        client=fake,
    )
    assert len(fake.calls) == 2
    # First call is critic, second is generator
    assert fake.calls[0]["prompt_template"] == "drafting/_critic.md"
    assert fake.calls[1]["prompt_template"] == "drafting/project_description.md"
    assert revised.parent_draft_id == first.id
    assert "contemplative-listening" in revised.content
    assert revised.critique is not None and "Echo DAAD" in revised.critique
    assert revised.cost_usd > first.cost_usd  # critique + generation


def test_hallucination_flags_attach_to_draft() -> None:
    run_migrations()
    refresh_artist_profile()
    _, app_id = _seed_opp_and_app()
    fake = _fake_with(
        "The artist will partner with Made-Up Foundation and Phantom Sound Lab."
    )
    result = generate_or_revise_draft(
        application_id=app_id, kind="cover_letter", client=fake
    )
    assert "Made-Up Foundation" in result.hallucination_flags
    assert "Phantom Sound Lab" in result.hallucination_flags

    with connection() as conn:
        row = conn.execute(
            "SELECT hallucination_flags FROM drafts WHERE id = ?", (result.id,)
        ).fetchone()
    stored = json.loads(row["hallucination_flags"])
    assert "Made-Up Foundation" in stored


def test_known_proper_nouns_not_flagged() -> None:
    run_migrations()
    refresh_artist_profile()
    _, app_id = _seed_opp_and_app()
    fake = _fake_with(
        "Drawing Room Project responds to DAAD Berliner Künstlerprogramm. Alain Chiu's prior work…"
    )
    result = generate_or_revise_draft(
        application_id=app_id, kind="artist_statement", client=fake
    )
    assert "Drawing Room Project" not in result.hallucination_flags
    assert "DAAD Berliner Künstlerprogramm" not in result.hallucination_flags
    assert "Alain Chiu" not in result.hallucination_flags


def test_unknown_kind_raises() -> None:
    run_migrations()
    refresh_artist_profile()
    _, app_id = _seed_opp_and_app()
    fake = _fake_with("x")
    with pytest.raises(ValueError):
        generate_or_revise_draft(application_id=app_id, kind="not_a_kind", client=fake)


def test_revise_against_missing_parent_raises() -> None:
    run_migrations()
    refresh_artist_profile()
    _, app_id = _seed_opp_and_app()
    fake = _fake_with("x")
    with pytest.raises(ValueError):
        generate_or_revise_draft(
            application_id=app_id,
            kind="cover_letter",
            parent_id=99_999,
            with_critique=True,
            client=fake,
        )


def test_exemplar_appears_in_user_prompt_when_present() -> None:
    run_migrations()
    refresh_artist_profile()
    _, app_id = _seed_opp_and_app(type_="residency")
    with connection() as conn:
        conn.execute(
            "INSERT INTO past_applications (type, funder, result, excerpt, imported_at)"
            " VALUES ('residency', 'Akademie Solitude', 'won',"
            " 'I propose a slow listening room…', ?)",
            (utc_now_iso(),),
        )
    fake = _fake_with("draft text")
    generate_or_revise_draft(application_id=app_id, kind="artist_statement", client=fake)
    user_msg = fake.calls[0]["messages"][0]["content"]
    assert "Akademie Solitude" in user_msg
    assert "slow listening room" in user_msg


def test_regenerate_creates_sibling_not_child() -> None:
    """Without with_critique, parent_id should NOT be persisted — it's a new sample."""
    run_migrations()
    refresh_artist_profile()
    _, app_id = _seed_opp_and_app()
    fake = _fake_with("first")
    first = generate_or_revise_draft(
        application_id=app_id, kind="cover_letter", client=fake
    )
    fake2 = _fake_with("second")
    second = generate_or_revise_draft(
        application_id=app_id,
        kind="cover_letter",
        parent_id=first.id,
        with_critique=False,
        client=fake2,
    )
    assert second.parent_draft_id is None  # sibling, not child
