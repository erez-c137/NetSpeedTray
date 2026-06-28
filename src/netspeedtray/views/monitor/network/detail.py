"""
ConnectionDetailPanel — the per-app "detail-on-demand" panel for the Monitor's Network tab.

Clicking an app in the AppBarList opens this panel for that app: its PIDs, the distinct remote hosts
it's talking to, and the full list of live connections (TCP/UDP, local -> remote, state). It reuses
the AppActivityWorker payload the list already receives (``distinct_hosts`` + ``endpoints``), so it
needs no extra sampling — and it reuses the standalone App Activity window's translated strings, so
the Monitor and that window read identically. This is the view that lets the Monitor retire the
standalone App Activity window.

Graph-free + matplotlib-free.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QToolButton, QPlainTextEdit,
)

from netspeedtray.utils import styles as su
from netspeedtray.constants.styles import styles as tokens


class ConnectionDetailPanel(QWidget):
    """App name + PIDs header (with a close affordance) over a scrollable hosts/connections body."""

    closed = pyqtSignal()   #: the user dismissed the panel (close button)

    def __init__(self, i18n, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._i18n = i18n
        self._last_name = ""
        self._current_key = ""   # which app the body currently shows (to decide scroll reset vs keep)
        c = su.semantic_colors()

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 8, 8, 12)
        root.setSpacing(6)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        self._title = QLabel(self._tr("APP_ACTIVITY_DETAILS_TITLE", "Connection Details"))
        self._title.setFont(su.font(tokens.TYPE_BODY_STRONG))
        self._title.setStyleSheet(f"color: {c['text_primary']}; background: transparent;")
        self._title.setWordWrap(True)
        header.addWidget(self._title, 1)
        self._close = QToolButton()
        self._close.setText(chr(0xE8BB))   # Segoe Fluent "ChromeClose"
        self._close.setCursor(Qt.CursorShape.PointingHandCursor)
        self._close.setToolTip(self._tr("APP_ACTIVITY_CLOSE_BUTTON", "Close"))
        self._close.setStyleSheet(
            f"QToolButton {{ background: transparent; color: {c['text_secondary']};"
            f" font-family: 'Segoe Fluent Icons','Segoe MDL2 Assets';"
            f" border: none; padding: 2px 6px; font-size: 12px; }}"
            f" QToolButton:hover {{ color: {c['text_primary']}; }}")
        self._close.clicked.connect(self.closed.emit)
        header.addWidget(self._close, 0, Qt.AlignmentFlag.AlignTop)
        root.addLayout(header)

        self._body = QPlainTextEdit()
        self._body.setReadOnly(True)
        self._body.setFrameShape(QPlainTextEdit.Shape.NoFrame)
        mono = QFont("Cascadia Mono", 9)
        mono.setStyleHint(QFont.StyleHint.Monospace)
        mono.setFamilies(["Cascadia Mono", "Consolas", "Courier New", "monospace"])
        self._body.setFont(mono)
        self._body.setStyleSheet(
            f"QPlainTextEdit {{ background: {c['subtle_fill']}; color: {c['text_primary']};"
            f" border-radius: {tokens.RADIUS_CONTROL}px; padding: 8px; }}")
        root.addWidget(self._body, 1)

    # --- inputs -----------------------------------------------------------------
    def set_row(self, row: Dict[str, Any]) -> None:
        """Render an app's detail. Preserves the scroll position so the 2 s live refresh doesn't
        yank the body while the user is reading down a long connection list."""
        key = str(row.get("identity_key", row.get("display_name", "")))
        new_app = key != self._current_key
        self._current_key = key
        name = str(row.get("display_name", "Unknown"))
        self._last_name = name
        pids: List[Any] = row.get("pids", []) or []
        hosts: List[Any] = row.get("distinct_hosts", []) or []
        endpoints: List[Any] = row.get("endpoints", []) or []

        pid_text = ", ".join(str(p) for p in pids) if pids else "-"
        self._title.setText(
            self._tr("APP_ACTIVITY_DETAILS_HEADER_TEMPLATE", "{name}   (PID {pids})")
            .format(name=name, pids=pid_text) if pids else name)

        sections: List[str] = []
        if hosts:
            sections.append(self._tr("APP_ACTIVITY_TALKING_TO_SECTION", "Talking to:")
                            + "\n" + "\n".join(f"  {h}" for h in hosts))
        if endpoints:
            sections.append(self._tr("APP_ACTIVITY_CONNECTIONS_SECTION", "Connections:")
                            + "\n" + "\n".join(f"  {e}" for e in endpoints))
        if not sections:
            sections.append(self._tr("APP_ACTIVITY_NO_CONNECTION_DETAILS", "No connection details available."))
        # New app => start at the top; same app refreshing live => keep the user's scroll position.
        self._set_body("\n\n".join(sections), reset_scroll=new_app)

    def mark_inactive(self) -> None:
        """The selected app dropped off the list (no live connections). Keep its name in the header
        but show that there's nothing to detail right now, rather than blanking or closing."""
        if self._last_name:
            self._title.setText(self._last_name)
        self._set_body(self._tr("APP_ACTIVITY_NO_CONNECTION_DETAILS", "No connection details available."),
                       reset_scroll=True)

    def _set_body(self, text: str, reset_scroll: bool = False) -> None:
        bar = self._body.verticalScrollBar()
        pos = 0 if reset_scroll else bar.value()
        self._body.setPlainText(text)
        bar.setValue(min(pos, bar.maximum()))

    def _tr(self, key: str, default: str) -> str:
        return str(getattr(self._i18n, key, default)) if self._i18n is not None else default
