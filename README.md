# Tumble

An autonomous ML research agent: point it at your data, tell it what to
optimize, and it iteratively proposes changes, runs real experiments, and
keeps only what actually improves the score — like a rock tumbler for model
code. Rough drafts go in, get agitated over and over, and a polished result
comes out the other end.

**Status: early development.** This is being built as a public product —
sandboxed execution for untrusted, agent-generated code; multi-tenant
sessions so strangers can safely run their own experiments; a live
streaming UI. None of that is here yet. What's here now is the validated
core loop.

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

## Run it (current, single-tenant state)

```bash
uv sync
cp .env.example .env
# fill in WANDB_API_KEY, WANDB_PROJECT, E2B_API_KEY
uv run python main.py
```

Currently still runs against a hardcoded scikit-learn breast-cancer
baseline and executes locally, same as the coursework version — the E2B
sandbox swap and dataset generalization are the next steps.

See [`DESIGN.md`](DESIGN.md) for the full decisions log — why E2B over
CoreWeave, the planned multi-tenant architecture, and safety/cost caps.

## Roadmap

- [ ] Swap local subprocess execution for a real E2B sandbox (in progress)
- [ ] Generalize beyond one hardcoded dataset — agent writes its own
      baseline from an uploaded CSV
- [ ] Multi-tenant FastAPI backend, session-isolated workspaces
- [ ] Abuse/cost caps: per-IP rate limits, global daily budget, bot-check
- [ ] Live-streaming frontend (trajectory view + metric chart)
- [ ] Deploy, soft-launch, then go public
