"""Parser for the §8 output blocks the scout agent produces.

The agent's output is a sequence of fielded blocks separated by long horizontal
rules. Real-world output drifts: markdown bolding (`**TITLE:**`), extra blank
lines, missing optional fields, headings before/after the list. The parser must
be tolerant — when one block fails to parse, log it verbatim and skip rather
than killing the run (build brief §1.3).
"""
from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable

from scout.logging import get_logger
from scout.models import OPPORTUNITY_TYPES, ParsedFinding

log = get_logger("scout.parser")

# Long horizontal rule. The brief uses U+2500 BOX DRAWINGS LIGHT HORIZONTAL,
# but we accept regular hyphens / em-dashes / underscores too.
_SEPARATOR_RX = re.compile(r"^[\s]*[─\-—_]{5,}[\s]*$", re.MULTILINE)

# Strip surrounding **bold** or __bold__ off a label or value.
_BOLD_RX = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")

# ISO date anywhere in a string.
_ISO_DATE_RX = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")

# A "rolling" deadline indicator.
_ROLLING_RX = re.compile(r"\brolling\b", re.IGNORECASE)


# Label → field name. Order matters: longer/more-specific labels first so
# "ELIGIBILITY (citizenship)" wins over "ELIGIBILITY".
_LABELS: list[tuple[str, str]] = [
    (r"ELIGIBILITY\s*\(\s*citizenship\s*\)", "eligibility_citizenship"),
    (r"ELIGIBILITY\s*\(\s*career\s*stage\s*\)", "eligibility_career_stage"),
    (r"ELIGIBILITY\s*\(\s*other\s*\)", "eligibility_other"),
    (r"WHY\s*THIS\s*FITS(?:\s*\([^)]*\))?", "why_fits"),
    (r"RISK\s*/?\s*WATCH[\s-]?OUT", "risk_watchout"),
    (r"ANGLE\s*TO\s*DEPLOY", "primary_angle"),
    (r"ANGLE\s*BACKUP", "backup_angle"),
    (r"FIT\s*SCORE", "fit_score"),
    (r"KEY\s*ASKS", "key_asks"),
    (r"EFFORT\s*ESTIMATE", "effort_estimate"),
    (r"COMPETITIVENESS", "competitiveness"),
    (r"DURATION/?\s*AMOUNT", "duration_amount"),
    (r"DEADLINE", "deadline_raw"),
    (r"LOCATION", "location"),
    (r"TITLE", "title"),
    (r"ORG", "org"),
    (r"TYPE", "type"),
    (r"URL", "url"),
]

_LABEL_RX = re.compile(
    r"^[\s\*_>#-]*(?:" + "|".join(f"(?P<{f}>{p})" for p, f in _LABELS) + r")\s*:\s*(.*)$",
    re.MULTILINE | re.IGNORECASE,
)


def _strip_bold(s: str) -> str:
    return _BOLD_RX.sub(lambda m: m.group(1) or m.group(2) or "", s)


def _split_blocks(text: str) -> list[str]:
    parts = _SEPARATOR_RX.split(text)
    return [p.strip() for p in parts if p.strip()]


def _parse_field_value(field: str, raw: str) -> object:
    raw = raw.strip()
    raw = re.sub(r"^\[|\]$", "", raw).strip()  # strip placeholder brackets
    if field == "fit_score":
        m = re.search(r"[1-5]", raw)
        return int(m.group(0)) if m else None
    if field == "type":
        lo = raw.lower().split()[0] if raw else ""
        for canonical in OPPORTUNITY_TYPES:
            if canonical in lo:
                return canonical
        return lo or None
    return raw or None


def _split_deadline(raw: str) -> tuple[str | None, str | None]:
    """Return (iso_deadline, deadline_note)."""
    if not raw:
        return None, None
    m = _ISO_DATE_RX.search(raw)
    iso = m.group(1) if m else None
    note = raw if (not iso or len(raw) > len(iso) + 2) else None
    if not iso and _ROLLING_RX.search(raw):
        note = "rolling"
    return iso, note


def _parse_block(block: str) -> ParsedFinding | None:
    body = _strip_bold(block)
    matches = list(_LABEL_RX.finditer(body))
    if not matches:
        return None

    fields: dict[str, str] = {}
    for i, m in enumerate(matches):
        field = next(k for k in m.groupdict() if m.group(k) and k != "0")
        first_line = m.group(m.lastindex)
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        rest = body[m.end() : end].strip()
        value = (first_line.strip() + ("\n" + rest if rest else "")).strip()
        # Last block wins on duplicate labels — defensive against repeated keys.
        fields[field] = value

    if not fields.get("title"):
        return None

    deadline_raw = fields.get("deadline_raw", "")
    deadline_iso, deadline_note = _split_deadline(deadline_raw)

    return ParsedFinding(
        title=_parse_field_value("title", fields["title"]) or "(untitled)",
        org=_parse_field_value("org", fields.get("org", "")),
        type=_parse_field_value("type", fields.get("type", "")),
        url=_parse_field_value("url", fields.get("url", "")),
        deadline=deadline_iso,
        deadline_note=deadline_note,
        location=_parse_field_value("location", fields.get("location", "")),
        duration_amount=_parse_field_value("duration_amount", fields.get("duration_amount", "")),
        eligibility_citizenship=_parse_field_value("eligibility_citizenship", fields.get("eligibility_citizenship", "")),
        eligibility_career_stage=_parse_field_value("eligibility_career_stage", fields.get("eligibility_career_stage", "")),
        eligibility_other=_parse_field_value("eligibility_other", fields.get("eligibility_other", "")),
        fit_score=_parse_field_value("fit_score", fields.get("fit_score", "")),
        primary_angle=_parse_field_value("primary_angle", fields.get("primary_angle", "")),
        backup_angle=_parse_field_value("backup_angle", fields.get("backup_angle", "")),
        key_asks=_parse_field_value("key_asks", fields.get("key_asks", "")),
        effort_estimate=_parse_field_value("effort_estimate", fields.get("effort_estimate", "")),
        competitiveness=_parse_field_value("competitiveness", fields.get("competitiveness", "")),
        why_fits=_parse_field_value("why_fits", fields.get("why_fits", "")),
        risk_watchout=_parse_field_value("risk_watchout", fields.get("risk_watchout", "")),
        raw_block=block,
    )


_OPP_LIKE_LABELS_RX = re.compile(
    r"^[\s\*_>#-]*(TITLE|ORG|TYPE|URL|DEADLINE|FIT\s*SCORE|ANGLE\s*TO\s*DEPLOY)\s*:",
    re.MULTILINE | re.IGNORECASE,
)


def _looks_like_opportunity_block(block: str) -> bool:
    """A block is opportunity-like if it has ≥2 of our schema labels.
    Pure prose (summary, what's-absent, preamble) gets skipped silently."""
    return len(_OPP_LIKE_LABELS_RX.findall(block)) >= 2


def parse_findings(text: str) -> tuple[list[ParsedFinding], list[str]]:
    """Return (parsed, quarantined_raw_blocks). Never raises on bad input."""
    parsed: list[ParsedFinding] = []
    quarantined: list[str] = []
    for block in _split_blocks(text):
        if not _looks_like_opportunity_block(block):
            continue  # prose / summary section; not an attempted opportunity
        try:
            finding = _parse_block(block)
        except Exception as e:
            quarantined.append(block)
            log.warning("parse_block_error", error=repr(e), preview=block[:200])
            continue
        if finding is None:
            quarantined.append(block)
            log.warning("parse_block_skipped", reason="missing_title", preview=block[:200])
        else:
            parsed.append(finding)
    log.info("parse_done", parsed=len(parsed), quarantined=len(quarantined))
    return parsed, quarantined


def normalize_url(url: str) -> str:
    """Canonicalise a URL for dedup: strip whitespace + trailing slash + fragment, lowercase scheme/host."""
    if not url:
        return ""
    u = url.strip()
    u = re.sub(r"#.*$", "", u)
    u = u.rstrip("/")
    m = re.match(r"^(https?://)([^/]+)(.*)$", u, re.IGNORECASE)
    if m:
        scheme, host, rest = m.groups()
        u = scheme.lower() + host.lower() + rest
    return u


def url_hash(url: str) -> str:
    norm = normalize_url(url)
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def deduplicate(findings: Iterable[ParsedFinding]) -> tuple[list[ParsedFinding], int]:
    """Dedup a list by url_hash within the same response. Returns (unique, dropped_count)."""
    seen: set[str] = set()
    out: list[ParsedFinding] = []
    dropped = 0
    for f in findings:
        if not f.url:
            out.append(f)
            continue
        h = url_hash(f.url)
        if h in seen:
            dropped += 1
            continue
        seen.add(h)
        out.append(f)
    return out, dropped
