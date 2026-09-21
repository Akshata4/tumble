import json
import os
import subprocess

WORKSPACE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".workspace"))
STATE_PATH = os.path.join(WORKSPACE_ROOT, "state.json")
TRAIN_SCRIPT = os.path.join(WORKSPACE_ROOT, "train.py")
RUN_TIMEOUT_SECONDS = 30
WANDB_PROJECT = "tumble"
WANDB_ENTITY = os.environ["WANDB_PROJECT"].split("/")[0]

import wandb  # noqa: E402

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the full text contents of a file in the experiment workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path relative to the workspace root, e.g. 'train.py'."},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Overwrite a file's full contents in the experiment workspace. Use this to propose your next change to train.py before calling run_experiment.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path relative to the workspace root, e.g. 'train.py'."},
                    "content": {"type": "string", "description": "Full text content to write to the file."},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "List files in the experiment workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative path to list. Use '.' for the workspace root."},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_experiment",
            "description": (
                "Run the current train.py as one experiment. Parses the METRIC line it prints, "
                "logs a real Weights & Biases run, and automatically keeps the change (git commit) if "
                "it beats the best metric so far, or reverts it (git checkout) if it doesn't or the "
                "script errors. This decision is NOT yours to make -- it happens automatically based "
                "on the result, so you don't need to ask to keep or revert."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "description": {
                        "type": "string",
                        "description": "One sentence describing what you changed in this attempt (used in logs and the final report).",
                    },
                },
                "required": ["description"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_history",
            "description": "Return the full log of every experiment run so far: iteration number, metric, whether it was kept or reverted, and its description.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]


def _resolve_in_workspace(path: str) -> str:
    full = os.path.abspath(os.path.join(WORKSPACE_ROOT, path))
    if os.path.commonpath([full, WORKSPACE_ROOT]) != WORKSPACE_ROOT:
        raise ValueError(f"'{path}' resolves outside the workspace ({WORKSPACE_ROOT})")
    return full


def _load_state() -> dict:
    with open(STATE_PATH) as f:
        return json.load(f)


def _save_state(state: dict) -> None:
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


def read_file(path: str) -> str:
    try:
        full = _resolve_in_workspace(path)
    except ValueError as e:
        return f"Refused: {e}"
    try:
        with open(full) as f:
            return f.read()
    except OSError as e:
        return f"Error reading {path}: {e}"


def write_file(path: str, content: str) -> str:
    try:
        full = _resolve_in_workspace(path)
    except ValueError as e:
        return f"Refused: {e}"
    try:
        with open(full, "w") as f:
            f.write(content)
        return f"Wrote {len(content)} characters to {path}"
    except OSError as e:
        return f"Error writing {path}: {e}"


def list_dir(path: str) -> str:
    try:
        full = _resolve_in_workspace(path)
    except ValueError as e:
        return f"Refused: {e}"
    try:
        return "\n".join(sorted(os.listdir(full)))
    except OSError as e:
        return f"Error listing {path}: {e}"


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=WORKSPACE_ROOT,
        capture_output=True,
        text=True,
    )


def _parse_metric(stdout: str) -> float | None:
    metric = None
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("METRIC:"):
            try:
                metric = float(line.split("METRIC:", 1)[1].strip())
            except ValueError:
                continue
    return metric


def _log_to_wandb(iteration: int, description: str, metric: float | None, kept: bool, error: str | None) -> None:
    # wandb.init() auto-reads a WANDB_PROJECT env var as its own setting (a
    # standard wandb convention), but we already use that exact name for an
    # "entity/project" combo string (Part A/B's convention) -- and it reads
    # that env var during its own settings ingestion regardless of what's
    # passed as the explicit project= kwarg, rejecting the combined string
    # as invalid. Override it only for this call, then restore it, so
    # llm_client.py's use of the same env var elsewhere is unaffected.
    original = os.environ.get("WANDB_PROJECT")
    os.environ["WANDB_PROJECT"] = WANDB_PROJECT
    try:
        run = wandb.init(
            entity=WANDB_ENTITY,
            project=WANDB_PROJECT,
            name=f"iter-{iteration}",
            group="autoresearch",
            config={"description": description},
            reinit=True,
        )
        payload = {"kept": int(kept)}
        if metric is not None:
            payload["metric"] = metric
        if error is not None:
            payload["error"] = error
        run.log(payload)
        run.finish()
    finally:
        if original is not None:
            os.environ["WANDB_PROJECT"] = original


def run_experiment(description: str) -> str:
    state = _load_state()
    iteration = state["iteration"] + 1

    try:
        result = subprocess.run(
            ["uv", "run", "python", "train.py"],
            cwd=WORKSPACE_ROOT,
            capture_output=True,
            text=True,
            timeout=RUN_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        _git("checkout", "--", "train.py")
        state["iteration"] = iteration
        state["history"].append({"iteration": iteration, "metric": None, "kept": False, "description": description, "error": "timed out"})
        _save_state(state)
        _log_to_wandb(iteration, description, None, False, "timed out")
        return f"Iteration {iteration}: TIMED OUT after {RUN_TIMEOUT_SECONDS}s. Change reverted."

    metric = _parse_metric(result.stdout) if result.returncode == 0 else None

    if metric is None:
        error = result.stderr.strip()[-500:] or "no METRIC line in output"
        _git("checkout", "--", "train.py")
        state["iteration"] = iteration
        state["history"].append({"iteration": iteration, "metric": None, "kept": False, "description": description, "error": error})
        _save_state(state)
        _log_to_wandb(iteration, description, None, False, error)
        return f"Iteration {iteration}: FAILED ({error}). Change reverted; train.py is back to the last kept version."

    best = state["best_metric"]
    kept = metric > best

    if kept:
        _git("add", "train.py")
        _git(
            "-c", "user.name=autoresearch-harness",
            "-c", "user.email=autoresearch@local",
            "commit", "-m", f"iteration {iteration}: metric={metric} (kept) -- {description}",
        )
        state["best_metric"] = metric
    else:
        _git("checkout", "--", "train.py")

    state["iteration"] = iteration
    state["history"].append({"iteration": iteration, "metric": metric, "kept": kept, "description": description})
    _save_state(state)
    _log_to_wandb(iteration, description, metric, kept, None)

    verdict = "NEW BEST — change kept and committed" if kept else f"did not beat the best ({best}) — change reverted"
    return f"Iteration {iteration}: metric={metric}. This is a {verdict}. Current best: {state['best_metric']}."


def get_history() -> str:
    state = _load_state()
    lines = [f"Best so far: {state['best_metric']} (after {state['iteration']} iteration(s))", ""]
    for entry in state["history"]:
        metric_str = entry["metric"] if entry["metric"] is not None else f"FAILED ({entry.get('error', 'unknown error')})"
        kept_str = "kept" if entry["kept"] else "reverted"
        lines.append(f"  iter {entry['iteration']}: {metric_str} ({kept_str}) -- {entry['description']}")
    return "\n".join(lines)


TOOL_FUNCTIONS = {
    "read_file": read_file,
    "write_file": write_file,
    "list_dir": list_dir,
    "run_experiment": run_experiment,
    "get_history": get_history,
}
