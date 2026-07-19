"""Hydration history: how many glasses per day, and your streak.

This is the module that would have been "the database" if we'd built a backend.
Instead it's a JSON file of ``{"2026-07-19": 6}`` entries. A year of history is
about 6 KB. Rewriting the whole file on every change is not merely acceptable
here, it's simpler and safer than any incremental scheme.
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta

from . import config

log = logging.getLogger(__name__)

# Keep roughly a year, so the file can't grow without bound.
RETAIN_DAYS = 400


class Stats:
    def __init__(self, days: dict[str, int] | None = None):
        # Maps ISO date string -> glasses logged that day.
        self._days: dict[str, int] = days or {}

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------
    def count_for(self, day: date) -> int:
        return self._days.get(day.isoformat(), 0)

    def today_count(self) -> int:
        return self.count_for(date.today())

    def streak(self, goal: int, today: date | None = None) -> int:
        """Consecutive days ending yesterday-or-today where the goal was met.

        Today counts only if you've already hit the goal, so a fresh morning
        doesn't show your streak as broken before you've had a chance to drink
        anything.
        """
        today = today or date.today()
        streak = 0
        cursor = today
        if self.count_for(today) < goal:
            cursor = today - timedelta(days=1)
        while self.count_for(cursor) >= goal:
            streak += 1
            cursor -= timedelta(days=1)
        return streak

    def last_n_days(self, n: int, today: date | None = None) -> list[tuple[date, int]]:
        """Oldest-first list of (date, count) for charting later."""
        today = today or date.today()
        return [
            (today - timedelta(days=offset), self.count_for(today - timedelta(days=offset)))
            for offset in range(n - 1, -1, -1)
        ]

    # ------------------------------------------------------------------
    # Writing
    # ------------------------------------------------------------------
    def log_glass(self, when: date | None = None) -> int:
        """Record one glass. Returns the new count for that day."""
        key = (when or date.today()).isoformat()
        self._days[key] = self._days.get(key, 0) + 1
        self._prune()
        self.save()
        return self._days[key]

    def undo_glass(self, when: date | None = None) -> int:
        """Remove one glass, never going below zero. For misclicks."""
        key = (when or date.today()).isoformat()
        if self._days.get(key):
            self._days[key] -= 1
            if self._days[key] == 0:
                del self._days[key]
        self.save()
        return self._days.get(key, 0)

    def _prune(self) -> None:
        cutoff = (date.today() - timedelta(days=RETAIN_DAYS)).isoformat()
        stale = [k for k in self._days if k < cutoff]
        for key in stale:
            del self._days[key]

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    @classmethod
    def load(cls) -> "Stats":
        path = config.STATS_FILE
        if not path.exists():
            return cls()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.error("Could not read stats (%s); starting fresh", exc)
            return cls()

        days = raw.get("days") if isinstance(raw, dict) else None
        if not isinstance(days, dict):
            log.error("Stats file malformed; starting fresh")
            return cls()

        # Drop anything that isn't a clean "date -> positive int" pair rather
        # than letting a bad entry poison arithmetic later.
        clean: dict[str, int] = {}
        for key, value in days.items():
            try:
                date.fromisoformat(key)
            except (ValueError, TypeError):
                continue
            if isinstance(value, int) and value > 0:
                clean[key] = value
        return cls(clean)

    def save(self) -> None:
        path = config.STATS_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        try:
            tmp.write_text(json.dumps({"days": self._days}, indent=2), encoding="utf-8")
            tmp.replace(path)
        except OSError as exc:
            log.error("Could not save stats: %s", exc)
