"""Scout discovery loop.

Loads `prompts/scout_agent.md` as the system prompt, issues a user brief
(first-run or weekly), runs Claude with Anthropic's server-side web_search tool
capped at SCOUT_MAX_WEB_SEARCHES, parses the §8 output blocks, dedupes within
the response, and writes one row per finding to the `raw_findings` staging
table (Phase 5a). The normaliser then runs to convert pending findings into
canonical `opportunities` rows. Robust against pause_turn, malformed blocks,
and duplicate URLs.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from scout.agents.artist_profile import refresh_artist_profile
from scout.agents.normaliser import insert_raw_finding, normalise_pending
from scout.agents.parser import deduplicate, parse_findings
from scout.config import get_settings
from scout.db import connection
from scout.llm.client import LLMClient, LLMResponse, LLMUsage, estimate_cost
from scout.llm.factory import build_default_client
from scout.llm.prompts import load_prompt
from scout.llm.web_search import ANTHROPIC_WEB_SEARCH_TOOL
from scout.logging import get_logger
from scout.models import ParsedFinding, utc_now_iso

log = get_logger("scout.agent")

_MAX_PAUSE_TURNS = 5  # hard cap on pause_turn cycles per run


def _is_first_run() -> bool:
    with connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM runs WHERE kind='scout' AND status='success'"
        ).fetchone()
    return (row["n"] or 0) == 0


def _build_user_brief(*, is_first_run: bool, today_iso: str) -> str:
    if is_first_run:
        return (
            f"Today's date is {today_iso}. Run the §12 first-run brief from your instructions:\n"
            "1. Sweep the §7 funder universe at headline level — surface anything live with a deadline in the next 90 days.\n"
            "2. Add at least 5 PhD-with-funding opportunities (deadlines 6–12 months out) across UK, EU, AU, and Canada.\n"
            "3. Return the structured list per §8 — segment into:\n"
            "   - Section A: deadlines ≤ 30 days\n"
            "   - Section B: deadlines 30–90 days\n"
            "   - Section C: longer-horizon flagships + PhDs\n"
            "4. End with TOP 3 FOR THIS WEEK and WHAT'S ABSENT.\n"
            "Hard guardrails: cap searches per your tool budget; verify deadlines on the funder's own site; "
            "cite direct call URLs, not homepages; honestly score fit (1-5)."
        )
    return (
        f"Today's date is {today_iso}. Run the weekly variant.\n"
        "1. Refresh deadlines and add any new live calls since the prior run.\n"
        "2. Segment per §9: (i) ≤30 days, (ii) 30–90 days, (iii) longer-horizon flagships + PhDs.\n"
        "3. Return structured per §8.\n"
        "4. End with TOP 3 FOR THIS WEEK and WHAT'S ABSENT.\n"
        "Honest fit scoring; verify deadlines on funder sites; deduplicate against aggregators."
    )


def _start_run(kind: str) -> int:
    now = utc_now_iso()
    with connection() as conn:
        cur = conn.execute(
            "INSERT INTO runs (kind, started_at, status) VALUES (?, ?, 'running')",
            (kind, now),
        )
        return int(cur.lastrowid)


def _finish_run(
    run_id: int,
    *,
    status: str,
    found: int = 0,
    added: int = 0,
    cost_usd: float = 0.0,
    log_path: str | None = None,
    error: str | None = None,
) -> None:
    with connection() as conn:
        conn.execute(
            "UPDATE runs SET finished_at=?, status=?, opportunities_found=?,"
            " opportunities_added=?, cost_usd=?, log_path=?, error=? WHERE id=?",
            (utc_now_iso(), status, found, added, cost_usd, log_path, error, run_id),
        )


def _persist_raw_findings(findings: list[ParsedFinding], *, run_id: int) -> list[int]:
    """Write one raw_findings row per parsed §8 block. Returns inserted IDs.

    Phase 5a refactor: scout_agent no longer writes to `opportunities` directly.
    The block text + structured fields are stored as the raw_text body, and the
    parsed fields go into source_meta_json so the normaliser has structured
    data (cheap to re-validate) rather than free-form prose (expensive)."""
    ids: list[int] = []
    for f in findings:
        rendered = _render_finding_for_raw_text(f)
        meta = {
            "scout_run_id": run_id,
            "parsed_finding": asdict(f),
        }
        # Drop the bulky raw_block from meta — it's already in raw_text.
        meta["parsed_finding"].pop("raw_block", None)
        ids.append(
            insert_raw_finding(
                source="scout_agent",
                raw_text=rendered,
                source_url=f.url or None,
                source_meta=meta,
            )
        )
    return ids


def _render_finding_for_raw_text(f: ParsedFinding) -> str:
    """Render a parsed finding back as text the normaliser can re-extract from.

    Preserves the original block plus a structured rendering — defensive against
    parser drift between scout output and what the normaliser expects."""
    pairs = [
        ("TITLE", f.title),
        ("ORG", f.org),
        ("TYPE", f.type),
        ("URL", f.url),
        ("DEADLINE", f.deadline or f.deadline_note),
        ("LOCATION", f.location),
        ("DURATION/AMOUNT", f.duration_amount),
        ("ELIGIBILITY (citizenship)", f.eligibility_citizenship),
        ("ELIGIBILITY (career stage)", f.eligibility_career_stage),
        ("ELIGIBILITY (other)", f.eligibility_other),
        ("FIT SCORE", f.fit_score),
        ("ANGLE TO DEPLOY", f.primary_angle),
        ("ANGLE BACKUP", f.backup_angle),
        ("KEY ASKS", f.key_asks),
        ("EFFORT ESTIMATE", f.effort_estimate),
        ("COMPETITIVENESS", f.competitiveness),
        ("WHY THIS FITS", f.why_fits),
        ("RISK/WATCHOUT", f.risk_watchout),
    ]
    lines = [f"{label}: {value}" for label, value in pairs if value not in (None, "")]
    structured = "\n".join(lines)
    if f.raw_block and f.raw_block.strip() not in structured:
        return f"{structured}\n\n--- ORIGINAL BLOCK ---\n{f.raw_block.strip()}"
    return structured


def _write_run_log(run_id: int, payload: dict) -> Path:
    s = get_settings()
    s.runs_dir.mkdir(parents=True, exist_ok=True)
    path = s.runs_dir / f"{run_id:06d}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _quarantine(run_id: int, blocks: list[str]) -> Path | None:
    if not blocks:
        return None
    s = get_settings()
    qdir = s.runs_dir / f"{run_id:06d}_quarantine"
    qdir.mkdir(parents=True, exist_ok=True)
    for i, b in enumerate(blocks):
        (qdir / f"block_{i:03d}.txt").write_text(b, encoding="utf-8")
    return qdir


def _call_scout_agent(llm: LLMClient, *, run_id: int, today_iso: str) -> LLMResponse:
    s = get_settings()
    system_prompt = load_prompt("scout_agent.md")
    user_text = _build_user_brief(is_first_run=_is_first_run(), today_iso=today_iso)

    # Server-side web_search is Anthropic-only. On other providers (e.g. Poe),
    # discovery routes to a search-capable bot which handles retrieval itself,
    # so we pass tools=None.
    tools: list[dict] | None = None
    if (s.llm_provider or "anthropic").lower() == "anthropic":
        tools = [{**ANTHROPIC_WEB_SEARCH_TOOL, "max_uses": s.max_web_searches}]

    messages: list[dict] = [{"role": "user", "content": user_text}]
    text_parts: list[str] = []
    accumulated = LLMUsage(model=s.scout_model)
    last_stop: str | None = None

    for cycle in range(_MAX_PAUSE_TURNS):
        resp = llm.create_message(
            system=[
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=messages,
            max_tokens=s.max_output_tokens,
            tools=tools,
            thinking={"type": "adaptive"},
            effort=s.scout_effort,
            prompt_template="scout_agent",
            run_id=run_id,
        )
        text_parts.append(resp.text)
        last_stop = resp.stop_reason
        if resp.usage:
            accumulated.input_tokens += resp.usage.input_tokens
            accumulated.output_tokens += resp.usage.output_tokens
            accumulated.cache_creation_input_tokens += resp.usage.cache_creation_input_tokens
            accumulated.cache_read_input_tokens += resp.usage.cache_read_input_tokens
        if resp.stop_reason != "pause_turn":
            break
        # Server-side tool hit its iteration limit; re-send to continue per the skill guidance.
        messages.append({"role": "assistant", "content": resp.raw_content})
        log.info("pause_turn_resume", run_id=run_id, cycle=cycle + 1)

    # Cost estimator assumes Anthropic per-token pricing. Poe is points-based
    # and the per-message cost varies by bot, so we report 0 and rely on the
    # Poe dashboard for actual spend.
    if (s.llm_provider or "anthropic").lower() == "anthropic":
        accumulated.cost_usd = estimate_cost(s.scout_model, accumulated)
    return LLMResponse(
        text="\n\n".join(text_parts),
        raw_content=[],
        usage=accumulated,
        stop_reason=last_stop,
    )


def run_scout(
    *,
    kind: str = "scout",
    dry_run: bool = False,
    client: LLMClient | None = None,
    today_iso: str | None = None,
) -> str:
    """Execute one discovery run. Returns a one-line summary."""
    s = get_settings()
    today_iso = today_iso or utc_now_iso()[:10]
    refresh_artist_profile()

    if dry_run:
        provider = (s.llm_provider or "anthropic").lower()
        if provider == "poe":
            return (
                f"dry-run: would call Poe bot '{s.poe_scout_bot}' with scout_agent.md "
                f"as system ({s.max_output_tokens} output tokens; provider handles search)"
            )
        return (
            f"dry-run: would call {s.scout_model} with scout_agent.md as system "
            f"(max {s.max_web_searches} searches, {s.max_output_tokens} output tokens)"
        )

    if client is None:
        client = build_default_client()

    run_id = _start_run(kind)
    log.info("scout_run_start", run_id=run_id, kind=kind, today=today_iso)
    try:
        resp = _call_scout_agent(client, run_id=run_id, today_iso=today_iso)
        findings, quarantined = parse_findings(resp.text)
        unique, dropped_in_response = deduplicate(findings)
        qdir = _quarantine(run_id, quarantined)
        raw_ids = _persist_raw_findings(unique, run_id=run_id)

        # Drain the staging table immediately so a single `scout run` produces
        # opportunities end-to-end. The 15-min scheduler is a backstop for
        # findings inserted by other sources between runs. Pass the same
        # client through so test injection still works (a single FakeLLMClient
        # scripts both `scout_agent` and `normaliser` templates).
        norm_results = normalise_pending(limit=max(len(raw_ids), 1), client=client)
        added = sum(1 for r in norm_results if r.status == "normalised")
        rejected = sum(1 for r in norm_results if r.status == "rejected")
        duplicates = sum(1 for r in norm_results if r.status == "duplicate")
        norm_errors = sum(1 for r in norm_results if r.status == "error")
        norm_cost = sum(r.cost_usd for r in norm_results)

        scout_cost = resp.usage.cost_usd if resp.usage else 0.0
        cost = scout_cost + norm_cost
        log_path = _write_run_log(
            run_id,
            {
                "run_id": run_id,
                "kind": kind,
                "today": today_iso,
                "model": s.scout_model,
                "stop_reason": resp.stop_reason,
                "parsed_findings": len(findings),
                "deduped_in_response": dropped_in_response,
                "quarantined_blocks": len(quarantined),
                "quarantine_dir": str(qdir) if qdir else None,
                "raw_findings_inserted": len(raw_ids),
                "normaliser": {
                    "normalised": added,
                    "rejected": rejected,
                    "duplicates": duplicates,
                    "errors": norm_errors,
                    "cost_usd": norm_cost,
                },
                "scout_cost_usd": scout_cost,
                "cost_usd": cost,
                "usage": asdict(resp.usage) if resp.usage else None,
                "text": resp.text,
            },
        )
        _finish_run(
            run_id,
            status="success",
            found=len(findings),
            added=added,
            cost_usd=cost,
            log_path=str(log_path),
        )
        log.info(
            "scout_run_done",
            run_id=run_id,
            found=len(findings),
            raw_inserted=len(raw_ids),
            added=added,
            duplicates=duplicates,
            rejected=rejected,
            normaliser_errors=norm_errors,
            quarantined=len(quarantined),
            cost_usd=cost,
        )
        return (
            f"run {run_id}: parsed {len(findings)}, added {added}, "
            f"duplicates {duplicates}, rejected {rejected}, "
            f"quarantined {len(quarantined)}, cost ${cost:.4f}"
        )
    except Exception as e:
        log.exception("scout_run_error", run_id=run_id, error=repr(e))
        _finish_run(run_id, status="error", error=repr(e))
        raise


# ── Digest ────────────────────────────────────────────────────────────────


def latest_digest() -> str:
    """Human-readable summary of the latest scout run + top 3 by (deadline × fit)."""
    with connection() as conn:
        run = conn.execute(
            "SELECT * FROM runs WHERE kind='scout' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        counts = {
            r["status"]: r["n"]
            for r in conn.execute(
                "SELECT status, COUNT(*) AS n FROM opportunities GROUP BY status"
            )
        }
        # Top 3 by (fit desc, soonest deadline first, NULLs last)
        top = conn.execute(
            "SELECT id, title, deadline, deadline_note, fit_score, primary_angle, status,"
            "       eligibility_citizenship, why_fits"
            "  FROM opportunities"
            " WHERE status IN ('lead', 'triaged')"
            " ORDER BY fit_score DESC NULLS LAST,"
            "          CASE WHEN deadline IS NULL THEN 1 ELSE 0 END,"
            "          deadline ASC"
            " LIMIT 3"
        ).fetchall()

    lines: list[str] = []
    if run is None:
        return "no scout runs yet. run `scout run` to seed the catalogue."

    lines.append(f"Latest scout run: id={run['id']}  status={run['status']}  "
                 f"started={run['started_at']}  cost=${run['cost_usd'] or 0:.4f}")
    lines.append(f"  found={run['opportunities_found']}  added={run['opportunities_added']}"
                 f"  log={run['log_path'] or '(none)'}")
    if run["error"]:
        lines.append(f"  error: {run['error']}")

    lines.append("")
    lines.append("Catalogue by status:")
    if not counts:
        lines.append("  (empty)")
    for status, n in sorted(counts.items()):
        lines.append(f"  {status:>10}  {n}")

    lines.append("")
    lines.append("Top 3 (fit × deadline, status=lead|triaged):")
    if not top:
        lines.append("  (no live leads)")
    for row in top:
        deadline_display = row["deadline"] or row["deadline_note"] or "—"
        lines.append(
            f"  [{row['id']:>4}] fit={row['fit_score'] or '?'}  due {deadline_display}  "
            f"angle {row['primary_angle'] or '?'}  — {row['title']}"
        )
        if row["why_fits"]:
            short = row["why_fits"].split("\n", 1)[0][:120]
            lines.append(f"         {short}")

    return "\n".join(lines)
