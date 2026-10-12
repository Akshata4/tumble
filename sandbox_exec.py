"""Sandboxed execution of an agent's train.py, via E2B.

One SandboxSession = one E2B sandbox, created lazily on first use and
reused for every experiment in the session's lifetime, so the `pip
install` cost is paid once, not on every iteration. Each Workspace
(one per Tumble session, CLI or multi-tenant) owns exactly one of these.
Swapping sandbox providers later should only mean rewriting this file --
callers only see SandboxSession.run()/close().
"""

import time
from dataclasses import dataclass
from typing import Callable

from e2b import CommandExitException, TimeoutException
from e2b_code_interpreter import Sandbox

SANDBOX_LIFETIME_SECONDS = 900  # safety net; we also kill it explicitly when the run ends
TRAIN_SCRIPT_NAME = "train.py"
DATASET_NAME = "dataset.csv"


@dataclass
class ExecResult:
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool


class SandboxSession:
    def __init__(
        self,
        dataset_path: str | None = None,
        on_log: Callable[[str], None] = lambda msg: None,
        on_close: Callable[[float], None] = lambda alive_seconds: None,
    ):
        """`dataset_path` (if given) is uploaded into the sandbox once, at
        creation. `on_log` is called with human-readable status lines
        (sandbox creation is slow enough to want feedback) -- pass `print`
        for a CLI, or a queue-push for a streamed session. `on_close` is
        called once, on close(), with how many seconds the sandbox was
        alive -- a coarse proxy for sandbox-seconds billed, for a caller
        tracking a spend budget."""
        self._dataset_path = dataset_path
        self._on_log = on_log
        self._on_close = on_close
        self._sandbox: Sandbox | None = None
        self._created_at: float | None = None

    def _get_sandbox(self) -> Sandbox:
        if self._sandbox is None:
            self._on_log("creating E2B sandbox and installing scikit-learn + pandas...")
            self._sandbox = Sandbox.create(timeout=SANDBOX_LIFETIME_SECONDS)
            self._created_at = time.monotonic()
            self._sandbox.commands.run("pip install scikit-learn pandas", timeout=120)
            if self._dataset_path is not None:
                with open(self._dataset_path, "rb") as f:
                    self._sandbox.files.write(DATASET_NAME, f.read())
            self._on_log(f"sandbox ready (id={self._sandbox.sandbox_id})")
        return self._sandbox

    def run(self, code: str, timeout: int) -> ExecResult:
        """Write `code` into the sandbox as train.py and execute it there."""
        sandbox = self._get_sandbox()
        sandbox.files.write(TRAIN_SCRIPT_NAME, code)

        try:
            result = sandbox.commands.run(f"python3 {TRAIN_SCRIPT_NAME}", timeout=timeout)
            return ExecResult(result.stdout, result.stderr, result.exit_code, timed_out=False)
        except CommandExitException as e:
            return ExecResult(e.stdout, e.stderr, e.exit_code, timed_out=False)
        except TimeoutException:
            return ExecResult("", "", -1, timed_out=True)

    def close(self) -> None:
        if self._sandbox is not None:
            self._sandbox.kill()
            self._sandbox = None
            self._on_close(time.monotonic() - self._created_at)
            self._created_at = None
