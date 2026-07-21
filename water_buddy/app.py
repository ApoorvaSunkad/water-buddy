"""Application wiring.

Every other module in this package is deliberately ignorant of the others:
the scheduler doesn't know what a character is, the overlay doesn't know what a
glass of water is, the stats file doesn't know a UI exists. This module is the
one place that knows about all of them, and its whole job is to connect signals
to slots.

That shape -- dumb, independent parts plus one place that composes them -- is
why you can change how reminders are *timed* without touching how they *look*,
and it's the single most useful habit to take away from this project.
"""

from __future__ import annotations

import datetime as dt
import logging
import logging.handlers
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from . import characters, config, platform_win
from .overlay import BuddyOverlay
from .scheduler import ReminderScheduler
from .settings import Settings
from .settings_window import SettingsWindow
from .single_instance import SingleInstance
from .stats import Stats
from .tray import TrayIcon

log = logging.getLogger(__name__)

SNOOZE_MINUTES = 5

# How many intervals in a row Windows may claim you're "busy" before we show
# the reminder anyway. Some environments -- Remote Desktop sessions and VMs in
# particular -- report QUNS_BUSY permanently, which would otherwise mean the
# app never reminds you at all and never tells you why. Failing loud beats
# failing silent for something whose entire purpose is to interrupt you.
MAX_BUSY_SUPPRESSIONS = 3

MESSAGES = [
    "Time to drink water!",
    "Hydration break!",
    "Your body wants water 💧",
    "Quick sip?",
    "Water o'clock!",
]


def _setup_logging() -> None:
    """Log to a rotating file, plus the console when one exists.

    A rotating handler caps the log at a megabyte so a long-running tray app
    can't quietly fill a disk. When packaged with pythonw there is no console,
    hence the guard on ``sys.stderr``.
    """
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [
        logging.handlers.RotatingFileHandler(
            config.LOG_FILE, maxBytes=1_000_000, backupCount=2, encoding="utf-8"
        )
    ]
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler(sys.stderr))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)-22s %(message)s",
        handlers=handlers,
    )


class WaterBuddyApp:
    def __init__(self, argv: list[str]):
        _setup_logging()
        log.info("Starting %s", config.APP_DISPLAY_NAME)

        platform_win.set_app_user_model_id()

        self.qt = QApplication(argv)
        self.qt.setApplicationName(config.APP_DISPLAY_NAME)
        # Critical for a tray app: closing the settings window must not quit
        # the process. Without this, Qt exits when the last window closes and
        # the app would vanish the first time you dismissed settings.
        self.qt.setQuitOnLastWindowClosed(False)

        # Must come after QApplication (it needs an event loop) but before we
        # touch any data files, so a second copy exits without ever reading or
        # writing the settings and stats the first copy owns.
        self.guard = SingleInstance(config.APP_NAME)
        if self.guard.already_running:
            return
        self.guard.activated.connect(self.show_settings)

        self.settings = Settings.load()
        self.stats = Stats.load()

        self.overlay = BuddyOverlay()
        self.scheduler = ReminderScheduler(
            self.settings.interval_seconds, should_suppress=self._should_suppress
        )
        self.tray = TrayIcon()
        self.window = SettingsWindow(self.settings)

        self._message_index = 0
        # The interval the scheduler is *currently running with*. We cannot
        # detect changes by comparing against self.settings, because the
        # settings window mutates that same object in place before telling us
        # about it -- by the time we look, the "old" value is already gone.
        # Keeping our own copy of what was actually applied is the fix.
        self._applied_interval = self.settings.interval_seconds
        # Guards the pause handler against the app -> button -> app loop.
        self._syncing_pause = False
        # If Windows claims we're busy for interval after interval, the app
        # would go permanently silent without ever saying so. Counted here.
        self._consecutive_suppressions = 0

        self._connect()

        if not QSystemTrayIcon.isSystemTrayAvailable():
            log.warning("No system tray available on this desktop")

        self.tray.show()
        self.scheduler.start()
        self._refresh_progress()

        # A midnight rollover check, so "today's count" resets without needing
        # a restart. Cheap enough to run every minute.
        self._day = dt.date.today()
        self._rollover = QTimer()
        self._rollover.setInterval(60_000)
        self._rollover.timeout.connect(self._check_day_rollover)
        self._rollover.start()

    # ------------------------------------------------------------------
    # Wiring
    # ------------------------------------------------------------------
    def _connect(self) -> None:
        self.scheduler.due.connect(self.show_buddy)
        self.scheduler.tick.connect(self._on_tick)
        self.scheduler.resumed.connect(self._on_resumed)

        self.overlay.drank.connect(self._on_glass_logged)
        self.overlay.snoozed.connect(lambda: self.scheduler.snooze(SNOOZE_MINUTES))

        self.tray.open_settings.connect(self.show_settings)
        self.tray.log_glass.connect(self._on_glass_logged)
        self.tray.remind_now.connect(self.show_buddy)
        self.tray.toggle_pause.connect(self._on_pause)
        self.tray.quit_requested.connect(self.quit)

        self.window.settings_changed.connect(self._on_settings_changed)
        self.window.preview_requested.connect(self.show_buddy)
        self.window.glass_logged.connect(self._on_glass_logged)
        self.window.pause_toggled.connect(self._on_pause)

    # ------------------------------------------------------------------
    # Reminder flow
    # ------------------------------------------------------------------
    def _should_suppress(self) -> tuple[bool, str]:
        """Decide whether now is a bad moment. Called by the scheduler.

        Suppression reasons fall into two kinds, and the difference matters:

        * DELIBERATE -- quiet hours, goal already met, reminders switched off.
          The user asked for this. It can last all night and that's correct.
        * ADVISORY -- Windows reporting that a fullscreen app is running. This
          is a guess on the OS's part, and in some environments it's simply
          wrong forever. We let it win a few times, then overrule it, because
          a reminder app that never reminds is broken in the worst way: the
          way that looks like it's working.
        """
        if not self.settings.enabled:
            return True, "reminders disabled"

        if self.settings.is_quiet_now(dt.datetime.now().time()):
            return True, "quiet hours"

        # Already met the daily goal -- stop nagging. Checked before the busy
        # test so that hitting your goal doesn't leave the override counter
        # climbing in the background.
        if self.stats.today_count() >= self.settings.daily_goal_glasses:
            return True, "daily goal already met"

        if self.settings.skip_when_fullscreen:
            busy, reason = platform_win.user_is_busy()
            if busy:
                self._consecutive_suppressions += 1
                if self._consecutive_suppressions > MAX_BUSY_SUPPRESSIONS:
                    log.warning(
                        "Windows has reported '%s' for %d intervals running; "
                        "showing the reminder anyway. If this keeps happening, "
                        "turn off 'stay hidden during fullscreen apps' in "
                        "settings -- some Remote Desktop and VM sessions report "
                        "this permanently.",
                        reason, self._consecutive_suppressions,
                    )
                    self._consecutive_suppressions = 0
                    return False, ""
                return True, reason

        self._consecutive_suppressions = 0
        return False, ""

    def show_buddy(self) -> None:
        character = characters.resolve_character(self.settings.character)
        message = MESSAGES[self._message_index % len(MESSAGES)]
        self._message_index += 1
        self.overlay.show_reminder(
            character_id=character,
            message=message,
            hold_seconds=self.settings.display_seconds,
            monitor_index=self.settings.monitor_index,
            entrance=self.settings.entrance_style,
        )

    def _on_glass_logged(self) -> None:
        count = self.stats.log_glass()
        log.info("Logged a glass (%s today)", count)
        self._refresh_progress()

    def _on_resumed(self, gap_seconds: float) -> None:
        log.info("Machine was away for %.0f minutes; countdown restarted",
                 gap_seconds / 60)

    def _on_pause(self, paused: bool) -> None:
        # Both the tray menu and the settings window can toggle pause, and each
        # one has to be updated when the *other* changes it. Updating a widget
        # makes it emit its own signal, which comes straight back here -- so we
        # flag that a sync is in progress and ignore the echo.
        if self._syncing_pause:
            return
        self._syncing_pause = True
        try:
            self.scheduler.set_paused(paused)
            self.tray.set_paused(paused)
            if self.window.pause_button.isChecked() != paused:
                self.window.pause_button.setChecked(paused)
        finally:
            self._syncing_pause = False

    # ------------------------------------------------------------------
    # Periodic UI refresh
    # ------------------------------------------------------------------
    def _on_tick(self, remaining: int) -> None:
        total = self.settings.interval_seconds
        paused = self.scheduler.paused
        today = self.stats.today_count()
        goal = self.settings.daily_goal_glasses

        self.tray.update_countdown(remaining, total, paused, today, goal)
        if self.window.isVisible():
            self.window.update_countdown(remaining, total, paused)

    def _refresh_progress(self) -> None:
        today = self.stats.today_count()
        goal = self.settings.daily_goal_glasses
        self.window.update_progress(today, goal, self.stats.streak(goal))

    def _check_day_rollover(self) -> None:
        today = dt.date.today()
        if today != self._day:
            log.info("Day rolled over to %s", today)
            self._day = today
            self._refresh_progress()

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------
    def _on_settings_changed(self, settings: Settings) -> None:
        self.settings = settings
        settings.save()

        if settings.interval_seconds != self._applied_interval:
            self._applied_interval = settings.interval_seconds
            self.scheduler.set_interval(settings.interval_seconds)

        # Character choice may have changed which artwork we need.
        characters.clear_cache()
        self._refresh_progress()

    def show_settings(self) -> None:
        self.window.load_from(self.settings)
        self._refresh_progress()
        self.window.update_countdown(
            self.scheduler.seconds_remaining,
            self.settings.interval_seconds,
            self.scheduler.paused,
        )
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def quit(self) -> None:
        log.info("Shutting down")
        self.settings.save()
        self.stats.save()
        self.scheduler.stop()
        self.tray.hide()
        self.qt.quit()

    def run(self) -> int:
        return self.qt.exec()


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv)
    app = WaterBuddyApp(argv)
    if app.guard.already_running:
        # The running copy has been told to show its window. Exiting with 0
        # rather than an error code: from the user's point of view launching
        # the app succeeded -- their window appeared.
        return 0
    return app.run()
