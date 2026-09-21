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

## Multi-tenant architecture (planned, not yet built)

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
  an explicit "start" click. `sessions/<uuid>/{train.py, state.json, .git/}`
  on disk (same nested-git keep/revert mechanism as today, just scoped per
  session instead of one fixed `.workspace/`). In-memory session store is
  fine at this scale — no need for Redis/Postgres yet.
- **API**: `POST /sessions` (bot-check + rate limit here) → `POST
  /sessions/{id}/dataset` (CSV upload + preview) → `POST
  /sessions/{id}/start` (kicks off the loop as a background task) → `GET
  /sessions/{id}/stream` (SSE, live tool calls/results/metrics) → `GET
  /sessions/{id}/report` + `/download/script`.
- **Safety caps** (ship before going public, not after): 3 runs/day/IP, 10
  experiments/session, 20s/experiment, 5 min wall-clock/session, 5MB CSV
  cap, a global daily budget ceiling on sandbox-seconds + LLM tokens, and a
  Cloudflare Turnstile bot-check before a run can start.
- **The one behavior change that makes this a product, not a fixed demo**:
  the agent writes its *own* baseline script from a preview of the
  uploaded dataset, instead of being handed a hardcoded one.
- **Frontend**: leaning toward a small custom frontend (not Streamlit) —
  Streamlit's rerun-on-interaction model fights a long-running background
  job with live SSE updates; a plain HTML/JS or light React page calling
  the FastAPI backend directly is a more natural fit and reads as more of
  a real product.

## Naming

**Tumble** — like a rock tumbler: rough draft code goes in, gets agitated
(iterated on) repeatedly, a polished result comes out. Chosen over more
"serious" options (Ratchet, Kepler) because it was explicitly meant to be
playful and is easy to explain in one sentence.

## Current status / next step

The core loop (LLM client, tools, trajectory logging, the propose → run →
keep/revert loop) is ported from the coursework project and verified
working end-to-end in this repo. It still executes locally and still
targets one hardcoded dataset.

**Next**: swap `run_experiment`'s local `subprocess.run` call in
`tools.py` for a real E2B sandbox call (reusing one sandbox per session,
not recreating it per experiment, to avoid paying the `pip install` cost
every iteration). Everything else in the roadmap follows after that.
