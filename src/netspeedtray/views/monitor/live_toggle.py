"""
LiveToggle - a compact pill that freezes/resumes the Monitor graph's realtime updates.

The canonical "is live" state lives on the shared GraphHost (one engine drives both the Network and
Hardware tabs), so this is a thin *view*: it reads ``host.is_live``, flips it on click via
``host.set_live()``, and re-renders from the host's ``live_changed`` signal - which means a pause on
one tab is instantly reflected on the other's pill.

Matplotlib-free and graph-package-free (it only holds a reference to the already-built host object),
so it doesn't trip the Monitor's import firewall and can sit in either tab header.
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QPushButton, QWidget

from netspeedtray.utils import styles as su
from netspeedtray.constants.styles import styles as tokens

# The same green the Overview RAM tile / RAM graph line use, so "live" reads as one consistent hue.
_LIVE_GREEN_DARK = "#4CAF50"
_LIVE_GREEN_LIGHT = "#388E3C"


class LiveToggle(QPushButton):
    """Live ⇄ Paused pill bound to a shared GraphHost. Press to freeze the graph; press to resume."""

    def __init__(self, host, i18n, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._host = host
        self._i18n = i18n
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(30)
        self.setFont(su.font(tokens.TYPE_CAPTION))
        self.clicked.connect(self._toggle)
        try:
            host.live_changed.connect(self._render)
        except Exception:
            pass
        self._render(bool(getattr(host, "is_live", True)))

    def _toggle(self) -> None:
        try:
            self._host.set_live(not self._host.is_live)
        except Exception:
            pass

    def _render(self, live: bool) -> None:
        c = su.semantic_colors()
        if live:
            green = _LIVE_GREEN_DARK if su.is_dark_mode() else _LIVE_GREEN_LIGHT
            self.setText("● " + self._tr("MONITOR_LIVE", "Live"))     # ● filled dot = broadcasting
            self.setToolTip(self._tr("MONITOR_PAUSE_TIP", "Pause live updates"))
            fg, border, weight = green, c["card_stroke"], "600"
        else:
            self.setText("▶ " + self._tr("MONITOR_PAUSED", "Paused"))  # ▶ play = press to resume
            self.setToolTip(self._tr("MONITOR_RESUME_TIP", "Resume live updates"))
            fg, border, weight = c["accent"], c["accent"], "600"
        # accessibleName carries the STATE for screen readers; the tooltip carries the action.
        self.setAccessibleName(self.text().replace("● ", "").replace("▶ ", ""))
        self.setStyleSheet(
            f"QPushButton {{ background: {c['subtle_fill']}; color: {fg}; font-weight: {weight};"
            f" border: 1px solid {border}; border-radius: {tokens.RADIUS_CONTROL}px;"
            f" padding: 4px 12px; }} QPushButton:hover {{ border-color: {c['accent']}; }}"
            f" QPushButton:focus {{ border-color: {c['accent']}; }}")   # visible keyboard focus (WCAG 2.4.7)

    def _tr(self, key: str, default: str) -> str:
        return str(getattr(self._i18n, key, default)) if self._i18n is not None else default
