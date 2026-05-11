"""Scout discovery loop.

Loads `prompts/scout_agent.md` as the system prompt, issues a user brief
(first-run or weekly), runs Claude with Anthropic's server-side web_search tool
capped at SCOUT_MAX_WEB_SEARCHES, parses the §8 output blocks, dedupes by
url_hash, and persists. Robust against pause_turn, malformed blocks, and
duplicate URLs (build brief §1.2-1.4).
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from scout.agents.artist_profile import refresh_artist_profile
from scout.agents.parser import deduplicate, normalize_url, parse_findings, url_hash
from scout.config import get_settings
from scout.db import connection
from scout.llm.client import AnthropicClient, LLMClient, LLMResponse, LLMUsage, estimate_cost
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


def _persist_findings(findings: list[ParsedFinding], *, run_id: int) -> tuple[int, int]:
    """Insert new opportunities, skip those whose url_hash exists. Returns (added, skipped)."""
    added = skipped = 0
    now = utc_now_iso()
    with connection() as conn:
        for f in findings:
            # No URL → still useful to record; synthetic hash keeps the unique constraint happy.
            h = url_hash(f.url) if f.url else "no-url:" + (f.title or "")[:120]
            existing = conn.execute(
                "SELECT id FROM opportunities WHERE url_hash=?", (h,)
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE opportunities SET updated_at=? WHERE id=?",
                    (now, existing["id"]),
                )
                skipped += 1
                continue
            conn.execute(
                "INSERT INTO opportunities ("
                " title, type, url, url_hash, deadline, deadline_note, location, amount,"
                " eligibility_citizenship, eligibility_career_stage, eligibility_other,"
                " fit_score, primary_angle, backup_angle, why_fits, risk_watchout,"
                " effort_estimate, competitiveness, raw_finding_json, source_run_id,"
                " status, discovered_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'lead', ?, ?)",
                (
                    f.title,
                    (f.type or "other"),
                    normalize_url(f.url) if f.url else "",
                    h,
                    f.deadline,
                    f.deadline_note,
                    f.location,
                    f.duration_amount,
                    f.eligibility_citizenship,
                    f.eligibility_career_stage,
                    f.eligibility_other,
                    f.fit_score,
                    f.primary_angle,
                    f.backup_angle,
                    f.why_fits,
                    f.risk_watchout,
                    f.effort_estimate,
                    f.competitiveness,
                    json.dumps(asdict(f), ensure_ascii=False),
                    run_id,
                    now,
                    now,
                ),
            )
            added += 1
    return added, skipped


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

    web_search_tool = {**ANTHROPIC_WEB_SEARCH_TOOL, "max_uses": s.max_web_searches}
    tools = [web_search_tool]

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
        return (
            f"dry-run: would call {s.scout_model} with scout_agent.md as system "
            f"(max {s.max_web_searches} searches, {s.max_output_tokens} output tokens)"
        )

    if client is None:
        if not s.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set; cannot run scout without --dry-run")
        client = AnthropicClient(api_key=s.anthropic_api_key, model=s.scout_model)

    run_id = _start_run(kind)
    log.info("scout_run_start", run_id=run_id, kind=kind, today=today_iso)
    try:
        resp = _call_scout_agent(client, run_id=run_id, today_iso=today_iso)
        findings, quarantined = parse_findings(resp.text)
        unique, dropped_in_response = deduplicate(findings)
        qdir = _quarantine(run_id, quarantined)
        added, skipped = _persist_findings(unique, run_id=run_id)

        cost = resp.usage.cost_usd if resp.usage else 0.0
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
                "added": added,
                "db_dedup_skipped": skipped,
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
            added=added,
            db_dedup_skipped=skipped,
            quarantined=len(quarantined),
            cost_usd=cost,
        )
        return (
            f"run {run_id}: parsed {len(findings)}, added {added}, "
            f"dedup-in-db {skipped}, quarantined {len(quarantined)}, "
            f"cost ${cost:.4f}"
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
