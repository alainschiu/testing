"""scout CLI: init | migrate | doctor | run | digest."""
from __future__ import annotations

import sys

import typer

from scout.config import get_settings
from scout.db import connection, run_migrations
from scout.logging import configure_logging, get_logger, redact_secret

app = typer.Typer(no_args_is_help=True, help="Scout — arts-funding discovery agent")
log = get_logger("scout.cli")


@app.command()
def init() -> None:
    """Ensure data dirs exist and run migrations."""
    s = get_settings()
    s.ensure_dirs()
    applied = run_migrations()
    typer.echo(f"data_dir: {s.data_dir}")
    typer.echo(f"db_path:  {s.db_path}")
    typer.echo(f"applied:  {applied or '(already up to date)'}")


@app.command()
def migrate() -> None:
    """Apply pending migrations. Safe to run repeatedly."""
    applied = run_migrations()
    if applied:
        typer.echo(f"applied {len(applied)}: {', '.join(applied)}")
    else:
        typer.echo("up to date")


@app.command()
def doctor() -> None:
    """Green-checks on env, DB, and Anthropic reachability. Network call is best-effort."""
    configure_logging()
    s = get_settings()
    ok = True

    def check(name: str, condition: bool, detail: str = "") -> None:
        nonlocal ok
        symbol = "✓" if condition else "✗"
        typer.echo(f"  {symbol} {name}" + (f" — {detail}" if detail else ""))
        if not condition:
            ok = False

    provider = (s.llm_provider or "anthropic").lower()
    typer.echo(f"env:  (provider: {provider})")
    if provider == "anthropic":
        check("ANTHROPIC_API_KEY", bool(s.anthropic_api_key), redact_secret(s.anthropic_api_key))
    elif provider == "poe":
        check("POE_API_KEY", bool(s.poe_api_key), redact_secret(s.poe_api_key))
        check("POE_DRAFTING_BOT set", bool(s.poe_drafting_bot), s.poe_drafting_bot)
        check("POE_SCOUT_BOT set", bool(s.poe_scout_bot), s.poe_scout_bot)
        if "search" not in (s.poe_scout_bot or "").lower():
            typer.echo(
                "    ⚠ POE_SCOUT_BOT name lacks 'search' — discovery may have no web access"
            )
    else:
        check(f"LLM_PROVIDER={provider}", False, "unknown provider")
    check("data_dir exists", s.data_dir.exists(), str(s.data_dir))
    check("prompts_dir exists", s.prompts_dir.exists(), str(s.prompts_dir))
    check("scout_agent.md present", (s.prompts_dir / "scout_agent.md").exists())

    typer.echo("db:")
    try:
        with connection() as conn:
            conn.execute("SELECT 1").fetchone()
        check("connect", True, str(s.db_path))
    except Exception as e:
        check("connect", False, repr(e))

    typer.echo("migrations:")
    try:
        applied = run_migrations()
        check("idempotent", True, f"new this run: {len(applied)}")
    except Exception as e:
        check("idempotent", False, repr(e))

    typer.echo("provider reachability:")
    if provider == "anthropic":
        if not s.anthropic_api_key:
            check("anthropic", False, "no API key set (skipped)")
        else:
            try:
                import anthropic

                client = anthropic.Anthropic(api_key=s.anthropic_api_key)
                models = client.models.list(limit=1)
                check("anthropic", True, f"first model: {models.data[0].id if models.data else '(empty)'}")
            except Exception as e:
                check("anthropic", False, type(e).__name__)
    elif provider == "poe":
        if not s.poe_api_key:
            check("poe", False, "no API key set (skipped)")
        else:
            try:
                import httpx

                r = httpx.get(
                    "https://api.poe.com/v1/models",
                    headers={"Authorization": f"Bearer {s.poe_api_key}"},
                    timeout=10.0,
                )
                if r.status_code == 200:
                    check("poe", True, f"/v1/models returned {len(r.json().get('data', []))} bots")
                else:
                    check("poe", False, f"HTTP {r.status_code}")
            except Exception as e:
                check("poe", False, type(e).__name__)

    typer.echo("status: " + ("OK" if ok else "FAIL"))
    sys.exit(0 if ok else 1)


@app.command("run")
def run_cmd(
    kind: str = typer.Option("scout", help="Run kind: scout | deadline_check"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Don't write to DB; print plan only"),
) -> None:
    """One-shot scout run. Phase 1 wires the real agent loop."""
    from scout.agents.scout_agent import run_scout

    summary = run_scout(kind=kind, dry_run=dry_run)
    typer.echo(summary)


@app.command()
def digest() -> None:
    """Print a human-readable summary of the latest run."""
    from scout.agents.scout_agent import latest_digest

    typer.echo(latest_digest())


@app.command("normalise")
def normalise_cmd(
    limit: int = typer.Option(50, help="Max pending raw_findings to process"),
) -> None:
    """Drain pending raw_findings: convert to opportunities, reject, or duplicate."""
    from scout.agents.normaliser import normalise_pending, pending_count

    pending = pending_count()
    if pending == 0:
        typer.echo("nothing pending")
        return
    results = normalise_pending(limit=limit)
    counts: dict[str, int] = {}
    cost = 0.0
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
        cost += r.cost_usd
    summary = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
    typer.echo(
        f"processed {len(results)} of {pending} pending — {summary} (cost ${cost:.4f})"
    )


watchers_app = typer.Typer(help="Manage watchers — site/RSS/JSON pollers that feed raw_findings.")
app.add_typer(watchers_app, name="watchers")


@watchers_app.command("list")
def watchers_list() -> None:
    """Show every watcher: state, last check, consecutive failures."""
    from scout.queries_watchers import list_watchers

    rows = list_watchers()
    if not rows:
        typer.echo("(no watchers — run `scout watchers seed` to install the starter set)")
        return
    for w in rows:
        state = "ACTIVE" if w["active"] else "OFF   "
        last = w["last_checked_at"] or "(never)"
        fails = w["consecutive_failures"] or 0
        typer.echo(
            f"  {state}  [{w['id']:>3}]  {w['name']:<32}  "
            f"{w['kind']:<11}  last={last}  fails={fails}"
        )


@watchers_app.command("seed")
def watchers_seed() -> None:
    """Insert the 20 starter watchers from Phase 5b §5b.5. Idempotent."""
    from scout.sources.seed_watchers import seed

    inserted, total = seed()
    typer.echo(f"seeded {inserted}/{total} watchers")


@watchers_app.command("run")
def watchers_run(name_or_id: str = typer.Argument(..., help="Watcher name or numeric id")) -> None:
    """Run one watcher right now (ignores its cron schedule)."""
    from scout.queries_watchers import get_watcher, get_watcher_by_name
    from scout.sources.watcher_runner import run_watcher

    w = None
    if name_or_id.isdigit():
        w = get_watcher(int(name_or_id))
    if w is None:
        w = get_watcher_by_name(name_or_id)
    if w is None:
        typer.echo(f"no watcher matching {name_or_id!r}", err=True)
        raise typer.Exit(1)
    result = run_watcher(w)
    typer.echo(
        f"{result.name}: status={result.status}  inserted={result.findings_inserted}  "
        f"cost=${result.cost_usd:.4f}"
        + (f"  error: {result.error}" if result.error else "")
        + (f"  ({result.detail})" if result.detail else "")
    )


@watchers_app.command("toggle")
def watchers_toggle(
    name_or_id: str = typer.Argument(...),
    active: bool = typer.Option(..., "--active/--inactive"),
) -> None:
    """Activate or deactivate a watcher."""
    from scout.queries_watchers import get_watcher, get_watcher_by_name, set_active

    w = None
    if name_or_id.isdigit():
        w = get_watcher(int(name_or_id))
    if w is None:
        w = get_watcher_by_name(name_or_id)
    if w is None:
        typer.echo(f"no watcher matching {name_or_id!r}", err=True)
        raise typer.Exit(1)
    set_active(int(w["id"]), active)
    typer.echo(f"{w['name']}: active={active}")


@app.command("import-application")
def import_application(
    path: str = typer.Argument(..., help="Path to a .md or .txt past application"),
    opportunity_id: int | None = typer.Option(
        None, "--opportunity-id", help="Derive type/funder from this opportunity"
    ),
    type: str | None = typer.Option(
        None, "--type", help="Opportunity type (when --opportunity-id is omitted)"
    ),
    funder: str | None = typer.Option(
        None, "--funder", help="Funder name (when --opportunity-id is omitted)"
    ),
    result: str = typer.Option("won", "--result", help="won | shortlisted"),
    max_chars: int = typer.Option(8000, help="Truncate excerpt to N chars"),
) -> None:
    """Ingest a past successful application as a few-shot exemplar for drafting."""
    from pathlib import Path

    from scout.db import connection, run_migrations
    from scout.models import utc_now_iso

    run_migrations()
    p = Path(path)
    if not p.exists():
        typer.echo(f"file not found: {p}", err=True)
        raise typer.Exit(1)
    if p.suffix.lower() not in {".md", ".txt"}:
        typer.echo("only .md and .txt are supported in v1 — convert PDFs first", err=True)
        raise typer.Exit(1)
    excerpt = p.read_text(encoding="utf-8")[:max_chars]

    resolved_type = type
    resolved_funder = funder
    if opportunity_id is not None:
        with connection() as conn:
            opp = conn.execute(
                "SELECT type, title FROM opportunities WHERE id = ?", (opportunity_id,)
            ).fetchone()
        if opp is None:
            typer.echo(f"opportunity {opportunity_id} not found", err=True)
            raise typer.Exit(1)
        resolved_type = resolved_type or opp["type"]
        resolved_funder = resolved_funder or opp["title"]

    if not resolved_type:
        typer.echo("--type (or --opportunity-id) is required", err=True)
        raise typer.Exit(1)
    if result not in {"won", "shortlisted"}:
        typer.echo("--result must be 'won' or 'shortlisted'", err=True)
        raise typer.Exit(1)

    with connection() as conn:
        cur = conn.execute(
            "INSERT INTO past_applications"
            " (type, funder, result, excerpt, source_path, opportunity_id, imported_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                resolved_type,
                resolved_funder,
                result,
                excerpt,
                str(p.resolve()),
                opportunity_id,
                utc_now_iso(),
            ),
        )
    typer.echo(
        f"imported #{cur.lastrowid}: {resolved_funder or '(no funder)'} "
        f"[{resolved_type}, {result}, {len(excerpt)} chars]"
    )


if __name__ == "__main__":
    app()
