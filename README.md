# TokenPilot

AI spend ledger and bill audit for OpenAI and Anthropic usage. You run it locally with your own admin keys, so the keys never leave your machine.

## Requirements
- [uv](https://docs.astral.sh/uv/) (installs Python 3.12 for the project)
- Docker (local Postgres 17 on port 5433)
- Node.js 22+ (only to build the audit page)

## Quick start
```bash
cp backend/.env.example backend/.env
make install     # virtualenv + dependencies
make db-up       # start Postgres (creates tokenpilot and tokenpilot_test databases)
make migrate     # apply migrations
make check       # lint + tests
make run         # API on http://localhost:5001  (GET /health, /api/spend/audit?tenant=demo)
```

## See the audit page
```bash
make db-up && make migrate && make seed   # once
make demo                                 # example report, shown until you upload your own data
make web                                  # build the page (once, and after frontend changes)
make run                                  # open http://localhost:5001
```
With no data of your own, the page shows the example report (synthetic, labelled as an example) under the upload box. Re-run `make demo` to refresh its dates.

## Try it on your own data (command line)
```bash
make db-up && make migrate
make seed                                        # model catalog + prices
make import FILES="/path/to/exports/*.csv"       # your CSV exports (relative paths start in backend/)
make report                                      # spend by model / key / project + reconciliation
make audit                                       # savings findings (MIN=0 shows tiny ones too)
```

Or pull the same data from the providers' admin APIs (keys in `backend/.env`, see `.env.example`):
```bash
make sync            # new days up to yesterday (first run: last 30 days)
make sync DAYS=45    # re-sync the last 45 days
```
The page shows a "Sync now" button when at least one key is configured.

See an audit with findings without your own data:
```bash
make demo                    # 30 days of synthetic usage in tenant "demo" (replaced on each run)
make audit TENANT=demo
```
Supported exports: OpenAI *Activity* (completions usage) and *Cost* (grouped by line item), Anthropic Console *Usage* and *Cost*. Details: `docs/export-formats.md`.

## Claude Code panel (optional)
[tokenpilot-panel](https://github.com/balazsarva-dev/tokenpilot-panel) is a Claude Code plugin that shows this install's numbers in a side panel: 30-day spend and trend, top models and projects, and audit savings. It is a separate repo, and installing it does not download TokenPilot. The panel only reads from this API, so TokenPilot has to be running (`make run`) with your data in the `local` tenant.

At the Claude Code prompt:
```
/plugin install tokenpilot --marketplace balazsarva-dev/tokenpilot-panel
```
Then type `/tokenpilot` to open the panel. It needs this repo at commit `f78104d` or later (the `/api/spend/daily` endpoint).

## Layout
```
backend/
  tokenpilot/       framework-independent core (no Flask imports outside web/)
    web/            Flask layer
  migrations/       Alembic
  tests/            pytest, real Postgres (tokenpilot_test)
frontend/           audit page (Vite + React + TypeScript + Tailwind); built into frontend/dist
docs/               provider export formats
docker/             database init scripts
```

## How this project is built
TokenPilot is written by an AI coding agent (Claude Code) and directed by its owner. The owner sets the goals, approves each design at agreed checkpoints, reviews the changes and decides what ships; the agent writes the code, tests and docs, researches provider APIs and prices, and proposes designs with their trade-offs.

Guardrails:
- **Small steps.** Work goes in small, reviewed steps, and the agent stops at each checkpoint until it gets approval.
- **Numbers.** Money is never a float, and an unknown price is never 0. Prices come only from the providers' official pages. The product checks its own numbers against the provider's billed cost (reconciliation).
- **Checks.** `make check` (lint plus tests on a real Postgres) runs before every commit.
- **Secrets.** Admin keys live only in `backend/.env`, never in git.

## License
MIT, see [`LICENSE`](LICENSE).
