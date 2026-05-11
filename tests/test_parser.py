"""Parser tolerates real-world formatting drift; quarantines what it can't read."""
from scout.agents.parser import deduplicate, normalize_url, parse_findings, url_hash

HAPPY = """\
─────────────────────────────────────────────
TITLE: DAAD Berliner Künstlerprogramm
ORG: DAAD
TYPE: Residency
URL: https://www.daad.de/en/the-daad/berliner-kuenstlerprogramm/
DEADLINE: 2026-08-31
LOCATION: Berlin, Germany
DURATION/AMOUNT: 12 months, €3000/mo + travel

ELIGIBILITY (citizenship): Both — open to non-German artists, HK ✓ CA ✓
ELIGIBILITY (career stage): Mid-career ✓
ELIGIBILITY (other): Composition / sound art track exists

FIT SCORE: 5
ANGLE TO DEPLOY: A — Drawing Room research aligns with DAAD's contemporary music line
ANGLE BACKUP: B

KEY ASKS: 1500-word project, 3 work samples, CV, two references
EFFORT ESTIMATE: Heavy
COMPETITIVENESS: ~5% acceptance per public reports

WHY THIS FITS (3 sentences max): DAAD's music programme funds practice-led research
with no production deliverables. Drawing Room's nocturne/listening axis fits the
"contemplative listening" priority of the 2025 jury.
RISK / WATCH-OUT: Heavy app; needs German-resident reference letter.
─────────────────────────────────────────────
"""

BOLDED_AND_DRIFT = """\
─────────────────────────────────────────────
**TITLE:** Akademie Schloss Solitude
**ORG:** Schloss Solitude

**TYPE:** Fellowship
**URL:** https://www.akademie-solitude.de/en/apply
**DEADLINE:** Rolling (next jury 2027)
LOCATION: Stuttgart
DURATION/AMOUNT:  9 months, €1300/mo + studio

ELIGIBILITY (citizenship): Both
ELIGIBILITY (career stage): No stated restriction
ELIGIBILITY (other): Up to 35 → BORDERLINE — flag

FIT SCORE: 3
ANGLE TO DEPLOY: B
KEY ASKS: portfolio, project, CV
EFFORT ESTIMATE: Medium
COMPETITIVENESS: unknown
WHY THIS FITS (3 sentences max): Solitude funds composers and sound artists.
RISK / WATCH-OUT: Age borderline; verify cutoff against current call.
─────────────────────────────────────────────
"""

MALFORMED = """\
─────────────────────────────────────────────
ORG: Mystery Funder
TYPE: Grant
URL: https://example.org/call
DEADLINE: 2026-09-01
─────────────────────────────────────────────
"""


def test_happy_path_full_block() -> None:
    findings, quarantined = parse_findings(HAPPY)
    assert len(findings) == 1
    assert quarantined == []
    f = findings[0]
    assert f.title.startswith("DAAD")
    assert f.type == "residency"
    assert f.url.startswith("https://www.daad.de/")
    assert f.deadline == "2026-08-31"
    assert f.fit_score == 5
    assert f.primary_angle.startswith("A")
    assert f.eligibility_citizenship and "HK" in f.eligibility_citizenship
    assert "contemplative listening" in f.why_fits


def test_handles_bold_blank_lines_and_rolling_deadline() -> None:
    findings, quarantined = parse_findings(BOLDED_AND_DRIFT)
    assert len(findings) == 1
    assert quarantined == []
    f = findings[0]
    assert f.title.startswith("Akademie Schloss Solitude")
    assert f.type == "fellowship"
    assert f.deadline is None  # not an ISO date
    assert "olling" in (f.deadline_note or "")
    assert f.fit_score == 3
    assert f.eligibility_other and "BORDERLINE" in f.eligibility_other


def test_malformed_block_is_quarantined_not_raised() -> None:
    findings, quarantined = parse_findings(MALFORMED)
    assert findings == []
    assert len(quarantined) == 1


def test_mixed_good_and_bad_blocks_in_one_response() -> None:
    text = HAPPY + "\n" + MALFORMED + "\n" + BOLDED_AND_DRIFT
    findings, quarantined = parse_findings(text)
    assert len(findings) == 2
    assert len(quarantined) == 1


def test_response_with_preamble_and_top3_summary_after_blocks() -> None:
    text = (
        "Here are the opportunities I found. Today is 2026-05-11; sweeping next 90 days.\n\n"
        + HAPPY
        + "\n**TOP 3 FOR THIS WEEK**\n\n1. DAAD — bullseye, plan to apply.\n\n"
        "**WHAT'S ABSENT**\n\nHKADC's project grant cycle is between rounds; next opens 2026-07.\n"
    )
    findings, _ = parse_findings(text)
    assert len(findings) == 1
    assert findings[0].title.startswith("DAAD")


def test_dedup_within_response_by_url_hash() -> None:
    text = HAPPY + "\n" + HAPPY.replace("DAAD Berliner Künstlerprogramm", "DAAD Berlin (alt)")
    findings, _ = parse_findings(text)
    assert len(findings) == 2
    unique, dropped = deduplicate(findings)
    assert len(unique) == 1
    assert dropped == 1


def test_url_normalization() -> None:
    h1 = url_hash("https://example.org/Call/")
    h2 = url_hash("HTTPS://Example.Org/Call#anchor")
    h3 = url_hash("https://example.org/Call")
    assert h1 == h2 == h3
    assert normalize_url("https://Example.org/x/") == "https://example.org/x"
