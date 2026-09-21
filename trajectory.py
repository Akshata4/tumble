import json
import os
import time
import uuid

# Anchored to this file's own directory (not the process cwd) so logs land
# in trajectories/ regardless of where `uv run` is invoked from.
TRAJECTORY_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trajectories")


class Trajectory:
    """Append-only JSONL event log for one agent run. Every system prompt,
    user message, assistant turn, and tool result is written as its own
    line the moment it happens, so a run can be fully reconstructed later
    even if the process crashes mid-run."""

    def __init__(self, provider: str, model: str):
        os.makedirs(TRAJECTORY_DIR, exist_ok=True)
        run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        self.path = os.path.join(TRAJECTORY_DIR, f"{run_id}.jsonl")
        self._file = open(self.path, "a")
        self._log("meta", {"provider": provider, "model": model})

    def _log(self, event_type: str, data: dict) -> None:
        record = {"ts": time.time(), "type": event_type, **data}
        self._file.write(json.dumps(record) + "\n")
        self._file.flush()

    def log_system(self, content: str) -> None:
        self._log("system", {"content": content})

    def log_user(self, content: str) -> None:
        self._log("user", {"content": content})

    def log_assistant(self, content: str | None, tool_calls: list[dict] | None) -> None:
        self._log("assistant", {"content": content, "tool_calls": tool_calls})

    def log_tool_result(self, name: str, args: dict, result: str) -> None:
        self._log("tool", {"name": name, "args": args, "result": result})

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
