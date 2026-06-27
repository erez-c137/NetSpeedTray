"""
PreviewWidget — a standalone, non-interactive render of the taskbar widget (refactor C1).

This is the keystone shared surface. It owns its own `WidgetRenderer` and draws through the
exact same `render_widget` path the live taskbar widget uses, so a preview matches the real
thing pixel-for-pixel for a given config + metrics. It has no taskbar coupling, no timers, no
monitoring — you hand it a config and a `WidgetMetrics` snapshot and it draws.

Consumers:
  - the Settings live-preview (shows the effect of a setting before Apply),
  - the unified Monitor window's Overview tile (a faithful thumbnail of the widget).
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPainter, QPaintEvent
from PyQt6.QtWidgets import QWidget

from netspeedtray.utils.widget_renderer import WidgetRenderer
from netspeedtray.utils.widget_paint import (
    WidgetMetrics, render_widget, font_from_config, demo_metrics,
)


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
        self.setFixedSize(width, height)

    # --- inputs -----------------------------------------------------------------
    def set_config(self, config: Dict[str, Any]) -> None:
        """Apply a new config dict and repaint (rebuilds the renderer's RenderConfig + font)."""
        self._config = dict(config)
        self._renderer.update_config(self._config)
        self._font = font_from_config(self._config)
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
        """'horizontal' or 'vertical' (small-taskbar two-row) layout."""
        self._layout_mode = mode
        self.update()

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
