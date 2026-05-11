"""Seed the watchers table with the 20 starting sources from Phase 5b §5b.5.

URLs are hints from the brief — they may need refinement (drill into the
specific open-calls page for each org). Selectors are left null so the LLM
extractor handles each site until a hand-written selector is added.

Idempotent: `upsert_watcher_by_name` updates existing rows in place, so
re-running this changes URLs/schedules but never duplicates.
"""
from __future__ import annotations

from scout.db import run_migrations
from scout.logging import get_logger
from scout.queries_watchers import upsert_watcher_by_name

log = get_logger("scout.seed")

# (name, url, kind, schedule_cron)
SEEDS: list[tuple[str, str, str, str]] = [
    ("ResArtis open calls",            "https://resartis.org/open-calls",                             "html_js",     "0 6 * * *"),
    ("TransArtists open calls",        "https://www.transartists.org/en",                             "html_static", "15 6 * * *"),
    ("e-flux Announcements",           "https://www.e-flux.com/announcements/feed",                   "rss",         "0 */4 * * *"),
    ("Rivet listings",                 "https://www.rivet.net",                                       "html_static", "30 6 * * *"),
    ("On the Move funding",            "https://on-the-move.org/funding",                             "html_static", "45 6 * * *"),
    ("HKADC funding",                  "https://www.hkadc.org.hk",                                    "html_js",     "0 7 * * *"),
    ("Canada Council deadlines",       "https://canadacouncil.ca/funding/grants/deadlines",           "html_static", "15 7 * * *"),
    ("BC Arts Council programs",       "https://www.bcartscouncil.ca/programs",                       "html_static", "30 7 * * *"),
    ("Ontario Arts Council grants",    "https://www.arts.on.ca/grants",                               "html_static", "30 7 * * *"),
    ("FACTOR program deadlines",       "https://www.factor.ca/programs",                              "html_static", "45 7 * * *"),
    ("SOCAN Foundation",               "https://www.socanfoundation.ca",                              "html_static", "0 8 * * 1"),
    ("DAAD Berliner Kunstprogramm",    "https://www.daad.de/en/the-daad/berliner-kuenstlerprogramm", "html_static", "15 8 * * 1"),
    ("Akademie Schloss Solitude",      "https://www.akademie-solitude.de",                            "html_static", "30 8 * * 1"),
    ("IRCAM Cursus",                   "https://www.ircam.fr",                                        "html_js",     "45 8 * * 1"),
    ("ZKM open calls",                 "https://zkm.de",                                              "html_static", "0 9 * * 1"),
    ("Pro Helvetia",                   "https://prohelvetia.ch",                                      "html_static", "0 8 * * 2"),
    ("docARTES",                       "https://orpheusinstituut.be",                                 "html_static", "0 9 1 * *"),
    ("CRiSAP",                         "https://www.crisap.org",                                      "html_static", "15 9 1 * *"),
    ("Asian Cultural Council",         "https://www.asianculturalcouncil.org",                        "html_static", "30 8 * * 2"),
    ("Pollock-Krasner Foundation",     "https://pkf.org",                                             "html_static", "0 8 * * 3"),
]


def seed() -> tuple[int, int]:
    """Insert/update all seed watchers. Returns (inserted_or_updated, total)."""
    run_migrations()
    n = 0
    for name, url, kind, cron in SEEDS:
        wid = upsert_watcher_by_name(
            name=name, url=url, kind=kind, schedule_cron=cron,
            notes="Seeded from Phase 5b §5b.5; URL may need refinement.",
        )
        log.info("watcher_seeded", id=wid, name=name)
        n += 1
    return n, len(SEEDS)
