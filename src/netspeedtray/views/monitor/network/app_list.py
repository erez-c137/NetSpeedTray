"""
AppBarList — the per-app connection list for the Monitor's Network tab.

Renders the AppActivityWorker's honest payload (live connections per app, never estimated bytes) as
a calm bar list: one row per app, an activity bar sized (log scale) to its share of live
connections, active apps in the accent colour and idle apps muted. Rows are keyed by app identity
and reused across ticks (no flicker); ordering is stable (selected-first, then active-first, then by
name), so a row only moves when its active/idle state actually flips — never because its connection
count jittered. The selected row is pinned to the top so its highlight (and the detail panel reading
it) never slides out from under the user when its active/idle state flips.

Graph-free + matplotlib-free.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QPainter, QColor, QFontMetrics
from PyQt6.QtWidgets import (
    QWidget, QFrame, QVBoxLayout, QHBoxLayout, QLabel, QScrollArea, QSizePolicy,
)

from netspeedtray.utils import styles as su
from netspeedtray.constants.styles import styles as tokens

_NAME_W = 180
_COUNT_W = 64


def _is_active(row: Dict[str, Any]) -> bool:
    """An app is "active" if it holds an established TCP connection OR talks to any off-box host —
    so UDP-only traffic (QUIC/HTTP3, DNS, calls, games) isn't wrongly shown idle just because UDP
    never reports ESTABLISHED."""
    return int(row.get("established_count", 0)) > 0 or int(row.get("host_count", 0)) > 0


def _row_key(row: Dict[str, Any]) -> str:
    return str(row.get("identity_key", row.get("display_name", "")))


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
                # Solid muted fill for idle (an alpha override washed it out on the light track);
                # floor at 4px (not a full-height circle) so small counts stay distinguishable.
                fill = QColor(c["accent"]) if self._active else QColor(c["text_secondary"])
                p.setBrush(fill)
                p.drawRoundedRect(0, int(y), max(fill_w, 4), h, r, r)
        finally:
            p.end()


class AppRow(QFrame):
    """One app: name (elided) + activity bar + live-connection count. Clickable: selecting a row
    opens the detail panel for that app (its hosts + live connections)."""

    clicked = pyqtSignal(str)   #: emitted with the row's identity_key on mouse press

    def __init__(self, i18n, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._i18n = i18n
        self._key = ""
        self._selected = False
        self._active = False
        self.setObjectName("appRow")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        c = su.semantic_colors()
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
        self._apply_row_style()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._key:
            self.clicked.emit(self._key)
        super().mousePressEvent(event)

    def set_selected(self, on: bool) -> None:
        if on != self._selected:
            self._selected = on
            self._apply_row_style()

    def _apply_row_style(self) -> None:
        # Selected row gets a subtle fill + a left accent stripe; hover gives a lighter fill so the
        # whole list reads as clickable. Re-resolved on each call so a theme switch is picked up.
        c = su.semantic_colors()
        if self._selected:
            self.setStyleSheet(
                f"#appRow {{ background: {c['subtle_fill']}; border-radius: {tokens.RADIUS_CONTROL}px;"
                f" border-left: 2px solid {c['accent']}; }}")
        else:
            self.setStyleSheet(
                f"#appRow {{ background: transparent; border-radius: {tokens.RADIUS_CONTROL}px;"
                f" border-left: 2px solid transparent; }}"
                f" #appRow:hover {{ background: {c['subtle_fill']}; }}")

    def update_row(self, row: Dict[str, Any], max_conn: int) -> None:
        self._key = str(row.get("identity_key", row.get("display_name", "")))
        name = str(row.get("display_name", "—"))
        conn = int(row.get("conn_count", 0))
        est = int(row.get("established_count", 0))
        hosts = int(row.get("host_count", 0))
        active = _is_active(row)
        self._active = active

        # Perceptual (log) scale, not linear: one system process (svchost, 70-300+ conns) would
        # otherwise squash every user app's bar to a stub. log1p keeps small counts differentiated.
        frac = (math.log1p(conn) / math.log1p(max_conn)) if max_conn > 0 else 0.0

        fm = QFontMetrics(self._name.font())
        self._name.setText(fm.elidedText(name, Qt.TextElideMode.ElideRight, _NAME_W))
        self._bar.set_value(frac, active)
        self._count.setText(str(conn))
        # Dim idle apps so the eye lands on what's actually talking. Re-resolve colours each tick so
        # a mid-session light/dark switch is picked up (the bar already re-reads per paint).
        c = su.semantic_colors()
        name_color = c["text_primary"] if active else c["text_secondary"]
        self._name.setStyleSheet(f"color: {name_color}; background: transparent;")
        self._apply_row_style()   # keep selection/hover styling fresh across theme changes
        self.setToolTip(f"{name} — {conn} connections, {est} active, {hosts} hosts")


class AppBarList(QWidget):
    """Header + summary + a scrollable, in-place-updated list of per-app activity rows. Rows are
    clickable; the currently-selected app's identity_key is surfaced via ``row_selected`` so an
    owning view (the Monitor's Network tab) can show that app's connection detail."""

    row_selected = pyqtSignal(str)   #: identity_key of the clicked app

    def __init__(self, i18n, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._i18n = i18n
        self._rows: Dict[str, AppRow] = {}
        self._row_data: Dict[str, Dict[str, Any]] = {}   # latest payload row per identity_key
        self._selected_key: Optional[str] = None
        self._scroll_pending = False   # one-shot: bring a freshly-selected row into view after relayout
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
        # AsNeeded (not AlwaysOff): the tab pins a min width, but if the user still drags the
        # master-detail handle narrow, recover the fixed count column by scrolling vs silent clipping.
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._content = QWidget()
        self._list_layout = QVBoxLayout(self._content)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(2)
        self._list_layout.addStretch(1)   # single trailing stretch, kept across refreshes
        self._scroll.setWidget(self._content)
        root.addWidget(self._scroll, 1)

    # --- inputs -----------------------------------------------------------------
    def set_payload(self, payload: Dict[str, Any]) -> None:
        rows: List[Dict[str, Any]] = payload.get("rows", []) if isinstance(payload, dict) else []
        # Refresh the keyed data store (used by the detail panel to re-read the selected app live).
        self._row_data = {_row_key(r): r for r in rows}
        sel = self._selected_key
        max_conn = max((int(r.get("conn_count", 0)) for r in rows), default=0)
        if rows:
            # STABLE order: selected-first (so the highlighted/detail row never slides), then
            # active-first, then by name — NOT by the second-to-second-jittery conn_count.
            rows = sorted(rows, key=lambda r: (0 if _row_key(r) == sel else 1,
                                               0 if _is_active(r) else 1,
                                               str(r.get("display_name", "")).casefold()))
        seen = set()
        ordered: List[AppRow] = []
        for r in rows:
            key = _row_key(r)
            seen.add(key)
            w = self._rows.get(key)
            if w is None:
                w = AppRow(self._i18n)
                w.clicked.connect(self._on_row_clicked)
                self._rows[key] = w
            w.update_row(r, max_conn)
            w.set_selected(key == sel)
            ordered.append(w)
        # Relayout: detach only the row widgets (keep the single trailing stretch), prune gone rows
        # (an EMPTY payload prunes them all — no stale last-seen apps linger), re-insert before the
        # stretch so no QSpacerItem is leaked per tick.
        for i in reversed(range(self._list_layout.count())):
            if self._list_layout.itemAt(i).widget() is not None:
                self._list_layout.takeAt(i)
        for key in [k for k in self._rows if k not in seen]:
            self._rows.pop(key).deleteLater()
        for j, w in enumerate(ordered):
            self._list_layout.insertWidget(j, w)
        self._summary.setText(self._summary_text(payload, rows) if rows else self._empty_text(payload))
        # One-shot after a fresh click: bring the now-pinned selected row into view (without yanking
        # the scroll back every 2 s tick if the user has since scrolled away to browse other apps).
        if self._scroll_pending:
            w = self._rows.get(sel) if sel else None
            if w is not None:
                self._scroll.ensureWidgetVisible(w)
            self._scroll_pending = False

    def _empty_text(self, payload: Dict[str, Any]) -> str:
        if isinstance(payload, dict) and payload.get("access_limited"):
            return self._tr("APP_ACTIVITY_ACCESS_LIMITED_MESSAGE",
                            "Limited permissions detected. Showing only processes you can access.")
        return self._tr("NO_APP_DATA_MESSAGE", "No application activity.")

    # --- selection --------------------------------------------------------------
    def _on_row_clicked(self, key: str) -> None:
        self._selected_key = key
        self._scroll_pending = True   # next relayout pins this row to the top + scrolls it into view
        for k, w in self._rows.items():
            w.set_selected(k == key)
        self.row_selected.emit(key)

    def get_row(self, key: str) -> Optional[Dict[str, Any]]:
        """The latest payload data for ``key`` (None once the app drops off the list)."""
        return self._row_data.get(key)

    def selected_key(self) -> Optional[str]:
        return self._selected_key

    def clear_selection(self) -> None:
        self._selected_key = None
        for w in self._rows.values():
            w.set_selected(False)

    def set_unavailable(self, reason: str) -> None:
        if reason == "rdp":
            self._summary.setText(self._tr(
                "APP_ACTIVITY_RDP_UNAVAILABLE_MESSAGE",
                "App Activity is unavailable in Remote Desktop sessions."))
        else:
            self._summary.setText(self._tr("NO_APP_DATA_MESSAGE", "No application activity."))

    def _summary_text(self, payload: Dict[str, Any], rows: List[Dict[str, Any]]) -> str:
        tmpl = self._tr("APP_ACTIVITY_SUMMARY_TEMPLATE",
                        "{app_count} apps · {active} active · {total_conn} connections · Updated {updated_at}")
        # Recompute "active" with the list's UDP-aware definition so the summary count agrees with the
        # rows' accent styling (the worker's active_app_count is TCP-ESTABLISHED-only).
        active = sum(1 for r in rows if _is_active(r))
        try:
            s = tmpl.format(
                app_count=int(payload.get("app_count", len(rows))),
                active=active,
                total_conn=int(payload.get("total_conn_count", 0)),
                updated_at=payload.get("updated_at", "--:--:--"))
        except Exception:
            s = ""
        if payload.get("access_limited"):
            s = f"{s} {self._tr('APP_ACTIVITY_SUMMARY_LIMITED_SUFFIX', '(limited access without admin rights)')}"
        return s

    def _tr(self, key: str, default: str) -> str:
        return str(getattr(self._i18n, key, default)) if self._i18n is not None else default
