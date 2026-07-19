"""The buddy overlay: a transparent window that walks in from the screen edge.

This is the module that makes the app feel like a character rather than a
notification. Three ideas do most of the work:

  * The WINDOW is invisible. ``WA_TranslucentBackground`` plus a frameless hint
    means Qt composites only the pixels we actually paint. Everything else is
    genuinely see-through -- not "filled with the desktop colour", actually
    transparent, including antialiased edges and soft shadows.

  * WALKING is faked, and that's fine. A static PNG slid horizontally reads as
    sliding. The same PNG slid horizontally *while bobbing up and down and
    rocking a couple of degrees* reads as walking, because those are the two
    motions your eye actually uses to detect a gait. Two sine waves buy you
    most of what a hand-drawn walk cycle would.

  * The window must never steal focus. ``Qt.Tool`` keeps it out of alt-tab and
    the taskbar; ``WA_ShowWithoutActivating`` means showing it does not move
    keyboard focus away from whatever you were typing in. Without those two,
    an hourly reminder would eat an hourly keystroke, which would make this app
    genuinely worse than useless.
"""

from __future__ import annotations

import logging
import math

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPoint,
    QPropertyAnimation,
    QRectF,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import characters, config, platform_win

log = logging.getLogger(__name__)

# How far off the right edge the buddy starts, so it slides in from nothing.
ENTRY_OFFSET_PX = 40

# Two entrance styles, because which one looks right depends entirely on the
# artwork:
#
#   "glide" -- pure horizontal movement, eased, with a short fade. Correct for
#              a character drawn standing still (which is what most single-pose
#              renders are). Adding a fake gait to a figure with both feet
#              planted reads as stumbling, not walking.
#   "walk"  -- adds a vertical bob and a slight rock. Convincing only if the
#              pose already suggests mid-stride, or once real walk frames exist.
GLIDE_IN_MS = 950
WALK_IN_MS = 1100
EXIT_MS = 750

# Fade applied at the start of the entrance so the character materialises
# rather than snapping into existence at the screen edge.
FADE_IN_MS = 320

# Bob geometry, used only by the "walk" style.
BOB_AMPLITUDE_PX = 7.0
ROCK_AMPLITUDE_DEG = 2.5
STEPS_PER_WALK = 4.0  # sine cycles across the entry animation

# Amplitude of the slow breathing motion once the character has arrived.
IDLE_BREATH_PX = 2.0


class CharacterWidget(QWidget):
    """Draws the character pixmap with an animatable walk bob.

    The ``phase`` property runs 0.0 -> 1.0 across the walk. Qt's animation
    system can drive any Python property declared with ``Property``, which is
    how a plain float ends up producing a walk cycle.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap = QPixmap()
        self._phase = 0.0
        self._walking = False
        self._style = "glide"
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def set_style(self, style: str) -> None:
        self._style = style if style in ("glide", "walk") else "glide"

    def set_pixmap(self, pixmap: QPixmap) -> None:
        self._pixmap = pixmap
        # Logical size = physical size / DPR, so layout maths stays in the same
        # coordinate space regardless of display scaling.
        dpr = pixmap.devicePixelRatio() or 1.0
        self.setFixedSize(int(pixmap.width() / dpr), int(pixmap.height() / dpr))
        self.update()

    def get_phase(self) -> float:
        return self._phase

    def set_phase(self, value: float) -> None:
        self._phase = value
        self.update()

    phase = Property(float, get_phase, set_phase)

    def set_walking(self, walking: bool) -> None:
        self._walking = walking
        self.update()

    def paintEvent(self, event) -> None:
        if self._pixmap.isNull():
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        if self._walking and self._style == "walk":
            angle = math.sin(self._phase * math.tau * STEPS_PER_WALK)
            # abs() so the body rises twice per stride -- once per footfall --
            # which is what a real gait does.
            bob = -abs(math.sin(self._phase * math.tau * STEPS_PER_WALK)) * BOB_AMPLITUDE_PX
            rock = angle * ROCK_AMPLITUDE_DEG
        elif self._walking:
            # Gliding: the window itself is moving, so the character needs no
            # motion of its own. Any wobble here fights the smooth translation
            # rather than adding to it.
            bob = 0.0
            rock = 0.0
        else:
            # Standing still: a slow, shallow breathing motion.
            bob = math.sin(self._phase * math.tau) * IDLE_BREATH_PX
            rock = 0.0

        # Rotate about the feet, not the image centre, or the character pivots
        # like a compass needle instead of leaning.
        painter.translate(self.width() / 2.0, float(self.height()))
        painter.rotate(rock)
        painter.translate(-self.width() / 2.0, -float(self.height()))

        painter.drawPixmap(QPoint(0, int(bob)), self._pixmap)
        painter.end()


class SpeechBubble(QWidget):
    """A rounded speech bubble with a downward tail, drawn by hand."""

    RADIUS = 16
    TAIL_W = 18
    TAIL_H = 12
    PAD = 14

    def __init__(self, parent=None):
        super().__init__(parent)
        self._bg = QColor(255, 255, 255, 245)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(self.PAD, self.PAD, self.PAD,
                                  self.PAD + self.TAIL_H)
        layout.setSpacing(10)

        self.message = QLabel("Time to drink water!")
        font = QFont()
        font.setPointSize(11)
        font.setBold(True)
        self.message.setFont(font)
        self.message.setStyleSheet("color: #1b2b3a;")
        self.message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.message)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.drank_button = QPushButton("Done")
        self.snooze_button = QPushButton("5 min")
        self.close_button = QPushButton("✕")
        for button, style in (
            (self.drank_button, "background:#2f8fd8; color:white;"),
            (self.snooze_button, "background:#e6edf3; color:#25415c;"),
            (self.close_button, "background:#e6edf3; color:#5a6b7c;"),
        ):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setFixedHeight(28)
            button.setStyleSheet(
                f"QPushButton {{ {style} border:none; border-radius:14px;"
                f" padding:0 14px; font-size:11px; font-weight:600; }}"
                "QPushButton:hover { opacity:0.9; }"
            )
        self.close_button.setFixedWidth(30)
        buttons.addStretch()
        buttons.addWidget(self.drank_button)
        buttons.addWidget(self.snooze_button)
        buttons.addWidget(self.close_button)
        buttons.addStretch()
        layout.addLayout(buttons)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(0, 0, 0, 70))
        self.setGraphicsEffect(shadow)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        body = QRectF(0, 0, self.width(), self.height() - self.TAIL_H)
        path = QPainterPath()
        path.addRoundedRect(body, self.RADIUS, self.RADIUS)

        # Tail, pointing down toward the character's head.
        tail_x = self.width() * 0.5
        tail = QPainterPath()
        tail.moveTo(tail_x - self.TAIL_W / 2, body.bottom() - 1)
        tail.lineTo(tail_x, body.bottom() + self.TAIL_H)
        tail.lineTo(tail_x + self.TAIL_W / 2, body.bottom() - 1)
        tail.closeSubpath()

        painter.fillPath(path.united(tail), self._bg)
        painter.end()


class BuddyOverlay(QWidget):
    """The window that appears, walks in, waits, and leaves."""

    #: User confirmed they drank -- app logs a glass.
    drank = Signal()
    #: User asked for five more minutes.
    snoozed = Signal()
    #: The overlay finished hiding, for any reason.
    finished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.bubble = SpeechBubble(self)
        self.character = CharacterWidget(self)
        layout.addWidget(self.bubble, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(self.character, 0, Qt.AlignmentFlag.AlignHCenter)

        self.bubble.drank_button.clicked.connect(self._on_drank)
        self.bubble.snooze_button.clicked.connect(self._on_snoozed)
        self.bubble.close_button.clicked.connect(self.dismiss)

        # Animation handles are kept as attributes because a QPropertyAnimation
        # that goes out of scope is garbage collected mid-flight and the
        # animation silently stops.
        self._walk_in: QPropertyAnimation | None = None
        self._walk_out: QPropertyAnimation | None = None
        self._phase_anim: QPropertyAnimation | None = None
        self._idle_anim: QPropertyAnimation | None = None
        self._fade: QPropertyAnimation | None = None

        self._dismiss_timer = QTimer(self)
        self._dismiss_timer.setSingleShot(True)
        self._dismiss_timer.timeout.connect(self.dismiss)

        self._leaving = False

    # ------------------------------------------------------------------
    # Showing
    # ------------------------------------------------------------------
    def show_reminder(self, character_id: str, message: str, hold_seconds: int,
                      monitor_index: int = 0, entrance: str = "glide") -> None:
        if self.isVisible():
            # Already on screen: restart the hold rather than stacking a second
            # walk-in on top of the first.
            self._dismiss_timer.start(hold_seconds * 1000)
            return

        self._leaving = False
        self._stop_animations()

        screen = self._screen_for(monitor_index)
        dpr = screen.devicePixelRatio()
        self.character.set_pixmap(
            characters.load_sprite(character_id, config.CHARACTER_HEIGHT_PX, dpr)
        )
        self.bubble.message.setText(message)

        # Let the layout settle so sizeHint reflects the real pixmap and text.
        self.adjustSize()
        size = self.sizeHint()
        self.resize(size)

        area = screen.geometry()  # full screen, so we may overlap the taskbar
        target_x = area.right() - size.width() - 24
        # Sit the feet on the very bottom edge, next to the clock.
        target_y = area.bottom() - size.height() + 4
        start_x = area.right() + ENTRY_OFFSET_PX

        entrance = entrance if entrance in ("glide", "walk") else "glide"
        self.character.set_style(entrance)
        duration = WALK_IN_MS if entrance == "walk" else GLIDE_IN_MS

        self.move(start_x, target_y)
        self.setWindowOpacity(0.0)
        self.show()
        platform_win.raise_to_topmost(int(self.winId()))

        # Horizontal travel.
        self._walk_in = QPropertyAnimation(self, b"pos", self)
        self._walk_in.setDuration(duration)
        self._walk_in.setStartValue(QPoint(start_x, target_y))
        self._walk_in.setEndValue(QPoint(target_x, target_y))
        # OutQuint decelerates hard at the end: most of the distance is covered
        # early, then it eases into place over the last few frames. That long
        # settle is what makes the arrival read as smooth rather than as a
        # panel sliding to a stop. OutCubic is the gentler equivalent used for
        # the walk, where the gait already supplies the visual interest.
        self._walk_in.setEasingCurve(
            QEasingCurve.Type.OutCubic if entrance == "walk"
            else QEasingCurve.Type.OutQuint
        )

        # Fade in over the first fraction of the travel, so the character
        # appears out of nothing instead of popping in at full opacity while
        # partially off-screen.
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(FADE_IN_MS)
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)

        # Drives the gait for "walk", and simply runs out the clock for
        # "glide" so both styles share the same arrival handling.
        self.character.set_walking(True)
        self._phase_anim = QPropertyAnimation(self.character, b"phase", self)
        self._phase_anim.setDuration(duration)
        self._phase_anim.setStartValue(0.0)
        self._phase_anim.setEndValue(1.0)
        self._phase_anim.finished.connect(self._on_arrived)

        self._walk_in.start()
        self._fade.start()
        self._phase_anim.start()

        self._dismiss_timer.start(duration + hold_seconds * 1000)
        log.info("Buddy shown (%s, %s) for %ss", character_id, entrance,
                 hold_seconds)

    def _on_arrived(self) -> None:
        """Switch from walking to a gentle idle breathing loop."""
        if self._leaving:
            return
        self.character.set_walking(False)
        self._idle_anim = QPropertyAnimation(self.character, b"phase", self)
        self._idle_anim.setDuration(2400)
        self._idle_anim.setStartValue(0.0)
        self._idle_anim.setEndValue(1.0)
        self._idle_anim.setLoopCount(-1)
        self._idle_anim.start()

    # ------------------------------------------------------------------
    # Hiding
    # ------------------------------------------------------------------
    def dismiss(self) -> None:
        """Walk back out to the right, then hide."""
        if self._leaving or not self.isVisible():
            return
        self._leaving = True
        self._dismiss_timer.stop()
        if self._idle_anim:
            self._idle_anim.stop()

        screen = self.screen() or QGuiApplication.primaryScreen()
        exit_x = screen.geometry().right() + ENTRY_OFFSET_PX

        self.character.set_walking(True)
        self._phase_anim = QPropertyAnimation(self.character, b"phase", self)
        self._phase_anim.setDuration(EXIT_MS)
        self._phase_anim.setStartValue(0.0)
        self._phase_anim.setEndValue(1.0)
        self._phase_anim.start()

        self._walk_out = QPropertyAnimation(self, b"pos", self)
        self._walk_out.setDuration(EXIT_MS)
        self._walk_out.setStartValue(self.pos())
        self._walk_out.setEndValue(QPoint(exit_x, self.pos().y()))
        # InCubic accelerates away -- the mirror of the eased arrival.
        self._walk_out.setEasingCurve(QEasingCurve.Type.InCubic)
        self._walk_out.finished.connect(self._on_left)
        self._walk_out.start()

        # Fade out over the tail of the exit so the character dissolves rather
        # than clipping abruptly at the screen edge.
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(EXIT_MS)
        self._fade.setStartValue(self.windowOpacity())
        self._fade.setEndValue(0.0)
        self._fade.setEasingCurve(QEasingCurve.Type.InCubic)
        self._fade.start()

    def _on_left(self) -> None:
        self.hide()
        self._leaving = False
        self.finished.emit()

    def _on_drank(self) -> None:
        self.drank.emit()
        self.bubble.message.setText("Nice one \U0001f4a7")
        # Brief beat so the confirmation is readable before the buddy leaves.
        QTimer.singleShot(700, self.dismiss)

    def _on_snoozed(self) -> None:
        self.snoozed.emit()
        self.dismiss()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _screen_for(index: int):
        screens = QGuiApplication.screens()
        if not screens:
            return QGuiApplication.primaryScreen()
        # Clamp rather than fail: monitors get unplugged between sessions.
        return screens[min(max(index, 0), len(screens) - 1)]

    def _stop_animations(self) -> None:
        for anim in (self._walk_in, self._walk_out, self._phase_anim,
                     self._idle_anim, self._fade):
            if anim is not None:
                anim.stop()
