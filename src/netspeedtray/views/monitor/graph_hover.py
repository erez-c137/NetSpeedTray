"""
GraphHoverTooltip — a lightweight, stat-agnostic hover readout for the Monitor's shared graph.

The old standalone Graph window had a rich hover layer (crosshair + focus dots + tooltip + blit) hard-
wired to the network download/upload dual-axis. The Monitor graph is multi-stat (network OR cpu/gpu/ram),
so instead of porting that, this is a clean Qt-label tooltip that reads whatever lines are currently
plotted in the axis under the cursor and shows their value at the nearest time — so it works for the
network graph AND the hardware graphs, and survives a re-render (it reads the live Line2D data each move,
caches no artists, and triggers no matplotlib redraw, so it stays cheap).

matplotlib is already loaded by the time this attaches (GraphHost.ensure_loaded built the canvas), so the
top-level matplotlib import here is fine — this module is only imported from inside ensure_loaded().
"""
from __future__ import annotations

import logging
from typing import List, Optional, Tuple

import numpy as np
import matplotlib.dates as mdates
from PyQt6.QtCore import QObject, Qt
from PyQt6.QtWidgets import QLabel

from netspeedtray.utils import styles as su
from netspeedtray.constants.styles import styles as tokens
from netspeedtray.utils.helpers import format_speed

# Don't pop a tooltip when the cursor is miles from any point (in axis-fraction of the x-range).
_MAX_SNAP_FRAC = 0.04


class GraphHoverTooltip(QObject):
    """Follows the cursor over the graph and shows the value(s) at the nearest sample."""

    def __init__(self, host) -> None:
        super().__init__()
        self._host = host
        self.logger = logging.getLogger("NetSpeedTray.GraphHover")
        self._label: Optional[QLabel] = None
        self._cid_move = None
        self._cid_leave = None
        self._attached = False

    # ------------------------------------------------------------------ lifecycle
    def attach(self) -> None:
        """Create the tooltip label + connect canvas mouse events. Idempotent."""
        if self._attached:
            return
        canvas = getattr(self._host.renderer, "canvas", None)
        if canvas is None:
            return
        c = su.semantic_colors()
        self._label = QLabel(canvas)
        self._label.setObjectName("graphHover")
        self._label.setVisible(False)
        self._label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._label.setStyleSheet(
            f"#graphHover {{ background: {c['card_bg']}; color: {c['text_primary']};"
            f" border: 1px solid {c['card_stroke']}; border-radius: 6px; padding: 4px 8px; }}")
        self._label.setFont(su.font(tokens.TYPE_CAPTION))
        self._cid_move = canvas.mpl_connect("motion_notify_event", self._on_move)
        self._cid_leave = canvas.mpl_connect("axes_leave_event", lambda _e: self._hide())
        self._attached = True

    def detach(self) -> None:
        canvas = getattr(self._host.renderer, "canvas", None)
        try:
            if canvas is not None:
                if self._cid_move is not None:
                    canvas.mpl_disconnect(self._cid_move)
                if self._cid_leave is not None:
                    canvas.mpl_disconnect(self._cid_leave)
        except Exception:
            pass
        if self._label is not None:
            try:
                self._label.deleteLater()
            except Exception:
                pass
        self._label = None
        self._attached = False

    # ------------------------------------------------------------------ hover
    def _on_move(self, event) -> None:
        try:
            ax = event.inaxes
            if ax is None or event.xdata is None or self._label is None:
                self._hide()
                return
            xmin, xmax = ax.get_xlim()
            span = (xmax - xmin) or 1.0
            rows: List[Tuple[str, float, str]] = []   # (label, y, colour)
            nearest_x = None
            best_dx = None
            for line in ax.get_lines():
                lbl = line.get_label()
                if not lbl or lbl.startswith("_"):     # skip crosshairs / non-legend helper lines
                    continue
                xd = np.asarray(line.get_xdata(), dtype=float)
                yd = np.asarray(line.get_ydata(), dtype=float)
                if xd.size == 0 or yd.size != xd.size:
                    continue
                idx = int(np.argmin(np.abs(xd - event.xdata)))
                dx = abs(xd[idx] - event.xdata)
                if dx > span * _MAX_SNAP_FRAC:          # cursor too far from this line's samples
                    continue
                rows.append((str(lbl), float(yd[idx]), line.get_color()))
                if best_dx is None or dx < best_dx:
                    best_dx, nearest_x = dx, xd[idx]
            if not rows or nearest_x is None:
                self._hide()
                return
            self._label.setText(self._format(nearest_x, rows))
            self._label.adjustSize()
            self._place(event)
            self._label.setVisible(True)
        except Exception as e:
            self.logger.debug("hover move skipped: %s", e)
            self._hide()

    def _format(self, x_num: float, rows: List[Tuple[str, float, str]]) -> str:
        when = mdates.num2date(x_num).strftime("%H:%M:%S")
        is_net = self._host._current_stat == "network"
        parts = [f"<span style='color:{su.semantic_colors()['text_secondary']};'>{when}</span>"]
        for lbl, y, colour in rows:
            val = self._fmt_speed(y) if is_net else f"{y:.0f}%"
            parts.append(f"<span style='color:{colour};'>{lbl}</span> {val}")
        return "<br>".join(parts)

    def _fmt_speed(self, bps: float) -> str:
        cfg = self._host.config
        return format_speed(bps, self._host.i18n,
                            force_mega_unit=(cfg.get("speed_display_mode") == "always_mbps"),
                            decimal_places=int(cfg.get("decimal_places", 1)),
                            unit_type=cfg.get("unit_type", "bits_decimal"),
                            short_labels=cfg.get("short_unit_labels", False))

    def _place(self, event) -> None:
        """Position the label near the cursor (matplotlib pixels are bottom-left; Qt is top-left),
        flipping/clamping so it stays inside the canvas."""
        canvas = self._host.renderer.canvas
        cw, ch = canvas.width(), canvas.height()
        lw, lh = self._label.width(), self._label.height()
        x = int(event.x) + 14
        y = int(ch - event.y) - lh - 14
        if x + lw > cw:
            x = int(event.x) - lw - 14
        if y < 0:
            y = int(ch - event.y) + 14
        self._label.move(max(0, min(x, cw - lw)), max(0, min(y, ch - lh)))

    def _hide(self) -> None:
        if self._label is not None and self._label.isVisible():
            self._label.setVisible(False)
