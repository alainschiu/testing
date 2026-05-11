"""Critic pass for drafts. Single LLM call against the `_critic.md` rubric;
returns the critique text which is fed back into a revision generation.
"""
from __future__ import annotations

from string import Template

from scout.llm.client import LLMClient
from scout.llm.prompts import load_prompt


def critique_draft(
    *,
    client: LLMClient,
    kind: str,
    draft_text: str,
    opportunity_summary: str,
    run_id: int | None = None,
) -> tuple[str, float]:
    """Return (critique_text, cost_usd)."""
    template = load_prompt("drafting/_critic.md")
    rendered = Template(template).safe_substitute(
        kind=kind,
        opportunity_summary=opportunity_summary,
        draft_text=draft_text,
    )
    resp = client.create_message(
        system="You are a careful critic of arts-funding application drafts. Be terse and specific.",
        messages=[{"role": "user", "content": rendered}],
        max_tokens=2_000,
        effort="medium",
        prompt_template="drafting/_critic.md",
        run_id=run_id,
    )
    cost = resp.usage.cost_usd if resp.usage else 0.0
    return resp.text.strip(), cost
