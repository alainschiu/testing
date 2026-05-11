"""Jinja2 environment + template helpers.

Centralised so routes don't re-instantiate the Environment and we can register
shared filters (status pill colours, deadline urgency) in one place.
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from fastapi.templating import Jinja2Templates

TEMPLATES_DIR = Path(__file__).parent / "templates"

_STATUS_CLASS = {
    "lead": "bg-slate-100 text-slate-700 dark:bg-slate-700 dark:text-slate-200",
    "triaged": "bg-blue-100 text-blue-800 dark:bg-blue-800 dark:text-blue-100",
    "drafting": "bg-amber-100 text-amber-800 dark:bg-amber-800 dark:text-amber-100",
    "applied": "bg-purple-100 text-purple-800 dark:bg-purple-800 dark:text-purple-100",
    "won": "bg-green-100 text-green-800 dark:bg-green-800 dark:text-green-100",
    "lost": "bg-neutral-100 text-neutral-600 dark:bg-neutral-700 dark:text-neutral-300",
    "withdrawn": "bg-neutral-100 text-neutral-600 dark:bg-neutral-700 dark:text-neutral-300",
    "dismissed": "bg-neutral-100 text-neutral-500 line-through dark:bg-neutral-800 dark:text-neutral-400",
}


def status_class(status: str | None) -> str:
    return _STATUS_CLASS.get(status or "lead", _STATUS_CLASS["lead"])


def days_until(iso: str | None) -> int | None:
    if not iso:
        return None
    try:
        d = datetime.strptime(iso[:10], "%Y-%m-%d").date()
    except ValueError:
        return None
    return (d - date.today()).days


def deadline_class(iso: str | None) -> str:
    n = days_until(iso)
    if n is None:
        return "text-slate-400 dark:text-slate-500"
    if n < 0:
        return "text-slate-400 line-through dark:text-slate-500"
    if n <= 7:
        return "text-red-600 font-semibold dark:text-red-400"
    if n <= 14:
        return "text-amber-600 font-semibold dark:text-amber-400"
    if n <= 30:
        return "text-slate-800 dark:text-slate-200"
    return "text-slate-500 dark:text-slate-400"


def deadline_label(iso: str | None, note: str | None) -> str:
    if iso:
        n = days_until(iso)
        suffix = f" ({n}d)" if n is not None and n >= 0 else " (past)"
        return iso + suffix
    return note or "—"


def fit_dots(score: int | None) -> str:
    if not score:
        return "·····"
    return "●" * int(score) + "·" * max(0, 5 - int(score))


def short_text(s: str | None, limit: int = 140) -> str:
    if not s:
        return ""
    s = s.strip().replace("\n", " ")
    return s if len(s) <= limit else s[: limit - 1].rstrip() + "…"


def usd(value: float | int | None) -> str:
    return f"${(value or 0):.4f}"


def _fromjson(s: str | None):
    import json
    if not s:
        return None
    try:
        return json.loads(s)
    except (TypeError, ValueError):
        return None


templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.filters["status_class"] = status_class
templates.env.filters["deadline_class"] = deadline_class
templates.env.filters["deadline_label"] = deadline_label
templates.env.filters["days_until"] = days_until
templates.env.filters["fit_dots"] = fit_dots
templates.env.filters["short"] = short_text
templates.env.filters["usd"] = usd
templates.env.filters["fromjson"] = _fromjson
