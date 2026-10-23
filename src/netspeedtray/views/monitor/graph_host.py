"""
GraphHost - reuse the existing graph engine (GraphRenderer / GraphDataWorker / GraphCoordinator)
behind ONE lazily-imported, reparented matplotlib canvas.

The Monitor's chart tabs (Network now, Hardware later) share a single GraphHost: one renderer, one
worker thread, one coordinator, one canvas that gets reparented into whichever tab is active. The
heavy graph package (and matplotlib) is imported only inside ensure_loaded(), so a glance at the
matplotlib-free Overview tab never pays for it.

GraphHost presents the exact host surface GraphCoordinator drives (renderer / ui / interaction /
config_handler / _is_live_update_enabled / update_graph / update_graph_range), so coordinator.py,
worker.py and renderer.py are reused **byte-for-byte**. The window-specific glue (overlay stat
cards, zoom, tooltips) is intentionally NOT reused - the Monitor shows stats in its tab header, so
GraphHost writes its own clean, render-only data callback.

IMPORT FIREWALL: this module imports nothing from netspeedtray.views.graph at module scope. Every
graph import is lazy (inside ensure_loaded / the refresh methods).
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Any, Dict, Optional

from PyQt6.QtCore import QObject, Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import QWidget, QVBoxLayout

from netspeedtray.utils import styles as su   # matplotlib-free; OK at module scope under the firewall


def bind_reader_thread(thread: QThread, widget_state, on_ident=None) -> None:
    """Tie a history-reading worker thread's read connection to the thread's own lifetime.

    ``finished`` is emitted ON the worker thread just before it exits; with a DirectConnection the
    slot runs there too, so the connection is closed by the thread that used it - never from the UI
    thread while it might be mid-query (2.2.0-beta.1 lifecycle review). ``on_ident`` receives the
    thread's ``threading.get_ident()`` (from ``started``, also on that thread) so a caller can target
    ``WidgetState.interrupt_reads`` at it."""
    direct = Qt.ConnectionType.DirectConnection
    if on_ident is not None:
        # Hold the owner WEAKLY: a detached thread (stop_reader_thread) can outlive the window, and a
        # lambda closing over a bound method would keep the whole tab / host alive with it (M7).
        import weakref
        ref = weakref.WeakMethod(on_ident) if hasattr(on_ident, "__self__") else (lambda: on_ident)

        def _started() -> None:
            fn = ref()
            if fn is not None:
                fn(threading.get_ident())
        thread.started.connect(_started, direct)
    release = getattr(widget_state, "release_read_conn", None)
    if release is not None:
        thread.finished.connect(release, direct)


def stop_reader_thread(thread: QThread, worker, widget_state, thread_ident: Optional[int],
                       logger: logging.Logger) -> bool:
    """Quit + join a history-reading worker thread, bounded. True when it stopped.

    First the thread gets GRAPH_THREAD_QUIT_WAIT_MS to finish its current request; then the SQLite
    statement it is running is interrupted (thread-safe) and it gets GRAPH_THREAD_STOP_WAIT_MS more.
    If it is STILL running, it is detached: kept referenced so nothing destroys a running QThread (Qt
    aborts the process), and monitor.py exits hard at quit while a detached thread runs. The caller
    must then not deleteLater() it; it frees itself when it finally finishes."""
    from netspeedtray import constants
    t = constants.timeouts
    thread.quit()
    if thread.wait(int(t.GRAPH_THREAD_QUIT_WAIT_MS)):
        return True
    interrupt = getattr(widget_state, "interrupt_reads", None)
    if interrupt is not None and thread_ident is not None:
        interrupt(thread_id=thread_ident)
    if thread.wait(int(t.GRAPH_THREAD_STOP_WAIT_MS)):
        return True
    logger.warning("Graph worker thread still busy %d ms after close; detaching it instead of "
                   "blocking the UI.", int(t.GRAPH_THREAD_QUIT_WAIT_MS + t.GRAPH_THREAD_STOP_WAIT_MS))
    from netspeedtray.core.database import detach_running_worker
    try:
        thread._nst_worker = worker            # the worker must outlive its running slot too
    except Exception:
        pass
    try:
        if worker is not None:
            thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
    except Exception:
        pass
    detach_running_worker(thread)
    return False


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
        if "history_period_slider_value" in updates:
            updates = {**updates, "history_period_chosen": True}   # a deliberate pick (M2)
        try:
            mw = self._host._main_widget
            mw.config.update(updates)
            mw.config_manager.save(mw.config)
        except Exception:
            pass



def hardware_history_off_reasons(config: Dict[str, Any]) -> Dict[str, Optional[str]]:
    """Per hardware graph role: None when its history is recorded (controller.hardware_persist_allowed,
    the writer's own rule), "monitor" when its CPU/GPU monitoring is off (2.2 default; fix it in
    Settings > Hardware), "record" when monitoring is on but Record hardware history is off
    (Settings > Advanced)."""
    from netspeedtray.core.controller import hardware_persist_allowed
    out: Dict[str, Optional[str]] = {}
    for role, switch in (("cpu", "monitor_cpu_enabled"), ("gpu", "monitor_gpu_enabled")):
        if hardware_persist_allowed(role, config):
            out[role] = None
        elif not config.get(switch, False):
            out[role] = "monitor"
        else:
            out[role] = "record"
    return out

class GraphHost(QObject):
    """One shared, lazily-loaded graph engine + canvas for the Monitor's chart tabs."""

    # MUST stay pyqtSignal(object), NOT pyqtSignal(DataRequest): importing DataRequest at class
    # scope would run views.graph.__init__ (which eagerly imports GraphWindow -> matplotlib) and
    # break the lazy firewall. Every graph import in this module is method-scoped for the same reason.
    request_data_processing = pyqtSignal(object)

    #: machine-wide totals for the active period, emitted after a network render: (up_bytes,
    #: down_bytes, period_key, reconstructed). The Network header band subscribes to show period
    #: totals; ``reconstructed`` (D5) = part of them was estimated from speeds saved before 2.2.
    network_totals_ready = pyqtSignal(float, float, str, bool)

    #: live updates were turned on/off. Both tab headers' Live/Pause pills bind to this so the shared
    #: engine's one canonical state stays in sync no matter which tab toggled it.
    live_changed = pyqtSignal(bool)

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
        self._is_live_update_enabled = True   # pause is transient view state - always open live (M11)
        self._history_period_value = int(config.get("history_period_slider_value", 2))
        self._current_request_id = 0
        self._last_processed_id = -1
        self._totals_reconstructed: Dict[int, bool] = {}   # sequence_id -> D5 flag (worker provenance)
        self._graph_coverage: Dict[int, Dict[str, Any]] = {}   # sequence_id -> resolution + measured spans
        self.interface_filter = None          # None / "all" -> every interface
        self._show_loading_status = bool(config.get("show_loading", False))
        self._current_stat = "network"        # set per active tab via attach_to()
        self._accept_from_seq = 0             # drop in-flight results from a previous stat (cross-tab)
        self._cached_boot_time = None         # fetched once for the uptime range (mirrors GraphWindow)
        self._cached_earliest_db = None
        self._earliest_db_fetched = False

        # shims the coordinator drives (matplotlib-free)
        self.ui = _UiShim()
        self.interaction = _InteractionShim()
        self.config_handler = _ConfigHandlerShim(self)

        # built on first ensure_loaded()
        self.renderer = None
        self.worker = None
        self.coordinator = None
        self._hover = None
        self._thread: Optional[QThread] = None
        self._thread_ident: Optional[int] = None   # the worker thread's get_ident(), set as it starts
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
        # Theme from the OS apps theme (su.is_dark_mode), NOT config['dark_mode'] - every other Monitor
        # surface themes that way, and config['dark_mode'] is never synced to the OS (it stays at its
        # default), so reading it rendered a dark graph inside a light Monitor on a light-mode PC.
        self.renderer.apply_theme(su.is_dark_mode())

        # Worker on its own QThread.
        self._thread = QThread()
        self.worker = GraphDataWorker(self._main_widget.widget_state)
        self.worker.moveToThread(self._thread)
        self.worker.totals_provenance.connect(self._on_totals_provenance)
        self.worker.graph_coverage.connect(self._on_graph_coverage)
        self.worker.data_ready.connect(self._on_data_ready)
        # A bound method, never a lambda capturing self: PyQt keeps a lambda slot alive with the
        # worker, and through it this host + its coordinator - one leaked pair per Monitor open (M7).
        self.worker.error.connect(self._on_worker_error)
        self.request_data_processing.connect(self.worker.process_data)
        # The worker's read connection is opened, and closed, on its own thread.
        bind_reader_thread(self._thread, self._main_widget.widget_state, self._set_thread_ident)
        self._thread.start()

        # Coordinator drives THIS object as its host (unchanged coordinator.py).
        self.coordinator = GraphCoordinator(self)

        # Lightweight, stat-agnostic hover readout (the one real thing the old graph window had that the
        # Monitor lacked). Reads the live plotted lines, so it works for both the network and hardware
        # graphs and survives a re-render without managing any matplotlib artists.
        from netspeedtray.views.monitor.graph_hover import GraphHoverTooltip
        self._hover = GraphHoverTooltip(self)
        self._hover.attach()

        self._loaded = True

    def _set_thread_ident(self, ident: int) -> None:
        self._thread_ident = ident   # runs on the worker thread (started, DirectConnection)

    def _on_worker_error(self, msg: str) -> None:
        self.logger.debug("graph worker error: %s", msg)

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

    def set_stat(self, stat_type: str) -> None:
        """Switch the active stat on the already-mounted canvas (no reparent) - used by the Hardware
        tab's graph-mode / CPU-GPU toggle. Race-safe: raises the dedup floor like set_period so an
        in-flight reply for the previous stat can't paint the new one."""
        if self._is_closing or stat_type == self._current_stat:
            return
        self.ensure_loaded()
        self._current_stat = stat_type
        self._accept_from_seq = self._current_request_id + 1
        self.update_graph(show_loading=True)

    def start_realtime(self) -> None:
        self.ensure_loaded()
        try:
            self.coordinator.start_realtime()   # itself a no-op while _is_live_update_enabled is False
        except Exception as e:
            self.logger.debug("start_realtime failed: %s", e)

    def stop_realtime(self) -> None:
        try:
            if self.coordinator is not None:
                self.coordinator.stop_realtime()
        except Exception:
            pass

    # ------------------------------------------------------------------ live/pause
    @property
    def is_live(self) -> bool:
        """Whether the graph auto-refreshes as new samples land (the canonical, tab-shared state)."""
        return bool(self._is_live_update_enabled)

    def set_live(self, enabled: bool) -> None:
        """Freeze (False) or resume (True) realtime updates. Starts/stops the coordinator's timer and -
        on resume - refreshes once so the frozen view catches up immediately. No-op (and silent) if
        already in the requested state. Emits ``live_changed`` so every Live/Pause pill across the tabs
        reflects the new state.

        Pause is TRANSIENT per-session view state (NOT persisted): a "pause to inspect, then close" must
        not reopen the Monitor frozen on a stale graph next session - every open starts live."""
        enabled = bool(enabled)
        if enabled == self._is_live_update_enabled or self._is_closing:
            return
        self._is_live_update_enabled = enabled
        if enabled:
            self.start_realtime()
            self.update_graph(show_loading=False)   # jump to "now" instead of waiting a tick
        else:
            self.stop_realtime()
        self.live_changed.emit(enabled)

    def set_period(self, period_value: int) -> None:
        """Change the timeline window (driven by the Network header's period control). Routes
        through coordinator.handle_timeline_change, which persists config, debounces rapid clicks,
        resets the renderer's sticky y-limits, and clears stale visuals - so switching periods on
        the shared canvas behaves exactly like the standalone graph window."""
        if self._is_closing:
            return
        self.ensure_loaded()
        self._history_period_value = int(period_value)
        # Raise the dedup floor SYNCHRONOUSLY: handle_timeline_change only debounces (it doesn't issue
        # a request now), so an already-in-flight previous-period reply is still the newest sequence_id
        # and would otherwise pass the guards in _on_data_ready and paint/emit stale totals under the
        # new period. Anything requested before this change is now dropped.
        self._accept_from_seq = self._current_request_id + 1
        from netspeedtray.views.graph.logic import GraphLogic
        period_key = GraphLogic.get_period_key(self._history_period_value)
        try:
            self.coordinator.handle_timeline_change(period_key)
        except Exception as e:
            self.logger.debug("set_period via coordinator failed (%s); direct refresh", e)
            self.config_handler.queue_config_update(
                {"history_period_slider_value": self._history_period_value})
            self.update_graph(show_loading=False)

    # ------------------------------------------------------------------ host surface: refresh

    def sync_period_from_config(self) -> None:
        """Adopt the period chosen on ANOTHER tab (the Overview writes config, not the host) before a
        chart tab shows (M1: picking Week on the Overview left Network/Hardware on 24 h). Only once
        a period was ever chosen: a fresh install keeps each tab's own opening view."""
        if self._is_closing or not self.config.get("history_period_chosen", False):
            return
        try:
            value = int(self.config.get("history_period_slider_value", self._history_period_value))
        except (TypeError, ValueError):
            return
        if value == self._history_period_value:
            return
        self._history_period_value = value
        self._accept_from_seq = self._current_request_id + 1   # an older-period reply must not paint
        try:
            if self.renderer is not None:
                self.renderer.reset_ylim()   # sticky y-limits belong to the old period
        except Exception:
            pass

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

    def set_interface_filter(self, name: Optional[str]) -> None:
        """Scope the network graph + its totals to one NIC ('all'/None = every interface). Raises the
        dedup floor so an in-flight all-interface reply can't paint the newly-filtered view."""
        if self._is_closing:
            return
        self.ensure_loaded()
        self.interface_filter = None if name in (None, "all") else name
        self._accept_from_seq = self._current_request_id + 1
        self.update_graph(show_loading=False)

    def _hw_styles(self) -> Dict[str, Any]:
        """Vendor-aware (color, linestyle) per role for the combined CPU+GPU graph; Monitor-settings
        color overrides (when set) win over the auto vendor default."""
        from netspeedtray.utils import hardware_vendors as hv
        cpu_c = self.config.get("monitor_cpu_graph_color") or None
        gpu_c = self.config.get("monitor_gpu_graph_color") or None
        is_dark = su.is_dark_mode()   # OS apps theme (matches apply_theme + the rest of the Monitor)
        ram_c = self.config.get("monitor_ram_graph_color") or ("#4CAF50" if is_dark else "#388E3C")
        return {"cpu": hv.graph_line_style("cpu", cpu_c, is_dark),
                "gpu": hv.graph_line_style("gpu", gpu_c, is_dark),
                "ram": (ram_c, "-"),   # RAM% as a green solid line (matches the Overview RAM tile)
                "legend": bool(self.config.get("monitor_graph_legend", True)),
                "smoothing": bool(self.config.get("monitor_graph_smoothing", False)),
                "fixed_axis": bool(self.config.get("monitor_graph_fixed_axis", True)),
                # Why each role's history isn't being recorded (None = it is), so an empty ranged view
                # that will never fill says so instead of "Collecting data" forever.
                "history_off": hardware_history_off_reasons(self.config),
                # The first stored record, so an empty range can say why it is empty (renderer).
                "earliest": self._earliest_record()}

    def _net_styles(self, sequence_id: Optional[int] = None) -> Dict[str, Any]:
        """The network graph's styles: what ``_hw_styles`` carries (the renderer reads "earliest" from
        it for the empty state) plus the user's speed unit (S2: the y axes follow unit_type like the
        widget does) and the worker's coverage for reply ``sequence_id`` (S3: the stored resolution,
        which names the statistic; S4: the measured spans, which tell idle from unmeasured time).
        No coverage (the in-memory Session) leaves both None: the renderer then claims nothing."""
        styles = self._hw_styles()
        styles.update({
            "unit_type": self.config.get("unit_type", "bits_decimal"),
            "force_mega": self.config.get("speed_display_mode") == "always_mbps",
            "resolution": None,
            "measured": None,
        })
        if sequence_id is not None:
            styles.update(self._graph_coverage.pop(int(sequence_id), None) or {})
        return styles

    def _earliest_record(self):
        """The first stored record (cached). Re-read only while there is none yet - a fresh install's
        first rows arrive while the Monitor is open - so this is one UI-thread read in steady state."""
        if self._cached_earliest_db is None:
            try:
                self._cached_earliest_db = self._main_widget.widget_state.get_earliest_data_timestamp()
            except Exception:
                self._cached_earliest_db = None
        return self._cached_earliest_db

    def _time_range(self):
        from netspeedtray.views.graph.logic import GraphLogic
        period_key = GraphLogic.get_period_key(self._history_period_value)
        # Fetch boot/earliest ONCE for the uptime range. These are UI-thread DB calls and _time_range
        # runs on every refresh + realtime tick - GraphWindow caches them the same way (and the cache
        # is naturally fresh each session, since GraphHost is recreated per Monitor window).
        # ALL needs the earliest row too, not just SYSTEM UPTIME. Without it, get_start_time() falls
        # back to `now - 10 years`, so "All" drew an axis from ~2016 with every real sample crushed
        # into a sliver at the right edge - the one period whose whole job is to show everything was
        # the one that showed nothing. The Overview tab already asked for both; this path had drifted.
        if period_key in ("TIMELINE_SYSTEM_UPTIME", "TIMELINE_ALL") and not self._earliest_db_fetched:
            # Latch on the ATTEMPT, not on the result: an empty database legitimately returns None,
            # and this runs on every refresh and realtime tick - retrying would be a UI-thread DB
            # call per tick, forever.
            self._earliest_db_fetched = True
            try:
                self._cached_earliest_db = self._main_widget.widget_state.get_earliest_data_timestamp()
            except Exception:
                self._cached_earliest_db = None
        # The boot time is fetched on its own (psutil, no DB). It used to ride the earliest-row latch,
        # so "All" first and then "Since boot" never fetched it and drew the whole history (M4).
        if period_key == "TIMELINE_SYSTEM_UPTIME" and self._cached_boot_time is None:
            try:
                self._cached_boot_time = GraphLogic.get_boot_time()
            except Exception:
                self._cached_boot_time = None
        return GraphLogic.get_time_range(self._history_period_value, self.session_start_time,
                                         self._cached_boot_time, self._cached_earliest_db)

    def _on_data_ready(self, data, total_up, total_down, sequence_id) -> None:
        """Render-only callback (no overlay stat cards / tooltips - those live in the tab header)."""
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
            # Race guard: dict payload is for the multi-series stats (overview + hwcombined);
            # single-stat tabs (network/cpu/gpu) want a list.
            dict_stats = ("overview", "hwcombined", "hwseparate")
            if self._current_stat in dict_stats and not isinstance(data, dict):
                return
            if self._current_stat not in dict_stats and isinstance(data, dict):
                return
            styles = (self._net_styles(sequence_id) if self._current_stat == "network"
                      else self._hw_styles())
            self.renderer.render(data, start, end, period_key,
                                 boot_time=self._cached_boot_time, stat_type=self._current_stat,
                                 hw_styles=styles)
            # Surface the worker's period totals to the Network header. For ranged periods these are
            # machine-wide (interface filter None sums every NIC); for SESSION they reflect the active
            # interface mode (auto/selected/...), mirroring the standalone graph's session aggregation.
            if self._current_stat == "network":
                recon = bool(self._totals_reconstructed.pop(sequence_id, False))
                self.network_totals_ready.emit(float(total_up or 0.0), float(total_down or 0.0),
                                               period_key, recon)
        except Exception as e:
            self.logger.error("GraphHost render error: %s", e, exc_info=True)

    def _on_graph_coverage(self, sequence_id: int, coverage) -> None:
        """Keep the worker's coverage for reply ``sequence_id`` until its data_ready arrives (emitted
        first, from the same thread - delivered in order)."""
        store = self._graph_coverage
        if isinstance(coverage, dict):
            store[int(sequence_id)] = {"resolution": coverage.get("resolution"),
                                       "measured": coverage.get("measured")}
        for old in [k for k in store if k < sequence_id - 16]:   # replies that were dropped
            store.pop(old, None)

    def _on_totals_provenance(self, sequence_id: int, reconstructed: bool) -> None:
        """Remember whether the totals of reply ``sequence_id`` are partly estimated (D5) until its
        data_ready arrives (the worker emits this first, from the same thread)."""
        store = self._totals_reconstructed
        store[int(sequence_id)] = bool(reconstructed)
        for old in [k for k in store if k < sequence_id - 16]:   # replies that were dropped
            store.pop(old, None)

    # ------------------------------------------------------------------ teardown

    def teardown(self) -> None:
        """Stop the realtime loop, fully stop the worker thread, then free the figure + canvas.
        Honest caveat: matplotlib's module code stays resident once imported - this frees the heavy
        objects, not the module. (Overview never imports it, so a glance-only session stays at
        baseline.) Idempotent: safe if called more than once or before ensure_loaded()."""
        self._is_closing = True
        self.stop_realtime()

        try:
            if getattr(self, "_hover", None) is not None:
                self._hover.detach()
        except Exception:
            pass

        # Stop the coordinator's debounce timer too (latent today - set_period bypasses it - but it
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
                for sig, slot in ((self.worker.data_ready, self._on_data_ready),
                                  (self.worker.totals_provenance, self._on_totals_provenance),
                                  (self.worker.graph_coverage, self._on_graph_coverage),
                                  (self.worker.error, self._on_worker_error),
                                  (self.request_data_processing, self.worker.process_data)):
                    try:
                        sig.disconnect(slot)
                    except (TypeError, RuntimeError):
                        pass
        except Exception:
            pass

        # A process_data() can be mid-SQLite-query for well over 700 ms on a big DB. Bounded: past the
        # quit wait its query is interrupted, and past that the thread is detached rather than blocking
        # the UI (this wait used to be unbounded). The figure/canvas below are GUI-thread objects the
        # worker never touches (its signals are disconnected above), so freeing them is safe either way.
        stopped = True
        try:
            if self._thread is not None:
                stopped = stop_reader_thread(self._thread, self.worker,
                                             getattr(self._main_widget, "widget_state", None),
                                             getattr(self, "_thread_ident", None), self.logger)
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

        # Release the worker + thread - only once wait() confirmed the thread stopped. A detached one
        # deletes itself on finished (stop_reader_thread).
        try:
            if stopped:
                if self.worker is not None:
                    self.worker.deleteLater()
                if self._thread is not None:
                    self._thread.deleteLater()
        except Exception:
            pass
        self.worker = None
        self._thread = None

        # Drop the heavy references so the figure / Line2D arrays / canvas and the host↔coordinator
        # reference cycle can be reclaimed promptly instead of lingering until a later GC pass - the
        # "Monitor RAM grew and didn't drop on close" symptom. (matplotlib's MODULE stays imported for
        # the session by design; an Overview-only session never loads it. This frees the per-window
        # objects, not the module.)
        self.renderer = None
        self.coordinator = None
        self._hover = None
        self._canvas_container = None
