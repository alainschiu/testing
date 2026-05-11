# Scout

Single-user local-first agent for discovering and tracking arts-funding opportunities
(grants, residencies, fellowships, commissions, PhD funding), then assisting with
applications. See `prompts/scout_agent.md` for the discovery agent's system prompt
and the build brief for architecture.

## Install

```sh
uv sync
cp .env.example .env   # then fill in API keys (see "LLM provider" below)
make migrate           # initialise the SQLite DB
make doctor            # green checks on env, DB, API reachability
```

## Pipeline (Phase 5a onward)

Every source — the scout agent, future watchers, RSS feeds, the email-ingest
inbox, manual capture — funnels findings into a single `raw_findings`
staging table. A normaliser agent (cheap Sonnet-class by default) drains
`pending` rows, calling Claude with `prompts/normaliser.md` to either:

- emit one or more canonical `opportunities` rows (verdict `opportunity`),
- mark the row `rejected` (verdict `not_an_opportunity`),
- mark the row `error` for manual UI triage (verdict `unclear`),
- mark the row `duplicate` and link to the existing opportunity (URL collision).

`scout run` triggers normalisation at the end of each scout pass, so a single
command still produces opportunities end-to-end. Between runs, an APScheduler
job drains pending findings every 15 minutes (cheap when the queue is empty).
`scout normalise` runs the drainer manually.

## Watchers (Phase 5b)

Site/RSS/JSON pollers feed `raw_findings` on a per-source cron. Each kind
maps to a fetcher:

- `html_static` — plain HTTP + selectolax (no JS execution)
- `html_js`     — Playwright + headless Chromium (install with the `js` extra)
- `rss`         — feedparser; the watcher's URL is the feed
- `json_api`    — JSON GET; the watcher's `selector` is a dotted path
                  (e.g. `data.items`)

Politeness: every fetch checks robots.txt (cached daily), waits ≥10s between
requests to the same host, and backs off exponentially on 429/403. The
User-Agent identifies us as a single-user research agent with a contact
email — be findable, not stealthy.

Failure handling: 3 consecutive failures auto-deactivate the watcher. The
last error is shown on `/watchers`; the dashboard footer shows the count of
deactivated watchers in red.

LLM extraction: watchers without a CSS selector hand their scoped HTML to a
Sonnet-class bot using `prompts/listing_extractor.md`. A daily cap
(`EXTRACTION_DAILY_USD_CAP`, default $2) defers extraction once spent, so
fetchers keep updating their content hashes but no further LLM calls fire
until the next day.

### Common commands

```sh
scout watchers seed              # install the 20 starter watchers
scout watchers list              # show state, last check, failure count
scout watchers run "name or id"  # run one watcher now (ignores cron)
scout watchers toggle <id> --inactive
```

In the web UI, `/watchers` shows the same rows with HTMX-powered "Run now"
buttons so you can poke each one without leaving the page.

### Optional: Playwright for JS-rendered sites

```sh
uv pip install -e .[js]
playwright install chromium      # ~400MB once
```

If Playwright isn't installed, `html_js` watchers fail with a clear error
on their next run and auto-deactivate after 3 strikes — the rest of the app
keeps working.

## LLM provider

Two backends — switch with `LLM_PROVIDER` in `.env`:

- **`anthropic`** (default) — calls Claude directly. Requires `ANTHROPIC_API_KEY`.
  Uses adaptive thinking, prompt caching, and Anthropic's server-side
  `web_search` tool with a per-run cap. Cost reported in USD against published
  per-token pricing.
- **`poe`** — calls Poe's OpenAI-compatible endpoint
  (`https://api.poe.com/v1/chat/completions`). Requires `POE_API_KEY`. Pick the
  bot per role: `POE_DRAFTING_BOT` (default `Claude-Opus-4.7`) for drafting +
  critic; `POE_SCOUT_BOT` (default `Claude-Opus-4.7-Search`) must be a
  search-capable bot for discovery, since Poe doesn't expose `web_search` as
  an attachable tool. `cache_control`, `thinking`, `effort`, and tool
  attachments are silently dropped. Cost reports as `$0` (Poe is points-based)
  — see your Poe dashboard for actual spend.

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
