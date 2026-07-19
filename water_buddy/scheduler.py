"""The reminder clock.

The naive version of this module is ``QTimer.singleShot(3600_000, remind)`` and
it is wrong in three separate ways. This version fixes all three:

1. LAPTOP SLEEP. A one-shot timer set for an hour from now does not advance
   while the machine is suspended. Close the lid at 2pm, open it at 5pm, and a
   naive timer still thinks 55 minutes remain. So instead of trusting a timer's
   duration we tick once a second and compare against the *wall clock*. Time
   passing is measured, never assumed.

2. THE BURST PROBLEM. Once you compare against the wall clock you get the
   opposite bug: wake from a 3-hour sleep and you're 3 intervals overdue. Fire
   naively and the poor user gets three buddies stacked on top of each other.
   We never queue missed reminders -- being overdue by any amount produces
   exactly one reminder.

3. WAKING SOMEONE WHO JUST SAT DOWN. Even one reminder fired the instant the
   lid opens is bad: the screen hasn't finished redrawing and you get a
   character sliding across a half-painted desktop. If we detect a gap larger
   than SLEEP_GAP_SECONDS between ticks, we treat it as "the machine was away"
   and restart the countdown cleanly rather than firing at all.

The scheduler deliberately knows nothing about quiet hours, fullscreen apps or
characters. It asks a caller-supplied ``should_suppress`` callback whether now
is a bad moment. That keeps the timing logic testable in isolation -- you can
drive it with a fake clock and a fake suppression rule and never open a window.
"""

from __future__ import annotations

import logging
import time

from PySide6.QtCore import QObject, QTimer, Signal

from . import config

log = logging.getLogger(__name__)


class ReminderScheduler(QObject):
    """Emits :attr:`due` every ``interval_seconds`` of real, awake time."""

    #: Time to drink. The app connects this to "show the buddy".
    due = Signal()
    #: Emitted every tick with seconds remaining, so the UI can show a countdown.
    tick = Signal(int)
    #: Emitted when we detect the machine slept and we silently restarted.
    resumed = Signal(float)  # seconds the machine was away

    def __init__(self, interval_seconds: int, should_suppress=None, parent=None):
        super().__init__(parent)
        self._interval = max(60, int(interval_seconds))
        self._should_suppress = should_suppress or (lambda: (False, ""))
        self._paused = False

        now = time.time()
        self._next_due = now + self._interval
        self._last_tick = now
        # The longest we are *legitimately* allowed to be waiting right now.
        # Normally one interval, but a snooze deliberately sets a longer wait.
        # The backwards-clock guard below compares against this rather than
        # against the interval -- otherwise a snooze looks exactly like a clock
        # jump and gets cancelled, which is the opposite of what the user asked
        # for.
        self._max_wait = self._interval

        self._timer = QTimer(self)
        self._timer.setInterval(config.TICK_MS)
        self._timer.timeout.connect(self._on_tick)

    # ------------------------------------------------------------------
    # Control
    # ------------------------------------------------------------------
    def start(self) -> None:
        self.reset()
        self._timer.start()
        log.info("Scheduler started, interval=%ss", self._interval)

    def stop(self) -> None:
        self._timer.stop()

    def set_interval(self, seconds: int) -> None:
        """Change the interval and restart the countdown from now.

        Restarting rather than preserving elapsed time is the intuitive
        behaviour: you just changed the setting, so the new interval should
        start now, not fire immediately because you'd already been waiting
        longer than the new (shorter) interval.
        """
        self._interval = max(60, int(seconds))
        self.reset()
        log.info("Interval changed to %ss", self._interval)

    def reset(self) -> None:
        """Restart the countdown from this moment."""
        now = time.time()
        self._next_due = now + self._interval
        self._last_tick = now
        self._max_wait = self._interval

    def snooze(self, minutes: int) -> None:
        wait = minutes * 60
        self._next_due = time.time() + wait
        # A snooze may be longer than the interval; allow it explicitly.
        self._max_wait = max(wait, self._interval)
        log.info("Snoozed for %s minutes", minutes)

    def set_paused(self, paused: bool) -> None:
        self._paused = paused
        if not paused:
            self.reset()
        log.info("Scheduler %s", "paused" if paused else "resumed")

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def seconds_remaining(self) -> int:
        return max(0, int(round(self._next_due - time.time())))

    # ------------------------------------------------------------------
    # The tick
    # ------------------------------------------------------------------
    def _on_tick(self) -> None:
        now = time.time()
        gap = now - self._last_tick
        self._last_tick = now

        # --- Case 1: the wall clock jumped. Machine slept, or someone changed
        # the system clock / a DST boundary passed. Either way the elapsed time
        # was not time the user spent sitting at their desk, so it should not
        # count toward the countdown.
        if gap > config.SLEEP_GAP_SECONDS:
            log.info("Detected a %.0fs clock jump; restarting countdown", gap)
            self.reset()
            self.resumed.emit(gap)
            return

        # A backwards jump (clock corrected, timezone change) would otherwise
        # strand next_due far in the future. Clamp it.
        if self._next_due - now > self._max_wait:
            log.info("Clock moved backwards; clamping next due time")
            self.reset()
            return

        if self._paused:
            return

        self.tick.emit(self.seconds_remaining)

        # --- Case 2: not due yet. Nothing to do.
        if now < self._next_due:
            return

        # --- Case 3: due. Reschedule FIRST, unconditionally.
        # Doing this before the suppression check and before emitting means
        # there is no path -- not a suppressed reminder, not an exception thrown
        # by a slot connected to `due` -- that can leave next_due in the past
        # and cause us to fire again on the very next tick.
        self._next_due = now + self._interval
        self._max_wait = self._interval  # any snooze allowance is now spent

        suppressed, reason = self._should_suppress()
        if suppressed:
            log.info("Reminder suppressed: %s", reason)
            return

        log.info("Reminder due")
        self.due.emit()
