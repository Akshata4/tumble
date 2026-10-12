import json
import os
import time
import uuid
from typing import Callable

# Anchored to this file's own directory (not the process cwd) so logs land
# in trajectories/ regardless of where `uv run` is invoked from.
TRAJECTORY_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trajectories")


class Trajectory:
    """Append-only JSONL event log for one agent run. Every system prompt,
    user message, assistant turn, and tool result is written as its own
    line the moment it happens, so a run can be fully reconstructed later
    even if the process crashes mid-run.

    Also the single place other code observes a run as it happens: a
    caller can `subscribe()` a callback that fires with every event record
    the instant it's logged -- the CLI uses this to print, the FastAPI
    server uses it to push to a per-session queue for SSE streaming."""

    def __init__(self):
        os.makedirs(TRAJECTORY_DIR, exist_ok=True)
        run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        self.path = os.path.join(TRAJECTORY_DIR, f"{run_id}.jsonl")
        self._file = open(self.path, "a")
        self._subscribers: list[Callable[[dict], None]] = []

    def subscribe(self, fn: Callable[[dict], None]) -> None:
        self._subscribers.append(fn)

    def _log(self, event_type: str, data: dict) -> None:
        record = {"ts": time.time(), "type": event_type, **data}
        self._file.write(json.dumps(record) + "\n")
        self._file.flush()
        for sub in self._subscribers:
            try:
                sub(record)
            except Exception:
                pass  # a broken subscriber (e.g. a dead SSE client) must never crash the run

    def log_meta(self, provider: str, model: str) -> None:
        self._log("meta", {"provider": provider, "model": model})

    def log_system(self, content: str) -> None:
        self._log("system", {"content": content})

    def log_user(self, content: str) -> None:
        self._log("user", {"content": content})

    def log_assistant(self, content: str | None, tool_calls: list[dict] | None, turn: int | None = None) -> None:
        self._log("assistant", {"content": content, "tool_calls": tool_calls, "turn": turn})

    def log_tool_result(self, name: str, args: dict, result: str, turn: int | None = None) -> None:
        self._log("tool", {"name": name, "args": args, "result": result, "turn": turn})

    def log_notice(self, content: str) -> None:
        self._log("notice", {"content": content})

    def close(self) -> None:
        self._file.close()


def _preview(text: str, limit: int = 300) -> str:
    return text if len(text) <= limit else text[:limit] + " [truncated]"


def replay_file(path: str) -> None:
    """Reprint a trajectory file's events in the same format the live run
    printed them in, proving the log is a faithful, complete record."""
    with open(path) as f:
        for line in f:
            event = json.loads(line)
            etype = event["type"]

            if etype == "meta":
                print(f"[replay of {path}]")
                print(f"[provider={event['provider']} model={event['model']}]\n")
            elif etype == "system":
                print(f"[system] {event['content']}\n")
            elif etype == "user":
                print(f"you> {event['content']}\n")
            elif etype == "assistant":
                for call in event.get("tool_calls") or []:
                    print(f"[tool call] {call['name']}({call['args']})")
                if event.get("content"):
                    print(f"assistant> {event['content']}\n")
            elif etype == "tool":
                print(f"    -> {_preview(event['result'])}")
            elif etype == "notice":
                print(f"\n[{event['content']}]")
