"""Canned scout-agent outputs for tests. Keep them realistic — markdown
bolding, mixed casing, an ISO deadline and a "rolling" one, a malformed block
to exercise the quarantine path.
"""

VALID_BLOCK_A = """\
─────────────────────────────────────────────
TITLE: DAAD Berliner Künstlerprogramm
ORG: DAAD
TYPE: Residency
URL: https://www.daad.de/en/the-daad/berliner-kuenstlerprogramm/apply
DEADLINE: 2026-08-31
LOCATION: Berlin, Germany
DURATION/AMOUNT: 12 months, €3000/mo + travel

ELIGIBILITY (citizenship): Both — HK ✓ CA ✓
ELIGIBILITY (career stage): Mid-career ✓
ELIGIBILITY (other): Composition / sound art track

FIT SCORE: 5
ANGLE TO DEPLOY: A — Drawing Room research aligns with DAAD's contemplative line
KEY ASKS: 1500w project, 3 work samples, CV, 2 references
EFFORT ESTIMATE: Heavy
COMPETITIVENESS: ~5%
WHY THIS FITS (3 sentences max): Funds practice-led research with no production deliverable.
RISK / WATCH-OUT: Needs Germany-resident referee
─────────────────────────────────────────────
"""

VALID_BLOCK_B = """\
─────────────────────────────────────────────
**TITLE:** Akademie Schloss Solitude Fellowship
**ORG:** Schloss Solitude
**TYPE:** Fellowship
**URL:** https://www.akademie-solitude.de/en/apply
DEADLINE: Rolling
LOCATION: Stuttgart
DURATION/AMOUNT: 9 months, €1300/mo

ELIGIBILITY (citizenship): Both
ELIGIBILITY (career stage): No stated restriction
ELIGIBILITY (other): Up to 35 — borderline, flag

FIT SCORE: 3
ANGLE TO DEPLOY: B
KEY ASKS: portfolio, project, CV
EFFORT ESTIMATE: Medium
COMPETITIVENESS: unknown
WHY THIS FITS (3 sentences max): Solitude funds composers and sound artists.
RISK / WATCH-OUT: Age borderline — verify call.
─────────────────────────────────────────────
"""

MALFORMED_BLOCK = """\
─────────────────────────────────────────────
ORG: Mystery Funder
TYPE: Grant
URL: https://example.org/no-title-here
DEADLINE: 2026-09-01
ANGLE TO DEPLOY: A
─────────────────────────────────────────────
"""

TOP3_TAIL = """
**TOP 3 FOR THIS WEEK**

1. DAAD — bullseye for Drawing Room research.
2. Solitude — rolling, low-effort filing.
3. (n/a)

**WHAT'S ABSENT**

No HKADC project cycle currently open; next window expected 2026-07.
"""

HAPPY_TWO_BLOCKS = VALID_BLOCK_A + "\n" + VALID_BLOCK_B + TOP3_TAIL
HAPPY_WITH_MALFORMED = VALID_BLOCK_A + "\n" + MALFORMED_BLOCK + "\n" + VALID_BLOCK_B + TOP3_TAIL
