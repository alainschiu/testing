"""Normaliser agent. Single code path from raw_finding → opportunity.

Every source (scout_agent, watchers, RSS, email, manual capture) writes a
`raw_findings` row. This module drains pending rows, calls the LLM with
`prompts/normaliser.md` to extract structured opportunities or reject the
finding, and persists the result.

Verdict routing:
- `opportunity` + non-empty list → insert one opportunity per item, dedup by
  url_hash. The first item links back via raw_findings.opportunity_id; if
  multiple items result (multi-call newsletter), the raw_finding row keeps
  the first opportunity_id and the rest are linked via their own
  source_meta_json (`from_raw_finding_id`).
- `not_an_opportunity` → status='rejected', reject_reason set.
- `unclear` → status='error' (visible in the UI for manual triage).
- URL-hash collision → status='duplicate', opportunity_id linked to the
  existing row, existing opportunity's updated_at bumped.

JSON parsing is forgiving — strips ```json fences, trims leading/trailing
prose, and on failure escalates once to the drafting (Opus) bot before
giving up. Two strikes → status='error', preserved for manual review.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from scout.agents.parser import normalize_url, url_hash
from scout.config import get_settings
from scout.db import connection
from scout.llm.client import LLMClient, estimate_cost
from scout.llm.factory import build_default_client, build_normaliser_client
from scout.llm.prompts import load_prompt
from scout.logging import get_logger
from scout.models import OPPORTUNITY_TYPES, utc_now_iso

log = get_logger("scout.normaliser")

_JSON_FENCE_RX = re.compile(r"^```(?:json)?\s*|\s*```\s*$", re.IGNORECASE | re.MULTILINE)
_JSON_OBJECT_RX = re.compile(r"\{.*\}", re.DOTALL)


@dataclass
class NormaliseResult:
    finding_id: int
    status: str  # 'normalised' | 'rejected' | 'duplicate' | 'error'
    opportunity_ids: list[int]
    reject_reason: str | None = None
    cost_usd: float = 0.0
    error: str | None = None


# ── Public API ────────────────────────────────────────────────────────────


def normalise_pending(*, limit: int = 50, client: LLMClient | None = None) -> list[NormaliseResult]:
    """Process up to `limit` pending raw_findings. Returns one result per row.

    Skips rows that have already been retried twice (status='error', attempts>=2)
    so we don't spend money in a loop on the same broken input.
    """
    with connection() as conn:
        rows = conn.execute(
            "SELECT * FROM raw_findings"
            " WHERE status IN ('pending', 'error') AND attempts < 2"
            " ORDER BY captured_at ASC LIMIT ?",
            (limit,),
        ).fetchall()

    if not rows:
        return []

    primary = client or build_normaliser_client()
    fallback = build_default_client() if client is None else client

    results: list[NormaliseResult] = []
    for row in rows:
        results.append(_normalise_one(dict(row), primary=primary, fallback=fallback))
    return results


def pending_count() -> int:
    """Count findings still eligible for normalisation: never tried yet, plus
    'error' rows below the retry cap. Rows that exhaust attempts stay visible
    in the UI but won't burn more LLM calls."""
    with connection() as conn:
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM raw_findings"
            " WHERE status IN ('pending', 'error') AND attempts < 2"
        ).fetchone()["n"]
    return int(n or 0)


def insert_raw_finding(
    *,
    source: str,
    raw_text: str,
    source_url: str | None = None,
    raw_html: str | None = None,
    source_meta: dict[str, Any] | None = None,
) -> int:
    """Insert a raw_finding and return its id. Used by every source."""
    now = utc_now_iso()
    meta = json.dumps(source_meta, ensure_ascii=False) if source_meta else None
    with connection() as conn:
        cur = conn.execute(
            "INSERT INTO raw_findings"
            " (source, source_url, raw_text, raw_html, captured_at, status, source_meta_json)"
            " VALUES (?, ?, ?, ?, ?, 'pending', ?)",
            (source, source_url, raw_text, raw_html, now, meta),
        )
        return int(cur.lastrowid)


# ── Internals ─────────────────────────────────────────────────────────────


def _normalise_one(row: dict[str, Any], *, primary: LLMClient, fallback: LLMClient) -> NormaliseResult:
    finding_id = row["id"]

    # First attempt with cheap normaliser model.
    parsed, cost1, err1 = _call_and_parse(primary, row, prompt_template="normaliser")
    cost = cost1
    if parsed is None:
        # Escalate to the bigger model — common cause is the cheap model
        # wrapping JSON in prose despite the prompt's instruction.
        log.info("normaliser_escalate", finding_id=finding_id, first_error=err1)
        parsed, cost2, err2 = _call_and_parse(fallback, row, prompt_template="normaliser_escalated")
        cost += cost2
        if parsed is None:
            return _record_error(finding_id, attempts_inc=1, error=err2 or err1, cost=cost)

    verdict = (parsed.get("verdict") or "").lower()
    if verdict == "not_an_opportunity":
        return _record_rejected(
            finding_id, reason=parsed.get("reject_reason") or "not_an_opportunity", cost=cost
        )
    if verdict == "unclear":
        return _record_error(finding_id, attempts_inc=1, error="verdict=unclear", cost=cost)
    if verdict != "opportunity":
        return _record_error(finding_id, attempts_inc=1, error=f"unknown verdict: {verdict!r}", cost=cost)

    items = parsed.get("opportunities") or []
    if not isinstance(items, list) or not items:
        return _record_error(
            finding_id, attempts_inc=1, error="verdict=opportunity but opportunities[] empty", cost=cost
        )

    return _persist_opportunities(finding_id, items, source=row["source"], cost=cost)


def _call_and_parse(
    client: LLMClient, row: dict[str, Any], *, prompt_template: str
) -> tuple[dict[str, Any] | None, float, str | None]:
    s = get_settings()
    system_prompt = load_prompt("normaliser.md")
    source_label = row["source"]
    source_url = row.get("source_url") or "(none captured)"
    user_text = (
        f"Source: {source_label}\n"
        f"Source URL: {source_url}\n"
        f"Captured at: {row['captured_at']}\n\n"
        f"--- BEGIN RAW TEXT ---\n{row['raw_text']}\n--- END RAW TEXT ---"
    )

    try:
        resp = client.create_message(
            system=system_prompt,
            messages=[{"role": "user", "content": user_text}],
            max_tokens=8_000,
            prompt_template=prompt_template,
            run_id=None,
        )
    except Exception as e:
        return None, 0.0, f"llm_error: {type(e).__name__}: {e}"

    cost = (
        resp.usage.cost_usd
        if resp.usage and (s.llm_provider or "anthropic").lower() == "anthropic"
        else 0.0
    )
    if not cost and resp.usage and (s.llm_provider or "anthropic").lower() == "anthropic":
        cost = estimate_cost(s.normaliser_model, resp.usage)

    text = resp.text or ""
    parsed = _try_parse_json(text)
    if parsed is None:
        return None, cost, f"json_parse_failed: {text[:200]!r}"
    return parsed, cost, None


def _try_parse_json(text: str) -> dict[str, Any] | None:
    """Forgiving JSON parser: strips fences, finds the outermost {...}."""
    s = text.strip()
    s = _JSON_FENCE_RX.sub("", s).strip()
    try:
        result = json.loads(s)
        return result if isinstance(result, dict) else None
    except json.JSONDecodeError:
        pass
    m = _JSON_OBJECT_RX.search(s)
    if not m:
        return None
    try:
        result = json.loads(m.group(0))
        return result if isinstance(result, dict) else None
    except json.JSONDecodeError:
        return None


def _persist_opportunities(
    finding_id: int, items: list[dict[str, Any]], *, source: str, cost: float
) -> NormaliseResult:
    now = utc_now_iso()
    inserted: list[int] = []
    duplicates: list[tuple[int, int]] = []  # (existing_opp_id, item_idx)

    with connection() as conn:
        for idx, item in enumerate(items):
            url = (item.get("url") or "").strip()
            title = (item.get("title") or "").strip() or "(untitled)"
            h = url_hash(url) if url else f"no-url:{title[:120]}"

            existing = conn.execute(
                "SELECT id FROM opportunities WHERE url_hash = ?", (h,)
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE opportunities SET updated_at = ? WHERE id = ?",
                    (now, existing["id"]),
                )
                duplicates.append((existing["id"], idx))
                continue

            opp_type = (item.get("type") or "other").lower()
            if opp_type not in OPPORTUNITY_TYPES:
                opp_type = "other"

            cur = conn.execute(
                "INSERT INTO opportunities ("
                " title, type, url, url_hash, deadline, deadline_note, location, amount,"
                " eligibility_citizenship, eligibility_career_stage, eligibility_other,"
                " fit_score, primary_angle, backup_angle, why_fits, risk_watchout,"
                " effort_estimate, competitiveness, raw_finding_json, raw_finding_id,"
                " status, discovered_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'lead', ?, ?)",
                (
                    title,
                    opp_type,
                    normalize_url(url) if url else "",
                    h,
                    item.get("deadline"),
                    item.get("deadline_note"),
                    item.get("location"),
                    item.get("duration_amount"),
                    item.get("eligibility_citizenship"),
                    item.get("eligibility_career_stage"),
                    item.get("eligibility_other"),
                    _coerce_fit_score(item.get("fit_score")),
                    item.get("primary_angle"),
                    item.get("backup_angle"),
                    item.get("why_fits"),
                    item.get("risk_watchout"),
                    item.get("effort_estimate"),
                    item.get("competitiveness"),
                    json.dumps(item, ensure_ascii=False),
                    finding_id,
                    now,
                    now,
                ),
            )
            inserted.append(int(cur.lastrowid))

        # Decide what to write back to the raw_finding row.
        if inserted:
            primary_opp = inserted[0]
            status = "normalised"
            reason = None
        elif duplicates:
            primary_opp = duplicates[0][0]
            status = "duplicate"
            reason = "duplicate"
        else:
            primary_opp = None
            status = "error"
            reason = "no_items_persisted"

        conn.execute(
            "UPDATE raw_findings SET status = ?, reject_reason = ?, opportunity_id = ?,"
            " processed_at = ?, attempts = attempts + 1, cost_usd = cost_usd + ?"
            " WHERE id = ?",
            (status, reason, primary_opp, now, cost, finding_id),
        )

    log.info(
        "normalised",
        finding_id=finding_id,
        source=source,
        inserted=len(inserted),
        duplicates=len(duplicates),
        cost_usd=cost,
    )
    return NormaliseResult(
        finding_id=finding_id,
        status=status,
        opportunity_ids=inserted + [d[0] for d in duplicates],
        reject_reason=reason,
        cost_usd=cost,
    )


def _record_rejected(finding_id: int, *, reason: str, cost: float) -> NormaliseResult:
    now = utc_now_iso()
    with connection() as conn:
        conn.execute(
            "UPDATE raw_findings SET status='rejected', reject_reason=?, processed_at=?,"
            " attempts = attempts + 1, cost_usd = cost_usd + ? WHERE id = ?",
            (reason, now, cost, finding_id),
        )
    log.info("rejected", finding_id=finding_id, reason=reason, cost_usd=cost)
    return NormaliseResult(
        finding_id=finding_id, status="rejected", opportunity_ids=[], reject_reason=reason, cost_usd=cost
    )


def _record_error(finding_id: int, *, attempts_inc: int, error: str | None, cost: float) -> NormaliseResult:
    now = utc_now_iso()
    with connection() as conn:
        conn.execute(
            "UPDATE raw_findings SET status='error', reject_reason=?, processed_at=?,"
            " attempts = attempts + ?, cost_usd = cost_usd + ? WHERE id = ?",
            (error[:200] if error else None, now, attempts_inc, cost, finding_id),
        )
    log.warning("normaliser_error", finding_id=finding_id, error=error, cost_usd=cost)
    return NormaliseResult(
        finding_id=finding_id, status="error", opportunity_ids=[], error=error, cost_usd=cost
    )


def _coerce_fit_score(val: Any) -> int | None:
    if val is None:
        return None
    try:
        n = int(val)
    except (TypeError, ValueError):
        return None
    return max(1, min(5, n))
