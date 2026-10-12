# Design notes and decisions log

Context for picking this project back up in a fresh session — the "why"
behind what's already been decided, not just the "what."

## Goal

A public product: someone (a stranger, e.g. via a resume link) uploads a
dataset, tells Tumble what to optimize, and it autonomously runs real
experiments and reports back — with hard limits so it can't run up an
unbounded bill or be abused.

## Sandbox execution: E2B (not CoreWeave)

The agent writes and runs arbitrary Python on user-uploaded data, so
execution has to be sandboxed, not a local `subprocess` call (fine for a
single-tenant coursework demo, not fine once strangers can trigger it).

**CoreWeave Sandboxes was investigated and rejected.** It looked ideal on
paper — free during its public preview, and authenticates with the same
`WANDB_API_KEY` this project already uses. In practice: traced the actual
`cwsandbox` SDK source to confirm the request was built correctly (sends
`x-entity-id`/`x-project-name` headers derived from `WANDB_ENTITY`/
`WANDB_PROJECT`), auth itself succeeded, but the server consistently
rejected every attempt with `"project not found"` regardless of which real,
existing W&B project was targeted. Docs claim no enablement/allowlist is
required for the W&B serverless runtime, but the evidence points to an
undocumented provisioning gap for this account on a public-preview product.
Not something more local debugging fixes.

**E2B is what's actually integrated.** Verified end-to-end with the real
workload (write a file, `pip install scikit-learn`, run a training script,
capture a metric) — worked correctly, ~4.6s total, output matched the local
baseline exactly.

**E2B's free tier, precisely**: a **one-time $100 signup credit**, not a
recurring monthly allowance (an earlier claim of "100 free hours/month,
ongoing" was wrong — corrected after being challenged, sourced from a
secondary blog, not E2B's own pricing page). Billing is
$0.000014/vCPU-second + $0.0000045/GiB RAM-second. For this workload
(~20s/experiment on a small sandbox), that's roughly **$0.0007/experiment**,
so $100 covers on the order of **10,000+ full runs** — plenty of runway for
resume-link-scale traffic, but finite, not infinite. Budget monitoring
should be a real feature, not an afterthought.

Execution lives behind one interface (`run_in_sandbox(code, files, timeout)`
— not yet written) so swapping providers later (e.g. revisiting CoreWeave
once it's GA, or if E2B's economics stop working) is a one-file change.

## Multi-tenant architecture

```
Browser (upload data, watch live trajectory)
        │ HTTPS + SSE
        ▼
FastAPI backend (session orchestrator)
        │                          │
        │ LLM calls                │ sandboxed exec
        ▼                          ▼
  W&B Inference              E2B sandbox
        │
        ▼
  W&B Runs, tagged per session
```

- **Session model**: one session = one visitor's one run, created only on
  an explicit "start" click. `sessions/<id>/workspace/{train.py, state.json,
  .git/}` on disk (same nested-git keep/revert mechanism as before, just
  scoped per session instead of one fixed `.workspace/`) plus
  `sessions/<id>/upload.csv` held separately, outside the workspace
  directory, so `Workspace.init()`'s wipe-and-recreate of the workspace can
  never delete the very upload it's about to copy back in. In-memory
  session store (a plain `dict[str, Session]` in `server.py`) is fine at
  this scale — no need for Redis/Postgres yet.
- **API** (`server.py`, built): `POST /sessions` → `POST
  /sessions/{id}/dataset` (multipart CSV + target column, returns the
  preview) → `POST /sessions/{id}/start` (metric/minimize/prompt/
  turnstile_token, spawns a background thread running the same
  `agent_loop.run_agent` the CLI uses) → `GET /sessions/{id}/stream` (SSE,
  one JSON event per line -- the exact same event shape `Trajectory`
  writes to its JSONL file) → `GET /sessions/{id}/status`, `/events`,
  `/report`, `/download/script`, `GET /budget`, `GET /limits`. The
  bot-check and rate limit both ended up on `/start`, not `/sessions` as
  first sketched -- see "Turnstile bot-check done" below for why.
- **Safety caps** (all built): 3 runs/day/IP, 10 experiments/session, 20s/
  experiment, 5 min wall-clock/session, 5MB CSV cap, a global daily budget
  ceiling on sandbox-seconds + LLM tokens, and a Cloudflare Turnstile
  bot-check (off by default until you provide your own key pair).
- **The one behavior change that makes this a product, not a fixed demo**:
  the agent writes its *own* baseline script from a preview of the
  uploaded dataset, instead of being handed a hardcoded one. (Built --
  see "Dataset generalization done" below.)
- **Frontend**: React (Vite), not Streamlit or plain HTML/JS -- decided in
  favor of React specifically because planned enhancements (rendering the
  uploaded dataset as an actual table, picking the target column from it
  instead of typing its name) want real component state, and Streamlit's
  rerun-on-interaction model still fights the long-running background job
  with live SSE updates regardless. Built -- see "React frontend done"
  below.

## Naming

**Tumble** — like a rock tumbler: rough draft code goes in, gets agitated
(iterated on) repeatedly, a polished result comes out. Chosen over more
"serious" options (Ratchet, Kepler) because it was explicitly meant to be
playful and is easy to explain in one sentence.

## Current status / next step

**E2B swap done.** `run_experiment` executes `train.py` in a real E2B
sandbox instead of a local `subprocess.run`. Verified: running the same
unchanged `train.py` locally and through the sandbox produced the exact
same metric, so the swap is a transparent execution-environment change.

**Dataset generalization done.** No dataset is hardcoded anymore -- given
a CSV and a target column, `dataset_preview.py` builds a shape/dtypes/
head/target-distribution summary for the system prompt, and the agent
writes its own `train.py` from scratch (no starting script exists).
Verified end-to-end on the sklearn wine dataset (13 features, 3-class
`cultivar` target) -- a dataset the code never references anywhere -- and
the agent correctly wrote its own `pandas`-based loader, hit 100% accuracy
with a RandomForest, tried a variant, and correctly reverted it.

**Multi-tenant FastAPI backend done.** This required refactoring the
single-tenant globals (`tools.py`'s module-level `WORKSPACE_ROOT`/
`state.json` path, `sandbox_exec.py`'s module-level sandbox singleton)
into instance state, so more than one session can run at once without
stepping on each other:
- `tools.Workspace` -- one instance per session; owns its own root dir,
  `state.json`, and git repo.
- `sandbox_exec.SandboxSession` -- one instance per session; owns its own
  E2B sandbox, created lazily and reused for that session's experiments.
- `agent_loop.run_agent()` -- the propose/execute/evaluate/keep-revert
  loop extracted out of `main.py`, taking a `Workspace`'s tool functions
  and a `Trajectory` as parameters. Contains no I/O assumptions (no
  `print`), so it's identical code whether the caller is the CLI or a
  server background thread.
- `Trajectory` gained a `subscribe()` hook, fired synchronously on every
  event it logs. The CLI (`main.py`) subscribes a print function; the
  server (`server.py`) subscribes a per-session `queue.Queue` push, which
  `GET /sessions/{id}/stream` drains as SSE. Same event stream, two
  consumers -- this is what let the refactor add streaming for free
  instead of building a second, parallel logging path.
- `main.py` is now a thin CLI wrapper: builds one `Workspace` +
  `SandboxSession` rooted at `.workspace/`, same as always.
- `server.py` is the new multi-tenant entry point: in-memory
  `dict[str, Session]`, one background thread per running session, each
  with its own `Workspace`/`SandboxSession`/`Trajectory` triple rooted at
  `sessions/<id>/`.

Verified concurrently, for real: two sessions started back-to-back against
two different datasets (sklearn wine -> `cultivar`, sklearn breast-cancer
-> `diagnosis`), each with its own SSE stream attached. Both ran their E2B
sandboxes in parallel, wrote completely different `train.py`s tailored to
their own dataset, and finished with independent `state.json`/`REPORT.md`/
git history under `sessions/<id>/workspace/` -- no cross-contamination.

**Safety/cost caps done, except bot-check.** Split across the two places
that made sense:
- `tools.py` (so the CLI gets them too, not just server sessions):
  `RUN_TIMEOUT_SECONDS` dropped 30s -> 20s; `MAX_EXPERIMENTS = 10` is now
  a hard cap checked at the top of `run_experiment` *before* touching the
  sandbox (a call past the cap costs nothing and returns a refusal telling
  the agent to write its report), not just advisory prompt text. The
  system prompt's stated budget (`prompts.py`) now imports this same
  constant instead of a separate, driftable number.
- `agent_loop.run_agent()` gained two optional params: `max_wall_clock_seconds`
  (checked at the top of every turn; returns `"time_limit_exceeded"`) and
  `budget: DailyBudget | None` (checked the same way; returns
  `"budget_exhausted"`; also accumulates `response.usage.total_tokens`
  after every LLM call). Both are opt-in -- the CLI doesn't pass them
  (unlimited, same as before), `server.py` does (300s, plus the shared
  `DailyBudget`).
- `budget.py` (`DailyBudget`) and `rate_limit.py` (`IpRateLimiter`) are
  new, small, thread-safe, in-memory, process-wide, resetting at local
  midnight (`date.today()`, the server host's system timezone, not UTC)
  -- same "good enough at this scale, no Redis/Postgres yet" call as the
  session store. `server.py` wires:
  `POST /sessions/{id}/start` checks the rate limiter (`429` past 3/day/IP)
  and the budget (`503` once exhausted) before spawning the background
  thread; `POST /sessions/{id}/dataset` streams the upload in 1MB chunks
  and aborts (`413`, deletes the partial file) past 5MB, rather than
  trusting a `Content-Length` header; `SandboxSession` gained an
  `on_close` callback reporting how long the sandbox was alive, wired to
  `DailyBudget.add_sandbox_seconds` as a coarse proxy for billed
  sandbox-seconds. `GET /budget` exposes current usage.
- The two daily budget numbers (3600 sandbox-seconds, 2,000,000 LLM
  tokens) are placeholder defaults, not derived from a specific cost
  target the way the E2B per-experiment cost was -- override via
  `DAILY_SANDBOX_SECONDS_BUDGET`/`DAILY_LLM_TOKEN_BUDGET` env vars once
  real traffic/cost tolerance is known.

Verified for real, not just unit-tested in isolation: started 4 session
starts back-to-back from the same IP against the real running server --
the first 3 ran for real (real LLM calls, real E2B sandboxes) and the 4th
got a real `429`; uploaded a real 8MB CSV and got a real `413` with the
partial file cleaned up; after the 3 real sessions finished, `GET /budget`
showed real accumulated numbers (889,519 LLM tokens, 248.7 sandbox-seconds)
that had been exactly zero beforehand. The per-session experiment cap and
the `run_agent` wall-clock/budget short-circuits were verified directly
(a fake sandbox that raises if `.run()` is ever called, to prove the cap
short-circuits *before* spending anything) rather than by waiting out the
real 5-minute/10-experiment limits, since those are slow to hit naturally.

**React frontend done.** `frontend/` -- a Vite + React SPA, no extra state
library (the whole flow fits in a handful of `useState`s in `App.jsx`).
- `src/api.js`: thin fetch/EventSource wrappers over every `server.py`
  endpoint. `streamEvents()` wraps a native `EventSource` against
  `/stream`, dispatching on the default `message` event for trajectory
  events and the server's explicit `event: done` for stream close.
- `SetupForm.jsx`: two-step, matching the API's own shape -- "Load
  dataset" (create session + upload CSV, shows the backend's preview text
  so the user can sanity-check the target column before spending
  anything) then "Start run" (metric/minimize/prompt, unlocked only after
  a successful upload).
- `TrajectoryFeed.jsx`: renders the *exact* event stream `Trajectory`
  logs -- meta/assistant/tool/notice/log -- with no separate frontend
  vocabulary to keep in sync. One special case: `write_file`'s `content`
  arg is summarized (`path="x.py", 712 chars`) instead of dumped as
  escaped JSON, which was unreadable in practice (caught by looking at an
  actual screenshot, not by inspection).
- `App.jsx`'s hero regex-parses `run_experiment`'s own result string
  (`"Iteration N: metric=X. ... Current best: Y."`) for the live
  best-metric/attempt-count display, rather than the backend emitting a
  separate structured event for it -- one source of truth, no schema to
  keep in sync between the harness and the frontend.
- Design: dark graphite (not pure-black) background, IBM Plex Sans (UI) +
  IBM Plex Mono (the trajectory feed and dataset preview, since that
  content genuinely is code/data, not a decorative label choice), two
  muted accents (sage for "kept"/success, amber for sandbox-log/notice
  lines) instead of one bright neon accent, hairline borders instead of
  card-shadow chrome. No numbered-step markers, no ALL-CAPS eyebrows, no
  arrow-suffixed buttons.
- CORS: `server.py` now allows all origins (`CORSMiddleware`, configurable
  via `CORS_ORIGINS`) -- fine for now since there's no auth either; needs
  locking down together, before going public.

Verified in an actual browser, not just `vite build` succeeding: installed
Playwright, launched both dev servers, and drove the real flow headlessly
against the real backend -- upload the sklearn wine CSV, load the dataset
preview, start a run, watch the live feed populate over real SSE, wait for
completion, confirm the report rendered. Screenshots at each step were
reviewed by hand; console had zero errors throughout. The one thing this
caught that static review wouldn't have: the `write_file` args dump being
unreadable in the actual rendered feed. Playwright and its downloaded
Chromium were removed again afterward -- it isn't a project dependency,
just how this got checked.

**Not built: the Cloudflare Turnstile bot-check.** Deliberately deferred,
not skipped -- it's a client+server pairing, and there was no way to
generate or verify a real Turnstile token without the widget half. Now
that a real frontend exists, this is unblocked and is the natural next
piece.

**Report/code readability pass, after first real usage.** The first real
use of the frontend (uploading the sklearn iris dataset by hand) surfaced
that the report was rendering as literal Markdown source (`#`, `**`)
instead of formatted text -- exactly the kind of gap that only shows up
once a person, not a script, looks at the page.
- `MarkdownReport.jsx` renders the report via `react-markdown`, with no
  `rehype-raw`/HTML plugin enabled. That's a deliberate security choice,
  not just the default: any literal HTML an agent's report text happened
  to contain renders as inert text instead of markup. This matters
  because in the multi-tenant server case the report's content ultimately
  traces back to an uploaded dataset (and thus an untrusted visitor), so
  it can't be treated as trusted HTML the way a hand-written page's own
  copy can.
- `CodeViewer.jsx` adds a "View code"/"Copy" toggle next to the download
  button, reusing the exact same `/download/script` endpoint -- a
  `fetch()` read is unaffected by the `Content-Disposition: attachment`
  header that makes a direct browser navigation to that URL download
  instead of display, so no new backend endpoint was needed.

Verified in-browser again the same way: Playwright against the real
running app (this time the iris dataset, matching what surfaced the
issue), confirming the report renders as actual `<h1>`/`<h2>`/`<ul>`
elements (no literal `#`/`**` visible), the code panel shows real Python
matching the downloaded script, and the copy button's clipboard content
matches it exactly.

**Click-to-pick target column, after the report/code pass.** `csv.js`
reads only the first 64KB of the picked `File` client-side (a minimal
quoted-field-aware line splitter, not a full RFC 4180 parser -- it only
feeds a cosmetic preview, the backend still does the real parsing with
pandas), so the preview table renders instantly on file selection with no
backend round trip and no target column needed yet. `DatasetTable.jsx`'s
column headers are clickable, filling the same `target` state a typed
name would; the two stay in sync by comparing the current value against
each header, so typing a name that happens to match a column highlights
it too, for free. Verified in-browser: clicking a header fills the field,
highlights the whole column, and the existing "Load dataset" flow is
unaffected.

**Three more "quick win" additions, same session:**
- **Live metric-per-attempt chart** (`MetricChart.jsx`, plain inline SVG,
  no charting library). Followed the dataviz skill's procedure: this is a
  single-series "change over time" job, so no categorical palette
  decision (and thus no CVD validator run) applies -- it reuses the sage
  accent already established elsewhere in the app as "kept/improved,"
  rather than picking a new hue. Mark specs from the skill: 2px round-cap
  line, >=8px markers (filled sage when an attempt was kept, hollow when
  reverted -- an existing per-point attribute, not a second series/legend),
  a surface-color ring around each marker so it stays legible crossing the
  line, hairline solid gridlines, direct end-label, and a crosshair +
  tooltip on hover (value first, attempt/kept-reverted second). Plots each
  attempt's own metric value (not the monotonic running-best), so real
  variability across attempts is visible, not hidden behind a ratchet line.
- **Shareable session links.** Starting a run now does
  `history.pushState` to `/session/<id>`; reopening that URL resumes it.
  This needed one real backend addition: `GET /sessions/{id}/events`,
  which replays a session's `Trajectory` JSONL file from disk. That's
  distinct from `/stream` on purpose -- `/stream` drains a single-consumer
  in-memory queue, so a *second* viewer connecting after the first one
  already consumed it would see nothing and hang forever waiting on events
  that already happened. `/events` reads the durable file instead, so it's
  safe to call any number of times. The frontend's resume logic branches
  on status: a still-`running` session just attaches to `/stream` like a
  fresh one (accepting the same single-consumer caveat if a first viewer
  already drained it -- an existing, documented limitation, not a new
  one); a terminal session (`completed`/`failed`/etc.) fetches its full
  history via `/events` plus the report/script, with no `/stream` call at
  all. Sessions are still in-memory only (per the original session-store
  decision), so a link stops resolving if the server process has restarted
  since -- the frontend catches that 404 and falls back to the setup form
  rather than hanging.
- **Per-IP "runs left today."** `IpRateLimiter` gained a read-only
  `remaining(ip)` alongside its existing `check_and_increment(ip)`, and a
  new `GET /limits` reports it for the caller's IP. Shown as a small note
  above the setup form, refreshed after every run starts -- the same cap
  `POST /start` already enforced with a `429`, just made visible instead
  of only discoverable by hitting it.

Verified together, live: started a real run, confirmed the chart plots
points as they arrive and its tooltip reports the right value/attempt,
confirmed the URL updates to `/session/<id>`, then did a **full page
reload** on that exact URL after the run finished and confirmed the hero,
chart, trajectory feed, and rendered report all reconstructed correctly
from `/events` alone -- no client state carried over. Confirmed the
"runs left today" note decrements after a run starts.

**Turnstile bot-check done.** Gates `POST /sessions/{id}/start` (not
`POST /sessions` as the original architecture sketch above says) -- moved
to match where the rate limiter and budget check already live, since
session creation alone is free and starting is where real cost begins;
all three gates now sit together in one place.
- `turnstile.py`: `verify(token, remote_ip)` calls Cloudflare's
  `siteverify` REST API (via `httpx`, now an explicit dependency --
  previously only transitive through `openai`/`e2b`). Returns `True`
  unconditionally when `TURNSTILE_SECRET_KEY` is unset (disabled by
  default, same opt-in pattern as `CORS_ORIGINS` and the budget env
  vars), `False` on a missing token without ever calling Cloudflare, and
  `False` (never an exception) if Cloudflare itself is unreachable --
  failing the check, not the request, on a third-party outage.
- `StartRequest` gained `turnstile_token: str | None`; `start_session`
  checks it before the rate limiter, so a bot-check failure costs the
  caller nothing against their daily quota.
- `Turnstile.jsx` renders Cloudflare's widget (loaded via a `<script>` in
  `index.html`) through its explicit `turnstile.render()` API rather than
  the auto-render div convention, for a clean React mount/unmount; it
  renders nothing and blocks nothing when `VITE_TURNSTILE_SITE_KEY` is
  unset. `SetupForm.jsx` disables "Start run" until the widget's callback
  hands back a token, only when a site key is actually configured.
- Both env vars are opt-in and independent -- CLI and local dev are never
  gated by this either.

Verified against Cloudflare's own real API, not mocked: `turnstile.py`'s
`verify()` was exercised directly against `siteverify` using Cloudflare's
publicly documented test key pairs -- the "always passes" secret with a
garbage token returned `True`, the "always fails" secret with the same
garbage token returned `False`, and an unconfigured/missing-token case
returned `True`/`False` respectively without any network call. Then the
full stack end to end: set the real (test) site key pair in both
`.env`s, restarted both servers, and drove the actual app -- confirmed
"Start run" is disabled until the widget's callback fires, confirmed a
direct `curl` to `/start` with no token gets a real `403` and does *not*
consume a rate-limit slot, and confirmed a real browser run through the
widget produces a token that a real run starts and completes with. The
test key pair (Cloudflare's own published "always passes" values, not a
real site's credentials) was removed again afterward, returning both
`.env`s to disabled -- it provides zero actual bot protection, so leaving
it in would look configured without being so. Enabling it for real needs
your own free key pair from a Cloudflare account, which only you can
create.

**Next**: deploy, soft-launch.
