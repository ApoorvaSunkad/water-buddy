"""Tests for persistence and the quiet-hours rule.

Every test redirects config paths into pytest's ``tmp_path`` fixture, so no test
can ever touch your real %APPDATA%\\WaterBuddy folder. Tests that write to real
user data are tests you stop trusting and then stop running.
"""

from __future__ import annotations

import datetime as dt
import json

import pytest

from water_buddy import config
from water_buddy.settings import Settings
from water_buddy.stats import Stats


@pytest.fixture(autouse=True)
def isolated_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(config, "STATS_FILE", tmp_path / "stats.json")
    return tmp_path


# ---------------------------------------------------------------------------
# Settings persistence
# ---------------------------------------------------------------------------

def test_defaults_when_no_file_exists():
    settings = Settings.load()
    assert settings.interval_minutes == 60
    assert settings.character == "female"


def test_round_trip():
    original = Settings(interval_minutes=45, character="male", display_seconds=3)
    original.save()
    assert Settings.load().interval_minutes == 45
    assert Settings.load().character == "male"
    assert Settings.load().display_seconds == 3


def test_unknown_keys_are_ignored(isolated_paths):
    """A file written by a future version must not crash an older app."""
    config.SETTINGS_FILE.write_text(
        json.dumps({"interval_minutes": 20, "future_feature": "hello"}),
        encoding="utf-8",
    )
    assert Settings.load().interval_minutes == 20


def test_missing_keys_fall_back_to_defaults(isolated_paths):
    """A file written by an older version must not break a newer app."""
    config.SETTINGS_FILE.write_text(
        json.dumps({"interval_minutes": 20}), encoding="utf-8"
    )
    settings = Settings.load()
    assert settings.interval_minutes == 20
    assert settings.daily_goal_glasses == 8  # the default


def test_corrupt_file_falls_back_to_defaults(isolated_paths):
    config.SETTINGS_FILE.write_text("{ not json at all", encoding="utf-8")
    assert Settings.load().interval_minutes == 60


def test_out_of_range_values_are_clamped(isolated_paths):
    config.SETTINGS_FILE.write_text(
        json.dumps({"interval_minutes": 999999, "display_seconds": -5,
                    "character": "dragon"}),
        encoding="utf-8",
    )
    settings = Settings.load()
    assert settings.interval_minutes == config.MAX_INTERVAL_MINUTES
    assert settings.display_seconds == config.MIN_DISPLAY_SECONDS
    assert settings.character == "female"


def test_entrance_style_round_trips():
    Settings(entrance_style="walk").save()
    assert Settings.load().entrance_style == "walk"


def test_unknown_entrance_style_falls_back_to_glide(isolated_paths):
    config.SETTINGS_FILE.write_text(
        json.dumps({"entrance_style": "moonwalk"}), encoding="utf-8"
    )
    assert Settings.load().entrance_style == "glide"


def test_entrance_style_defaults_to_glide():
    """Glide is the safe default: it suits a standing pose, which is what
    single-image character renders almost always are."""
    assert Settings().entrance_style == "glide"


def test_save_is_atomic(isolated_paths):
    """No stray temp file should survive a successful save."""
    Settings(interval_minutes=33).save()
    leftovers = list(isolated_paths.glob("*.tmp"))
    assert leftovers == []
    assert Settings.load().interval_minutes == 33


# ---------------------------------------------------------------------------
# Quiet hours
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("hour,expected", [
    (22, True), (23, True), (0, True), (3, True), (7, True),
    (8, False), (12, False), (21, False),
])
def test_quiet_hours_across_midnight(hour, expected):
    settings = Settings(quiet_hours_enabled=True,
                        quiet_start="22:00", quiet_end="08:00")
    assert settings.is_quiet_now(dt.time(hour, 0)) is expected


@pytest.mark.parametrize("hour,expected", [
    (12, False), (13, True), (14, False), (15, False),
])
def test_quiet_hours_within_one_day(hour, expected):
    settings = Settings(quiet_hours_enabled=True,
                        quiet_start="13:00", quiet_end="14:00")
    assert settings.is_quiet_now(dt.time(hour, 0)) is expected


def test_quiet_hours_disabled_is_never_quiet():
    settings = Settings(quiet_hours_enabled=False,
                        quiet_start="00:00", quiet_end="23:59")
    assert settings.is_quiet_now(dt.time(3, 0)) is False


def test_zero_width_quiet_window_is_never_quiet():
    settings = Settings(quiet_hours_enabled=True,
                        quiet_start="09:00", quiet_end="09:00")
    assert settings.is_quiet_now(dt.time(9, 0)) is False


def test_malformed_quiet_time_falls_back():
    settings = Settings(quiet_hours_enabled=True, quiet_start="not a time")
    assert settings.quiet_start_time == dt.time(22, 0)


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

def test_logging_a_glass_increments_today():
    stats = Stats()
    assert stats.today_count() == 0
    assert stats.log_glass() == 1
    assert stats.log_glass() == 2


def test_undo_never_goes_negative():
    stats = Stats()
    stats.undo_glass()
    assert stats.today_count() == 0


def test_stats_persist():
    stats = Stats()
    stats.log_glass()
    stats.log_glass()
    assert Stats.load().today_count() == 2


def test_streak_counts_consecutive_goal_days():
    today = dt.date(2026, 7, 19)
    stats = Stats({
        (today - dt.timedelta(days=1)).isoformat(): 8,
        (today - dt.timedelta(days=2)).isoformat(): 9,
        (today - dt.timedelta(days=3)).isoformat(): 8,
    })
    assert stats.streak(goal=8, today=today) == 3


def test_streak_ignores_today_until_goal_is_met():
    """A fresh morning must not read as a broken streak."""
    today = dt.date(2026, 7, 19)
    stats = Stats({
        today.isoformat(): 1,  # only one glass so far today
        (today - dt.timedelta(days=1)).isoformat(): 8,
        (today - dt.timedelta(days=2)).isoformat(): 8,
    })
    assert stats.streak(goal=8, today=today) == 2


def test_streak_includes_today_once_goal_is_met():
    today = dt.date(2026, 7, 19)
    stats = Stats({
        today.isoformat(): 8,
        (today - dt.timedelta(days=1)).isoformat(): 8,
    })
    assert stats.streak(goal=8, today=today) == 2


def test_streak_breaks_on_a_missed_day():
    today = dt.date(2026, 7, 19)
    stats = Stats({
        (today - dt.timedelta(days=1)).isoformat(): 8,
        # day 2 missed entirely
        (today - dt.timedelta(days=3)).isoformat(): 8,
    })
    assert stats.streak(goal=8, today=today) == 1


def test_corrupt_stats_file_starts_fresh(isolated_paths):
    config.STATS_FILE.write_text("garbage", encoding="utf-8")
    assert Stats.load().today_count() == 0


def test_malformed_entries_are_dropped(isolated_paths):
    config.STATS_FILE.write_text(
        json.dumps({"days": {"2026-07-19": 5, "not-a-date": 3,
                             "2026-07-18": "four", "2026-07-17": -2}}),
        encoding="utf-8",
    )
    stats = Stats.load()
    assert stats.count_for(dt.date(2026, 7, 19)) == 5
    assert stats.count_for(dt.date(2026, 7, 18)) == 0
    assert stats.count_for(dt.date(2026, 7, 17)) == 0
