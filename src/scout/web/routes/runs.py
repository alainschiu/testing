from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from scout.queries import cost_this_month, list_runs
from scout.web.templating import templates

router = APIRouter()


@router.get("/runs", response_class=HTMLResponse)
def runs_view(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "runs.html",
        {"runs": list_runs(), "cost_mtd": cost_this_month()},
    )
