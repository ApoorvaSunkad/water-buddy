"""Loading (and, if necessary, inventing) the character artwork.

Two jobs:

1. Find and cache the character images, scaled for the current display's DPI.
2. If an image is missing, draw a placeholder instead of crashing.

Point 2 matters more than it looks. It means the app is runnable the moment the
code exists, before any art has been produced -- you can build and test all the
timing, positioning and animation logic against a stick figure, then swap in
real artwork later by dropping files into a folder. Nothing in the app has to
change. That separation of "does it work" from "does it look good" is worth a
lot when you're learning, because it stops a missing PNG from blocking you.
"""

from __future__ import annotations

import logging
import random
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QLinearGradient, QPainter, QPixmap

from . import config

log = logging.getLogger(__name__)

# Filename we look for inside assets/characters/<id>/
PRIMARY_SPRITE = "drinking.png"

# Fallback names, tried in order, so you can name your file loosely.
SPRITE_CANDIDATES = (PRIMARY_SPRITE, "idle.png", "character.png", "buddy.png")

_cache: dict[tuple[str, int, float], QPixmap] = {}


def available_characters() -> list[str]:
    return list(config.CHARACTER_IDS)


def resolve_character(preference: str) -> str:
    """Turn a settings value ("female" / "male" / "random") into a concrete id."""
    if preference == "random":
        return random.choice(config.CHARACTER_IDS)
    if preference in config.CHARACTER_IDS:
        return preference
    return config.CHARACTER_IDS[0]


def sprite_path(character_id: str) -> Path | None:
    """Return the first existing sprite file for this character, else None."""
    folder = config.CHARACTERS_DIR / character_id
    for name in SPRITE_CANDIDATES:
        candidate = folder / name
        if candidate.exists():
            return candidate
    return None


def load_sprite(character_id: str, height_px: int, dpr: float = 1.0) -> QPixmap:
    """Return the character scaled to ``height_px`` logical pixels.

    ``dpr`` is the display's device pixel ratio. Your screen reported 1.5, which
    means one logical pixel is 1.5 physical pixels. If we scaled the image to
    260 logical pixels and stopped there, Windows would stretch it to 390
    physical pixels and it would look soft. Instead we scale the bitmap to the
    full physical size and then tell Qt the ratio, so it draws crisply.
    """
    key = (character_id, height_px, dpr)
    if key in _cache:
        return _cache[key]

    physical_height = int(height_px * dpr)
    path = sprite_path(character_id)

    if path is None:
        log.warning("No artwork for %r; using a placeholder. Drop a PNG at %s",
                    character_id, config.CHARACTERS_DIR / character_id / PRIMARY_SPRITE)
        pixmap = _placeholder(character_id, physical_height)
    else:
        source = QPixmap(str(path))
        if source.isNull():
            log.error("Could not decode %s; using a placeholder", path)
            pixmap = _placeholder(character_id, physical_height)
        else:
            pixmap = source.scaledToHeight(
                physical_height, Qt.TransformationMode.SmoothTransformation
            )

    pixmap.setDevicePixelRatio(dpr)
    _cache[key] = pixmap
    return pixmap


def clear_cache() -> None:
    """Called when the DPI changes or the user swaps artwork at runtime."""
    _cache.clear()


# ---------------------------------------------------------------------------
# Placeholder art
# ---------------------------------------------------------------------------

def _placeholder(character_id: str, height: int) -> QPixmap:
    """Draw a simple figure holding a bottle, so the app is usable without art.

    Deliberately cartoonish and obviously provisional -- it should look like a
    placeholder, so you're never confused about whether your real image loaded.
    """
    width = int(height * 0.45)
    pixmap = QPixmap(width, height)
    pixmap.fill(Qt.GlobalColor.transparent)

    female = character_id == "female"
    shirt = QColor("#e8eaed") if female else QColor("#7d8b5a")
    trousers = QColor("#5a7ca6") if female else QColor("#6d7f96")
    skin = QColor("#f0c9a8")
    hair = QColor("#4a342a")

    p = QPainter(pixmap)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setPen(Qt.PenStyle.NoPen)

    unit = height / 100.0

    # Legs
    p.setBrush(trousers)
    p.drawRoundedRect(QRectF(width * 0.28, height * 0.55, width * 0.18, height * 0.38),
                      unit * 2, unit * 2)
    p.drawRoundedRect(QRectF(width * 0.54, height * 0.55, width * 0.18, height * 0.38),
                      unit * 2, unit * 2)

    # Shoes
    p.setBrush(QColor("#f5f5f5") if female else QColor("#6b4a32"))
    p.drawRoundedRect(QRectF(width * 0.24, height * 0.92, width * 0.24, height * 0.05),
                      unit, unit)
    p.drawRoundedRect(QRectF(width * 0.52, height * 0.92, width * 0.24, height * 0.05),
                      unit, unit)

    # Torso
    p.setBrush(shirt)
    p.drawRoundedRect(QRectF(width * 0.20, height * 0.30, width * 0.60, height * 0.28),
                      unit * 4, unit * 4)

    # Arm raised toward the mouth, holding the bottle
    p.setBrush(skin)
    p.drawRoundedRect(QRectF(width * 0.12, height * 0.26, width * 0.12, height * 0.20),
                      unit * 3, unit * 3)

    # Head
    p.setBrush(skin)
    p.drawEllipse(QRectF(width * 0.30, height * 0.10, width * 0.40, height * 0.20))

    # Hair
    p.setBrush(hair)
    p.drawEllipse(QRectF(width * 0.28, height * 0.07, width * 0.44, height * 0.13))
    if female:
        # Ponytail
        p.drawEllipse(QRectF(width * 0.62, height * 0.10, width * 0.16, height * 0.22))

    # Water bottle -- a translucent blue capsule with a cap
    bottle = QRectF(width * 0.02, height * 0.16, width * 0.16, height * 0.22)
    gradient = QLinearGradient(bottle.topLeft(), bottle.bottomRight())
    gradient.setColorAt(0.0, QColor(180, 220, 255, 220))
    gradient.setColorAt(1.0, QColor(120, 180, 240, 220))
    p.setBrush(QBrush(gradient))
    p.drawRoundedRect(bottle, unit * 2, unit * 2)
    p.setBrush(QColor("#3f7fd0"))
    p.drawRoundedRect(QRectF(bottle.left() + bottle.width() * 0.30,
                             bottle.top() - unit * 2.5,
                             bottle.width() * 0.40, unit * 3), unit, unit)

    # "PLACEHOLDER" watermark so this is never mistaken for the real art
    p.setPen(QColor(0, 0, 0, 90))
    font = QFont()
    font.setPointSizeF(max(5.0, unit * 4))
    font.setBold(True)
    p.setFont(font)
    p.drawText(QRectF(0, height * 0.96, width, height * 0.04),
               Qt.AlignmentFlag.AlignCenter, "placeholder")

    p.end()
    return pixmap
