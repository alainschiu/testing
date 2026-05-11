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

    typer.echo("env:")
    check("ANTHROPIC_API_KEY", bool(s.anthropic_api_key), redact_secret(s.anthropic_api_key))
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

    typer.echo("anthropic:")
    if not s.anthropic_api_key:
        check("reachable", False, "no API key set (skipped)")
    else:
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=s.anthropic_api_key)
            models = client.models.list(limit=1)
            check("reachable", True, f"first model: {models.data[0].id if models.data else '(empty)'}")
        except Exception as e:
            check("reachable", False, type(e).__name__)

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


if __name__ == "__main__":
    app()
