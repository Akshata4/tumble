"""Per-IP daily cap on starting a run. In-memory and per-process -- same
scale tradeoff as budget.py. Not a substitute for the bot-check (a
determined abuser can rotate IPs); it's the cheap first line of defense."""

import threading
from datetime import date


class IpRateLimiter:
    def __init__(self, max_per_day: int):
        self.max_per_day = max_per_day
        self._lock = threading.Lock()
        self._day: date | None = None
        self._counts: dict[str, int] = {}

    def _roll_if_new_day(self) -> None:
        today = date.today()
        if self._day != today:
            self._day = today
            self._counts = {}

    def check_and_increment(self, ip: str) -> bool:
        """Returns True (and counts the attempt) if `ip` is still under its
        daily cap, False if it's already at the limit."""
        with self._lock:
            self._roll_if_new_day()
            count = self._counts.get(ip, 0)
            if count >= self.max_per_day:
                return False
            self._counts[ip] = count + 1
            return True

    def remaining(self, ip: str) -> int:
        """Read-only: how many of `ip`'s daily allowance are left. Doesn't count
        as an attempt itself."""
        with self._lock:
            self._roll_if_new_day()
            return max(0, self.max_per_day - self._counts.get(ip, 0))
