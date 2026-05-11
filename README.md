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

Project status: **Phase 0–1 (skeleton + scout pipeline)** complete; UI and drafting still TODO.
