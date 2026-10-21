"""
Shared Fluent helpers for the Settings pages.

The 2.0 Settings pages are built from one idiom - a small section caption over a stack of
``SettingCard`` rows (the native Win11 Settings layout) - instead of the old ``QGroupBox`` frames.
This module holds the one piece that ``components.SettingCard`` doesn't: the section caption that
groups related cards. Keeping it here means every page renders the same heading at the same size,
color and inset.
"""
from __future__ import annotations

from PyQt6.QtWidgets import QLabel, QVBoxLayout

from netspeedtray.utils import styles as su
from netspeedtray.constants.styles import styles as tokens


def section_header(text: str) -> QLabel:
    """A Win11 Settings section caption: semibold, transparent (NO band), with generous space ABOVE so a
    new group reads as airy/title-led, a tight gap BELOW tying it to its cards, and a left indent so its
    text aligns with the card CONTENT (not the card stroke) - the native WinUI group-header rhythm.
    (Net spacing also gets the page layout's inter-widget gap added on each side.)"""
    c = su.semantic_colors()
    lbl = QLabel(text)
    lbl.setFont(su.font(tokens.TYPE_BODY_STRONG))
    lbl.setStyleSheet(f"color: {c['text_primary']}; background: transparent; padding: 20px 2px 4px 16px;")
    return lbl


def as_int(value, default: int) -> int:
    """A config value as the int a QSlider / QSpinBox ``setValue`` demands. Several schema keys allow
    ``(int, float)`` (e.g. ``graph_opacity``), and ``setValue(55.0)`` raises a TypeError that used to
    abort loading every later page (C1). Rounds floats; falls back to ``default`` for None / junk."""
    if value is None or isinstance(value, bool):
        value = default
    try:
        return int(round(float(value)))
    except (TypeError, ValueError, OverflowError):
        return int(default)


def is_hex(text) -> bool:
    """A complete ``#RRGGBB`` colour (what the config schema accepts for every colour key)."""
    t = (text or "").strip()
    return len(t) == 7 and t.startswith("#") and all(c in "0123456789abcdefABCDEF" for c in t[1:])


class LastValidHex:
    """The last COMPLETE ``#RRGGBB`` seen in each hex field (S8). A half-typed value ("#AB") must
    never reach the live widget or the config - config validation would turn it into the factory
    colour - so get() answers with the colour the user had until the field holds a whole one again.
    Same rule as the Hardware page's label colours."""

    def __init__(self, defaults: dict):
        self._values = dict(defaults)

    def remember(self, key: str, text) -> bool:
        """Record ``text`` if it is a complete hex; True when it was."""
        if is_hex(text):
            self._values[key] = text.strip()
            return True
        return False

    def get(self, key: str, text) -> str:
        self.remember(key, text)
        return self._values[key]


def page_layout(widget) -> QVBoxLayout:
    """A consistent vertical page layout: tight, even spacing between cards (the card strokes already
    provide the visual separation), plus a small bottom inset so the last card never abuts the fixed
    footer / scroll edge on a scrolling page."""
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 10)
    layout.setSpacing(tokens.SPACE_XS)
    return layout
