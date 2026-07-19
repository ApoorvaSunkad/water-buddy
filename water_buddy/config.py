"""Constants and filesystem paths.

Everything the app needs to know about *where* things live is decided here, so
no other module has to guess. Two different roots matter:

  ASSETS_DIR  - read-only files that ship WITH the app (character images).
  DATA_DIR    - files the app WRITES at runtime (your settings, your stats).

Keeping those apart is what makes the app packageable later: when PyInstaller
bundles everything into a .exe, the assets get frozen inside the executable
while your settings must still live somewhere writable in your user profile.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "WaterBuddy"
APP_DISPLAY_NAME = "Water Buddy"


def _assets_root() -> Path:
    """Locate the assets folder, whether running from source or from a .exe.

    PyInstaller unpacks bundled data into a temp folder and records the path in
    ``sys._MEIPASS``. When running normally that attribute doesn't exist, so we
    fall back to the project directory next to this source file.
    """
    bundled = getattr(sys, "_MEIPASS", None)
    if bundled:
        return Path(bundled) / "assets"
    return Path(__file__).resolve().parent.parent / "assets"


def _data_root() -> Path:
    """Per-user writable folder: %APPDATA%\\WaterBuddy on Windows."""
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) if appdata else Path.home() / ".config"
    return base / APP_NAME


ASSETS_DIR = _assets_root()
CHARACTERS_DIR = ASSETS_DIR / "characters"
ICONS_DIR = ASSETS_DIR / "icons"

DATA_DIR = _data_root()
SETTINGS_FILE = DATA_DIR / "settings.json"
STATS_FILE = DATA_DIR / "stats.json"
LOG_FILE = DATA_DIR / "water-buddy.log"

# --- Tunable behaviour -------------------------------------------------------

# Interval bounds exposed in the settings UI (minutes).
MIN_INTERVAL_MINUTES = 1
MAX_INTERVAL_MINUTES = 8 * 60

# How long the buddy stays on screen, bounds for the slider (seconds).
MIN_DISPLAY_SECONDS = 2
MAX_DISPLAY_SECONDS = 15

# If the wall clock jumps forward by more than this between scheduler ticks we
# assume the machine slept rather than that time genuinely passed, and we
# restart the countdown instead of firing a reminder at someone who just opened
# their laptop lid. See scheduler.py.
SLEEP_GAP_SECONDS = 120

# The scheduler wakes up this often to compare the clock against the due time.
# One second is plenty precise for an hourly reminder and costs nothing.
TICK_MS = 1000

# Character sprite is drawn at this height in logical pixels; width follows the
# image's aspect ratio.
CHARACTER_HEIGHT_PX = 260

CHARACTER_IDS = ("female", "male")
