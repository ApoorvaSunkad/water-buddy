"""The settings window: countdown dial, interval picker, character choice.

Layout is deliberately one column of labelled sections rather than tabs. With
this few settings, tabs would hide things for no benefit.

The one non-obvious piece is :class:`CountdownDial`, the "clock". It exists
because a reminder app that gives you no sense of when the next reminder is
coming feels like it's ignoring you. Seeing "23:14 until next glass" tick down
makes the app feel alive and, more practically, tells you instantly whether it
is actually running.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QRectF, Qt, QTime, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from . import characters, config, platform_win
from .settings import Settings

log = logging.getLogger(__name__)

INTERVAL_PRESETS = [
    ("30 min", 30),
    ("45 min", 45),
    ("1 hour", 60),
    ("90 min", 90),
    ("2 hours", 120),
]


class CountdownDial(QWidget):
    """A circular progress ring showing time until the next reminder."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(168, 168)
        self._remaining = 0
        self._total = 3600
        self._paused = False

    def set_state(self, remaining: int, total: int, paused: bool) -> None:
        self._remaining = max(0, remaining)
        self._total = max(1, total)
        self._paused = paused
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        inset = 12
        box = QRectF(inset, inset, self.width() - inset * 2, self.height() - inset * 2)

        # Track
        pen = QPen(QColor("#e3eaf1"), 12, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawArc(box, 0, 360 * 16)

        # Progress: fraction of the interval already elapsed.
        elapsed_fraction = 1.0 - (self._remaining / self._total)
        span = int(-360 * 16 * elapsed_fraction)  # negative = clockwise
        pen.setColor(QColor("#9aa7b4") if self._paused else QColor("#2f8fd8"))
        painter.setPen(pen)
        painter.drawArc(box, 90 * 16, span)  # start at 12 o'clock

        # Centre text
        minutes, seconds = divmod(self._remaining, 60)
        hours, minutes = divmod(minutes, 60)
        text = (f"{hours}:{minutes:02d}:{seconds:02d}" if hours
                else f"{minutes}:{seconds:02d}")

        font = QFont()
        font.setPointSize(20)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor("#9aa7b4") if self._paused else QColor("#1b2b3a"))
        painter.drawText(box, Qt.AlignmentFlag.AlignCenter,
                         "paused" if self._paused else text)

        font.setPointSize(8)
        font.setBold(False)
        painter.setFont(font)
        painter.setPen(QColor("#7d8b99"))
        painter.drawText(QRectF(0, self.height() - 22, self.width(), 18),
                         Qt.AlignmentFlag.AlignCenter,
                         "" if self._paused else "until next glass")
        painter.end()


def _section(title: str) -> QLabel:
    label = QLabel(title.upper())
    font = QFont()
    font.setPointSize(8)
    font.setBold(True)
    label.setFont(font)
    label.setStyleSheet("color:#7d8b99; letter-spacing:1px; margin-top:6px;")
    return label


def _divider() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setStyleSheet("color:#e3eaf1;")
    return line


class SettingsWindow(QWidget):
    """Edits a :class:`Settings` object and reports changes upward."""

    #: Emitted with the updated settings whenever the user changes anything.
    settings_changed = Signal(Settings)
    #: User pressed "Preview" -- show the buddy right now.
    preview_requested = Signal()
    #: User logged a glass from this window.
    glass_logged = Signal()
    #: Pause/resume toggled.
    pause_toggled = Signal(bool)

    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self._settings = settings
        self._loading = False  # guards against feedback loops while populating

        self.setWindowTitle(f"{config.APP_DISPLAY_NAME} — Settings")
        self.setMinimumWidth(400)
        self.setStyleSheet("QWidget { background:#ffffff; color:#1b2b3a; }")

        # All the controls live inside a scroll area rather than directly in
        # the window. On a 1280x720 display the full stack of settings is
        # taller than the 672px work area, so without this the bottom controls
        # would be unreachable -- the window would extend past the screen edge
        # with no way to scroll to what's below.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        content = QWidget()
        root = QVBoxLayout(content)
        root.setContentsMargins(22, 18, 22, 18)
        root.setSpacing(10)

        # --- Countdown + today's progress ---
        header = QHBoxLayout()
        self.dial = CountdownDial()
        header.addWidget(self.dial)

        progress_box = QVBoxLayout()
        progress_box.addStretch()
        self.today_label = QLabel("0 of 8 glasses today")
        today_font = QFont()
        today_font.setPointSize(13)
        today_font.setBold(True)
        self.today_label.setFont(today_font)
        progress_box.addWidget(self.today_label)

        self.streak_label = QLabel("No streak yet")
        self.streak_label.setStyleSheet("color:#7d8b99;")
        progress_box.addWidget(self.streak_label)

        buttons = QHBoxLayout()
        self.log_button = QPushButton("+ Log a glass")
        self.log_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.log_button.setStyleSheet(
            "QPushButton { background:#2f8fd8; color:white; border:none;"
            " border-radius:15px; padding:7px 16px; font-weight:600; }"
        )
        self.log_button.clicked.connect(self.glass_logged.emit)
        buttons.addWidget(self.log_button)

        self.pause_button = QPushButton("Pause")
        self.pause_button.setCheckable(True)
        self.pause_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.pause_button.setStyleSheet(
            "QPushButton { background:#eef3f7; color:#25415c; border:none;"
            " border-radius:15px; padding:7px 16px; font-weight:600; }"
            "QPushButton:checked { background:#ffe0b2; color:#8a5300; }"
        )
        self.pause_button.toggled.connect(self._on_pause_toggled)
        buttons.addWidget(self.pause_button)
        buttons.addStretch()
        progress_box.addLayout(buttons)
        progress_box.addStretch()

        header.addLayout(progress_box)
        header.addStretch()
        root.addLayout(header)
        root.addWidget(_divider())

        # --- Interval ---
        root.addWidget(_section("Remind me every"))
        presets = QHBoxLayout()
        presets.setSpacing(6)
        self._preset_group = QButtonGroup(self)
        self._preset_group.setExclusive(True)
        for label, minutes in INTERVAL_PRESETS:
            button = QPushButton(label)
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setProperty("minutes", minutes)
            button.setStyleSheet(
                "QPushButton { background:#eef3f7; border:none; border-radius:14px;"
                " padding:6px 12px; font-size:11px; }"
                "QPushButton:checked { background:#2f8fd8; color:white; font-weight:600; }"
            )
            self._preset_group.addButton(button)
            presets.addWidget(button)
        presets.addStretch()
        self._preset_group.buttonClicked.connect(self._on_preset_clicked)
        root.addLayout(presets)

        custom = QHBoxLayout()
        custom.addWidget(QLabel("Custom:"))
        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(config.MIN_INTERVAL_MINUTES,
                                    config.MAX_INTERVAL_MINUTES)
        self.interval_spin.setSuffix(" min")
        self.interval_spin.valueChanged.connect(self._on_interval_changed)
        custom.addWidget(self.interval_spin)
        custom.addStretch()
        root.addLayout(custom)
        root.addWidget(_divider())

        # --- Character ---
        root.addWidget(_section("Your buddy"))
        char_row = QHBoxLayout()
        self.character_combo = QComboBox()
        self.character_combo.addItem("Female", "female")
        self.character_combo.addItem("Male", "male")
        self.character_combo.addItem("Surprise me", "random")
        self.character_combo.currentIndexChanged.connect(self._on_character_changed)
        char_row.addWidget(self.character_combo)

        self.preview_button = QPushButton("Preview")
        self.preview_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.preview_button.setStyleSheet(
            "QPushButton { background:#eef3f7; border:none; border-radius:14px;"
            " padding:6px 16px; font-weight:600; }"
        )
        self.preview_button.clicked.connect(self.preview_requested.emit)
        char_row.addWidget(self.preview_button)
        char_row.addStretch()
        root.addLayout(char_row)

        self.art_warning = QLabel()
        self.art_warning.setWordWrap(True)
        self.art_warning.setStyleSheet(
            "color:#8a5300; background:#fff5e6; border-radius:8px; padding:8px;"
        )
        self.art_warning.hide()
        root.addWidget(self.art_warning)

        hold = QHBoxLayout()
        hold.addWidget(QLabel("Stays on screen:"))
        self.hold_slider = QSlider(Qt.Orientation.Horizontal)
        self.hold_slider.setRange(config.MIN_DISPLAY_SECONDS,
                                  config.MAX_DISPLAY_SECONDS)
        self.hold_slider.valueChanged.connect(self._on_hold_changed)
        hold.addWidget(self.hold_slider)
        self.hold_value = QLabel("5s")
        self.hold_value.setFixedWidth(28)
        hold.addWidget(self.hold_value)
        root.addLayout(hold)

        monitor_row = QHBoxLayout()
        monitor_row.addWidget(QLabel("Show on:"))
        self.monitor_combo = QComboBox()
        self.monitor_combo.currentIndexChanged.connect(self._on_monitor_changed)
        monitor_row.addWidget(self.monitor_combo)
        monitor_row.addStretch()
        root.addLayout(monitor_row)
        root.addWidget(_divider())

        # --- Goal & politeness ---
        root.addWidget(_section("Preferences"))
        grid = QGridLayout()
        grid.setVerticalSpacing(8)

        grid.addWidget(QLabel("Daily goal:"), 0, 0)
        self.goal_spin = QSpinBox()
        self.goal_spin.setRange(1, 30)
        self.goal_spin.setSuffix(" glasses")
        self.goal_spin.valueChanged.connect(self._on_goal_changed)
        grid.addWidget(self.goal_spin, 0, 1)

        self.quiet_check = QCheckBox("Quiet hours")
        self.quiet_check.toggled.connect(self._on_quiet_toggled)
        grid.addWidget(self.quiet_check, 1, 0)

        quiet_row = QHBoxLayout()
        self.quiet_start = QTimeEdit()
        self.quiet_end = QTimeEdit()
        for edit in (self.quiet_start, self.quiet_end):
            edit.setDisplayFormat("HH:mm")
            edit.timeChanged.connect(self._on_quiet_time_changed)
        quiet_row.addWidget(self.quiet_start)
        quiet_row.addWidget(QLabel("to"))
        quiet_row.addWidget(self.quiet_end)
        quiet_row.addStretch()
        grid.addLayout(quiet_row, 1, 1)
        root.addLayout(grid)

        self.fullscreen_check = QCheckBox(
            "Stay hidden during fullscreen apps, games and presentations"
        )
        self.fullscreen_check.toggled.connect(self._on_fullscreen_toggled)
        root.addWidget(self.fullscreen_check)

        self.startup_check = QCheckBox("Start Water Buddy when Windows starts")
        self.startup_check.toggled.connect(self._on_startup_toggled)
        root.addWidget(self.startup_check)

        root.addStretch()

        scroll = QScrollArea()
        scroll.setWidget(content)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        outer.addWidget(scroll)

        self._populate_monitors()
        self.load_from(settings)
        self._size_to_screen()

    def _size_to_screen(self) -> None:
        """Open at a comfortable size that always fits the user's display."""
        from PySide6.QtGui import QGuiApplication

        screen = QGuiApplication.primaryScreen()
        if screen is None:
            self.resize(430, 640)
            return
        work = screen.availableGeometry()
        # Leave a margin so the window never sits flush against the taskbar.
        self.resize(min(440, work.width() - 40), min(660, work.height() - 60))

    # ------------------------------------------------------------------
    # Populating from settings
    # ------------------------------------------------------------------
    def load_from(self, settings: Settings) -> None:
        """Push values into the widgets without re-emitting change signals."""
        self._loading = True
        self._settings = settings

        self.interval_spin.setValue(settings.interval_minutes)
        self._sync_preset_buttons(settings.interval_minutes)

        index = self.character_combo.findData(settings.character)
        self.character_combo.setCurrentIndex(max(0, index))

        self.hold_slider.setValue(settings.display_seconds)
        self.hold_value.setText(f"{settings.display_seconds}s")
        self.goal_spin.setValue(settings.daily_goal_glasses)

        self.quiet_check.setChecked(settings.quiet_hours_enabled)
        self.quiet_start.setTime(QTime.fromString(settings.quiet_start, "HH:mm"))
        self.quiet_end.setTime(QTime.fromString(settings.quiet_end, "HH:mm"))
        self._set_quiet_enabled(settings.quiet_hours_enabled)

        self.fullscreen_check.setChecked(settings.skip_when_fullscreen)
        # Read the real registry state rather than trusting the settings file,
        # since the user may have removed the entry via Task Manager.
        self.startup_check.setChecked(platform_win.is_launch_on_startup_enabled())

        if self.monitor_combo.count():
            self.monitor_combo.setCurrentIndex(
                min(settings.monitor_index, self.monitor_combo.count() - 1)
            )

        self._refresh_art_warning()
        self._loading = False

    def _populate_monitors(self) -> None:
        from PySide6.QtGui import QGuiApplication

        self.monitor_combo.clear()
        for i, screen in enumerate(QGuiApplication.screens()):
            geo = screen.geometry()
            primary = " (primary)" if screen == QGuiApplication.primaryScreen() else ""
            self.monitor_combo.addItem(
                f"Screen {i + 1} — {geo.width()}×{geo.height()}{primary}", i
            )
        self.monitor_combo.setEnabled(self.monitor_combo.count() > 1)

    def _refresh_art_warning(self) -> None:
        missing = [c for c in characters.available_characters()
                   if characters.sprite_path(c) is None]
        if missing:
            folder = config.CHARACTERS_DIR
            self.art_warning.setText(
                f"Using placeholder art for: {', '.join(missing)}. "
                f"Drop a transparent PNG named drinking.png into "
                f"{folder}\\<name>\\ to use your own character."
            )
            self.art_warning.show()
        else:
            self.art_warning.hide()

    # ------------------------------------------------------------------
    # Live state from the app
    # ------------------------------------------------------------------
    def update_countdown(self, remaining: int, total: int, paused: bool) -> None:
        self.dial.set_state(remaining, total, paused)

    def update_progress(self, today: int, goal: int, streak: int) -> None:
        self.today_label.setText(f"{today} of {goal} glasses today")
        if streak <= 0:
            self.streak_label.setText("No streak yet — today counts!")
        else:
            day_word = "day" if streak == 1 else "days"
            self.streak_label.setText(f"🔥 {streak} {day_word} hitting your goal")

    # ------------------------------------------------------------------
    # Widget handlers
    # ------------------------------------------------------------------
    def _emit(self) -> None:
        if not self._loading:
            self.settings_changed.emit(self._settings)

    def _sync_preset_buttons(self, minutes: int) -> None:
        for button in self._preset_group.buttons():
            button.setChecked(button.property("minutes") == minutes)

    def _on_preset_clicked(self, button) -> None:
        self.interval_spin.setValue(int(button.property("minutes")))

    def _on_interval_changed(self, value: int) -> None:
        self._settings.interval_minutes = value
        self._sync_preset_buttons(value)
        self._emit()

    def _on_character_changed(self, _index: int) -> None:
        self._settings.character = self.character_combo.currentData()
        self._emit()

    def _on_hold_changed(self, value: int) -> None:
        self._settings.display_seconds = value
        self.hold_value.setText(f"{value}s")
        self._emit()

    def _on_monitor_changed(self, index: int) -> None:
        if index >= 0:
            self._settings.monitor_index = index
            self._emit()

    def _on_goal_changed(self, value: int) -> None:
        self._settings.daily_goal_glasses = value
        self._emit()

    def _set_quiet_enabled(self, enabled: bool) -> None:
        self.quiet_start.setEnabled(enabled)
        self.quiet_end.setEnabled(enabled)

    def _on_quiet_toggled(self, checked: bool) -> None:
        self._settings.quiet_hours_enabled = checked
        self._set_quiet_enabled(checked)
        self._emit()

    def _on_quiet_time_changed(self, _time: QTime) -> None:
        self._settings.quiet_start = self.quiet_start.time().toString("HH:mm")
        self._settings.quiet_end = self.quiet_end.time().toString("HH:mm")
        self._emit()

    def _on_fullscreen_toggled(self, checked: bool) -> None:
        self._settings.skip_when_fullscreen = checked
        self._emit()

    def _on_startup_toggled(self, checked: bool) -> None:
        if self._loading:
            return
        if platform_win.set_launch_on_startup(checked):
            self._settings.launch_on_startup = checked
            self._emit()
        else:
            # Registry write failed -- put the checkbox back so the UI never
            # claims a state the system doesn't actually have.
            self._loading = True
            self.startup_check.setChecked(not checked)
            self._loading = False

    def _on_pause_toggled(self, checked: bool) -> None:
        self.pause_button.setText("Resume" if checked else "Pause")
        if not self._loading:
            self.pause_toggled.emit(checked)
