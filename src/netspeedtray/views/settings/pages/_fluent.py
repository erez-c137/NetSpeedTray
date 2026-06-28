"""
Shared Fluent helpers for the Settings pages.

The 2.0 Settings pages are built from one idiom — a small section caption over a stack of
``SettingCard`` rows (the native Win11 Settings layout) — instead of the old ``QGroupBox`` frames.
This module holds the one piece that ``components.SettingCard`` doesn't: the section caption that
groups related cards. Keeping it here means every page renders the same heading at the same size,
colour and inset.
"""
from __future__ import annotations

from PyQt6.QtWidgets import QLabel, QVBoxLayout

from netspeedtray.utils import styles as su
from netspeedtray.constants.styles import styles as tokens


def section_header(text: str) -> QLabel:
    """A Win11 Settings section caption: semibold, primary-coloured, with generous space above so it
    reads as the start of a new group (not crowding the card before it) and a small gap to its cards."""
    c = su.semantic_colors()
    lbl = QLabel(text)
    lbl.setFont(su.font(tokens.TYPE_BODY_STRONG))
    lbl.setStyleSheet(f"color: {c['text_primary']}; background: transparent; padding: 12px 2px 2px 2px;")
    return lbl


def page_layout(widget) -> QVBoxLayout:
    """A consistent vertical page layout: tight, even spacing between cards (the card strokes already
    provide the visual separation, so the old group-box gaps are too loose)."""
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(tokens.SPACE_XS)
    return layout
