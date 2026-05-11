from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from scout.agents.artist_profile import load_artist_profile
from scout.agents.scout_agent import run_scout
from scout.config import get_settings
from scout.logging import redact_secret
from scout.web.templating import templates

router = APIRouter(prefix="/settings")


def _env_view() -> dict[str, str]:
    s = get_settings()
    return {
        "SCOUT_MODEL": s.scout_model,
        "SCOUT_EFFORT": s.scout_effort,
        "SCOUT_MAX_WEB_SEARCHES": str(s.max_web_searches),
        "SCOUT_MAX_OUTPUT_TOKENS": str(s.max_output_tokens),
        "SCOUT_DATA_DIR": str(s.data_dir),
        "SCOUT_DB_PATH": str(s.db_path),
        "ANTHROPIC_API_KEY": redact_secret(s.anthropic_api_key),
        "TAVILY_API_KEY": redact_secret(s.tavily_api_key),
    }


@router.get("", response_class=HTMLResponse)
def settings_view(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "settings.html",
        {"env_view": _env_view(), "profile": load_artist_profile() or {}},
    )


@router.post("/run-scout", response_class=HTMLResponse)
def trigger_scout(request: Request, dry_run: bool = True) -> HTMLResponse:
    # UI surface only invokes dry-run for now — full runs go via the CLI/cron
    # so we don't accidentally charge from a click.
    summary = run_scout(dry_run=dry_run)
    return HTMLResponse(f"<pre>{summary}</pre>")
