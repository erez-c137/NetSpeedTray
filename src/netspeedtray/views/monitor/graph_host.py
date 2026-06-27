"""
GraphHost — reuse the existing graph engine (GraphRenderer / GraphDataWorker / GraphCoordinator)
behind ONE lazily-imported, reparented matplotlib canvas.

The Monitor's chart tabs (Network now, Hardware later) share a single GraphHost: one renderer, one
worker thread, one coordinator, one canvas that gets reparented into whichever tab is active. The
heavy graph package (and matplotlib) is imported only inside ensure_loaded(), so a glance at the
matplotlib-free Overview tab never pays for it.

GraphHost presents the exact host surface GraphCoordinator drives (renderer / ui / interaction /
config_handler / _is_live_update_enabled / update_graph / update_graph_range), so coordinator.py,
worker.py and renderer.py are reused **byte-for-byte**. The window-specific glue (overlay stat
cards, zoom, tooltips) is intentionally NOT reused — the Monitor shows stats in its tab header, so
GraphHost writes its own clean, render-only data callback.

IMPORT FIREWALL: this module imports nothing from netspeedtray.views.graph at module scope. Every
graph import is lazy (inside ensure_loaded / the refresh methods).
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, Optional

from PyQt6.QtCore import QObject, QThread, pyqtSignal
from PyQt6.QtWidgets import QWidget, QVBoxLayout


# --- Minimal stand-ins for the host surface GraphCoordinator pokes at -------------------------
# The Monitor surfaces status/stats in its own tab header and disables graph zoom, so these are
# inert. They exist only so coordinator.py can stay unchanged.

class _BtnShim:
    def show(self) -> None: ...
    def hide(self) -> None: ...


class _UiShim:
    """Stands in for GraphWindowUI: the coordinator calls a few status/overlay methods on .ui."""
    def __init__(self) -> None:
        self.reset_zoom_btn = _BtnShim()

    def set_status(self, *_a, **_k) -> None: ...
    def show_zoom_hint(self, *_a, **_k) -> None: ...
    def reposition_overlay_elements(self, *_a, **_k) -> None: ...


class _InteractionShim:
    """Stands in for GraphInteractionHandler. Zoom/tooltips are off in the Monitor graph (for now)."""
    def clear_selection(self) -> None: ...


class _ConfigHandlerShim:
    """Persists the coordinator's config updates (e.g. the chosen timeline period)."""
    def __init__(self, host: "GraphHost") -> None:
        self._host = host

    def queue_config_update(self, updates: Dict[str, Any]) -> None:
        try:
            mw = self._host._main_widget
            mw.config.update(updates)
            mw.config_manager.save(mw.config)
        except Exception:
            pass


class GraphHost(QObject):
    """One shared, lazily-loaded graph engine + canvas for the Monitor's chart tabs."""

    # MUST stay pyqtSignal(object), NOT pyqtSignal(DataRequest): importing DataRequest at class
    # scope would run views.graph.__init__ (which eagerly imports GraphWindow -> matplotlib) and
    # break the lazy firewall. Every graph import in this module is method-scoped for the same reason.
    request_data_processing = pyqtSignal(object)

    def __init__(self, main_widget, config: Dict[str, Any], i18n,
                 session_start_time: Optional[datetime] = None, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._main_widget = main_widget
        self.config = config
        self.i18n = i18n
        self.session_start_time = session_start_time or datetime.now()
        self.logger = logging.getLogger("NetSpeedTray.GraphHost")
        self._loaded = False
        self._is_closing = False

        # --- the exact state surface GraphCoordinator reads/writes on its host ---
        self._is_live_update_enabled = bool(config.get("live_update", True))
        self._history_period_value = int(config.get("history_period_slider_value", 2))
        self._current_request_id = 0
        self._last_processed_id = -1
        self.interface_filter = None          # None / "all" -> every interface
        self._show_loading_status = bool(config.get("show_loading", False))
        self._current_stat = "network"        # set per active tab via attach_to()
        self._accept_from_seq = 0             # drop in-flight results from a previous stat (cross-tab)
        self._cached_boot_time = None         # fetched once for the uptime range (mirrors GraphWindow)
        self._cached_earliest_db = None

        # shims the coordinator drives (matplotlib-free)
        self.ui = _UiShim()
        self.interaction = _InteractionShim()
        self.config_handler = _ConfigHandlerShim(self)

        # built on first ensure_loaded()
        self.renderer = None
        self.worker = None
        self.coordinator = None
        self._thread: Optional[QThread] = None
        self._canvas_container: Optional[QWidget] = None

    # ------------------------------------------------------------------ lazy load

    def ensure_loaded(self) -> None:
        """THE lazy-import point: matplotlib + the graph engine enter here, once per session."""
        if self._loaded:
            return
        from netspeedtray.views.graph.renderer import GraphRenderer
        from netspeedtray.views.graph.worker import GraphDataWorker
        from netspeedtray.views.graph.coordinator import GraphCoordinator

        # The renderer builds the canvas inside a container; we reparent renderer.canvas per tab.
        self._canvas_container = QWidget()
        _cl = QVBoxLayout(self._canvas_container)
        _cl.setContentsMargins(0, 0, 0, 0)
        self.renderer = GraphRenderer(self._canvas_container, self.i18n, self.logger)
        self.renderer.apply_theme(bool(self.config.get("dark_mode", True)))

        # Worker on its own QThread.
        self._thread = QThread()
        self.worker = GraphDataWorker(self._main_widget.widget_state)
        self.worker.moveToThread(self._thread)
        self.worker.data_ready.connect(self._on_data_ready)
        self.worker.error.connect(lambda msg: self.logger.debug("graph worker error: %s", msg))
        self.request_data_processing.connect(self.worker.process_data)
        self._thread.start()

        # Coordinator drives THIS object as its host (unchanged coordinator.py).
        self.coordinator = GraphCoordinator(self)
        self._loaded = True

    # ------------------------------------------------------------------ canvas hosting

    def attach_to(self, plot_slot_layout, stat_type: str) -> None:
        """Reparent the single canvas into ``plot_slot_layout`` and refresh for ``stat_type``."""
        self.ensure_loaded()
        self._current_stat = stat_type
        canvas = self.renderer.canvas
        canvas.setParent(None)
        plot_slot_layout.addWidget(canvas)
        canvas.show()
        self.update_graph(show_loading=True)
        # Anchor the dedup floor: ignore any in-flight result requested before this (maybe-different)
        # stat, so an old single-stat reply can't paint the newly-activated tab.
        self._accept_from_seq = self._current_request_id

    def start_realtime(self) -> None:
        self.ensure_loaded()
        try:
            self.coordinator.start_realtime()
        except Exception as e:
            self.logger.debug("start_realtime failed: %s", e)

    def stop_realtime(self) -> None:
        try:
            if self.coordinator is not None:
                self.coordinator.stop_realtime()
        except Exception:
            pass

    def set_period(self, period_value: int) -> None:
        """Change the timeline window. No caller yet — the period control is the 5.1 Network header.
        When wired, prefer routing through self.coordinator.handle_timeline_change(period_key): it
        already persists config, debounces rapid clicks, and resets the renderer's sticky y-limits.
        This direct path is fine for a single programmatic change but skips that debounce/reset."""
        self._history_period_value = int(period_value)
        self.config_handler.queue_config_update({"history_period_slider_value": self._history_period_value})
        self.update_graph(show_loading=False)

    # ------------------------------------------------------------------ host surface: refresh

    def update_graph(self, show_loading: bool = True) -> None:
        """Build a DataRequest for the active stat_type + dispatch to the worker thread."""
        if self._is_closing or not self._loaded:
            return
        from netspeedtray.views.graph.request import DataRequest
        from netspeedtray.views.graph.logic import GraphLogic
        start, end = self._time_range()
        period_key = GraphLogic.get_period_key(self._history_period_value)
        self._current_request_id += 1
        request = DataRequest(
            start_time=start,
            end_time=end,
            interface_name=None if self.interface_filter in (None, "all") else self.interface_filter,
            is_session_view=(period_key == "TIMELINE_SESSION"),
            sequence_id=self._current_request_id,
            stat_type=self._current_stat,
        )
        self.request_data_processing.emit(request)

    def update_graph_range(self, start, end) -> None:
        # Zoom is disabled in the Monitor graph for now; a range request just refreshes the view.
        self.update_graph(show_loading=False)

    def _time_range(self):
        from netspeedtray.views.graph.logic import GraphLogic
        period_key = GraphLogic.get_period_key(self._history_period_value)
        # Fetch boot/earliest ONCE for the uptime range. These are UI-thread DB calls and _time_range
        # runs on every refresh + realtime tick — GraphWindow caches them the same way (and the cache
        # is naturally fresh each session, since GraphHost is recreated per Monitor window).
        if period_key == "TIMELINE_SYSTEM_UPTIME" and self._cached_boot_time is None:
            try:
                self._cached_boot_time = GraphLogic.get_boot_time()
                self._cached_earliest_db = self._main_widget.widget_state.get_earliest_data_timestamp()
            except Exception:
                self._cached_boot_time = self._cached_earliest_db = None
        return GraphLogic.get_time_range(self._history_period_value, self.session_start_time,
                                         self._cached_boot_time, self._cached_earliest_db)

    def _on_data_ready(self, data, total_up, total_down, sequence_id) -> None:
        """Render-only callback (no overlay stat cards / tooltips — those live in the tab header)."""
        # Drop closing, out-of-order, and pre-stat-switch results. _accept_from_seq is the key
        # cross-tab guard: the shared canvas is reparented across tabs, so a reply requested for a
        # previously-active single-stat tab (also a list payload) must not paint the new tab.
        if (self._is_closing
                or sequence_id < self._last_processed_id
                or sequence_id < self._accept_from_seq):
            return
        self._last_processed_id = sequence_id
        try:
            from netspeedtray.views.graph.logic import GraphLogic
            start, end = self._time_range()
            period_key = GraphLogic.get_period_key(self._history_period_value)
            # Race guard (same as GraphWindow): dict payload is overview-only; single-stat tabs want a list.
            if self._current_stat == "overview" and not isinstance(data, dict):
                return
            if self._current_stat != "overview" and isinstance(data, dict):
                return
            self.renderer.render(data, start, end, period_key,
                                 boot_time=self._cached_boot_time, stat_type=self._current_stat)
        except Exception as e:
            self.logger.error("GraphHost render error: %s", e, exc_info=True)

    # ------------------------------------------------------------------ teardown

    def teardown(self) -> None:
        """Stop the realtime loop, fully stop the worker thread, then free the figure + canvas.
        Honest caveat: matplotlib's module code stays resident once imported — this frees the heavy
        objects, not the module. (Overview never imports it, so a glance-only session stays at
        baseline.) Idempotent: safe if called more than once or before ensure_loaded()."""
        self._is_closing = True
        self.stop_realtime()

        # Stop the coordinator's debounce timer too (latent today — set_period bypasses it — but it
        # would otherwise fire a refresh into a dead thread once a period control is wired).
        try:
            if self.coordinator is not None:
                self.coordinator.update_debounce_timer.stop()
        except Exception:
            pass

        # Disconnect cross-thread signals so no further work is queued and data_ready can't fire
        # into _on_data_ready mid-teardown.
        try:
            if self.worker is not None:
                self.worker.data_ready.disconnect(self._on_data_ready)
                self.request_data_processing.disconnect(self.worker.process_data)
        except Exception:
            pass

        # The thread MUST actually finish before we free the figure/canvas — a process_data() can be
        # mid-SQLite-query for longer than 700ms on a big DB. Never proceed on a still-running thread.
        try:
            if self._thread is not None:
                self._thread.quit()
                if not self._thread.wait(700):
                    self._thread.wait()  # unbounded fallback: wait out the in-flight query
        except Exception:
            pass

        try:
            if self.renderer is not None:
                fig = getattr(self.renderer, "figure", None)
                if fig is not None:
                    import matplotlib.pyplot as plt
                    fig.clear()
                    plt.close(fig)
                canvas = getattr(self.renderer, "canvas", None)
                if canvas is not None:
                    canvas.setParent(None)
                    canvas.deleteLater()
        except Exception:
            pass

        # Release the worker + thread (only after wait() confirmed the thread stopped).
        try:
            if self.worker is not None:
                self.worker.deleteLater()
            if self._thread is not None:
                self._thread.deleteLater()
        except Exception:
            pass
        self.worker = None
        self._thread = None
