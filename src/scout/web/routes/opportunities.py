from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from scout.queries import (
    change_status,
    get_opportunity,
    list_opportunities_with_search,
    status_history,
    update_opportunity_field,
)
from scout.web.templating import templates

router = APIRouter(prefix="/opportunities")


def _application_rows(opp_id: int) -> list[dict]:
    from scout.db import connection

    with connection() as conn:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT id, status, created_at FROM applications"
                " WHERE opportunity_id = ? ORDER BY id DESC",
                (opp_id,),
            )
        ]


@router.get("", response_class=HTMLResponse)
def list_view(
    request: Request,
    search: str | None = Query(default=None),
    status: str | None = Query(default=None),
    type: str | None = Query(default=None),
    citizenship: str | None = Query(default=None),
    min_fit: int | None = Query(default=None),
    angle: str | None = Query(default=None),
    within: int | None = Query(default=None),
) -> HTMLResponse:
    rows = list_opportunities_with_search(
        search=search,
        statuses=[status] if status else None,
        types=[type] if type else None,
        citizenship=citizenship,
        min_fit=min_fit if (min_fit and min_fit > 1) else None,
        angle=angle,
        deadline_within_days=within,
    )
    return templates.TemplateResponse(
        request,
        "opportunities/list.html",
        {
            "rows": rows,
            "filters": {
                "search": search,
                "status": status,
                "type": type,
                "citizenship": citizenship,
                "min_fit": min_fit,
                "angle": angle,
                "within": within,
            },
        },
    )


@router.get("/{opp_id}", response_class=HTMLResponse)
def detail_view(request: Request, opp_id: int) -> HTMLResponse:
    opp = get_opportunity(opp_id)
    if opp is None:
        raise HTTPException(status_code=404, detail="opportunity not found")
    return templates.TemplateResponse(
        request,
        "opportunities/detail.html",
        {
            "opp": opp,
            "history": status_history(opp_id),
            "applications": _application_rows(opp_id),
        },
    )


@router.post("/{opp_id}/field", response_class=HTMLResponse)
def patch_field(
    request: Request,
    opp_id: int,
    field: str = Form(...),
    value: str = Form(""),
) -> HTMLResponse:
    if get_opportunity(opp_id) is None:
        raise HTTPException(status_code=404, detail="opportunity not found")
    try:
        update_opportunity_field(opp_id, field, value or None)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    opp = get_opportunity(opp_id)
    return templates.TemplateResponse(
        request, "opportunities/_field.html", {"opp": opp, "field": field}
    )


@router.post("/{opp_id}/status", response_class=HTMLResponse)
def transition_status(request: Request, opp_id: int, to: str) -> HTMLResponse:
    if get_opportunity(opp_id) is None:
        raise HTTPException(status_code=404, detail="opportunity not found")
    try:
        opp = change_status(opp_id, to)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if opp is None:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request, "opportunities/_status_buttons.html", {"opp": opp}
    )
