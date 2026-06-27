"""
AppBarList — the per-app connection list for the Monitor's Network tab.

Renders the AppActivityWorker's honest payload (live connections per app, never estimated bytes) as
a calm bar list: one row per app, an activity bar sized to its share of live connections, active
apps in the accent colour and idle apps muted. Updates in place (rows keyed by app identity, reused
across ticks) so a refresh doesn't flicker or jump the scroll.

Graph-free + matplotlib-free.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPainter, QColor, QFontMetrics
from PyQt6.QtWidgets import (
    QWidget, QFrame, QVBoxLayout, QHBoxLayout, QLabel, QScrollArea, QSizePolicy,
)

from netspeedtray.utils import styles as su
from netspeedtray.constants.styles import styles as tokens

_NAME_W = 180
_COUNT_W = 64


class _ActivityBar(QWidget):
    """A rounded track + fill sized to a 0..1 fraction; accent when active, muted when idle."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._frac = 0.0
        self._active = False
        self.setMinimumHeight(8)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_value(self, frac: float, active: bool) -> None:
        self._frac = max(0.0, min(1.0, frac))
        self._active = active
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        c = su.semantic_colors()
        h = min(self.height(), 8)
        y = (self.height() - h) / 2.0
        r = h / 2.0
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            p.setPen(Qt.PenStyle.NoPen)
            track = QColor(c["card_stroke"])
            p.setBrush(track)
            p.drawRoundedRect(0, int(y), self.width(), h, r, r)
            fill_w = int(self.width() * self._frac)
            if fill_w > 0:
                fill = QColor(c["accent"]) if self._active else QColor(c["text_secondary"])
                if not self._active:
                    fill.setAlpha(120)
                p.setBrush(fill)
                p.drawRoundedRect(0, int(y), max(fill_w, int(h)), h, r, r)
        finally:
            p.end()


class AppRow(QFrame):
    """One app: name (elided) + activity bar + live-connection count."""

    def __init__(self, i18n, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._i18n = i18n
        c = su.semantic_colors()
        self._c = c
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 5, 8, 5)
        lay.setSpacing(12)

        self._name = QLabel("")
        self._name.setFont(su.font(tokens.TYPE_BODY))
        self._name.setFixedWidth(_NAME_W)
        self._name.setStyleSheet(f"color: {c['text_primary']}; background: transparent;")

        self._bar = _ActivityBar()

        self._count = QLabel("")
        self._count.setFont(su.font(tokens.TYPE_BODY))
        self._count.setFixedWidth(_COUNT_W)
        self._count.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._count.setStyleSheet(f"color: {c['text_secondary']}; background: transparent;")

        lay.addWidget(self._name)
        lay.addWidget(self._bar, 1)
        lay.addWidget(self._count)

    def update_row(self, row: Dict[str, Any], max_conn: int) -> None:
        name = str(row.get("display_name", "—"))
        conn = int(row.get("conn_count", 0))
        est = int(row.get("established_count", 0))
        hosts = int(row.get("host_count", 0))
        active = est > 0

        fm = QFontMetrics(self._name.font())
        self._name.setText(fm.elidedText(name, Qt.TextElideMode.ElideRight, _NAME_W))
        self._bar.set_value((conn / max_conn) if max_conn > 0 else 0.0, active)
        self._count.setText(str(conn))
        # Dim idle apps (no established connections) so the eye lands on what's actually talking.
        name_color = self._c["text_primary"] if active else self._c["text_secondary"]
        self._name.setStyleSheet(f"color: {name_color}; background: transparent;")
        self.setToolTip(f"{name} — {conn} connections, {est} active, {hosts} hosts")


class AppBarList(QWidget):
    """Header + summary + a scrollable, in-place-updated list of per-app activity rows."""

    def __init__(self, i18n, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._i18n = i18n
        self._rows: Dict[str, AppRow] = {}
        c = su.semantic_colors()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)

        # Column header + a one-line summary.
        head = QHBoxLayout()
        head.setContentsMargins(8, 0, 8, 0)
        proc = QLabel(self._tr("APP_ACTIVITY_PROCESS_HEADER", "Process"))
        proc.setFont(su.font(tokens.TYPE_CAPTION))
        proc.setStyleSheet(f"color: {c['text_secondary']}; background: transparent;")
        conn = QLabel(self._tr("APP_ACTIVITY_CONNECTIONS_HEADER", "Connections"))
        conn.setFont(su.font(tokens.TYPE_CAPTION))
        conn.setStyleSheet(f"color: {c['text_secondary']}; background: transparent;")
        head.addWidget(proc)
        head.addStretch(1)
        head.addWidget(conn)
        root.addLayout(head)

        self._summary = QLabel(self._tr("APP_ACTIVITY_LOADING_MESSAGE", "Loading application activity..."))
        self._summary.setFont(su.font(tokens.TYPE_CAPTION))
        self._summary.setStyleSheet(f"color: {c['text_secondary']}; background: transparent;")
        self._summary.setContentsMargins(8, 0, 8, 0)
        root.addWidget(self._summary)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._content = QWidget()
        self._list_layout = QVBoxLayout(self._content)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(1)
        self._list_layout.addStretch(1)
        self._scroll.setWidget(self._content)
        root.addWidget(self._scroll, 1)

    # --- inputs -----------------------------------------------------------------
    def set_payload(self, payload: Dict[str, Any]) -> None:
        rows: List[Dict[str, Any]] = payload.get("rows", []) if isinstance(payload, dict) else []
        if not rows:
            self._summary.setText(self._tr("NO_APP_DATA_MESSAGE", "No application activity."))
        else:
            max_conn = max((int(r.get("conn_count", 0)) for r in rows), default=0)
            seen = set()
            ordered: List[AppRow] = []
            for r in rows:
                key = str(r.get("identity_key", r.get("display_name", "")))
                seen.add(key)
                w = self._rows.get(key)
                if w is None:
                    w = AppRow(self._i18n)
                    self._rows[key] = w
                w.update_row(r, max_conn)
                ordered.append(w)
            # Relayout in payload order (active-first); detach all, drop gone, re-add kept + stretch.
            while self._list_layout.count():
                self._list_layout.takeAt(0)
            for key in [k for k in self._rows if k not in seen]:
                self._rows.pop(key).deleteLater()
            for w in ordered:
                self._list_layout.addWidget(w)
            self._list_layout.addStretch(1)
            self._summary.setText(self._summary_text(payload))

    def set_unavailable(self, reason: str) -> None:
        if reason == "rdp":
            self._summary.setText(self._tr(
                "APP_ACTIVITY_RDP_UNAVAILABLE_MESSAGE",
                "App Activity is unavailable in Remote Desktop sessions."))
        else:
            self._summary.setText(self._tr("NO_APP_DATA_MESSAGE", "No application activity."))

    def _summary_text(self, payload: Dict[str, Any]) -> str:
        tmpl = self._tr("APP_ACTIVITY_SUMMARY_TEMPLATE",
                        "{app_count} apps · {active} active · {total_conn} connections · Updated {updated_at}")
        try:
            s = tmpl.format(
                app_count=int(payload.get("app_count", 0)),
                active=int(payload.get("active_app_count", 0)),
                total_conn=int(payload.get("total_conn_count", 0)),
                updated_at=payload.get("updated_at", "--:--:--"))
        except Exception:
            s = ""
        if payload.get("access_limited"):
            s = f"{s} {self._tr('APP_ACTIVITY_SUMMARY_LIMITED_SUFFIX', '(limited access without admin rights)')}"
        return s

    def _tr(self, key: str, default: str) -> str:
        return str(getattr(self._i18n, key, default)) if self._i18n is not None else default
