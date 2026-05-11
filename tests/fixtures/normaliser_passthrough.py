"""Helpers that turn an LLM call into a deterministic normaliser response.

The normaliser receives a user message containing the rendered raw_text from
a raw_finding row (KEY: value lines). For tests we don't want to involve a
real LLM, but we do want the normaliser to produce the same opportunity that
the original raw_text described — otherwise dedup, persistence and the
status-change paths exercise nothing.

`build_passthrough_response` reads the user message, extracts the labelled
fields, and returns a JSON LLMResponse matching the normaliser prompt's
schema. Callable form is what FakeLLMClient invokes per call.
"""
from __future__ import annotations

import json
import re
from typing import Any

from scout.llm.client import LLMResponse, LLMUsage

_LABEL_RX = re.compile(r"^([A-Z][A-Z /()_-]+):\s*(.*)$", re.MULTILINE)

_LABEL_TO_FIELD = {
    "TITLE": "title",
    "ORG": "org",
    "TYPE": "type",
    "URL": "url",
    "DEADLINE": "deadline_raw",
    "LOCATION": "location",
    "DURATION/AMOUNT": "duration_amount",
    "ELIGIBILITY (CITIZENSHIP)": "eligibility_citizenship",
    "ELIGIBILITY (CAREER STAGE)": "eligibility_career_stage",
    "ELIGIBILITY (OTHER)": "eligibility_other",
    "FIT SCORE": "fit_score",
    "ANGLE TO DEPLOY": "primary_angle",
    "ANGLE BACKUP": "backup_angle",
    "KEY ASKS": "key_asks",
    "EFFORT ESTIMATE": "effort_estimate",
    "COMPETITIVENESS": "competitiveness",
    "WHY THIS FITS": "why_fits",
    "RISK/WATCHOUT": "risk_watchout",
}


def _extract_user_text(messages: list[dict[str, Any]]) -> str:
    for m in messages:
        if m.get("role") == "user":
            c = m.get("content", "")
            return c if isinstance(c, str) else json.dumps(c)
    return ""


def _extract_fields(text: str) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for label, value in _LABEL_RX.findall(text):
        canonical = _LABEL_TO_FIELD.get(label.strip().upper())
        if canonical:
            fields[canonical] = value.strip()

    deadline_raw = fields.pop("deadline_raw", "")
    if deadline_raw:
        m = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", deadline_raw)
        if m:
            fields["deadline"] = m.group(1)
        elif re.search(r"rolling", deadline_raw, re.IGNORECASE):
            fields["deadline_note"] = "rolling"
        else:
            fields["deadline_note"] = deadline_raw

    if "fit_score" in fields:
        m = re.search(r"[1-5]", str(fields["fit_score"]))
        fields["fit_score"] = int(m.group(0)) if m else None

    if "type" in fields:
        fields["type"] = fields["type"].lower().split()[0]

    return fields


def _payload(fields: dict[str, Any], *, verdict: str = "opportunity") -> dict[str, Any]:
    if verdict != "opportunity":
        return {"verdict": verdict, "reject_reason": "not_an_opportunity", "opportunities": []}
    return {
        "verdict": "opportunity",
        "reject_reason": None,
        "opportunities": [fields] if fields.get("title") else [],
    }


def build_passthrough_response(call_kwargs: dict[str, Any]) -> LLMResponse:
    user_text = _extract_user_text(call_kwargs.get("messages") or [])
    fields = _extract_fields(user_text)
    payload = _payload(fields)
    return LLMResponse(
        text=json.dumps(payload),
        usage=LLMUsage(model="fake-normaliser", input_tokens=200, output_tokens=120, cost_usd=0.0),
        stop_reason="end_turn",
    )


def build_rejection_response(_call_kwargs: dict[str, Any]) -> LLMResponse:
    payload = {"verdict": "not_an_opportunity", "reject_reason": "not_funding", "opportunities": []}
    return LLMResponse(
        text=json.dumps(payload),
        usage=LLMUsage(model="fake-normaliser", input_tokens=80, output_tokens=20, cost_usd=0.0),
        stop_reason="end_turn",
    )
