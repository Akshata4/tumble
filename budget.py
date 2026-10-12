"""A process-wide daily spend ceiling across every session, on top of the
per-session caps in tools.py/agent_loop.py. Those bound a single visitor's
worst case; this bounds everyone's combined worst case for one day, so a
lot of small legitimate sessions can't add up to an unbounded bill.

In-memory and per-process, matching DESIGN.md's call that a session store
this simple doesn't need Redis/Postgres yet -- restarting the process
resets the day's spend, which is an acceptable gap at this scale."""

import threading
from datetime import date


class DailyBudget:
    def __init__(self, max_sandbox_seconds: float, max_llm_tokens: int):
        self.max_sandbox_seconds = max_sandbox_seconds
        self.max_llm_tokens = max_llm_tokens
        self._lock = threading.Lock()
        self._day: date | None = None
        self._sandbox_seconds = 0.0
        self._llm_tokens = 0

    def _roll_if_new_day(self) -> None:
        today = date.today()
        if self._day != today:
            self._day = today
            self._sandbox_seconds = 0.0
            self._llm_tokens = 0

    def has_budget(self) -> bool:
        with self._lock:
            self._roll_if_new_day()
            return self._sandbox_seconds < self.max_sandbox_seconds and self._llm_tokens < self.max_llm_tokens

    def add_sandbox_seconds(self, seconds: float) -> None:
        with self._lock:
            self._roll_if_new_day()
            self._sandbox_seconds += seconds

    def add_llm_tokens(self, tokens: int) -> None:
        with self._lock:
            self._roll_if_new_day()
            self._llm_tokens += tokens

    def usage(self) -> dict:
        with self._lock:
            self._roll_if_new_day()
            return {
                "sandbox_seconds": self._sandbox_seconds,
                "max_sandbox_seconds": self.max_sandbox_seconds,
                "llm_tokens": self._llm_tokens,
                "max_llm_tokens": self.max_llm_tokens,
            }
