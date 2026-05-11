"""Watcher management UI. Phase 5b.6: list / toggle / delete / run-now.

Edit forms and the test-run preview are deferred to a follow-up; the CLI
(`scout watchers run/seed/toggle`) covers those flows for now."""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from scout.queries_watchers import (
    delete_watcher,
    get_watcher,
    list_watchers,
    set_active,
)
from scout.sources.watcher_runner import run_watcher
from scout.web.templating import templates

router = APIRouter()


@router.get("/watchers", response_class=HTMLResponse)
def watchers_index(request: Request) -> HTMLResponse:
    rows = list_watchers()
    return templates.TemplateResponse(
        request, "watchers.html", {"watchers": rows}
    )


@router.post("/watchers/{watcher_id}/toggle")
def watcher_toggle(watcher_id: int, active: bool = Form(...)) -> RedirectResponse:
    set_active(watcher_id, active)
    return RedirectResponse("/watchers", status_code=303)


@router.post("/watchers/{watcher_id}/delete")
def watcher_delete(watcher_id: int) -> RedirectResponse:
    delete_watcher(watcher_id)
    return RedirectResponse("/watchers", status_code=303)


@router.post("/watchers/{watcher_id}/run", response_class=HTMLResponse)
def watcher_run_now(request: Request, watcher_id: int) -> HTMLResponse:
    w = get_watcher(watcher_id)
    if w is None:
        return HTMLResponse("watcher not found", status_code=404)
    result = run_watcher(w)
    refreshed = get_watcher(watcher_id)
    return templates.TemplateResponse(
        request,
        "_watcher_row.html",
        {"w": refreshed, "result": result},
    )
