"""Drafting agent. Generates, revises, and persists drafts for one of the
six application components (see prompts/drafting/). Two-pass loop per the
build brief: pass 1 generate → optional pass 2 critique → optional pass 3
revise with critique applied. Each pass is a separate `drafts` row, linked
via `parent_draft_id`.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from string import Template
from typing import Any

from scout.agents.artist_profile import load_artist_profile
from scout.agents.critic import critique_draft
from scout.agents.hallucination import verify_proper_nouns
from scout.config import get_settings
from scout.db import connection
from scout.llm.client import LLMClient
from scout.llm.factory import build_default_client
from scout.llm.prompts import load_prompt
from scout.logging import get_logger
from scout.models import utc_now_iso
from scout.queries import get_opportunity

log = get_logger("scout.drafting")

DRAFT_KINDS = (
    "artist_statement",
    "project_description",
    "budget",
    "cover_letter",
    "cv",
    "work_samples",
)
# CV uses the cv_tailor template.
_TEMPLATE_BY_KIND = {
    "artist_statement": "drafting/artist_statement.md",
    "project_description": "drafting/project_description.md",
    "budget": "drafting/budget.md",
    "cover_letter": "drafting/cover_letter.md",
    "cv": "drafting/cv_tailor.md",
    "work_samples": "drafting/work_samples.md",
}

_DEFAULT_LENGTH = {
    "artist_statement": "500 words (set by variant)",
    "project_description": "match the call's requested word count; if unspecified, 600 words",
    "budget": "match the project scale implied by the opportunity (small €1–5k / medium €5–25k / large €25k+)",
    "cover_letter": "200–300 words",
    "cv": "no length cap; use the section caps in the template",
    "work_samples": "3–5 works, 2–3 sentence framing each",
}

# Angles A–F, one-line rationales sourced from artist_profile.md §6
_ANGLE_RATIONALES = {
    "A": "Drawing Room / loneliness as practice-led research — fits artistic-research, PhD, philosophy-adjacent contexts",
    "B": "Spatial audio / multimedia installation — fits tech-leaning residencies and museum commissions",
    "C": "Hong Kong site / memory / displacement — fits HK and Asia-focused funders",
    "D": "Diasporic / dual-nationality listening — fits Canadian and cross-cultural funders",
    "E": "Political listening / forensic — fits human-rights-adjacent and decolonial programmes",
    "F": "Sound art education / public engagement — fits education foundations and schools-residency programmes",
}


@dataclass
class DraftResult:
    id: int
    application_id: int
    kind: str
    variant: str | None
    content: str
    word_count: int
    model: str
    prompt_template: str
    parent_draft_id: int | None
    cost_usd: float
    hallucination_flags: list[str]
    critique: str | None


# ── helpers ───────────────────────────────────────────────────────────────


def _word_count(text: str) -> int:
    return len(re.findall(r"\S+", text))


def _length_constraint(kind: str, variant: str | None) -> str:
    if kind == "artist_statement" and variant in {"250w", "500w", "1000w"}:
        return f"{variant[:-1]} words (±10%)"
    return _DEFAULT_LENGTH[kind]


def _opportunity_summary(opp: dict) -> str:
    lines = [f"Title: {opp.get('title')}", f"Type: {opp.get('type')}"]
    for k in (
        "deadline",
        "deadline_note",
        "location",
        "amount",
        "duration",
        "eligibility_citizenship",
        "eligibility_career_stage",
        "eligibility_other",
        "primary_angle",
        "backup_angle",
        "fit_score",
        "effort_estimate",
        "competitiveness",
    ):
        v = opp.get(k)
        if v:
            lines.append(f"{k.replace('_',' ').title()}: {v}")
    if opp.get("url"):
        lines.append(f"URL: {opp['url']}")
    if opp.get("why_fits"):
        lines.append("\nFunder's stated priorities (paraphrased): " + opp["why_fits"])
    if opp.get("risk_watchout"):
        lines.append("Watch out for: " + opp["risk_watchout"])
    return "\n".join(lines)


def _past_exemplars(opp_type: str, *, limit: int = 2) -> str:
    with connection() as conn:
        rows = conn.execute(
            "SELECT funder, result, excerpt FROM past_applications"
            " WHERE type = ? AND result IN ('won','shortlisted')"
            " ORDER BY id DESC LIMIT ?",
            (opp_type, limit),
        ).fetchall()
    if not rows:
        return "(no past exemplars available for this opportunity type)"
    blocks = []
    for r in rows:
        blocks.append(
            f"— Past application to {r['funder'] or '?'} (result: {r['result']}):\n{r['excerpt']}"
        )
    return "\n\n".join(blocks)


def _work_samples_table() -> str:
    with connection() as conn:
        rows = conn.execute(
            "SELECT title, year, angle_tags, description, url, file_path, duration_seconds"
            "  FROM work_samples ORDER BY year DESC NULLS LAST"
        ).fetchall()
    if not rows:
        return "(no work samples imported yet — the artist must add some before drafting work_samples)"
    lines = ["Title · Year · Angles · Description · Link"]
    for r in rows:
        link = r["url"] or r["file_path"] or "(no link)"
        secs = f", {r['duration_seconds']}s" if r["duration_seconds"] else ""
        lines.append(
            f"- {r['title']} · {r['year'] or '?'}{secs} · [{r['angle_tags']}] · "
            f"{(r['description'] or '').strip()} · {link}"
        )
    return "\n".join(lines)


def _render_user_prompt(
    *,
    kind: str,
    opp: dict,
    variant: str | None,
    critique: str | None,
) -> str:
    template = load_prompt(_TEMPLATE_BY_KIND[kind])
    angle = (opp.get("primary_angle") or "A").strip().upper()[:1]
    work_samples = _work_samples_table() if kind == "work_samples" else ""
    critique_block = (
        f"\n## Apply this critique in your revision\n\n{critique}\n"
        if critique
        else ""
    )
    return Template(template).safe_substitute(
        artist_profile="(see frozen profile in system prompt)",
        opportunity_summary=_opportunity_summary(opp),
        past_exemplars=_past_exemplars(opp.get("type") or "other"),
        length_constraint=_length_constraint(kind, variant),
        primary_angle=angle,
        angle_rationale=_ANGLE_RATIONALES.get(angle, "no rationale on file"),
        work_samples_table=work_samples,
        why_fits=opp.get("why_fits") or "(none recorded — infer from the opportunity record)",
        critique=critique_block,
    )


def _render_system_prompt() -> str:
    base = load_prompt("drafting/_system.md")
    profile = load_artist_profile() or {}
    return Template(base).safe_substitute(artist_profile=profile.get("markdown", ""))


def _verification_sources(opp: dict, exemplars_text: str) -> list[str]:
    """Sources against which to verify proper nouns in the draft."""
    profile = load_artist_profile() or {}
    return [
        profile.get("markdown", ""),
        exemplars_text,
        opp.get("title") or "",
        opp.get("why_fits") or "",
        opp.get("eligibility_other") or "",
        opp.get("eligibility_citizenship") or "",
        opp.get("location") or "",
        opp.get("url") or "",
    ]


def _insert_draft(
    *,
    application_id: int,
    kind: str,
    variant: str | None,
    content: str,
    model: str,
    prompt_template: str,
    parent_draft_id: int | None,
    cost_usd: float,
    hallucination_flags: list[str],
    critique: str | None,
) -> int:
    now = utc_now_iso()
    with connection() as conn:
        cur = conn.execute(
            "INSERT INTO drafts (application_id, kind, variant, content, word_count,"
            " model, prompt_template, parent_draft_id, cost_usd, hallucination_flags,"
            " critique, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                application_id,
                kind,
                variant,
                content,
                _word_count(content),
                model,
                prompt_template,
                parent_draft_id,
                cost_usd,
                json.dumps(hallucination_flags) if hallucination_flags else None,
                critique,
                now,
            ),
        )
        return int(cur.lastrowid)


def _opp_for_application(application_id: int) -> dict[str, Any]:
    with connection() as conn:
        row = conn.execute(
            "SELECT opportunity_id FROM applications WHERE id = ?",
            (application_id,),
        ).fetchone()
    if row is None:
        raise ValueError(f"no application id {application_id}")
    opp = get_opportunity(row["opportunity_id"])
    if opp is None:
        raise ValueError(f"opportunity {row['opportunity_id']} missing")
    return dict(opp)


# ── public entry point ───────────────────────────────────────────────────


def generate_or_revise_draft(
    *,
    application_id: int,
    kind: str,
    variant: str | None = None,
    parent_id: int | None = None,
    with_critique: bool = False,
    client: LLMClient | None = None,
) -> DraftResult:
    """Produce one draft and persist it.

    - `parent_id=None`, `with_critique=False`: fresh generation.
    - `parent_id=N`, `with_critique=False`: regenerate as a sibling of N
      (no critique loop; new sample of the same prompt).
    - `parent_id=N`, `with_critique=True`: critique N, then revise into a
      child draft (parent_draft_id=N). Two LLM calls; two draft rows
      (critique stored on the *revision* row for traceability).
    """
    if kind not in DRAFT_KINDS:
        raise ValueError(f"unknown draft kind: {kind}")
    s = get_settings()
    if client is None:
        client = build_default_client()

    opp = _opp_for_application(application_id)

    critique_text: str | None = None
    total_cost = 0.0
    if parent_id is not None and with_critique:
        with connection() as conn:
            parent = conn.execute(
                "SELECT * FROM drafts WHERE id = ?", (parent_id,)
            ).fetchone()
        if parent is None:
            raise ValueError(f"parent draft {parent_id} not found")
        critique_text, critique_cost = critique_draft(
            client=client,
            kind=kind,
            draft_text=parent["content"],
            opportunity_summary=_opportunity_summary(opp),
        )
        total_cost += critique_cost
        log.info(
            "draft_critique_done",
            application_id=application_id,
            parent_id=parent_id,
            cost_usd=critique_cost,
        )

    user_prompt = _render_user_prompt(
        kind=kind,
        opp=opp,
        variant=variant,
        critique=critique_text,
    )
    resp = client.create_message(
        system=[
            {
                "type": "text",
                "text": _render_system_prompt(),
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_prompt}],
        max_tokens=8_000,
        thinking={"type": "adaptive"},
        effort="high",
        prompt_template=_TEMPLATE_BY_KIND[kind],
    )
    total_cost += resp.usage.cost_usd if resp.usage else 0.0

    exemplars_text = _past_exemplars(opp.get("type") or "other")
    _, unverified = verify_proper_nouns(
        resp.text, _verification_sources(opp, exemplars_text)
    )

    draft_id = _insert_draft(
        application_id=application_id,
        kind=kind,
        variant=variant,
        content=resp.text.strip(),
        model=s.scout_model,
        prompt_template=_TEMPLATE_BY_KIND[kind],
        parent_draft_id=parent_id if with_critique else None,
        cost_usd=total_cost,
        hallucination_flags=unverified,
        critique=critique_text,
    )
    log.info(
        "draft_generated",
        draft_id=draft_id,
        application_id=application_id,
        kind=kind,
        variant=variant,
        parent_id=parent_id,
        with_critique=with_critique,
        words=_word_count(resp.text),
        cost_usd=total_cost,
        hallucination_flags=len(unverified),
    )
    return DraftResult(
        id=draft_id,
        application_id=application_id,
        kind=kind,
        variant=variant,
        content=resp.text.strip(),
        word_count=_word_count(resp.text),
        model=s.scout_model,
        prompt_template=_TEMPLATE_BY_KIND[kind],
        parent_draft_id=parent_id if with_critique else None,
        cost_usd=total_cost,
        hallucination_flags=unverified,
        critique=critique_text,
    )
