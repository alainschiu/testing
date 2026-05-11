from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from scout.queries import (
    cost_this_month,
    dashboard_columns,
    latest_run,
    status_counts,
    top3_picks,
)
from scout.queries_sources import source_health_summary
from scout.web.templating import templates

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "columns": dashboard_columns(),
            "counts": status_counts(),
            "latest_run": latest_run(),
            "cost_mtd": cost_this_month(),
            "top3": top3_picks(),
            "source_health": source_health_summary(days=7),
        },
    )
