# Prompts changelog

Prompts are code — log every meaningful change here with a one-line rationale
so reverts and A/B comparisons stay legible. Date format: YYYY-MM-DD.

## 2026-05-11

- **scout_agent.md (was opportunity-scout-v1.md):** Initial discovery-agent
  prompt. Six pitch angles, hard eligibility filters, structured §8 output,
  weekly cadence; v2 drafting hooks wired but inactive.
- **artist_profile.md:** Extract of §2–4 of the scout prompt; loaded by the
  artist-profile snapshotter for use across discovery and drafting flows.
- **drafting/{artist_statement, project_description, budget, cover_letter,
  cv_tailor, work_samples}.md:** Phase-3 base templates. Each accepts
  `$artist_profile`, `$opportunity_summary`, `$past_exemplars`,
  `$length_constraint`, `$critique`. Frozen system prompt is
  `drafting/_system.md`; critic rubric is `drafting/_critic.md`.
