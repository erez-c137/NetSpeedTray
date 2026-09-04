"""
GraphHoverTooltip - a lightweight, stat-agnostic hover readout for the Monitor's shared graph.

The old standalone Graph window had a rich hover layer (crosshair + focus dots + tooltip + blit) hard-
wired to the network download/upload dual-axis. The Monitor graph is multi-stat (network OR cpu/gpu/ram),
so instead of porting that, this is a clean Qt-label tooltip that reads whatever lines are currently
plotted in the axis under the cursor and shows their value at the nearest time - so it works for the
network graph AND the hardware graphs, and survives a re-render (it reads the live Line2D data each move,
caches no artists, and triggers no matplotlib redraw, so it stays cheap).

matplotlib is already loaded by the time this attaches (GraphHost.ensure_loaded built the canvas), so the
top-level matplotlib import here is fine - this module is only imported from inside ensure_loaded().
"""
from __future__ import annotations

import logging
from typing import List, Optional, Tuple, Dict

import numpy as np
import matplotlib.dates as mdates
from PyQt6.QtCore import QObject, Qt
from PyQt6.QtWidgets import QLabel

from netspeedtray.utils import styles as su
from netspeedtray.constants.styles import styles as tokens
from netspeedtray.utils.helpers import format_speed
from netspeedtray import constants

# Don't pop a tooltip when the cursor is miles from any point (in axis-fraction of the x-range).
_MAX_SNAP_FRAC = 0.04
_PIXEL_SLACK = 1.5      # the cursor's pixel column plus half a pixel each side - what the eye reads


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
            rows, nearest_x = self._rows_at(ax, event.xdata)
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

    def _rows_at(self, ax, x: float) -> Tuple[List[Tuple[str, float, str]], Optional[float]]:
        """The value of every labelled series under the cursor, plus the x of the sample reported.

        "Under the cursor" means the highest sample within the pixel column the cursor is on
        (plus a pixel of slack), not the sample nearest `x`. At the one-hour window a pixel is
        about two seconds and a spike is one sample wide, so nearest-x picks the spike's neighbour
        half the time: the owner hovered a 12 Mbps spike and read 0.8. The line is drawn through
        the peak, so the peak is the honest readout. Where nothing falls inside the column (sparse
        data) the nearest sample within the snap distance is used, as before.

        Lines without a legend label (matplotlib's `_childN`) are helpers - crosshairs and the
        renderer's dashed zero bridges across gaps - and are skipped, so the hover never reports a
        synthesized zero as a measurement. The renderer draws one series as several segments when
        the data has gaps; those share a label and collapse to ONE row. X is read in axis units
        (`orig=False`), which is what `event.xdata` is in - the original data are datetimes.
        """
        xmin, xmax = ax.get_xlim()
        span = (xmax - xmin) or 1.0
        width_px = float(getattr(ax.bbox, "width", 0.0) or 0.0)
        column = span / width_px * _PIXEL_SLACK if width_px > 0 else 0.0   # data units per pixel column
        # label -> (rank, y, color, x_at, dx); rank sorts in-column peaks before nearest fallbacks
        best: Dict[str, Tuple[Tuple[int, float], float, str, float, float]] = {}
        for line in ax.get_lines():
            lbl = line.get_label()
            if not lbl or str(lbl).startswith("_"):
                continue
            xd = np.asarray(line.get_xdata(orig=False), dtype=float)
            yd = np.asarray(line.get_ydata(orig=False), dtype=float)
            if xd.size == 0 or yd.size != xd.size:
                continue
            dist = np.abs(xd - x)
            in_column = np.flatnonzero(dist <= column) if column > 0 else np.empty(0, dtype=int)
            if in_column.size:
                idx = int(in_column[np.argmax(yd[in_column])])       # the peak the line draws here
                rank = (0, -float(yd[idx]))
            else:
                idx = int(np.argmin(dist))
                if dist[idx] > span * _MAX_SNAP_FRAC:               # cursor too far from this line
                    continue
                rank = (1, float(dist[idx]))
            cur = best.get(str(lbl))
            if cur is None or rank < cur[0]:
                best[str(lbl)] = (rank, float(yd[idx]), line.get_color(), float(xd[idx]), float(dist[idx]))
        if not best:
            return [], None
        rows = [(lbl, y, color) for lbl, (_r, y, color, _xa, _dx) in best.items()]
        at = min(best.values(), key=lambda t: t[0])[3]                   # the reported peak's time
        return rows, at

    def _format(self, x_num: float, rows: List[Tuple[str, float, str]]) -> str:
        when = mdates.num2date(x_num).strftime("%H:%M:%S")
        is_net = self._host._current_stat == "network"
        parts = [f"<span style='color:{su.semantic_colors()['text_secondary']};'>{when}</span>"]
        for lbl, y, color in rows:
            # The network axes are plotted in Mbps (the renderer converts bytes/sec before plotting)
            # but format_speed() takes bytes/sec - convert back, or every readout is off by 125,000x.
            val = self._fmt_speed(y * constants.network.units.MEGA_DIVISOR / constants.network.units.BITS_PER_BYTE) if is_net else f"{y:.0f}%"
            parts.append(f"<span style='color:{color};'>{lbl}</span> {val}")
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
