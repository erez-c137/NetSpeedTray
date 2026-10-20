"""
PreviewWidget - a standalone, non-interactive render of the taskbar widget (refactor C1).

This is the keystone shared surface. It owns its own `WidgetRenderer` and draws through the
exact same `render_widget` path the live taskbar widget uses, so a preview matches the real
thing pixel-for-pixel for a given config + metrics. It has no taskbar coupling, no timers, no
monitoring - you hand it a config and a `WidgetMetrics` snapshot and it draws.

Consumers:
  - the Settings live-preview (shows the effect of a setting before Apply),
  - the unified Monitor window's Overview tile (a faithful thumbnail of the widget).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from PyQt6.QtCore import Qt, QRect
from PyQt6.QtGui import QImage, QPainter, QPaintEvent
from PyQt6.QtWidgets import QWidget

from netspeedtray import constants
from netspeedtray.utils.widget_renderer import WidgetRenderer
from netspeedtray.utils.widget_paint import (
    WidgetMetrics, render_widget, font_from_config, demo_metrics,
)

logger = logging.getLogger("NetSpeedTray.PreviewWidget")


class PreviewWidget(QWidget):
    """A faithful, inert preview of the speed widget for a given config + metrics snapshot."""

    DEFAULT_WIDTH = 260
    DEFAULT_HEIGHT = 44

    def __init__(self, config: Dict[str, Any], i18n, parent: Optional[QWidget] = None, *,
                 width: int = DEFAULT_WIDTH, height: int = DEFAULT_HEIGHT) -> None:
        super().__init__(parent)
        self.i18n = i18n
        self._config: Dict[str, Any] = dict(config)
        self._renderer = WidgetRenderer(self._config, i18n)
        self._metrics: WidgetMetrics = demo_metrics()
        self._cycle_mode: str = "network_only"
        self._layout_mode: str = "horizontal"
        self._font = font_from_config(self._config)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._min_width = width
        self._fitted_for: Optional[tuple] = None   # what the current width was measured for
        self.setFixedSize(width, height)
        self._fit_to_content()

    # --- inputs -----------------------------------------------------------------
    def set_config(self, config: Dict[str, Any]) -> None:
        """Apply a new config dict and repaint (rebuilds the renderer's RenderConfig + font)."""
        self._config = dict(config)
        self._renderer.update_config(self._config)
        self._font = font_from_config(self._config)
        self._fit_to_content()
        self.update()

    def set_metrics(self, metrics: WidgetMetrics) -> None:
        """Replace the drawn metrics snapshot and repaint."""
        self._metrics = metrics
        self.update()

    def set_cycle_mode(self, mode: str) -> None:
        """Which single metric to show when the config's display mode is 'cycle'."""
        self._cycle_mode = mode
        self.update()

    def set_layout_mode(self, mode: str) -> None:
        """'horizontal' (TOP/BOTTOM taskbar) or 'vertical' (LEFT/RIGHT side taskbar) - 'vertical' stacks
        the side_by_side segments top-to-bottom; every other mode draws the same either way."""
        self._layout_mode = mode
        self.update()

    def _fit_to_content(self) -> None:
        """Grow to the readout's measured width (never below the requested width), so a wider layout -
        side-by-side with separate upload/download graphs - shows whole instead of clipped. Measured
        through the same paint path on a throwaway surface - only when the config, layout or cycle phase
        changed since the last fit. The content bounds come from the segments' layout rects, not from
        painted pixels, so the probe only needs the preview's own size even when the content is wider."""
        fit_for = (dict(self._config), self._layout_mode, self._cycle_mode)
        if fit_for == self._fitted_for:
            return
        try:
            w, h = max(1, self._min_width), max(1, self.height())
            probe = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
            painter = QPainter(probe)
            try:
                render_widget(painter, QRect(0, 0, w, h), self._renderer, self._renderer.config, self._metrics,
                              layout_mode=self._layout_mode, cycle_mode=self._cycle_mode,
                              network_width=None, font=self._font)
            finally:
                painter.end()   # never leave the probe being painted - Qt aborts when it's freed mid-paint
            content = self._renderer.get_content_bounds().width()
            needed = content + constants.renderer.TEXT_MARGIN + constants.renderer.RIGHT_ANCHOR_PADDING
            self.setFixedWidth(max(self._min_width, needed))
            self._fitted_for = fit_for
        except Exception as e:
            # A preview must never break its host dialog: keep the current size, but say why.
            logger.warning("Could not fit the settings preview to its content: %s", e, exc_info=True)

    @property
    def config(self) -> Dict[str, Any]:
        return self._config

    # --- paint ------------------------------------------------------------------
    def paintEvent(self, event: QPaintEvent) -> None:
        if self._renderer is None or self._renderer.config is None:
            return
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            render_widget(
                painter, self.rect(), self._renderer, self._renderer.config, self._metrics,
                layout_mode=self._layout_mode, cycle_mode=self._cycle_mode,
                network_width=None, font=self._font,
            )
        except Exception:
            # A preview must never crash its host dialog; swallow and leave it blank.
            pass
        finally:
            if painter.isActive():
                painter.end()
