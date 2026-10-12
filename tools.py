import json
import os
import shutil
import subprocess

import wandb

from sandbox_exec import SandboxSession

RUN_TIMEOUT_SECONDS = 20
MAX_EXPERIMENTS = 2  # TEMP for UI testing -- revert to 10 before anything real
WANDB_PROJECT = "tumble"
WANDB_ENTITY = os.environ["WANDB_PROJECT"].split("/")[0]

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
                "on the result, so you don't need to ask to keep or revert. Hard-capped at "
                f"{MAX_EXPERIMENTS} calls per session -- calling it again after that returns a "
                "refusal instead of running anything."
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


class Workspace:
    """One agent's editable workspace: the git-backed train.py/state.json
    directory plus the sandbox its experiments run in. One Workspace per
    Tumble session (the single CLI session, or one of many concurrent
    server sessions) -- everything here is instance state so sessions
    never share a directory, a sandbox, or a best-metric."""

    def __init__(self, root: str, sandbox: SandboxSession, session_id: str = "cli"):
        self.root = os.path.abspath(root)
        self.sandbox = sandbox
        self.session_id = session_id
        self.state_path = os.path.join(self.root, "state.json")
        self.train_script = os.path.join(self.root, "train.py")

    def init(self, dataset_csv_path: str, target_column: str, metric_name: str, minimize: bool) -> None:
        """Reset the workspace for a fresh dataset: no train.py yet (the
        agent writes its own), an empty git history, and state.json
        tracking which metric/direction this session is optimizing for."""
        if os.path.exists(self.root):
            shutil.rmtree(self.root)
        os.makedirs(self.root)
        shutil.copyfile(dataset_csv_path, os.path.join(self.root, "dataset.csv"))
        self._git("init")
        self._save_state(
            {
                "best_metric": None,
                "iteration": -1,
                "metric_name": metric_name,
                "minimize": minimize,
                "target_column": target_column,
                "history": [],
            }
        )

    def tool_functions(self) -> dict:
        return {
            "read_file": self.read_file,
            "write_file": self.write_file,
            "list_dir": self.list_dir,
            "run_experiment": self.run_experiment,
            "get_history": self.get_history,
        }

    def _resolve(self, path: str) -> str:
        full = os.path.abspath(os.path.join(self.root, path))
        if os.path.commonpath([full, self.root]) != self.root:
            raise ValueError(f"'{path}' resolves outside the workspace ({self.root})")
        return full

    def _load_state(self) -> dict:
        with open(self.state_path) as f:
            return json.load(f)

    def _save_state(self, state: dict) -> None:
        with open(self.state_path, "w") as f:
            json.dump(state, f, indent=2)

    def read_file(self, path: str) -> str:
        try:
            full = self._resolve(path)
        except ValueError as e:
            return f"Refused: {e}"
        try:
            with open(full) as f:
                return f.read()
        except OSError as e:
            return f"Error reading {path}: {e}"

    def write_file(self, path: str, content: str) -> str:
        try:
            full = self._resolve(path)
        except ValueError as e:
            return f"Refused: {e}"
        try:
            with open(full, "w") as f:
                f.write(content)
            return f"Wrote {len(content)} characters to {path}"
        except OSError as e:
            return f"Error writing {path}: {e}"

    def list_dir(self, path: str) -> str:
        try:
            full = self._resolve(path)
        except ValueError as e:
            return f"Refused: {e}"
        try:
            return "\n".join(sorted(os.listdir(full)))
        except OSError as e:
            return f"Error listing {path}: {e}"

    def _git(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args],
            cwd=self.root,
            capture_output=True,
            text=True,
        )

    @staticmethod
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

    def _log_to_wandb(
        self, iteration: int, description: str, metric: float | None, kept: bool, error: str | None, metric_name: str
    ) -> None:
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
                name=f"{self.session_id}-iter-{iteration}",
                group=f"session-{self.session_id}",
                config={"description": description, "metric_name": metric_name},
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

    def run_experiment(self, description: str) -> str:
        state = self._load_state()
        iteration = state["iteration"] + 1
        metric_name = state["metric_name"]

        if iteration >= MAX_EXPERIMENTS:
            return (
                f"Experiment budget exhausted ({MAX_EXPERIMENTS}/{MAX_EXPERIMENTS} used). "
                "No more run_experiment calls are allowed this session -- write your final "
                "report to REPORT.md now and stop."
            )

        with open(self.train_script) as f:
            code = f.read()
        result = self.sandbox.run(code, timeout=RUN_TIMEOUT_SECONDS)

        if result.timed_out:
            self._git("checkout", "--", "train.py")
            state["iteration"] = iteration
            state["history"].append({"iteration": iteration, "metric": None, "kept": False, "description": description, "error": "timed out"})
            self._save_state(state)
            self._log_to_wandb(iteration, description, None, False, "timed out", metric_name)
            return f"Iteration {iteration}: TIMED OUT after {RUN_TIMEOUT_SECONDS}s. Change reverted."

        metric = self._parse_metric(result.stdout) if result.exit_code == 0 else None

        if metric is None:
            error = result.stderr.strip()[-500:] or "no METRIC line in output"
            self._git("checkout", "--", "train.py")
            state["iteration"] = iteration
            state["history"].append({"iteration": iteration, "metric": None, "kept": False, "description": description, "error": error})
            self._save_state(state)
            self._log_to_wandb(iteration, description, None, False, error, metric_name)
            return f"Iteration {iteration}: FAILED ({error}). Change reverted; train.py is back to the last kept version."

        best = state["best_metric"]
        if best is None:
            kept = True
        else:
            kept = metric < best if state["minimize"] else metric > best

        if kept:
            self._git("add", "train.py")
            self._git(
                "-c", "user.name=autoresearch-harness",
                "-c", "user.email=autoresearch@local",
                "commit", "-m", f"iteration {iteration}: {metric_name}={metric} (kept) -- {description}",
            )
            state["best_metric"] = metric
        else:
            self._git("checkout", "--", "train.py")

        state["iteration"] = iteration
        state["history"].append({"iteration": iteration, "metric": metric, "kept": kept, "description": description})
        self._save_state(state)
        self._log_to_wandb(iteration, description, metric, kept, None, metric_name)

        verdict = "NEW BEST — change kept and committed" if kept else f"did not beat the best ({best}) — change reverted"
        return f"Iteration {iteration}: {metric_name}={metric}. This is a {verdict}. Current best: {state['best_metric']}."

    def get_history(self) -> str:
        state = self._load_state()
        best = state["best_metric"] if state["best_metric"] is not None else "none yet"
        lines = [f"Best {state['metric_name']} so far: {best} (after {state['iteration'] + 1} attempt(s))", ""]
        for entry in state["history"]:
            metric_str = entry["metric"] if entry["metric"] is not None else f"FAILED ({entry.get('error', 'unknown error')})"
            kept_str = "kept" if entry["kept"] else "reverted"
            lines.append(f"  iter {entry['iteration']}: {metric_str} ({kept_str}) -- {entry['description']}")
        return "\n".join(lines)
