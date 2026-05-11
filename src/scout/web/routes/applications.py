"""Applications + drafting routes. Phase 2 only ships the routing skeleton;
Phase 3 fills in the drafting endpoints and templates."""
from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from scout.db import connection
from scout.models import utc_now_iso
from scout.queries import change_status, get_opportunity
from scout.web.templating import templates

router = APIRouter()


def _ensure_application_for(opp_id: int) -> int:
    """Return an existing in-progress application id, or create one."""
    with connection() as conn:
        row = conn.execute(
            "SELECT id FROM applications WHERE opportunity_id = ? AND status = 'drafting'"
            " ORDER BY id DESC LIMIT 1",
            (opp_id,),
        ).fetchone()
        if row:
            return int(row["id"])
        now = utc_now_iso()
        cur = conn.execute(
            "INSERT INTO applications (opportunity_id, status, created_at, updated_at)"
            " VALUES (?, 'drafting', ?, ?)",
            (opp_id, now, now),
        )
        return int(cur.lastrowid)


def _application_row(app_id: int):
    with connection() as conn:
        return conn.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone()


def _drafts_for(app_id: int) -> list[dict]:
    """Return latest draft per (kind, variant) ordered by kind."""
    with connection() as conn:
        rows = conn.execute(
            "SELECT * FROM drafts WHERE application_id = ?"
            " ORDER BY kind, variant, id DESC",
            (app_id,),
        ).fetchall()
    # Keep only the latest per (kind, variant)
    seen: set[tuple[str, str | None]] = set()
    latest: list[dict] = []
    for r in rows:
        key = (r["kind"], r["variant"])
        if key in seen:
            continue
        seen.add(key)
        latest.append(dict(r))
    return latest


def _draft_chain(draft_id: int) -> list[dict]:
    """Return [root → ... → leaf] for revision history."""
    with connection() as conn:
        chain: list[dict] = []
        cur_id = draft_id
        while cur_id:
            row = conn.execute("SELECT * FROM drafts WHERE id = ?", (cur_id,)).fetchone()
            if row is None:
                break
            chain.append(dict(row))
            cur_id = row["parent_draft_id"]
    return list(reversed(chain))


@router.post("/opportunities/{opp_id}/start-draft")
def start_draft(opp_id: int) -> RedirectResponse:
    if get_opportunity(opp_id) is None:
        raise HTTPException(status_code=404, detail="opportunity not found")
    change_status(opp_id, "drafting", note="started drafting from UI")
    app_id = _ensure_application_for(opp_id)
    return RedirectResponse(url=f"/applications/{app_id}", status_code=303)


@router.get("/applications/{app_id}", response_class=HTMLResponse)
def application_detail(request: Request, app_id: int) -> HTMLResponse:
    app_row = _application_row(app_id)
    if app_row is None:
        raise HTTPException(status_code=404, detail="application not found")
    opp = get_opportunity(app_row["opportunity_id"])
    return templates.TemplateResponse(
        request,
        "applications/detail.html",
        {
            "app": app_row,
            "opp": opp,
            "drafts": _drafts_for(app_id),
        },
    )


@router.post("/applications/{app_id}/drafts", response_class=HTMLResponse)
def create_draft(
    request: Request,
    app_id: int,
    kind: str = Form(...),
    variant: str | None = Form(default=None),
    parent_id: int | None = Form(default=None),
    critique: bool = Form(default=False),
) -> HTMLResponse:
    """Phase 3 entrypoint. Generates (or revises) a draft of `kind`."""
    from scout.agents.drafting_agent import generate_or_revise_draft

    app_row = _application_row(app_id)
    if app_row is None:
        raise HTTPException(status_code=404, detail="application not found")
    try:
        generate_or_revise_draft(
            application_id=app_id,
            kind=kind,
            variant=variant,
            parent_id=parent_id,
            with_critique=critique,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return templates.TemplateResponse(
        request,
        "applications/_tab_panel.html",
        {
            "app": app_row,
            "opp": get_opportunity(app_row["opportunity_id"]),
            "kind": kind,
            "drafts": _drafts_for(app_id),
        },
    )


@router.post("/applications/{app_id}/submit", response_class=HTMLResponse)
def mark_submitted(request: Request, app_id: int) -> HTMLResponse:
    app_row = _application_row(app_id)
    if app_row is None:
        raise HTTPException(status_code=404, detail="application not found")
    now = utc_now_iso()
    with connection() as conn:
        conn.execute(
            "UPDATE applications SET status='submitted', submitted_at=?, updated_at=? WHERE id=?",
            (now, now, app_id),
        )
    change_status(app_row["opportunity_id"], "applied", note=f"app #{app_id} submitted")
    return RedirectResponse(url=f"/opportunities/{app_row['opportunity_id']}", status_code=303)
