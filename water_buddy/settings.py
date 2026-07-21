"""User settings: an in-memory dataclass plus JSON persistence.

Why a dataclass instead of passing a plain dict around? Because a dict lets a
typo like ``settings["intervall"]`` fail silently at 3am, whereas
``settings.intervall`` fails immediately and visibly. The dataclass is the
single definition of what a setting *is*.

Forward-compatibility rules, which matter once you've shipped v1 and want to
add a field in v2 without wiping people's config:

  * Unknown keys in the file are ignored, not fatal. (A v1 app reading a v2
    file must not crash.)
  * Missing keys fall back to the dataclass default. (A v2 app reading a v1
    file just gets the new default.)

That combination means you can add fields freely and never write a migration.
"""

from __future__ import annotations

import dataclasses
import json
import logging
from dataclasses import dataclass, field
from datetime import time as dt_time

from . import config

log = logging.getLogger(__name__)


def _parse_hhmm(value: str, fallback: dt_time) -> dt_time:
    """Turn "22:30" into a time object, tolerating garbage in the file."""
    try:
        hh, mm = value.split(":")
        return dt_time(hour=int(hh), minute=int(mm))
    except (ValueError, AttributeError):
        log.warning("Bad time value %r in settings, using %s", value, fallback)
        return fallback


@dataclass
class Settings:
    # --- Core reminder behaviour ---
    interval_minutes: int = 60
    display_seconds: int = 5
    enabled: bool = True

    # --- Character ---
    # "female", "male", or "random" to alternate each time.
    character: str = "female"

    # How the buddy arrives: "glide" (smooth slide, correct for a standing
    # pose) or "walk" (adds a bob and rock, for art that looks mid-stride).
    entrance_style: str = "glide"

    # --- Where the buddy appears ---
    # Index into the list of screens; 0 is primary. Clamped at runtime in case
    # you unplug a monitor between sessions.
    monitor_index: int = 0

    # --- Politeness ---
    quiet_hours_enabled: bool = False
    quiet_start: str = "22:00"
    quiet_end: str = "08:00"
    skip_when_fullscreen: bool = True
    sound_enabled: bool = False

    # --- Tracking ---
    daily_goal_glasses: int = 8

    # --- System integration ---
    launch_on_startup: bool = False

    # ------------------------------------------------------------------
    # Derived helpers
    # ------------------------------------------------------------------
    @property
    def interval_seconds(self) -> int:
        return max(config.MIN_INTERVAL_MINUTES, self.interval_minutes) * 60

    @property
    def quiet_start_time(self) -> dt_time:
        return _parse_hhmm(self.quiet_start, dt_time(22, 0))

    @property
    def quiet_end_time(self) -> dt_time:
        return _parse_hhmm(self.quiet_end, dt_time(8, 0))

    def is_quiet_now(self, now: dt_time) -> bool:
        """True if ``now`` falls inside the quiet window.

        Handles the wrap-around case: 22:00 -> 08:00 spans midnight, so the
        window is "at or after start OR before end" rather than a simple
        between-check. A non-wrapping window like 13:00 -> 14:00 uses the
        ordinary between-check.
        """
        if not self.quiet_hours_enabled:
            return False
        start, end = self.quiet_start_time, self.quiet_end_time
        if start == end:
            return False  # zero-width window means "never quiet"
        if start < end:
            return start <= now < end
        return now >= start or now < end

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    def clamped(self) -> "Settings":
        """Return a copy with every value forced into a sane range.

        Called after loading, because the settings file is a text file a user
        can hand-edit -- and will, eventually, with a typo.
        """
        c = dataclasses.replace(self)
        c.interval_minutes = int(
            min(max(c.interval_minutes, config.MIN_INTERVAL_MINUTES),
                config.MAX_INTERVAL_MINUTES)
        )
        c.display_seconds = int(
            min(max(c.display_seconds, config.MIN_DISPLAY_SECONDS),
                config.MAX_DISPLAY_SECONDS)
        )
        if c.character not in (*config.CHARACTER_IDS, "random"):
            c.character = "female"
        if c.entrance_style not in ("glide", "walk"):
            c.entrance_style = "glide"
        c.monitor_index = max(0, int(c.monitor_index))
        c.daily_goal_glasses = min(max(int(c.daily_goal_glasses), 1), 30)
        return c

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    @classmethod
    def load(cls) -> "Settings":
        path = config.SETTINGS_FILE
        if not path.exists():
            log.info("No settings file at %s, using defaults", path)
            return cls()
        try:
            # utf-8-sig, not utf-8: Notepad and several Windows editors save
            # UTF-8 files with a byte-order mark, and a plain utf-8 read fails
            # on it. The user would open settings.json to change one number,
            # save it, and silently lose every setting they had. utf-8-sig
            # strips a BOM when present and behaves like utf-8 when not.
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            log.error("Could not read settings (%s); using defaults", exc)
            return cls()

        if not isinstance(raw, dict):
            log.error("Settings file is not an object; using defaults")
            return cls()

        known = {f.name for f in dataclasses.fields(cls)}
        filtered = {k: v for k, v in raw.items() if k in known}
        dropped = set(raw) - known
        if dropped:
            log.info("Ignoring unknown settings keys: %s", sorted(dropped))

        try:
            return cls(**filtered).clamped()
        except TypeError as exc:
            log.error("Settings had wrong value types (%s); using defaults", exc)
            return cls()

    def save(self) -> None:
        """Write settings atomically.

        Writing to a temp file and then replacing means a crash mid-write can
        never leave you with a half-written, unparseable settings file. On
        Windows ``Path.replace`` is atomic within the same directory.
        """
        path = config.SETTINGS_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        payload = json.dumps(dataclasses.asdict(self), indent=2)
        try:
            tmp.write_text(payload, encoding="utf-8")
            tmp.replace(path)
        except OSError as exc:
            log.error("Could not save settings: %s", exc)
