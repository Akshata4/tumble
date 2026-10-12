# Tumble

An autonomous ML research agent: point it at your data, tell it what to
optimize, and it iteratively proposes changes, runs real experiments, and
keeps only what actually improves the score — like a rock tumbler for model
code. Rough drafts go in, get agitated over and over, and a polished result
comes out the other end.

**Status: early development.** This is being built as a public product.
Sandboxed execution (E2B), a multi-tenant FastAPI backend with
session-isolated workspaces, abuse/cost caps (now including a Turnstile
bot-check), and a React frontend are all in now. Just missing: this
hasn't been deployed anywhere yet.

## The mechanic

1. **Propose** — the agent edits the current training script
2. **Execute** — it's run for real, for real accuracy
3. **Evaluate** — did the metric improve?
4. **Keep or revert** — a deterministic rule, not the LLM's own call: keep
   (git commit) if it's a new best, revert (git checkout) if it isn't or it
   errored
5. Repeat within a fixed experiment budget, then the agent writes its own
   final report

This is the same pattern real autoresearch systems (AIDE and similar) use,
and using git commits for the keep/revert step means durability/checkpointing
comes for free.

## Where this came from

This started as Part C of a separate coding-harness course project — a
single-tenant CLI tool, hardcoded to one dataset, executing generated code
as a local subprocess. That version is preserved at
[`harness-from-scratch/part-c-autoresearch`](https://github.com/Akshata4/harness-from-scratch)
(kept as the coursework deliverable). This repo is where it becomes an
actual product from here: sandboxed execution (E2B), any dataset a user
uploads, and a real UI.

## Setup

```bash
uv sync
cp .env.example .env
# fill in WANDB_API_KEY, WANDB_PROJECT, E2B_API_KEY
```

## Run it as a CLI (single session, local)

```bash
uv run python main.py --dataset path/to/data.csv --target target_column_name
```

`--metric` (default `accuracy`) names the metric the agent should compute
and print, and `--minimize` flips the keep/revert direction for
lower-is-better metrics (e.g. RMSE). Any trailing free text is passed to
the agent as extra instructions. `--replay <trajectory-file>` replays a
past run instead.

Each `run_experiment` call executes `train.py` inside a real E2B sandbox
(not a local subprocess), and the agent writes its own `train.py` from
scratch based on a preview of whatever CSV you point it at — no dataset is
hardcoded anymore.

## Run it as a server (multiple concurrent sessions)

```bash
uv run uvicorn server:app --reload
```

```bash
SID=$(curl -s -X POST localhost:8000/sessions | python3 -c "import sys,json;print(json.load(sys.stdin)['session_id'])")
curl -s -X POST localhost:8000/sessions/$SID/dataset -F target=diagnosis -F file=@data.csv
curl -s -X POST localhost:8000/sessions/$SID/start -H 'Content-Type: application/json' -d '{"metric":"accuracy"}'
curl -sN localhost:8000/sessions/$SID/stream        # live trajectory, one JSON event per SSE message
curl -s localhost:8000/sessions/$SID/events          # same events, replayed from disk -- safe to call anytime
curl -s localhost:8000/sessions/$SID/report          # once status is "completed"
curl -s localhost:8000/sessions/$SID/download/script
curl -s localhost:8000/limits                        # your remaining runs today
curl -s localhost:8000/budget                        # global daily spend across all sessions
```

Each session gets its own directory under `sessions/<id>/` and its own E2B
sandbox, so concurrent visitors never share a workspace, a git history, or
a best-metric.

## Run the frontend

```bash
cd frontend
npm install
cp .env.example .env       # VITE_API_BASE, defaults to localhost:8000
npm run dev
```

A React (Vite) single-page app: upload a CSV, pick the target column
(click a column header in the preview table, or type the name), start a
run, and watch it work — a live metric-per-attempt chart, the trajectory
feed, and the best-metric/iteration counter all update off the same
`/stream` SSE endpoint. Starting a run pushes the browser to
`/session/<id>`, so that link can be reopened later (or shared) and it
resumes into the finished report/chart/feed, reading everything back from
the session's own trajectory log instead of re-running anything. A small
note shows how many runs are left today for your connection, from the
same per-IP cap `/start` enforces. With the backend running on `:8000` and
this on `:5173`, open the Vite URL and go. The backend's CORS is wide open
for now (no auth exists yet either) — see `CORS_ORIGINS` in `.env.example`
to lock it down once this is actually deployed somewhere.

**Safety caps in effect**: a Cloudflare Turnstile bot-check on starting a
run (`403` if it fails — disabled by default until you set
`TURNSTILE_SECRET_KEY`/`VITE_TURNSTILE_SITE_KEY`, see below), 3 session
starts/day/IP (`429` past that), a 5MB dataset upload cap (`413` past
that), 10 experiments/session (enforced in the harness, not just the
prompt), 20s/experiment, a 5 min wall-clock cap per session, and a global
daily budget ceiling on sandbox-seconds + LLM tokens (`503` once
exhausted, visible at `GET /budget`) shared across every session in the
process.

**Turning on the bot-check**: get a free site key + secret key pair at
[dash.cloudflare.com](https://dash.cloudflare.com) → Turnstile, set
`TURNSTILE_SECRET_KEY` in the backend's `.env` and the matching
`VITE_TURNSTILE_SITE_KEY` in `frontend/.env`, and restart both. Leave
either unset and the check is skipped entirely — nothing is blocked, and
no widget renders. For testing without a Cloudflare account, Cloudflare
publishes an always-passes key pair (see `.env.example`).

See [`DESIGN.md`](DESIGN.md) for the full decisions log — why E2B over
CoreWeave, the session architecture, and the safety-cap numbers. See
[`IDEAS.md`](IDEAS.md) for a backlog of not-yet-started agent-capability
upgrades (parallel search, ensembling, a cross-session playbook, and more).

## Roadmap

- [x] Swap local subprocess execution for a real E2B sandbox
- [x] Generalize beyond one hardcoded dataset — agent writes its own
      baseline from a CSV
- [x] Multi-tenant FastAPI backend, session-isolated workspaces
- [x] Abuse/cost caps: per-IP rate limits, global daily budget
- [x] Live-streaming React frontend (trajectory view + best-metric counter)
- [x] Bot-check (Cloudflare Turnstile) — off by default, opt in via env vars
- [x] Render the dataset as a table and pick the target column from it
      (click a column header, or type the name — both stay in sync)
- [x] Render the final report as formatted Markdown, not raw text; view/copy
      `train.py` inline next to the download link
- [x] Live metric-per-attempt chart
- [x] Shareable session links (`/session/<id>`, resumable after the fact)
      and a per-IP "runs left today" note
- [ ] Deploy, soft-launch, then go public
