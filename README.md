# Scout

Single-user local-first agent for discovering and tracking arts-funding opportunities
(grants, residencies, fellowships, commissions, PhD funding), then assisting with
applications. See `prompts/scout_agent.md` for the discovery agent's system prompt
and the build brief for architecture.

## Install

```sh
uv sync
cp .env.example .env   # then fill in ANTHROPIC_API_KEY
make migrate           # initialise the SQLite DB
make doctor            # green checks on env, DB, API reachability
```

## Run

```sh
make dev               # FastAPI on http://127.0.0.1:8000
make scout             # one-shot discovery run
make test              # pytest with mocked LLM/web
```

## Layout

- `prompts/` — Scout agent and drafting prompts. Edit freely; log changes in `prompts/CHANGELOG.md`.
- `src/scout/` — application code. LLM and web-search calls behind interfaces; mocked in tests.
- `migrations/` — timestamped `.sql` files, applied in order on startup.
- `data/` — SQLite DB, run logs, drafts, artist files. Gitignored.

## Status

| Phase | Scope                                                                          | State |
|------:|--------------------------------------------------------------------------------|:-----:|
| 0     | Skeleton: config, DB, migrations, LLM wrapper, CLI, FastAPI hello              | ✅    |
| 1     | Scout discovery loop: §8 parser, dedup, pause_turn handling, weekly cron       | ✅    |
| 2     | Catalogue UI: dashboard, list+filters, inline-edit detail, status transitions  | ✅    |
| 3     | Drafting subsystem: 6 templates, critic, hallucination flagging, exemplars     | ✅    |
| 4     | Polish: notifications, calendar.ics, export CLI, cost dashboard, Dockerfile    | TODO  |

## Useful CLI commands

```sh
make migrate                              # apply new migrations
make doctor                               # green-check env + DB + Anthropic
make scout                                # one-shot discovery run
uv run scout digest                       # human-readable summary of latest run
uv run scout import-application PATH.md --opportunity-id 42 --result won
```

## Drafting

`/applications/{id}` has one tab per component (artist statement, project
description, budget, cover letter, CV, work samples). Each tab can generate,
regenerate (sibling sample), or **Revise with critique** — the latter runs
the critic pass against the previous draft and feeds the result into the next
generation as `parent_draft_id=N`, giving a navigable revision chain.

Every draft runs a post-generation proper-noun verification pass; flagged
phrases (names not found in the artist profile / opportunity record / past
exemplars) surface in an amber panel under the draft for human review. The
draft is never auto-stripped.
