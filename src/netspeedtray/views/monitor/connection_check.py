"""
ConnectionCheckSheet - the "Check connection" sheet (2.2 D3): a one-shot report you can paste.

Opened from the Overview's network hero (beside the latency pill) and from the Network tab header.
Modelled on StatsDetailSheet: the same dialog style, cards and stat cells, a Copy action. It starts a
`core.connection_check.ConnectionCheck` on open and fills in live (a `12 / 20` progress per hop), then
shows each hop's verdict and one plain attribution sentence from the D1 rule.

Privacy: the internet hop runs only when the public probe is on in Settings, or when the user ticks
"Also check the internet (sends pings to <host>)" here - a one-off that is never saved, with the host
named in the label. Changing the box restarts the check so it always matches what the box says.
Nothing the sheet measures is persisted; the standing probe is the record.
"""
from __future__ import annotations

import logging
import weakref
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QObject, Qt, pyqtSlot
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton,
    QToolButton, QVBoxLayout,
)

from netspeedtray import constants
from netspeedtray.constants.styles import styles as tokens
from netspeedtray.core import connection_check as CC
from netspeedtray.core import latency_probe as LP
from netspeedtray.utils import styles as su

_VERDICT_KEYS = {
    LP.VERDICT_GOOD: ("LATENCY_GOOD", "Good"),
    LP.VERDICT_OK: ("LATENCY_OK", "OK"),
    LP.VERDICT_SLOW: ("LATENCY_SLOW", "Slow"),
}
_ATTRIBUTION_KEYS = {
    LP.LOSS_NONE: ("CHECK_CONN_VERDICT_NONE", "No packet loss."),
    LP.LOSS_LOCAL: ("CHECK_CONN_VERDICT_LOCAL",
                    "Loss at your router points to Wi-Fi or the local network."),
    LP.LOSS_BEYOND_ROUTER: ("CHECK_CONN_VERDICT_BEYOND",
                            "Your router answered every ping, so the loss is beyond it - your ISP or "
                            "the path."),
    LP.LOSS_LOCAL_AND_BEYOND: ("CHECK_CONN_VERDICT_BOTH",
                               "Loss starts at your router (Wi-Fi or the local network) and grows "
                               "beyond it."),
    LP.LOSS_UNKNOWN: ("CHECK_CONN_VERDICT_UNKNOWN",
                      "Without replies from your router, this check can't tell whether loss is on "
                      "your Wi-Fi or local network or beyond it."),
}
# Stat cells per hop, in grid order: (field, i18n key, English fallback).
_CELLS = [
    ("sent", "CHECK_CONN_SENT", "Sent"),
    ("received", "CHECK_CONN_RECEIVED", "Received"),
    ("lost", "CHECK_CONN_LOST", "Lost"),
    ("loss_pct", "CHECK_CONN_LOSS", "Loss"),
    ("min_ms", "STAT_CELL_MIN", "Min"),
    ("median_ms", "STAT_CELL_MEDIAN", "Median"),
    ("p95_ms", "STAT_CELL_P95", "95th pct"),
    ("max_ms", "CHECK_CONN_MAX", "Max"),
    ("jitter_ms", "CHECK_CONN_JITTER", "Jitter"),
]


class _CheckRegistry(QObject):
    """Owns every ConnectionCheck worker until its thread has really ended, and every open sheet.

    A QThread destroyed while still running aborts the process ("QThread: Destroyed while thread is
    still running"), and a check on a dead link can run ~24 s. So a worker's lifetime is never tied to
    its sheet's: the registry keeps it until `finished`, then waits (already ended - instant) and
    deleteLater()s it. On application quit (tray Exit while the modal sheet is open: exec() returns
    without done()) and on the Monitor window's close, `stop_all()` closes the open sheets and stops
    + waits every live worker. Lives in the GUI thread, parented to the application."""

    def __init__(self, app: QCoreApplication) -> None:
        super().__init__(app)
        self._live: set = set()
        self._sheets: "weakref.WeakSet" = weakref.WeakSet()
        app.aboutToQuit.connect(self.stop_all)

    def track(self, worker) -> None:
        self._live.add(worker)
        worker.finished.connect(self._reap)   # queued: runs here, in the GUI thread

    def add_sheet(self, sheet) -> None:
        self._sheets.add(sheet)

    @pyqtSlot()
    def _reap(self) -> None:
        w = self.sender()
        for live in list(self._live):
            if live is w:
                live.wait(constants.network.latency.CHECK_STOP_WAIT_MS)   # emitted just before the end
                if not live.isRunning():
                    self._live.discard(live)
                    live.deleteLater()

    def live_workers(self) -> list:
        return [w for w in self._live if w.isRunning()]

    @pyqtSlot()
    def stop_all(self) -> None:
        for sheet in list(self._sheets):
            try:
                sheet.reject()
            except RuntimeError:
                pass   # its C++ side is already gone
        for w in list(self._live):
            w.stop()
        for w in list(self._live):
            w.wait(constants.network.latency.CHECK_STOP_WAIT_MS)
            if not w.isRunning():
                self._live.discard(w)


_registry: Optional[_CheckRegistry] = None


def _check_registry() -> Optional[_CheckRegistry]:
    global _registry
    app = QCoreApplication.instance()
    if app is None:
        return None
    if _registry is None:
        _registry = _CheckRegistry(app)
    return _registry


def stop_all_checks() -> None:
    """Close every open check sheet and stop + wait every running check (app quit, Monitor close)."""
    reg = _registry
    if reg is not None:
        reg.stop_all()


def _tr(i18n, key: str, default: str) -> str:
    return str(getattr(i18n, key, default)) if i18n is not None else default


class _HopCard(QFrame):
    """One hop: name + address on the left, live progress / verdict on the right, a 3x3 stat grid."""

    def __init__(self, title: str, i18n, c: Dict[str, str], parent=None) -> None:
        super().__init__(parent)
        self._i18n = i18n
        self._c = c
        self.setObjectName("hopCard")
        self.setStyleSheet(
            f"#hopCard {{ background: {c['card_bg']}; border: 1px solid {c['card_stroke']};"
            f" border-radius: 8px; }}")
        box = QVBoxLayout(self)
        box.setContentsMargins(16, 14, 16, 14)
        box.setSpacing(10)
        head = QHBoxLayout()
        self.title = QLabel(title)
        self.title.setFont(su.font(tokens.TYPE_BODY_STRONG))
        self.title.setStyleSheet(f"color: {c['text_primary']}; background: transparent;")
        self.address = QLabel("")
        self.address.setFont(su.font(tokens.TYPE_CAPTION))
        self.address.setStyleSheet(f"color: {c['text_secondary']}; background: transparent;")
        self.status = QLabel("")
        self.status.setFont(su.font(tokens.TYPE_BODY_STRONG))
        self.status.setTextFormat(Qt.TextFormat.RichText)
        self.status.setStyleSheet("background: transparent;")
        head.addWidget(self.title, 0, Qt.AlignmentFlag.AlignVCenter)
        head.addWidget(self.address, 0, Qt.AlignmentFlag.AlignVCenter)
        head.addStretch(1)
        head.addWidget(self.status, 0, Qt.AlignmentFlag.AlignVCenter)
        box.addLayout(head)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)
        self.values: Dict[str, QLabel] = {}
        for i, (field, key, default) in enumerate(_CELLS):
            cell, val = self._cell(_tr(i18n, key, default))
            self.values[field] = val
            grid.addWidget(cell, i // 3, i % 3)
        box.addLayout(grid)

        self.note = QLabel("")
        self.note.setFont(su.font(tokens.TYPE_CAPTION))
        self.note.setWordWrap(True)
        self.note.setStyleSheet(f"color: {c['text_secondary']}; background: transparent;")
        self.note.setVisible(False)
        box.addWidget(self.note)

    def _cell(self, caption: str) -> Tuple[QFrame, QLabel]:
        c = self._c
        cell = QFrame()
        cell.setStyleSheet(f"QFrame {{ background: {c['subtle_fill']}; border-radius: 6px; }}")
        v = QVBoxLayout(cell)
        v.setContentsMargins(12, 8, 12, 8)
        v.setSpacing(1)
        val = QLabel("-")
        if QApplication.layoutDirection() == Qt.LayoutDirection.RightToLeft:
            # Digits carry no direction of their own, so an RTL UI would still left-align "20" / "4 ms"
            # under a right-aligned Hebrew caption; pin the value to the caption's edge.
            val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignAbsolute
                             | Qt.AlignmentFlag.AlignVCenter)
        val.setFont(su.font(tokens.TYPE_SUBTITLE))
        val.setStyleSheet(f"color: {c['text_primary']}; background: transparent;")
        cap = QLabel(caption)
        cap.setFont(su.font(tokens.TYPE_CAPTION))
        cap.setStyleSheet(f"color: {c['text_secondary']}; background: transparent;")
        v.addWidget(val)
        v.addWidget(cap)
        return cell, val


class ConnectionCheckSheet(QDialog):
    """The modal check sheet. ``gateway_ip`` is the standing probe's router (None = look it up)."""

    def __init__(self, config: Dict[str, Any], i18n, gateway_ip: Optional[str] = None,
                 app_version: str = "", autostart: bool = True, parent=None) -> None:
        super().__init__(parent)
        self._config = config
        self._i18n = i18n
        self._app_version = app_version
        self._gateway_ip = gateway_ip
        self._include_internet = False          # the one-off tick; never written to config
        self._worker: Optional[CC.ConnectionCheck] = None
        self._echoes: Dict[str, List[Optional[float]]] = {}
        self._stats: Dict[str, CC.HopStats] = {}
        self._state = "idle"                    # idle / running / done / stopped
        self._started_at: Optional[datetime] = None
        self._resolved_gw: Optional[str] = gateway_ip
        self._anchor_host: Optional[str] = None
        self.logger = logging.getLogger("NetSpeedTray.ConnectionCheck")
        lat = constants.network.latency
        self._total = lat.CHECK_ECHOES

        self.setModal(True)
        # Release the sheet once closed (workers are owned by the registry, not by the sheet).
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        reg = _check_registry()
        if reg is not None:
            reg.add_sheet(self)
        self.setWindowTitle(self._tr("CHECK_CONN_TITLE", "Connection check"))
        self.setStyleSheet(su.dialog_style())
        self.setMinimumWidth(440)

        c = su.semantic_colors()
        self._c = c
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 18)
        root.setSpacing(14)

        title = QLabel(self._tr("CHECK_CONN_TITLE", "Connection check"))
        title.setFont(su.font(tokens.TYPE_TITLE))
        title.setStyleSheet(f"color: {c['text_primary']}; background: transparent;")
        root.addWidget(title)

        self._cards = {
            CC.HOP_GATEWAY: _HopCard(self._tr("CHECK_CONN_ROUTER", "Router"), i18n, c),
            CC.HOP_ANCHOR: _HopCard(self._tr("LATENCY_LABEL", "Internet"), i18n, c),
        }
        root.addWidget(self._cards[CC.HOP_GATEWAY])
        root.addWidget(self._cards[CC.HOP_ANCHOR])

        # One plain sentence from the D1 attribution rule, right under the figures it explains.
        self._sentence = QLabel("")
        self._sentence.setFont(su.font(tokens.TYPE_BODY))
        self._sentence.setWordWrap(True)
        self._sentence.setStyleSheet(f"color: {c['text_secondary']}; background: transparent;")
        self._sentence.setVisible(False)
        root.addWidget(self._sentence)

        # The one-off internet tick - offered only while the public probe is off in Settings.
        self._public_on = bool(config.get("latency_public_enabled", False))
        host = str(config.get("latency_public_host", "1.1.1.1") or "1.1.1.1")
        self._tick = QCheckBox(self._tr("CHECK_CONN_INCLUDE_INTERNET",
                                        "Also check the internet (sends pings to {host})").format(host=host))
        self._tick.setChecked(False)
        self._tick.toggled.connect(self._on_tick)
        self._hint = QLabel(self._tr(
            "CHECK_CONN_HINT", "To measure the internet all the time, turn on “{setting}” in Settings."
        ).format(setting=self._tr("CONN_PUBLIC_LABEL", "Also measure true internet latency")))
        self._hint.setFont(su.font(tokens.TYPE_CAPTION))
        self._hint.setWordWrap(True)
        self._hint.setStyleSheet(f"color: {c['text_secondary']}; background: transparent;")
        root.addWidget(self._tick)
        root.addWidget(self._hint)
        self._tick.setVisible(not self._public_on)
        self._hint.setVisible(not self._public_on)

        root.addSpacing(2)
        row = QHBoxLayout()
        row.setSpacing(8)
        self._copy_btn = QPushButton(self._tr("STATS_DETAIL_COPY", "Copy figures"))
        self._copy_btn.setStyleSheet(su.button_style(accent=False))
        self._copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._copy_btn.clicked.connect(self._copy)
        self._run_btn = QPushButton(self._tr("CHECK_CONN_AGAIN", "Check again"))
        self._run_btn.setStyleSheet(su.button_style(accent=True))
        self._run_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._run_btn.clicked.connect(self._on_run_clicked)
        row.addWidget(self._copy_btn)
        row.addStretch(1)
        row.addWidget(self._run_btn)
        root.addLayout(row)

        self._reset_view()
        if autostart:
            self.start_check()

    # ------------------------------------------------------------------ run control
    def include_internet(self) -> bool:
        return self._public_on or self._include_internet

    def start_check(self) -> None:
        """(Re)start the check with the hops the settings and the tick allow."""
        self._stop_worker(wait=False)
        self._anchor_host = CC.anchor_host_for_check(self._config, self._include_internet)
        self._echoes = {CC.HOP_GATEWAY: []}
        if self._anchor_host:
            self._echoes[CC.HOP_ANCHOR] = []
        self._stats = {}
        self._started_at = datetime.now()
        self._state = "running"
        self._reset_view()
        w = CC.ConnectionCheck(self._gateway_ip, self._anchor_host)
        w.gateway_resolved.connect(self._on_gateway)
        w.echo_done.connect(self._on_echo)
        w.check_finished.connect(self._on_finished)
        self._worker = w
        reg = _check_registry()
        if reg is not None:
            reg.track(w)   # outlives the sheet until the thread has ended
        w.start()

    def stop_check(self) -> None:
        if self._state == "running":
            self._stop_worker(wait=False)
            self._state = "stopped"
            self._refresh()

    def _stop_worker(self, wait: bool) -> None:
        w = self._worker
        if w is None:
            return
        self._worker = None
        if sip.isdeleted(w):
            return   # finished and already released by the registry
        for sig, slot in ((w.gateway_resolved, self._on_gateway), (w.echo_done, self._on_echo),
                          (w.check_finished, self._on_finished)):
            try:
                sig.disconnect(slot)
            except Exception:
                pass
        w.stop()   # the registry keeps the thread alive until it has really ended
        if wait:
            w.wait(constants.network.latency.CHECK_STOP_WAIT_MS)

    def _on_run_clicked(self) -> None:
        if self._state == "running":
            self.stop_check()
        else:
            self.start_check()

    def _on_tick(self, checked: bool) -> None:
        self._include_internet = bool(checked)
        self.start_check()   # the check always matches the box: unticking stops the public pings now

    # ------------------------------------------------------------------ worker signals
    def _on_gateway(self, ip) -> None:
        self._resolved_gw = ip
        self._refresh()

    def _on_echo(self, hop: str, index: int, ms) -> None:
        if hop in self._echoes:
            self._echoes[hop].append(None if ms is None else float(ms))
            self._refresh()

    def _on_finished(self, stats) -> None:
        self._worker = None   # done; the registry deleteLater()s it once its thread has ended
        self._stats = dict(stats or {})
        self._state = "done"
        self._refresh()

    # ------------------------------------------------------------------ view
    def _hop_stats(self, hop: str) -> Optional[CC.HopStats]:
        if hop in self._stats:
            return self._stats[hop]
        if hop not in self._echoes:
            return None
        echoes = self._echoes[hop]
        return CC.hop_stats([e for e in echoes if e is not None], len(echoes))

    def _reset_view(self) -> None:
        for card in self._cards.values():
            for v in card.values.values():
                v.setText("-")
            card.status.setText("")
            card.note.setVisible(False)
        self._refresh()

    def _refresh(self) -> None:
        c = self._c
        anchor_on = CC.HOP_ANCHOR in self._echoes
        self._cards[CC.HOP_ANCHOR].setVisible(anchor_on)
        self._cards[CC.HOP_ANCHOR].address.setText(self._anchor_host or "")
        gw_card = self._cards[CC.HOP_GATEWAY]
        gw_card.address.setText(self._resolved_gw or "")
        for hop in self._echoes:
            card = self._cards[hop]
            st = self._hop_stats(hop)
            if st is None:
                continue
            if st.sent:
                for field, _k, _d in _CELLS:
                    card.values[field].setText(self._fmt_cell(field, getattr(st, field)))
            card.status.setText(self._status_html(hop, st))
            silent = (hop == CC.HOP_GATEWAY and self._state == "done" and self._resolved_gw
                      and st.sent and not st.answered)
            card.note.setText(self._tr("CHECK_CONN_ROUTER_SILENT",
                                       "Your router doesn't answer pings - some never do. "
                                       "That alone is not packet loss."))
            card.note.setVisible(bool(silent))
        if self._state == "done" and not self._resolved_gw:
            gw_card.status.setText(
                f"<span style='color:{c['text_secondary']};'>"
                f"{self._tr('CHECK_CONN_NO_ROUTER', 'No router found')}</span>")

        sentence = self._attribution_text()
        self._sentence.setText(sentence)
        self._sentence.setVisible(bool(sentence))
        self._run_btn.setText(self._tr("CHECK_CONN_STOP", "Stop") if self._state == "running"
                              else self._tr("CHECK_CONN_AGAIN", "Check again"))
        self._copy_btn.setEnabled(self._state in ("done", "stopped"))
        self.adjustSize()

    def _status_html(self, hop: str, st: CC.HopStats) -> str:
        sub = self._c["text_secondary"]
        if self._state == "running":
            # An LTR embedding keeps "12 / 20" reading left to right in an RTL (Hebrew) layout, not "20 / 12".
            done = len(self._echoes.get(hop, []))
            return f"<span style='color:{sub};'>\u202a{done} / {self._total}\u202c</span>"
        if self._state == "stopped":
            return f"<span style='color:{sub};'>{self._tr('CHECK_CONN_STOPPED', 'Stopped')}</span>"
        if not st.sent:
            return ""
        if st.verdict is None:
            return f"<span style='color:{sub};'>{self._tr('CHECK_CONN_NO_REPLY', 'No reply')}</span>"
        key, default = _VERDICT_KEYS[st.verdict]
        color = constants.network.latency.VERDICT_COLORS[st.verdict]
        return f"<span style='color:{color};'>{self._tr(key, default)}</span>"

    def attribution_code(self) -> Optional[str]:
        if self._state != "done" or CC.HOP_GATEWAY not in self._stats:
            return None
        return CC.check_attribution(self._stats[CC.HOP_GATEWAY], self._stats.get(CC.HOP_ANCHOR))

    def _attribution_text(self) -> str:
        code = self.attribution_code()
        if code is None:
            return ""
        key, default = _ATTRIBUTION_KEYS[code]
        return self._tr(key, default)

    # ------------------------------------------------------------------ formatting
    def _num(self, value: float, places: int = 1) -> str:
        s = f"{value:.{places}f}"
        sep = getattr(self._i18n, "DECIMAL_SEPARATOR", ".")
        return s.replace(".", sep) if sep and sep != "." else s

    def _fmt_cell(self, field: str, v) -> str:
        if v is None:
            return "-"
        if field in ("sent", "received", "lost"):
            return str(int(v))
        if field == "loss_pct":
            return f"{v:.0f}%" if abs(v - round(v)) < 0.05 else f"{self._num(v)}%"
        if field == "jitter_ms":
            return f"{self._num(v)} ms" if v < 10 else f"{v:.0f} ms"
        # RTTs: Windows reports whole milliseconds, so a LAN reply often reads 0 - say "<1 ms", as ping does.
        if v < 1:
            return "<1 ms"
        return f"{v:.0f} ms" if abs(v - round(v)) < 0.05 else f"{self._num(v)} ms"

    # ------------------------------------------------------------------ copy
    def copy_text(self) -> str:
        """The paste-able report: local time, app version, both hops' rows, router IP, anchor host."""
        stamp = (self._started_at or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
        version = f" {self._app_version}" if self._app_version else ""
        lines = [f"NetSpeedTray{version} - {self._tr('CHECK_CONN_TITLE', 'Connection check')} - {stamp}"]
        if self._state == "stopped":
            lines[0] += f" ({self._tr('CHECK_CONN_STOPPED', 'Stopped')})"
        hops = [(CC.HOP_GATEWAY, self._tr("CHECK_CONN_ROUTER", "Router"), self._resolved_gw),
                (CC.HOP_ANCHOR, self._tr("LATENCY_LABEL", "Internet"), self._anchor_host)]
        for hop, name, addr in hops:
            st = self._hop_stats(hop)
            if hop == CC.HOP_ANCHOR and st is None:
                lines.append(f"{name}: {self._tr('CHECK_CONN_NOT_CHECKED', 'not checked')}")
                continue
            if hop == CC.HOP_GATEWAY and not addr:
                lines.append(f"{name}: {self._tr('CHECK_CONN_NO_ROUTER', 'No router found')}")
                continue
            head = f"{name} {addr}" if addr else name
            cells = "  ".join(f"{_tr(self._i18n, key, default)}: {self._fmt_cell(field, getattr(st, field))}"
                              for field, key, default in _CELLS)
            verdict = ""
            if self._state == "done" and st is not None and st.sent:
                if st.verdict is None:
                    verdict = f"  [{self._tr('CHECK_CONN_NO_REPLY', 'No reply')}]"
                else:
                    k, d = _VERDICT_KEYS[st.verdict]
                    verdict = f"  [{self._tr(k, d)}]"
            lines.append(f"{head}{verdict}\n  {cells}")
        sentence = self._attribution_text()
        if sentence:
            lines.append(sentence)
        return "\n".join(lines)

    def _copy(self) -> None:
        QApplication.clipboard().setText(self.copy_text())

    # ------------------------------------------------------------------ lifecycle
    def done(self, result: int) -> None:  # noqa: D401 (Qt override: accept/reject/close all land here)
        self._stop_worker(wait=True)   # any earlier (restarted) worker is already stopped; the
        super().done(result)           # registry holds it until its one in-flight echo returns

    def _tr(self, key: str, default: str) -> str:
        return _tr(self._i18n, key, default)


def make_check_button(i18n) -> QToolButton:
    """The "Check connection" action, styled like the Overview header's Export action - the same
    control on the network hero and in the Network tab header."""
    c = su.semantic_colors()
    btn = QToolButton()
    btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
    btn.setIcon(su.fluent_icon(0xE9D9, 16, c['text_secondary']))   # Fluent "Diagnostic"
    btn.setText(_tr(i18n, "CHECK_CONN_ACTION", "Check connection"))
    btn.setToolTip(_tr(i18n, "CHECK_CONN_ACTION_TIP",
                       "Ping your router 20 times (and the internet, if you choose) and show what came back"))
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    r = tokens.RADIUS_CONTROL
    btn.setStyleSheet(
        f"QToolButton {{ background: transparent; color: {c['text_secondary']};"
        f" border: 1px solid {c['card_stroke']}; border-radius: {r}px; padding: 4px 12px; }}"
        f" QToolButton:hover {{ color: {c['text_primary']}; border-color: {c['accent']}; }}"
        f" QToolButton:focus {{ border-color: {c['accent']}; }}")
    return btn


def open_connection_check(main_widget, config: Dict[str, Any], i18n, parent=None) -> None:
    """Open the sheet modally - the one entry point the Overview hero and the Network header share.
    The router address comes from the standing probe when it has one (no second WMI lookup)."""
    probe = getattr(main_widget, "latency_probe", None)
    gateway = getattr(probe, "gateway", None) if probe is not None else None
    try:
        from netspeedtray import __version__
    except Exception:
        __version__ = ""
    try:
        sheet = ConnectionCheckSheet(config, i18n, gateway_ip=gateway, app_version=__version__,
                                     parent=parent)
        sheet.exec()
    except Exception as e:
        logging.getLogger("NetSpeedTray.ConnectionCheck").error(
            "Could not open the connection check: %s", e, exc_info=True)
