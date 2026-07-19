"""Tests for the reminder timing logic.

The trick that makes these tests possible: we never wait. Instead of letting a
real QTimer fire and sleeping through an hour, we fake ``time.time`` and call
``_on_tick`` by hand. A three-hour laptop sleep becomes one line and runs in
microseconds.

That is only possible because the scheduler measures time by asking the clock
rather than by trusting a timer duration -- the same design decision that makes
it correct across sleep also makes it testable. Testable and correct usually
turn out to be the same property wearing different hats.
"""

from __future__ import annotations

import pytest

from water_buddy import config
from water_buddy.scheduler import ReminderScheduler


class FakeClock:
    """A stand-in for time.time() that only moves when we tell it to."""

    def __init__(self, start: float = 1_000_000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    fake = FakeClock()
    monkeypatch.setattr("water_buddy.scheduler.time.time", fake)
    return fake


def make_scheduler(interval=3600, suppress=None):
    """A scheduler with its due signal captured into a list."""
    sched = ReminderScheduler(interval, should_suppress=suppress)
    fired: list[int] = []
    sched.due.connect(lambda: fired.append(1))
    return sched, fired


def tick_for(sched, clock, seconds: int, step: int = 1) -> None:
    """Simulate ``seconds`` of wall time in ``step``-second ticks."""
    for _ in range(seconds // step):
        clock.advance(step)
        sched._on_tick()


# ---------------------------------------------------------------------------
# Basic timing
# ---------------------------------------------------------------------------

def test_does_not_fire_before_interval_elapses(clock):
    sched, fired = make_scheduler(interval=60)
    sched.reset()
    tick_for(sched, clock, 59)
    assert fired == []


def test_fires_once_when_interval_elapses(clock):
    sched, fired = make_scheduler(interval=60)
    sched.reset()
    tick_for(sched, clock, 60)
    assert len(fired) == 1


def test_fires_repeatedly_on_schedule(clock):
    sched, fired = make_scheduler(interval=60)
    sched.reset()
    tick_for(sched, clock, 180)
    assert len(fired) == 3


def test_seconds_remaining_counts_down(clock):
    sched, _ = make_scheduler(interval=60)
    sched.reset()
    assert sched.seconds_remaining == 60
    tick_for(sched, clock, 20)
    assert sched.seconds_remaining == 40


# ---------------------------------------------------------------------------
# The sleep problem -- the reason this module exists
# ---------------------------------------------------------------------------

def test_long_sleep_does_not_fire_a_burst(clock):
    """Three hours of laptop sleep must not produce three reminders."""
    sched, fired = make_scheduler(interval=3600)
    sched.reset()

    # One tick, then the machine suspends for three hours and ticks again.
    clock.advance(1)
    sched._on_tick()
    clock.advance(3 * 3600)
    sched._on_tick()

    assert fired == [], "should not fire at all immediately after waking"


def test_sleep_restarts_the_countdown(clock):
    sched, _ = make_scheduler(interval=3600)
    sched.reset()
    tick_for(sched, clock, 30)          # 3570 remaining
    clock.advance(3 * 3600)             # machine sleeps
    sched._on_tick()
    assert sched.seconds_remaining == 3600, "countdown should start over"


def test_reminder_still_fires_after_a_sleep_recovery(clock):
    """Waking must not leave the scheduler permanently dead."""
    sched, fired = make_scheduler(interval=60)
    sched.reset()
    clock.advance(config.SLEEP_GAP_SECONDS + 10)
    sched._on_tick()                    # detected as sleep, no fire
    assert fired == []
    tick_for(sched, clock, 60)          # a normal interval afterwards
    assert len(fired) == 1


def test_short_gap_is_not_treated_as_sleep(clock):
    """A brief stall (GC pause, heavy load) is normal, not a suspend."""
    sched, fired = make_scheduler(interval=60)
    sched.reset()
    clock.advance(65)                   # under SLEEP_GAP_SECONDS
    sched._on_tick()
    assert len(fired) == 1, "an ordinary overrun should still fire"


def test_clock_moving_backwards_is_clamped(clock):
    """A backwards clock jump must not strand the next reminder forever."""
    sched, _ = make_scheduler(interval=60)
    sched.reset()
    clock.advance(-3600)                # e.g. an NTP correction
    sched._on_tick()
    assert sched.seconds_remaining <= 60


# ---------------------------------------------------------------------------
# Suppression
# ---------------------------------------------------------------------------

def test_suppressed_reminder_does_not_fire(clock):
    sched, fired = make_scheduler(interval=60, suppress=lambda: (True, "quiet"))
    sched.reset()
    tick_for(sched, clock, 60)
    assert fired == []


def test_suppression_still_advances_the_schedule(clock):
    """A suppressed reminder must not re-fire on every subsequent tick."""
    calls: list[int] = []

    def suppress():
        calls.append(1)
        return True, "quiet"

    sched, fired = make_scheduler(interval=60, suppress=suppress)
    sched.reset()
    tick_for(sched, clock, 120)
    assert fired == []
    # Two intervals elapsed, so suppression should have been consulted twice --
    # not 60+ times, which is what a scheduler that failed to reschedule after
    # suppressing would do.
    assert len(calls) == 2


# ---------------------------------------------------------------------------
# Pause and snooze
# ---------------------------------------------------------------------------

def test_paused_scheduler_does_not_fire(clock):
    sched, fired = make_scheduler(interval=60)
    sched.reset()
    sched.set_paused(True)
    tick_for(sched, clock, 300)
    assert fired == []


def test_resuming_restarts_the_countdown(clock):
    sched, fired = make_scheduler(interval=60)
    sched.reset()
    sched.set_paused(True)
    tick_for(sched, clock, 300)
    sched.set_paused(False)
    assert sched.seconds_remaining == 60
    tick_for(sched, clock, 60)
    assert len(fired) == 1


def test_snooze_delays_the_next_reminder(clock):
    sched, fired = make_scheduler(interval=60)
    sched.reset()
    sched.snooze(5)
    tick_for(sched, clock, 60)
    assert fired == [], "snooze should push the reminder past the old due time"
    tick_for(sched, clock, 240)
    assert len(fired) == 1


def test_set_interval_restarts_countdown(clock):
    """Changing the interval applies immediately -- the bug the app had."""
    sched, fired = make_scheduler(interval=3600)
    sched.reset()
    tick_for(sched, clock, 100)
    sched.set_interval(600)
    assert sched.seconds_remaining == 600
    tick_for(sched, clock, 600)
    assert len(fired) == 1


def test_interval_has_a_floor(clock):
    """A one-second interval would be a denial-of-service on yourself."""
    sched, _ = make_scheduler(interval=1)
    assert sched.seconds_remaining >= 60
