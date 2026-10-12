# Future ideas -- agent capability upgrades

Brainstormed 2026-09-28. This is a backlog, not a committed roadmap (see
README.md's Roadmap for what's actually planned/built) -- pick one up
whenever there's time, in whatever order feels interesting. Unlike the
recent UI "quick wins," these all make the *agent* itself do more/better
research, not just present results better.

Rough starting point if picking blind: **ensembling top candidates**
(smallest change, most visible payoff on the existing budget). **The
playbook** is the most interesting long-term but the biggest lift.

## Search strategy

### Parallel / tree-search exploration
Right now the loop is strictly linear: propose -> run -> keep/revert,
one experiment at a time. Real AIDE-style systems search a *tree* of
approaches instead. Concretely: on each round, have the agent (or the
harness) kick off 2-3 genuinely different approaches at once (e.g. one
tree-based model, one linear, one boosting) in parallel sandboxes, keep
whichever branch wins, prune the rest.
- **Where it touches the code**: `Workspace` currently assumes one
  `SandboxSession` and one linear git history. Parallel branches need
  either N sandboxes per round (cheap, we already have `SandboxSession`
  as a reusable class) or N git branches within one workspace. The
  `run_experiment` tool would need to become "propose N variants, run
  them concurrently, keep the winner" rather than one call per attempt --
  which also changes the system prompt's contract in `prompts.py`.
- **Effort**: medium-large. Changes the core loop's shape, not just an
  addition.

### Ensembling top candidates
Only the single best-scoring "kept" model survives -- every other
attempt is discarded (git-reverted) even if it was decent. Add a final
step, once the experiment budget is spent, where the agent combines its
top-K *distinct* kept models (not just the single best) into an ensemble
(e.g. `VotingClassifier`/averaging) as a last polishing move.
- **Where it touches the code**: `Workspace` would need to stop discarding
  every non-best attempt's script -- e.g. keep each "kept" iteration's
  `train.py` as a separate git tag/commit reference (already have full git
  history, just need to expose "give me the top K kept scripts" via a new
  method), then a new tool (`build_ensemble`?) or just letting the agent's
  last `write_file`+`run_experiment` reference multiple saved scripts.
- **Effort**: small-medium. Reuses git history we already keep; the main
  work is a way to reference past kept versions from inside the sandbox.

### Hyperparameter sweep tool
A dedicated `run_sweep` tool: takes a small grid/random search spec over
named hyperparameters, runs the whole sweep as *one* sandbox invocation
(many fast fits in one round trip) instead of one `run_experiment` call
per configuration.
- **Where it touches the code**: new tool function in `tools.py` +
  schema in `TOOL_SCHEMAS`; the harness would need to decide how a sweep
  result maps to "kept" (probably: report the sweep's own best config's
  metric, same keep/revert logic as today).
- **Effort**: medium.

## Rigor / trust

### Cross-validation instead of one train/test split
We've already seen ties at 1.0 accuracy from lucky 80/20 splits (wine,
iris) -- a single split is noisy. Push (via the system prompt, or a
harness-side check) toward k-fold CV so "kept" means something more
robust.
- **Where it touches the code**: mostly `prompts.py` (tell the agent to
  use CV and report the mean), possibly a light validation in
  `Workspace.run_experiment` that nudges back if the script's METRIC
  looks like a single split with no CV (hard to detect robustly without
  parsing the script -- probably starts as prompt-only guidance).
- **Effort**: small to start (prompt change), larger if you want the
  harness to actually enforce/detect it.

### Leakage / sanity-check pass
A pre-flight check (either a tool the agent calls, or an automatic static
check the harness runs before `run_experiment` actually executes)
catching classic mistakes: fitting a scaler before the train/test split,
obvious target leakage (a feature that's a near-duplicate of the target),
etc. Saves burning sandbox runs on a script that's silently cheating.
- **Where it touches the code**: new function in `tools.py`
  (`check_experiment`?) or a lightweight AST-based static check invoked
  inside `run_experiment` itself before executing in the sandbox.
- **Effort**: medium -- the useful version needs real static analysis,
  not just string matching.

## Getting smarter over time

### Cross-session "playbook"
The coolest one long-term. Aggregate what's worked across *every* past
session (not just this one) -- e.g. "gradient boosting tends to win on
small tabular classification," "scaling helped when features had very
different ranges" -- distilled from past `REPORT.md`s, and feed a summary
of it into new sessions' system prompts (`prompts.py`). Tumble gets
measurably better at ML strategy the more people use it, not just faster
per-session.
- **Where it touches the code**: needs a persistent store across sessions
  (today `sessions/` is per-session and otherwise nothing is aggregated
  server-wide) -- likely a small `playbook.py` that reads past
  `sessions/*/workspace/REPORT.md` files (or a running summary file
  updated after each completed session) and a periodic/on-demand
  summarization pass (an LLM call over recent reports) that produces the
  text injected into `build_system_prompt()`.
- **Effort**: large -- the interesting part (distilling genuinely useful,
  non-generic lessons) is a real prompt-engineering problem, not just
  plumbing.

## Portfolio angle

### Plain-English report alongside the technical one
Right now `REPORT.md` is technical (assumes the reader knows ML). Add a
second, short plain-English summary -- what a non-technical reader (a
recruiter clicking a resume link) could actually follow -- shown
alongside the technical report in the UI.
- **Where it touches the code**: `prompts.py`'s final-report instructions
  (ask for a second file, e.g. `SUMMARY.md`), `tools.py`'s
  `_finalize_artifacts`-equivalent / `Workspace` (nothing new needed,
  `write_file` already handles arbitrary filenames), and the frontend's
  `Results` component (`App.jsx`) to fetch and show it next to the
  existing `MarkdownReport`.
- **Effort**: small. Almost entirely a prompt change plus one more fetch
  + render on the frontend, mirroring the existing report/script pattern.
