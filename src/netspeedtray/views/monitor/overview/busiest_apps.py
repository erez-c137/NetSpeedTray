"""
BusiestAppsCard — the Overview's "top talkers" tile.

Reuses the Network tab's AppActivityFeed (the psutil connection sampler on its own QThread) and AppRow
(the per-app bar row) to show the handful of apps with the most live connections, active-first. Honest
by construction: Windows can't attribute bytes to a process without admin/ETW, so this ranks by live
connection count + hosts — never an invented per-app speed. Clicking the card (or any row) jumps to the
Network tab for the full per-app breakdown.

The feed only polls while the Overview is visible (the tab drives start/stop/teardown), so the default
screen costs one cheap connection sweep every couple of seconds and nothing while hidden.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QWidget, QFrame, QVBoxLayout, QHBoxLayout, QLabel

from netspeedtray.utils import styles as su
from netspeedtray.constants.styles import styles as tokens
from netspeedtray.views.monitor.network.app_feed import AppActivityFeed
from netspeedtray.views.monitor.network.app_list import AppRow, _is_active, _row_key

_MAX_ROWS = 5   # the panel hard-cap: a glance, not the full list (that's the Network tab)


class BusiestAppsCard(QFrame):
    """Top-N apps by live connections, active-first. Click -> Network tab."""

    go_to_network = pyqtSignal()

    def __init__(self, i18n, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._i18n = i18n
        c = su.semantic_colors()
        self.setObjectName("busiestCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)   # keyboard-reachable (was mouse-only)
        self.setAccessibleName(self._tr("MONITOR_TOP_TALKERS", "Top talkers"))
        self.setStyleSheet(
            f"#busiestCard {{ background: {c['subtle_fill']}; border-radius: {tokens.RADIUS_CARD}px;"
            f" border: 2px solid transparent; }}"
            f" #busiestCard:hover {{ background: {c['card_stroke']}; }}"
            f" #busiestCard:focus {{ border: 2px solid {c['accent']}; }}")

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(8)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        title = QLabel(self._tr("MONITOR_TOP_TALKERS", "Top talkers"))
        title.setFont(su.font(tokens.TYPE_BODY_STRONG))
        title.setStyleSheet(f"color: {c['text_primary']}; background: transparent;")
        head.addWidget(title)
        head.addStretch(1)
        more = QLabel(self._tr("MONITOR_VIEW_ALL_APPS", "All apps →"))
        more.setFont(su.font(tokens.TYPE_CAPTION))
        more.setStyleSheet(f"color: {c['text_secondary']}; background: transparent;")
        head.addWidget(more)
        root.addLayout(head)

        self._status = QLabel(self._tr("APP_ACTIVITY_LOADING_MESSAGE", "Loading application activity..."))
        self._status.setFont(su.font(tokens.TYPE_CAPTION))
        self._status.setStyleSheet(f"color: {c['text_secondary']}; background: transparent;")
        root.addWidget(self._status)

        # A fixed pool of reusable rows (shown/hidden as the top-N changes) — no per-tick widget churn.
        self._rows: List[AppRow] = []
        for _ in range(_MAX_ROWS):
            r = AppRow(self._i18n)
            r.clicked.connect(lambda _key: self.go_to_network.emit())
            r.setVisible(False)
            self._rows.append(r)
            root.addWidget(r)

        self._feed = AppActivityFeed(self)
        self._feed.payload_ready.connect(self._on_payload)
        self._feed.unavailable.connect(self._on_unavailable)

    # --- lifecycle (driven by OverviewTab) --------------------------------------
    def start(self) -> None:
        self._feed.start()

    def stop(self) -> None:
        self._feed.stop()

    def teardown(self) -> None:
        self._feed.teardown()

    # --- data -------------------------------------------------------------------
    def _on_payload(self, payload: Dict[str, Any]) -> None:
        rows: List[Dict[str, Any]] = payload.get("rows", []) if isinstance(payload, dict) else []
        # Top talkers: active first, then by live connection count, then name. Take the top N.
        ranked = sorted(rows, key=lambda r: (0 if _is_active(r) else 1,
                                             -int(r.get("conn_count", 0)),
                                             str(r.get("display_name", "")).casefold()))[:_MAX_ROWS]
        max_conn = max((int(r.get("conn_count", 0)) for r in ranked), default=0)
        if not ranked:
            self._status.setText(self._tr("NO_APP_DATA_MESSAGE", "No application activity."))
            self._status.setVisible(True)
            for w in self._rows:
                w.setVisible(False)
            return
        self._status.setVisible(False)
        for i, w in enumerate(self._rows):
            if i < len(ranked):
                w.update_row(ranked[i], max_conn)
                w.setVisible(True)
            else:
                w.setVisible(False)

    def _on_unavailable(self, reason: str) -> None:
        msg = (self._tr("APP_ACTIVITY_RDP_UNAVAILABLE_MESSAGE",
                        "App Activity is unavailable in Remote Desktop sessions.")
               if reason == "rdp" else self._tr("NO_APP_DATA_MESSAGE", "No application activity."))
        self._status.setText(msg)
        self._status.setVisible(True)
        for w in self._rows:
            w.setVisible(False)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            self.go_to_network.emit()
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.go_to_network.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def _tr(self, key: str, default: str) -> str:
        return str(getattr(self._i18n, key, default)) if self._i18n is not None else default
