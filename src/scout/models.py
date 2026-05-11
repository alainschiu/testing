"""Plain dataclasses for typed access to DB rows. Not an ORM."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

OPPORTUNITY_STATUSES = (
    "lead", "triaged", "drafting", "applied", "won", "lost", "withdrawn", "dismissed",
)
OPPORTUNITY_TYPES = (
    "residency", "grant", "commission", "fellowship", "prize", "phd", "other",
)


@dataclass
class Opportunity:
    title: str
    type: str
    url: str
    url_hash: str
    discovered_at: str
    updated_at: str
    id: int | None = None
    funder_id: int | None = None
    deadline: str | None = None
    deadline_note: str | None = None
    location: str | None = None
    amount: str | None = None
    duration: str | None = None
    eligibility_citizenship: str | None = None
    eligibility_career_stage: str | None = None
    eligibility_other: str | None = None
    fit_score: int | None = None
    primary_angle: str | None = None
    backup_angle: str | None = None
    why_fits: str | None = None
    risk_watchout: str | None = None
    effort_estimate: str | None = None
    competitiveness: str | None = None
    raw_finding_json: str | None = None
    source_run_id: int | None = None
    status: str = "lead"
    status_changed_at: str | None = None
    user_notes: str | None = None


@dataclass
class Run:
    kind: str
    started_at: str
    status: str
    id: int | None = None
    finished_at: str | None = None
    opportunities_found: int = 0
    opportunities_added: int = 0
    cost_usd: float = 0.0
    log_path: str | None = None
    error: str | None = None


@dataclass
class ParsedFinding:
    """Structured representation of one §8 output block from the scout agent."""
    title: str
    org: str | None
    type: str | None
    url: str | None
    deadline: str | None
    deadline_note: str | None
    location: str | None
    duration_amount: str | None
    eligibility_citizenship: str | None
    eligibility_career_stage: str | None
    eligibility_other: str | None
    fit_score: int | None
    primary_angle: str | None
    backup_angle: str | None
    key_asks: str | None
    effort_estimate: str | None
    competitiveness: str | None
    why_fits: str | None
    risk_watchout: str | None
    raw_block: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


def utc_now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"
