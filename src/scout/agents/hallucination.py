"""Post-generation check: extract proper-noun-looking phrases from a draft
and flag any that don't appear in the artist profile, opportunity record, or
provided exemplars (build brief §3.3).

Flags only — never auto-strips. Output is shown to the artist for review.

Strategy: token-scan over the draft. A "proper-noun phrase" is a run of two
or more tokens where each token either starts with an uppercase letter
(allowing hyphens / apostrophes / accents inside the token) or is a known
lowercase connector ("of", "the", "von", "de", …). Connectors are
restricted to ones that genuinely sit inside multi-word proper nouns; "and"
is excluded because it commonly bridges distinct phrases and would create
false merges like "Made-Up Foundation and Phantom Sound Lab".
"""
from __future__ import annotations

import re
import unicodedata

# A token that looks proper-noun-ish: starts with uppercase (incl. accented),
# may contain letters, digits, hyphens, apostrophes (straight or curly).
_PROPER_TOKEN_RX = re.compile(r"^[A-ZÀ-ÖØ-Ý][\w'’\-]*$")

# Connectors that genuinely live inside multi-word proper nouns. Deliberately
# excludes "and" — it bridges distinct phrases too often.
_CONNECTORS = {
    "of", "the", "de", "des", "du", "von", "van", "der", "den", "le", "la",
    "für", "im", "am", "zu", "à", "y",
}

# Phrases that are capitalised-multi-word but not proper nouns in our context.
_STOPWORDS = {
    "selected works",
    "artist statement",
    "project description",
    "work samples",
    "cover letter",
    "dear selection panel",
    "dear programme manager",
    "selection panel",
    "programme manager",
    "this proposal",
    "this project",
    "to date",
    "in one sentence",
}

_TRAILING_PUNCT_RX = re.compile(r"(?:['’]s)?[\.,;:!\?\)\]\}]*$")
_LEADING_PUNCT_RX = re.compile(r"^[\(\[\{“\"']+")
_URL_RX = re.compile(r"https?://\S+")
_SENTENCE_SPLIT_RX = re.compile(r"(?<=[\.!?\n])\s+")


def _normalise(s: str) -> str:
    """Lowercase + strip accents so 'Künstlerprogramm' matches 'kunstlerprogramm'."""
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


def _flush(run: list[str], out: list[str]) -> None:
    while run and run[0].lower() in _CONNECTORS:
        run.pop(0)
    while run and run[-1].lower() in _CONNECTORS:
        run.pop()
    if len(run) >= 2 and any(_PROPER_TOKEN_RX.match(t) for t in run):
        out.append(" ".join(run))


def extract_proper_nouns(text: str) -> list[str]:
    """Return unique-preserving-order list of capitalised multi-word phrases."""
    cleaned = _URL_RX.sub(" ", text)
    phrases: list[str] = []

    for sentence in _SENTENCE_SPLIT_RX.split(cleaned):
        run: list[str] = []
        for raw in sentence.split():
            tok = _LEADING_PUNCT_RX.sub("", _TRAILING_PUNCT_RX.sub("", raw))
            if not tok:
                _flush(run, phrases)
                run = []
                continue
            if _PROPER_TOKEN_RX.match(tok) or (run and tok.lower() in _CONNECTORS):
                run.append(tok)
            else:
                _flush(run, phrases)
                run = []
        _flush(run, phrases)

    seen: set[str] = set()
    out: list[str] = []
    for p in phrases:
        norm = _normalise(p)
        if norm in _STOPWORDS or norm in seen:
            continue
        seen.add(norm)
        out.append(p)
    return out


def verify_proper_nouns(text: str, sources: list[str]) -> tuple[list[str], list[str]]:
    """Return (verified, unverified). Unverified phrases warrant review."""
    haystack = _normalise(" ".join(s or "" for s in sources))
    verified: list[str] = []
    unverified: list[str] = []
    for phrase in extract_proper_nouns(text):
        if _normalise(phrase) in haystack:
            verified.append(phrase)
        else:
            unverified.append(phrase)
    return verified, unverified
