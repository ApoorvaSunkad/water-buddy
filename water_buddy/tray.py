"""System tray icon and menu.

The tray is the app's only permanent presence. There is no main window in the
usual sense -- settings are opened on demand and closed again -- so the tray
icon is what proves the app is alive and gives you a way back in.

The icon is drawn in code rather than loaded from a .ico file. For a shape this
simple that avoids shipping an asset, and it means the icon can render a live
progress ring showing how far through the current interval you are.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from . import config

log = logging.getLogger(__name__)

ICON_PX = 64


def _drop_icon(fill_fraction: float = 1.0, paused: bool = False) -> QIcon:
    """Draw a water droplet filled to ``fill_fraction`` (0.0 - 1.0).

    The droplet empties as the interval elapses, so a glance at the tray tells
    you roughly how long you have left without opening anything.
    """
    pixmap = QPixmap(ICON_PX, ICON_PX)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    # Droplet outline: a circle with a point on top.
    drop = QPainterPath()
    cx, top, bottom = ICON_PX / 2, ICON_PX * 0.08, ICON_PX * 0.94
    radius = ICON_PX * 0.32
    centre_y = bottom - radius
    drop.moveTo(cx, top)
    drop.cubicTo(cx + radius * 1.15, centre_y - radius * 0.55,
                 cx + radius, centre_y + radius * 0.35, cx, bottom)
    drop.cubicTo(cx - radius, centre_y + radius * 0.35,
                 cx - radius * 1.15, centre_y - radius * 0.55, cx, top)

    base = QColor("#9aa7b4") if paused else QColor("#2f8fd8")

    # Empty portion, drawn faint.
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(base.red(), base.green(), base.blue(), 55))
    painter.drawPath(drop)

    # Filled portion, clipped to a rising water level.
    fraction = min(max(fill_fraction, 0.0), 1.0)
    painter.setClipPath(drop)
    level = bottom - (bottom - top) * fraction
    painter.setBrush(base)
    painter.drawRect(QRectF(0, level, ICON_PX, ICON_PX - level))
    painter.setClipping(False)

    painter.setPen(QPen(base, 3))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(drop)
    painter.end()

    return QIcon(pixmap)


class TrayIcon(QSystemTrayIcon):
    open_settings = Signal()
    log_glass = Signal()
    remind_now = Signal()
    toggle_pause = Signal(bool)
    quit_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setIcon(_drop_icon(1.0))
        self.setToolTip(config.APP_DISPLAY_NAME)

        menu = QMenu()
        self._status_action = QAction("Next glass in —", menu)
        self._status_action.setEnabled(False)  # a label, not a button
        menu.addAction(self._status_action)
        menu.addSeparator()

        log_action = QAction("Log a glass", menu)
        log_action.triggered.connect(self.log_glass.emit)
        menu.addAction(log_action)

        remind_action = QAction("Remind me now", menu)
        remind_action.triggered.connect(self.remind_now.emit)
        menu.addAction(remind_action)

        self._pause_action = QAction("Pause reminders", menu)
        self._pause_action.setCheckable(True)
        self._pause_action.toggled.connect(self._on_pause_toggled)
        menu.addAction(self._pause_action)

        menu.addSeparator()
        settings_action = QAction("Settings…", menu)
        settings_action.triggered.connect(self.open_settings.emit)
        menu.addAction(settings_action)

        quit_action = QAction("Quit", menu)
        quit_action.triggered.connect(self.quit_requested.emit)
        menu.addAction(quit_action)

        # Held as an attribute: a QMenu that goes out of scope is collected and
        # the tray icon ends up with no menu at all.
        self._menu = menu
        self.setContextMenu(menu)

        # Left-clicking the tray icon opens settings, which is what every
        # Windows user expects.
        self.activated.connect(self._on_activated)

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.open_settings.emit()

    def _on_pause_toggled(self, checked: bool) -> None:
        self._pause_action.setText("Resume reminders" if checked
                                   else "Pause reminders")
        self.toggle_pause.emit(checked)

    def set_paused(self, paused: bool) -> None:
        """Reflect pause state that was changed elsewhere (e.g. the window)."""
        if self._pause_action.isChecked() != paused:
            self._pause_action.blockSignals(True)
            self._pause_action.setChecked(paused)
            self._pause_action.setText("Resume reminders" if paused
                                       else "Pause reminders")
            self._pause_action.blockSignals(False)

    def update_countdown(self, remaining: int, total: int, paused: bool,
                         today: int, goal: int) -> None:
        if paused:
            self._status_action.setText("Reminders paused")
            self.setToolTip(f"{config.APP_DISPLAY_NAME} — paused")
            self.setIcon(_drop_icon(1.0, paused=True))
            return

        minutes, seconds = divmod(max(0, remaining), 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            pretty = f"{hours}h {minutes}m"
        elif minutes:
            pretty = f"{minutes}m {seconds}s"
        else:
            pretty = f"{seconds}s"

        self._status_action.setText(f"Next glass in {pretty}")
        self.setToolTip(
            f"{config.APP_DISPLAY_NAME}\nNext glass in {pretty}\n"
            f"{today} of {goal} glasses today"
        )
        # Droplet drains as the interval elapses.
        self.setIcon(_drop_icon(remaining / max(1, total)))
