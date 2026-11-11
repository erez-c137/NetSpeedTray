"""
ConnectionDetailPanel - the per-app "detail-on-demand" panel for the Monitor's Network tab.

Clicking an app in the AppBarList opens this panel for that app: its PIDs, the distinct remote hosts
it's talking to, and the full list of live connections (TCP/UDP, local -> remote, state). It reuses
the AppActivityWorker payload the list already receives (``distinct_hosts`` + ``endpoints``), so it
needs no extra sampling - and it reuses the standalone App Activity window's translated strings, so
the Monitor and that window read identically. This is the view that lets the Monitor retire the
standalone App Activity window.

Graph-free + matplotlib-free.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QTextCharFormat, QTextCursor
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
            f" font-family: '{su.icon_font_family()}';"
            f" border: none; padding: 2px 6px; font-size: 12px; }}"
            f" QToolButton:hover {{ color: {c['text_primary']}; }}")
        self._close.clicked.connect(self.closed.emit)
        header.addWidget(self._close, 0, Qt.AlignmentFlag.AlignTop)
        root.addLayout(header)

        self._body = QPlainTextEdit()
        self._body.setReadOnly(True)
        self._body.setFrameShape(QPlainTextEdit.Shape.NoFrame)
        self._body_face: Optional[str] = None   # chosen with the first text (_body_base_face), then fixed
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
        su.set_text_in_face(self._title,   # a process name the UI face lacks: in its own face
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
            su.set_text_in_face(self._title, self._last_name)
        self._set_body(self._tr("APP_ACTIVITY_NO_CONNECTION_DETAILS", "No connection details available."),
                       reset_scroll=True)

    def _body_base_face(self) -> str:
        """The body's ONE face, chosen once for the UI language: the installed mono face (a family list
        loads every family) when it has the language's section headings - the Latin-script UIs - else the
        UI face (Cascadia Mono has no Hebrew, CJK or Bengali). Fixed for the panel's life, so the box
        never switches faces between refreshes."""
        from netspeedtray.utils.font_runs import family_covers
        headings = "".join(self._tr(k, d) for k, d in (
            ("APP_ACTIVITY_TALKING_TO_SECTION", "Talking to:"),
            ("APP_ACTIVITY_CONNECTIONS_SECTION", "Connections:"),
            ("APP_ACTIVITY_NO_CONNECTION_DETAILS", "No connection details available.")))
        mono = su.mono_family()
        return mono if family_covers(mono, headings) else su.ui_family()

    def _set_body(self, text: str, reset_scroll: bool = False) -> None:
        if self._body_face is None:          # the first text: no face loads before the panel shows one
            self._body_face = self._body_base_face()
            body_font = QFont(self._body_face, 9)
            if self._body_face == su.mono_family():
                body_font.setStyleHint(QFont.StyleHint.Monospace)
            self._body.setFont(body_font)
        bar = self._body.verticalScrollBar()
        pos = 0 if reset_scroll else bar.value()
        runs = _face_runs(self._body_face, text)
        if len(runs) == 1 and runs[0][0] is None:
            self._body.setPlainText(text)
        else:
            # A host name with letters the body's face lacks (an IDN, CJK): those runs in a known face
            # that has them, the rest as always - never Qt's search through every installed family.
            self._body.clear()
            cursor = QTextCursor(self._body.document())
            for face, run in runs:
                fmt = QTextCharFormat()
                if face is not None:
                    fmt.setFontFamilies([face])
                cursor.insertText(run, fmt)
        bar.setValue(min(pos, bar.maximum()))

    def _tr(self, key: str, default: str) -> str:
        return str(getattr(self._i18n, key, default)) if self._i18n is not None else default


def _face_runs(base: str, text: str):
    """``text`` as ((face, run), ...): face None = ``base``; else the UI face or a known face with the
    run's letters (styles.face_for_text). Characters that are not letters (digits, dots, colons) stay
    with the run before them, so a run in another face carries its own punctuation. A run no known face
    has stays in ``base`` (Qt's fallback, as before)."""
    import unicodedata
    from netspeedtray.utils.font_runs import family_covers
    if family_covers(base, text):
        return ((None, text),)
    out = []
    for ch in text:
        if ch.isspace() or not unicodedata.category(ch).startswith(("L", "M")):
            face = out[-1][0] if out else None
            if face is not None and not family_covers(face, ch):
                face = None
        elif family_covers(base, ch):
            face = None
        else:
            face = su.ui_family() if family_covers(su.ui_family(), ch) else su.face_for_text(ch)
        if out and out[-1][0] == face:
            out[-1][1] += ch
        else:
            out.append([face, ch])
    return tuple((f, r) for f, r in out)
