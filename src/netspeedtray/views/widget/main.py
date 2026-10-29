from __future__ import annotations

# --- Standard Library Imports ---
import ctypes
import logging
import math
import os
import sys
import time
from ctypes import wintypes
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

# --- Third-Party Imports ---
import win32api
import win32con
import win32gui
from win32con import MONITOR_DEFAULTTONEAREST
from PyQt6.QtCore import QEvent, QObject, QPoint, QRect, QSize, QTimer, Qt
from PyQt6.QtGui import (
    QCloseEvent, QColor, QContextMenuEvent, QFont, QFontMetrics, QHideEvent,
    QIcon, QMouseEvent, QPaintEvent, QPainter, QShowEvent, QWheelEvent
)
from PyQt6.QtWidgets import QApplication, QMenu, QMessageBox, QWidget

# --- First-Party (Local) Imports ---
from netspeedtray import constants
from netspeedtray.core.controller import StatsController as CoreController
from netspeedtray.core.monitor_thread import StatsMonitorThread
from netspeedtray.core.tray_manager import TrayIconManager
from netspeedtray.core.widget_state import WidgetState as CoreWidgetState
from netspeedtray.utils.config import ConfigManager as CoreConfigManager
from netspeedtray.utils.timer_utils import resolve_poll_interval_seconds
from netspeedtray.utils.styles import set_app_reduce_motion
from netspeedtray.utils import color_scheme
from netspeedtray.core.position_manager import PositionManager, WindowState
from netspeedtray.core.input_handler import InputHandler
from netspeedtray.utils.taskbar_utils import (
    get_taskbar_info, is_taskbar_obstructed, is_taskbar_visible,
    get_process_name_from_hwnd, dock_signature
)

from netspeedtray.utils.widget_renderer import WidgetRenderer as CoreWidgetRenderer, RenderConfig
from netspeedtray.utils.widget_paint import WidgetMetrics, render_widget, split_network_phases
from netspeedtray.core.system_events import SystemEventHandler
from netspeedtray.views.widget.layout import WidgetLayoutManager
from netspeedtray.views.widget.theme import WidgetThemeManager
from netspeedtray.core.startup_manager import StartupManager, is_process_elevated
from netspeedtray.core.config_controller import ConfigController
from netspeedtray.core.update_checker import UpdateChecker
from netspeedtray.core import shutdown_channel
from netspeedtray.utils.helpers import effective_display_mode, hardware_in_hover_card_active

# Display modes whose taskbar widget paints a CPU (and RAM) / GPU (and VRAM) reading - a new value
# for one of them is worth a repaint. Read through effective_display_mode, so with "Show hardware in
# the hover card" on the widget stops repainting for hardware it no longer shows.
_CPU_BAR_MODES = ("cpu_only", "combined", "side_by_side", "cycle")
_GPU_BAR_MODES = ("gpu_only", "combined", "side_by_side", "cycle")

# --- Type Checking ---
if TYPE_CHECKING:
    from netspeedtray.constants.i18n import I18nStrings
    from netspeedtray.views.settings import SettingsDialog


# Win32 MSG layout for reading native messages in nativeEvent(). Defined once at
# module scope - nativeEvent is a hot path (fires for every native message), so we
# must not rebuild this Structure on each call.
class _NativeMSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND), ("message", wintypes.UINT),
        ("wParam", ctypes.c_size_t), ("lParam", ctypes.c_ssize_t),
        ("time", wintypes.DWORD), ("pt_x", wintypes.LONG), ("pt_y", wintypes.LONG),
    ]

_WM_POWERBROADCAST = 0x0218
_WM_QUERYENDSESSION = 0x0011
_WM_ENDSESSION = 0x0016
# Inside WM_QUERYENDSESSION / WM_ENDSESSION, how long the odometer + last history batch may wait for
# the database thread. Windows ends a logging-off app ~5 s after asking; the config save (an atomic
# file write, milliseconds) runs first, so this bounded wait is all that can use the budget.
_SESSION_END_DB_WAIT_S = 2.0
_PBT_APMRESUMESUSPEND = 0x0007
_PBT_APMRESUMEAUTOMATIC = 0x0012

# "TaskbarCreated" is broadcast by explorer whenever the taskbar window is
# (re)created - the canonical re-attach signal for tray-docked windows, and the
# instant alternative to waiting out the 3s validity poll after an explorer
# restart. RegisterWindowMessageW returns one system-wide atom for the string, so
# resolving it once at import is correct for the process lifetime. 0 means
# registration failed, in which case nativeEvent skips the branch and the
# validity poll remains the only (slower) recovery path.
try:
    _WM_TASKBARCREATED = int(ctypes.windll.user32.RegisterWindowMessageW("TaskbarCreated"))
except Exception:
    _WM_TASKBARCREATED = 0

# UIPI: a process running elevated silently drops messages broadcast by the
# non-elevated shell, so without MSGFLT_ALLOW on our window the TaskbarCreated
# re-dock never fires there. Per-window API, resolved once at import.
_MSGFLT_ALLOW = 1
try:
    _ChangeWindowMessageFilterEx = ctypes.windll.user32.ChangeWindowMessageFilterEx
    _ChangeWindowMessageFilterEx.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.DWORD, ctypes.c_void_p]
    _ChangeWindowMessageFilterEx.restype = wintypes.BOOL
except AttributeError:  # pre-Win7, unlikely
    _ChangeWindowMessageFilterEx = None

# The installer's graceful-close request (setup.iss [Code], or `--shutdown`; core/shutdown_channel.py). A registered
# message: one system-wide atom per string, so resolving it once at import is correct. 0 = failed,
# and nativeEvent then never matches (WM_NULL is 0, so the guard matters).
try:
    _WM_NST_SHUTDOWN = shutdown_channel.shutdown_message_id()
except Exception:
    _WM_NST_SHUTDOWN = 0
# A second launch's "show yourself" (#234, rc.2 W14) - same channel, same 0 guard.
try:
    _WM_NST_ACTIVATE = shutdown_channel.activate_message_id()
except Exception:
    _WM_NST_ACTIVATE = 0


# double_click_action -> the i18n key of its Settings label (the same words the user picked it by).
_DOUBLE_CLICK_HINT_LABELS = {
    constants.config.defaults.CLICK_ACTION_OPEN_MONITOR: "CLICK_ACTION_OPEN_MONITOR_LABEL",
    constants.config.defaults.CLICK_ACTION_SETTINGS: "CLICK_ACTION_SETTINGS_LABEL",
    constants.config.defaults.CLICK_ACTION_PAUSE: "CLICK_ACTION_PAUSE_LABEL",
}


def hover_hint_text(i18n, config: Dict[str, Any]) -> str:
    """The first-run gesture hint: what the right-click menu holds, plus the double-click action
    the user actually has configured (no line when it does nothing)."""
    lines = [i18n.WIDGET_HOVER_TOOLTIP]
    key = _DOUBLE_CLICK_HINT_LABELS.get(
        config.get("double_click_action", constants.config.defaults.DEFAULT_DOUBLE_CLICK_ACTION))
    if key:
        lines.append(i18n.WIDGET_HOVER_DOUBLE_CLICK_HINT.format(action=getattr(i18n, key)))
    return "\n".join(lines)


def widget_qt_scale(widget: Any) -> Optional[float]:
    """Qt's device pixel ratio for ``widget``'s window right now, rounded (None when it can't be read).
    What the font metrics the layout measures with follow - not always in step with the OS scale."""
    try:
        fn = getattr(widget, "devicePixelRatioF", None)
        return round(float(fn()), 3) if callable(fn) else None
    except Exception:
        return None


class NetworkSpeedWidget(QWidget):
    """Main widget for displaying network speeds near the Windows system tray."""


    def __init__(self, taskbar_height: int = constants.taskbar.taskbar.DEFAULT_HEIGHT, config: Optional[Dict[str, Any]] = None, i18n: Optional[constants.i18n.I18nStrings] = None, parent: QObject | None = None) -> None:
        """Initialize the NetworkSpeedTray with core components and UI setup."""
        super().__init__(parent)
        self.logger = logging.getLogger(f"{constants.app.APP_NAME}.{self.__class__.__name__}")
        self.logger.debug("Initializing NetworkSpeedWidget...")
        self.settings_dialog: Optional[SettingsDialog] = None

        # --- Core Application State ---
        self.session_start_time = datetime.now()
        self.config_manager = CoreConfigManager()
        
        # Initialize ConfigController
        # NOTE: We pass 'self' (the widget) to the controller. This requires careful handling
        # in the controller to avoid circular discrepancies, but allows it to orchestrate updates.
        self.config_controller = ConfigController(self, self.config_manager)
        
        self.config: Dict[str, Any] = config or self.config_controller.load_initial_config(taskbar_height)
        set_app_reduce_motion(self.config.get("reduce_motion", False))   # before any toggle animates
        
        if i18n is None:
            raise ValueError("An i18n instance must be provided to NetworkSpeedWidget.")
        self.i18n = i18n
        
        # These MUST be initialized before _init_managers() because it checks self.current_metrics
        self.current_font: QFont = None
        self.current_metrics: QFontMetrics = None

        # Read the Windows (taskbar) mode before anything builds a RenderConfig, so the first frame
        # paints in the right theme's colours. Nothing is written: the config holds both themes.
        color_scheme.refresh_shell_theme()
        self._init_managers() # Initialize managers first

        # --- Declare all instance attributes for clarity ---
        self.widget_state: CoreWidgetState
        self.controller: CoreController
        self.renderer: CoreWidgetRenderer
        self.position_manager: PositionManager
        self.input_handler: InputHandler
        self.layout_manager: WidgetLayoutManager
        self.theme_manager: WidgetThemeManager
        self.tray_manager: TrayIconManager
        self.monitor_thread: StatsMonitorThread
        self._cached_layout_mode: str = 'horizontal'  # taskbar-edge orientation; updated on taskbar changes
        self._dock_sig_seen: Optional[tuple] = None  # last taskbar dock_signature seen (#322)
        self._qt_scale_seen: Optional[float] = None  # Qt's device pixel ratio the size was measured at
        self.monitor_window = None  # unified Monitor (Overview / Network / Hardware), lazy
        self.update_checker: Optional[UpdateChecker] = None
        self.app_icon: QIcon
        # Note: self.current_font and self.current_metrics are initialized earlier before _init_managers()
        
        self.upload_speed: float = 0.0
        self.download_speed: float = 0.0
        # NaN, not 0.0: these are seeded before anything has measured them, and 0.0 is a value a
        # user reads as a measurement. Under RDP the GPU poll is skipped outright (monitor_thread.run
        # gates the whole block on `not _in_rdp`), so update_gpu_usage is NEVER called and the seed is
        # all the widget ever has - it rendered a confident "GPU 0%" for the entire session. The
        # renderer turns a non-finite value into N/A, the same way it already does for an unavailable
        # temperature or wattage.
        self.cpu_usage: float = float('nan')
        self.gpu_usage: float = float('nan')
        self.network_identity: Optional[object] = None  # NetworkIdentity|None: Wi-Fi band/SSID, set by update_network_identity (v2.1)
        self._identity_reserve_sig_last: object = None  # last identity reserve signature (band-shown, ssid); re-layout on change
        self._location_prompt_shown: bool = False  # SSID Location nudge shown at most once per session
        self._widgets_nudge_shown: bool = False  # #200 Widgets/weather overlap nudge, once per session
        self.gpu_present: bool = False  # LATCHES True once any poll detects GPU counters (never flips
        #                                 back - a GPU doesn't vanish), so the Monitor's GPU tile shows
        #                                 stably for a real/integrated GPU and stays hidden only on a
        #                                 genuinely GPU-less box. Default False => hidden until proven.
        self.cpu_temp: Optional[float] = None
        self.gpu_temp: Optional[float] = None
        self.cpu_power: Optional[float] = None
        self.gpu_power: Optional[float] = None
        self.system_power: Optional[float] = None   # true whole-system W (RAPL PSYS / battery), if available
        self.battery_power: Optional[float] = None  # signed battery draw: discharge +, charge - (display only)
        self.latency_probe = None                    # apply_latency_probe owns it (startup + every apply)
        self.latency_gw: Optional[float] = None      # gateway RTT ms (LAN latency); None = timeout
        self.latency_anchor: Optional[float] = None  # public-anchor RTT ms (internet latency); opt-in
        # Live loss% (~2 min) per hop, and the HEADLINE loss: the anchor's when the public probe is on,
        # the gateway's otherwise (latency_probe.headline_hop) - the figure the Overview pill shows.
        # Loss values are None until the hop's window holds constants.network.latency.LOSS_MIN_SAMPLES.
        self.latency_loss: Optional[float] = None
        self.latency_loss_gw: Optional[float] = None
        self.latency_loss_anchor: Optional[float] = None
        self.latency_headline_hop: Optional[str] = None   # "anchor" / "gateway"; None before a sample
        # Most recent successful RTT of the headline hop within its live window - the pill's ms, so one
        # lost ping does not blank it (and the router's ms never stands in for the internet's).
        self.latency_headline_ms: Optional[float] = None
        # The last sample said the connection is down (LatencySample.gw_link_lost): the pill reads
        # "No connection" at once rather than the window's stale "Good" (rc.2 VM session).
        self.latency_link_lost: bool = False
        from netspeedtray.core.latency_probe import LatencyLossWindows
        self._latency_loss_windows = LatencyLossWindows()
        # Local metrics endpoint (2.2 scope B) - created on first enable, None while never enabled.
        self.metrics_endpoint = None
        self.ram_used: Optional[float] = None
        self.ram_total: Optional[float] = None
        self.vram_used: Optional[float] = None
        self.vram_total: Optional[float] = None
        # True when vram_used/vram_total are an integrated GPU's shared system memory (set by the
        # controller from the stats dict's 'vram_is_shared').
        self.vram_is_shared: bool = False
        
        # Cycling State
        self._cycle_index: int = 0
        self._current_cycle_mode: str = "network_only"
        self._cycle_timer = QTimer(self)
        self._cycle_timer.timeout.connect(self._rotate_cycle)

        self.taskbar_height: int = taskbar_height
        self._dragging: bool = False
        self._drag_offset: QPoint = QPoint()
        self.startup_manager: StartupManager
        self.is_paused: bool = False
        # (net, cpu, gpu) graph histories frozen at pause() - see _graph_histories.
        self._paused_histories: Optional[Tuple[list, list, list]] = None
        self._is_context_menu_visible: bool = False
        self.last_tray_rect: Optional[Tuple[int, int, int, int]] = None
        self._taskbar_lost_count: int = 0
        self._no_taskbar_ticks: int = 0   # consecutive refreshes holding for a missing taskbar (W5)
        self._will_quit_app: bool = False # Flag to distinguish hide vs exit
        self._shutdown_armed_hwnd: int = 0  # HWND the --shutdown channel was armed on
        # "Show on all taskbars" (#229): the MirrorManager while the setting is on, else None - and
        # with it off nothing of views/widget/mirror.py is imported, built, connected or timed.
        self._mirrors = None
        
        # Hooks for system events
        self.system_event_handler: SystemEventHandler

        
        # Timers for periodic checks
        # self._tray_watcher_timer moved to PositionManager
        self._state_watcher_timer = QTimer(self) # The "Safety Net" timer

        # Hover usage card - a Win11 flyout we position ourselves (above the taskbar) so it is
        # never clipped, unlike Qt's built-in tooltip. Shown after a short rest on the widget.
        self._hover_card = None
        # Click-to-pin: a pinned card ignores the pointer leaving and stays until a 2nd click, another
        # app coming to the front, the context menu, the double-click action, a drag, a fullscreen app
        # or a dock change (InputHandler drives the clicks).
        self._hover_card_pinned: bool = False
        self._hover_card_timer = QTimer(self)
        self._hover_card_timer.setSingleShot(True)
        self._hover_card_timer.timeout.connect(self._show_usage_hover_card)
        self._hover_usage_cache: Optional[Tuple[Tuple[float, float], Tuple[float, float]]] = None
        self._hover_usage_cache_ts: float = 0.0
        # HWND the TaskbarCreated UIPI filter was last applied to (see _allow_taskbar_created).
        self._taskbar_created_allowed: int = 0
        self._hover_battery_avg_cache: Optional[float] = None
        self._hover_battery_avg_cache_ts: float = 0.0
        self._hover_usage_period_start: Optional[datetime] = None   # set by _hover_usage_totals

        self.setVisible(False)
        self.logger.debug("Widget initially hidden to stabilize position and size.")

        # --- Initialization Steps ---
        try:
            self.layout_manager.setup_window_properties()
            self._init_ui_components()
            self._init_core_components()
            
            # Now that all components are initialized, perform the initial resize.
            self.layout_manager.resize_widget_for_font()
            
            self._setup_connections()
            self._setup_timers()
            self.position_manager.update_position()
            self._synchronize_startup_task()
            self.apply_metrics_endpoint()   # opt-in, off by default: a no-op unless the user enabled it
            # Reachable by the installer's shutdown request from here on, not only once first shown.
            self._arm_shutdown_channel()

            QTimer.singleShot(0, self._delayed_initial_show)

            self.logger.debug("NetworkSpeedWidget initialized successfully.")

        except Exception as e:
            self.logger.critical("Initialization failed: %s", e, exc_info=True)
            # Stop whatever already started (monitor thread, DB worker, probes, hooks): the caller
            # never gets this object, so nothing else could, and a QThread still running at exit
            # aborts the process on top of this error. Not a state to save to the config.
            try:
                self.cleanup(save_config=False)
            except Exception:
                self.logger.error("Cleanup after the failed initialization failed.", exc_info=True)
            raise RuntimeError(f"Failed to initialize NetworkSpeedWidget: {e}") from e


    def _setup_timers(self) -> None:
        """Configures all application timers."""
        # PositionManager handles tray monitoring
        if hasattr(self, 'position_manager'):
            self.position_manager.start_monitoring()
        self.logger.debug("PositionManager monitoring started.")

        # This is the "Safety Net" timer. It runs to catch states missed by events.
        self._state_watcher_timer.setInterval(constants.timeouts.STATE_WATCHER_INTERVAL_MS)
        self._state_watcher_timer.timeout.connect(self._execute_refresh)
        self._state_watcher_timer.start()
        self.logger.debug(f"Safety net state watcher timer started ({constants.timeouts.STATE_WATCHER_INTERVAL_MS}ms).")

        # Data-cap usage alerts: check usage-vs-cap on a slow cadence (cheap; no-op unless
        # the cap + alerts are enabled). Notifies via the flyout (no system-tray icon).
        try:
            from netspeedtray.core.usage_alerts import UsageAlertController
            self._usage_alert = UsageAlertController(
                usage_getter=self.widget_state.get_usage_this_period,
                config_getter=lambda: self.config,
                period_getter=self.widget_state.get_usage_period_key,
                notify=self._show_usage_alert,
                save_state=lambda s: self.update_config({"usage_alert_state": s}),
                i18n=self.i18n,
            )
            self._usage_alert_timer = QTimer(self)
            self._usage_alert_timer.timeout.connect(self._usage_alert.check)
            self._usage_alert_timer.start(60 * 1000)  # every 60s
        except Exception as e:
            self.logger.error(f"Could not start usage-alert controller: {e}", exc_info=True)

        # Update Checker - delayed startup check
        self.update_checker = UpdateChecker(self.config, self)
        self.update_checker.update_available.connect(self._on_update_available)
        if self.update_checker.should_check():
            QTimer.singleShot(5000, self.update_checker.check_now)

        # Cycle Timer (not while "Show hardware in the hover card" collapses the widget to network-only)
        if effective_display_mode(self.config) == "cycle":
            self._cycle_timer.start(constants.renderer.CYCLE_INTERVAL_MS)
            self.logger.debug("Cycle timer started.")


    def _init_core_components(self) -> None:
        """
        Initialize non-UI core logic components.

        Sets up WidgetState, the stats monitor thread, StatsController, and WidgetRenderer.
        """
        self.logger.debug("Initializing core components...")
        if not self.config:
            raise RuntimeError("Cannot initialize core: Config missing")
        try:
            self.widget_state = CoreWidgetState(self.config)
            
            # Background Monitoring Thread
            # The monitor thread's sleep loop is the only sampling pacer. The rate is one of the
            # 1/2/5/10 s presets (the config migration put it there); the shared resolver is the same
            # clamp every seconds-per-sample computation uses, so they can never disagree.
            effective_interval = resolve_poll_interval_seconds(
                self.config.get("update_rate", constants.config.defaults.DEFAULT_UPDATE_RATE))
            self.monitor_thread = StatsMonitorThread(interval=effective_interval, config=self.config)
            
            self.controller = CoreController(config=self.config, widget_state=self.widget_state)
            self.controller.set_view(self)
            self.renderer = CoreWidgetRenderer(self.config, self.i18n)
            
            self.logger.debug("Core components initialized; monitor thread ready.")
        except Exception as e:
            self.logger.error("Failed to initialize core components: %s", e, exc_info=True)
            raise RuntimeError("Failed to initialize core application components") from e


    def _init_managers(self) -> None:
        """Initialize all helper managers."""
        self.logger.debug("Initializing Managers...")
        
        # 1. Layout & Theme Managers
        self.layout_manager = WidgetLayoutManager(self)
        self.theme_manager = WidgetThemeManager(self)
        self.startup_manager = StartupManager()
        
        if not self.current_metrics:
            self.layout_manager.init_font()

        try:
            # Position Manager. The widget's OWN taskbar from the start (preferred monitor, #72): a bare
            # get_taskbar_info() seeded the primary's, which the owner fallback and every read of
            # _state.taskbar_info before the first refresh then used (rc.2 W12).
            taskbar_info = get_taskbar_info(preferred_screen_name=self.config.get("preferred_monitor"))
            window_state = WindowState(
                config=self.config,
                widget=self,
                taskbar_info=taskbar_info,
                font_metrics=self.current_metrics
            )
            self.position_manager = PositionManager(window_state, parent=self)
            self.position_manager.on_side_no_room = self._maybe_show_side_no_room_notice
            # #24: the app buttons reached the widget (or left it) - re-run the visibility gate. Late
            # bound (looked up when called), like the persist hook below.
            self.position_manager.on_app_yield_changed = lambda: self._on_app_yield_changed()
            self.position_manager.on_app_yield_first = lambda: self._maybe_show_app_overlap_notice()
            self.position_manager.on_app_yield_persist = lambda updates: self.update_config(updates)
            # Note: InputHandler is initialized in _init_ui_components() after tray_manager is created
            
            self.logger.debug("Managers initialized successfully.")

        except Exception as e:
            self.logger.critical(f"Failed to initialize managers: {e}", exc_info=True)
            raise RuntimeError("Manager initialization failed") from e











    def _on_immediate_hide_requested(self) -> None:
        """Handle the fast fullscreen-obstruction hide, honoring the two states that must
        stay visible: the user's keep_visible_fullscreen choice (#107), and runtime
        free-float (#188) - where a taskbar-less preferred monitor falls back to the
        primary taskbar, so a fullscreen app on the primary would otherwise transiently
        hide the off-taskbar widget. Mirrors the visibility gate in _execute_refresh()."""
        # Never leave an always-on-top card (pinned or not) over a game/video - even when the widget
        # itself stays (keep_visible_fullscreen / free-float).
        self.dismiss_hover_card()
        if self.config.get("keep_visible_fullscreen", False):
            return
        if self.position_manager and self.position_manager.is_free_float_active():
            return
        self.setVisible(False)

    def _execute_refresh(self, hwnd: int = 0) -> None:
        """
        The AUTHORITATIVE refresh trigger. This version includes a grace period
        to handle temporary taskbar detection failures (e.g., during shell restarts).
        """
        if self._is_context_menu_visible or self._dragging:
            return
        
        try:
            # Resolve the widget's OWN taskbar (honoring the preferred_monitor
            # setting, #72), not the primary. Using a bare get_taskbar_info()
            # here re-pinned the widget to the primary taskbar within one
            # event-loop tick, dragging it off a secondary monitor even after
            # the initial placement landed correctly. The visibility/obstruction
            # checks below are also judged against the widget's own taskbar.
            preferred = self.config.get("preferred_monitor")
            taskbar_info = get_taskbar_info(preferred_screen_name=preferred)

            # Implement the "coasting" logic for taskbar detection failures.
            if taskbar_info.hwnd == 0: # hwnd=0 signifies a fallback object from get_taskbar_info
                self._taskbar_lost_count += 1
                if self._taskbar_lost_count % 10 == 0: # Log warning every 10 seconds
                    self.logger.warning(
                        f"Taskbar detection failing. Coasting on fallback/safe mode. "
                        f"Failure count: {self._taskbar_lost_count}"
                    )
                # Removed logic that hides widget after 5 failures. 
                # We now rely on 'safe fallback position' (bottom-right of screen) instead.
            else:
                # If we successfully found a real taskbar, reset the counter.
                self._taskbar_lost_count = 0

            if hwnd == 0:
                hwnd = win32gui.GetForegroundWindow()

            # #188: is the preferred monitor a taskbar-less display we should free-float on?
            free_float_active = self.position_manager.refresh_float_state() is not None

            # No taskbar at all (Explorer restarting, rc.2 W5): hide and keep the last position until
            # the new taskbar exists, rather than float at the screen-fallback spot. Bounded - past
            # TASKBAR_ABSENT_HOLD_MS the old fallback applies. `is True`: a mocked manager is not.
            # Debounced (#440 review L5): the first no-taskbar tick only holds the position; the widget
            # hides from the second consecutive one, so one transient failed lookup can't flicker it.
            if not free_float_active and self.position_manager.holding_for_taskbar(taskbar_info) is True:
                ticks = getattr(self, "_no_taskbar_ticks", 0)
                ticks = (ticks if isinstance(ticks, int) else 0) + 1
                self._no_taskbar_ticks = ticks
                if ticks >= constants.timeouts.TASKBAR_ABSENT_HIDE_AFTER_TICKS and self.isVisible():
                    self.dismiss_hover_card()
                    self.setVisible(False)
                return
            self._no_taskbar_ticks = 0

            # Allow user override to keep widget visible even when a fullscreen window is present
            keep_visible = self.config.get("keep_visible_fullscreen", False)
            # #24 app-button yield: attach / detach for this taskbar first (floating detaches it).
            self.position_manager.sync_app_overlap(taskbar_info)
            if free_float_active:
                # The accessory display has no taskbar to hide behind, and the visibility/obstruction
                # checks are judged against the (irrelevant) primary taskbar - so just keep it shown.
                should_be_visible = True
            else:
                should_be_visible = is_taskbar_visible(taskbar_info) and (keep_visible or not is_taskbar_obstructed(taskbar_info, hwnd))
                # #24: a native horizontal Win11 bar whose app buttons reach the widget - it steps
                # aside on this same hide path (never moves, #200). ANDed: neither this nor the
                # fullscreen hide / keep_visible_fullscreen overrides the other. ``is True``: a stand-in
                # position manager without the yield never hides the widget.
                if self.position_manager.is_yielding_to_apps() is True:
                    should_be_visible = False

            if self.isVisible() != should_be_visible:
                self.setVisible(should_be_visible)

            # Only update position if we are supposed to be visible.
            if self.isVisible():
                # Taskbar thickness/edge changed while running (#322): re-size (which repositions).
                if not free_float_active:
                    self._resize_if_dock_changed(taskbar_info)
                # Free-move keeps the user's exact saved spot (applied once); docked and free-float both
                # reposition on the refresh (free-float re-places on / re-detects its taskbar-less display).
                if not self.config.get("free_move", False):
                    # _float_refreshed=True: we already called refresh_float_state() above for the
                    # visibility check this tick, so don't re-enumerate taskbars inside (#188).
                    self.position_manager.update_position(fresh_taskbar_info=taskbar_info, _float_refreshed=True)
                    # Side bars: the icon alignment and the bounded "apps near the widget" re-check.
                    self.position_manager.side_bar_tick()
                
                # Always re-assert topmost status when visible to prevent falling behind taskbar (#77)
                self._ensure_win32_topmost()

                # (_ensure_win32_topmost also runs _recover_if_buried: the 1 s safety net behind
                # the event-driven burial recovery.)

                # One-time heads-up if we're sitting over the Win11 Widgets/weather panel (#200).
                self._maybe_nudge_widgets_overlap(taskbar_info)

        except Exception as e:
            self.logger.error(f"Critical error in _execute_refresh (failure count: {self._taskbar_lost_count}): {e}")
            # If we've had many consecutive failures, only then hide as a last resort.
            # Otherwise, keep it visible and hope for recovery on next tick.
            if self._taskbar_lost_count > 30 and self.isVisible():
                 self.logger.warning("Hiding widget as a last resort after sustained detection failure.")
                 self.setVisible(False)


    def _delayed_initial_show(self) -> None:
        """Triggers the initial authoritative visibility check."""
        self.logger.debug("Executing delayed initial show...")
        try:
            # Replace the call to the old manager with the proven, authoritative function.
            self._execute_refresh()
            
            if self.isVisible():
                self.logger.debug("Widget shown after stabilization")
        except Exception as e:
            self.logger.error(f"Error in delayed initial show: {e}", exc_info=True)
            # Ensure widget is hidden if an error occurs during the initial check.
            self.setVisible(False)
        # One-time 2.0 welcome, once the widget has settled.
        QTimer.singleShot(1200, self._maybe_show_welcome)

    def _maybe_show_welcome(self) -> None:
        """First-run onboarding. An upgrader gets the one-time 2.0 welcome dialog; a brand-new install
        gets no pop-up (the hover card's gesture hint is its onboarding)."""
        # Brand-new install: no interrupting pop-up until there's a real guided tour (the old 'unfold'
        # flyout only opened the Monitor and was removed in 2.2). We still advance the first-run flags
        # so nothing pops later and the upgrade dialog stays correctly suppressed.
        if self.config.get("first_run_ever", True):
            self.config["first_run_ever"] = False
            self.config["first_run_v2_seen"] = True  # a new 2.0 user has no "before"
            self.update_config({"first_run_ever": False, "first_run_v2_seen": True})
            return

        if self.config.get("first_run_v2_seen", False):
            return
        try:
            from netspeedtray.views.welcome_dialog import WelcomeDialog
            dlg = WelcomeDialog(self.i18n, parent=self)
            dlg.exec()
            if dlg.action == WelcomeDialog.ACTION_WHATS_NEW:
                import webbrowser
                webbrowser.open(
                    f"https://github.com/{constants.app.GITHUB_OWNER}/{constants.app.GITHUB_REPO}/releases/latest"
                )
        except Exception as e:
            self.logger.error(f"Error showing welcome dialog: {e}", exc_info=True)
        # Mark seen (even if the dialog errored) so it appears at most once.
        self.config["first_run_v2_seen"] = True
        self.update_config({"first_run_v2_seen": True})

    def _on_latency(self, sample) -> None:
        """A LatencySample arrived (main thread, queued from the probe thread). Update the live
        attributes + each hop's rolling loss%, and persist both RTTs and both hops' loss fractions so the
        Monitor's window loss% (the ISP-dispute figure) is honest for the internet, not just the router."""
        from netspeedtray.core.latency_probe import HOP_ANCHOR, headline_hop
        lat = constants.network.latency
        self.latency_gw = sample.gw_ms
        self.latency_anchor = sample.anchor_ms
        windows = self._latency_loss_windows
        windows.add(sample)
        self.latency_loss_gw = windows.gw_loss
        self.latency_loss_anchor = windows.anchor_loss
        self.latency_headline_hop = headline_hop(sample.anchor_enabled)
        self.latency_loss = (self.latency_loss_anchor if self.latency_headline_hop == HOP_ANCHOR
                             else self.latency_loss_gw)
        self.latency_headline_ms = windows.last_ms(self.latency_headline_hop)
        self.latency_link_lost = bool(getattr(sample, "gw_link_lost", False))
        ws = getattr(self, "widget_state", None)
        if ws is not None:
            try:
                if sample.gw_ms is not None:
                    ws.add_hardware_stat("latency_gw", float(sample.gw_ms))
                if sample.anchor_ms is not None:
                    ws.add_hardware_stat("latency_anchor", float(sample.anchor_ms))
                # The loss series: lost / sent of this cycle's burst (0, .25, .5, .75, 1). Old 0/1 rows
                # (one echo per cycle) mean the same thing - the share of that cycle's probes lost - so
                # every reader (rollup AVG, outage_summary's >= 0.5, the exporter) takes both unchanged.
                if not getattr(sample, "gw_excluded", False):   # gateway changed mid-cycle: not measured
                    ws.add_hardware_stat(lat.STAT_GW_TIMEOUT, float(sample.gw_loss_fraction))
                if sample.anchor_enabled:
                    ws.add_hardware_stat(lat.STAT_ANCHOR_TIMEOUT, float(sample.anchor_loss_fraction))
            except Exception:
                pass

    def apply_latency_probe(self) -> None:
        """Start / stop the latency probe to match ``latency_enabled``. Called at startup and from every
        settings apply; idempotent. Switching it off stops the probe at once (the probe also reads the
        switch before every echo, so not even the rest of a burst goes out - to the router or the public
        host) and clears the live figures; switching it back on starts a fresh probe. Never blocks the
        GUI thread: a stopped probe finishes its in-flight echo (<= 1 s) on its own and is joined at
        shutdown if it has not by then."""
        enabled = bool(self.config.get("latency_enabled", True))
        probe = getattr(self, "latency_probe", None)
        if probe is not None:
            probe.set_config(self.config)   # a settings rollback replaces the dict
            if enabled and probe.isRunning() and probe._running:
                return
            self._retire_latency_probe(probe)
            self.latency_probe = None
        if not enabled:
            if probe is not None:
                self._clear_live_latency()
            return
        try:
            from netspeedtray.core.latency_probe import LatencyProbe
            self.latency_probe = LatencyProbe(self.config)
            self.latency_probe.latency_ready.connect(self._on_latency)
            self.latency_probe.start()
        except Exception as e:
            self.latency_probe = None
            self.logger.debug("LatencyProbe not started: %s", e)

    def invalidate_latency_gateway(self) -> None:
        """Resume from sleep / a new primary route: the probe looks its default gateway up again before
        the next burst (it would otherwise ping the cached one for up to a minute and record that
        router's silence as loss)."""
        probe = getattr(self, "latency_probe", None)
        if probe is not None:
            try:
                probe.invalidate_gateway()
            except Exception as e:
                self.logger.debug("latency gateway not invalidated: %s", e)

    def _retire_latency_probe(self, probe) -> None:
        """Stop ``probe`` without waiting; keep a reference until its thread has ended (a QThread
        destroyed while running aborts the process). Shutdown joins whatever is still here."""
        try:
            probe.stop()
            probe.latency_ready.disconnect(self._on_latency)   # a sample already in flight is dropped
        except Exception:
            pass
        retiring = getattr(self, "_retired_latency_probes", None)
        if retiring is None:
            retiring = self._retired_latency_probes = []
        retiring[:] = [p for p in retiring if p.isRunning()]
        if probe.isRunning():
            retiring.append(probe)

    def _clear_live_latency(self) -> None:
        """Latency switched off: nothing on screen may keep showing the last reading."""
        from netspeedtray.core.latency_probe import LatencyLossWindows
        self.latency_gw = self.latency_anchor = None
        self.latency_loss = self.latency_loss_gw = self.latency_loss_anchor = None
        self.latency_headline_hop = None
        self.latency_headline_ms = None
        self.latency_link_lost = False
        self._latency_loss_windows = LatencyLossWindows()

    def apply_metrics_endpoint(self) -> None:
        """Start / stop / move the local metrics endpoint to match the config (2.2 scope B). Called at
        startup and from every settings apply; idempotent, so a live-preview tick that did not touch
        the endpoint keys costs nothing. A port that cannot be bound is reported via
        metrics_endpoint.status (Settings shows it), never raised."""
        enabled = bool(self.config.get("metrics_endpoint_enabled", False))
        if self.metrics_endpoint is None and not enabled:
            return   # never enabled: not even the object exists
        try:
            if self.metrics_endpoint is None:
                from netspeedtray.core.metrics_endpoint import MetricsEndpoint
                self.metrics_endpoint = MetricsEndpoint()
            was_running = self.metrics_endpoint.running
            self.metrics_endpoint.apply(enabled, int(self.config.get(
                "metrics_endpoint_port", constants.network.metrics.DEFAULT_PORT)))
            if self.metrics_endpoint.running and not was_running:
                self._publish_metrics_snapshot()   # the first scrape should not wait for a tick
        except Exception as e:
            self.logger.error("Could not apply the metrics endpoint setting: %s", e, exc_info=True)

    def _publish_metrics_snapshot(self, _stats: object = None) -> None:
        """GUI thread: freeze the live values into the endpoint's snapshot. The server thread only
        ever reads that frozen object - it never touches the widget, controller or WidgetState."""
        ep = self.metrics_endpoint
        if ep is None:
            return
        if not ep.running:
            try:
                # A port that was busy is retried with backoff; cheap when nothing is due.
                if not ep.retry_if_due():
                    return
            except Exception as e:
                self.logger.debug("metrics endpoint retry failed: %s", e)
                return
        try:
            from netspeedtray.core.metrics_snapshot import build_snapshot
            ep.publish(build_snapshot(self))
        except Exception as e:
            self.logger.debug("metrics snapshot not published: %s", e)

    def _on_monitor_error(self, message: str) -> None:
        """
        The monitor thread crossed its error threshold (now recoverable - it keeps retrying
        with backoff). Surface a calm one-time flyout so a degraded readout isn't silent.
        """
        try:
            self._show_usage_alert(
                self.i18n.MONITOR_ERROR_FLYOUT_TITLE,
                self.i18n.MONITOR_ERROR_FLYOUT_BODY,
            )
        except Exception as e:
            self.logger.error("Error showing monitor-error notice: %s", e, exc_info=True)

    def _on_db_schema_incompatible(self, file_version: int, supported_version: int,
                                   backup_path: str) -> None:
        """
        The database on disk was written by a newer build, so the worker runs read-only
        and records nothing (the downgrade guard). Without this flyout a rolled-back
        install looks perfectly healthy while silently recording nothing, forever - the
        exact shape that gets filed as "this version stopped recording". The worker's
        ERROR log names the newest pre-migration backup by full path; the flyout stays
        calm and short.
        """
        try:
            self.logger.info(
                "Surfacing read-only database notice (file v%d > supported v%d; backup: %s).",
                file_version, supported_version, backup_path or "none found",
            )
            self._show_usage_alert(
                self.i18n.DB_READONLY_FLYOUT_TITLE,
                self.i18n.DB_READONLY_FLYOUT_BODY,
            )
        except Exception as e:
            self.logger.error("Error showing database read-only notice: %s", e, exc_info=True)

    def _on_db_migration_deferred(self, reason: str) -> None:
        """
        The v7 -> v8 history upgrade could not finish this launch (low disk, the file held by
        another program, an interrupted earlier attempt), so the worker records nothing until the
        next start, when it retries. Same calm flyout as the downgrade notice - the title is shared,
        the body says what happens next; the worker's ERROR log carries the technical reason.
        """
        try:
            self.logger.info("Surfacing deferred database upgrade notice (reason: %s).", reason)
            body = (self.i18n.DB_UPGRADE_PAUSED_NO_SPACE_FLYOUT_BODY if reason == "no_space"
                    else self.i18n.DB_UPGRADE_PAUSED_FLYOUT_BODY)
            self._show_usage_alert(self.i18n.DB_READONLY_FLYOUT_TITLE, body)
        except Exception as e:
            self.logger.error("Error showing deferred database upgrade notice: %s", e, exc_info=True)

    def _on_db_history_upgraded(self) -> None:
        """
        This launch upgraded the history to v8 (2.2). Say so once, calmly: older days now read as
        estimates and can total LESS than 2.1 showed - 2.1 over-counted quiet minutes - and the
        data-cap count (the odometer) is unaffected. Without it the drop looks like lost history.
        The persisted flag keeps it to one showing even if a v7 file is ever upgraded again.
        """
        try:
            if self.config.get("history_upgrade_notice_shown", False):
                return
            self.logger.info("Surfacing the one-time history-upgraded notice.")
            self._show_usage_alert(self.i18n.DB_UPGRADE_DONE_FLYOUT_TITLE,
                                   self.i18n.DB_UPGRADE_DONE_FLYOUT_BODY,
                                   auto_dismiss_ms=constants.timers.HISTORY_UPGRADE_NOTICE_MS)
            self.config_controller.update_config({"history_upgrade_notice_shown": True},
                                                 apply_and_repaint=False)
        except Exception as e:
            self.logger.error("Error showing the history-upgraded notice: %s", e, exc_info=True)

    def _show_usage_alert(self, title: str, message: str, auto_dismiss_ms: int = 15000) -> None:
        """Show a data-cap usage alert via the flyout (no system-tray icon needed)."""
        try:
            from netspeedtray.views.flyout import Flyout
            from PyQt6 import sip
            previous = getattr(self, "_usage_alert_flyout", None)
            if previous is not None and not sip.isdeleted(previous):
                previous.close()   # Qt-owned and self-deleting: replace it, never stack two
            self._usage_alert_flyout = Flyout(title, message, auto_dismiss_ms=auto_dismiss_ms)
            geo = self.frameGeometry()
            self._usage_alert_flyout.show_at(QPoint(geo.left(), geo.top()))
        except Exception as e:
            self.logger.error(f"Error showing usage alert: {e}", exc_info=True)

    def pause(self) -> None:
        """Freeze the live readout. The monitor keeps recording history in the background; only
        the on-widget numbers stop updating (see the `is_paused` gate in the update_* slots),
        until resume(). Opt-in - surfaced in the tray menu only when `pause_in_menu` is set."""
        if self.is_paused:
            self.logger.debug("Widget already paused")
            return
        self.logger.info("Pausing widget updates")
        # is_paused is a transient live-view toggle only - deliberately NOT persisted, so the
        # widget never reboots into a frozen state that looks like a hang.
        self.is_paused = True
        self._paused_histories = None
        try:
            self._paused_histories = self._live_graph_histories()
        except Exception as e:
            self.logger.debug("Could not snapshot the graph for pause: %s", e)
        self.update()


    def resume(self) -> None:
        """Resume the live readout after pause()."""
        if not self.is_paused:
            self.logger.debug("Widget already running")
            return
        self.logger.info("Resuming widget updates")
        self.is_paused = False
        self._paused_histories = None
        self.update()


    def update_display_speeds(self, upload_mbps: float, download_mbps: float) -> None:
        """
        Slot for the controller's `display_speed_updated` signal.
        Receives aggregated speeds in Mbps and schedules a repaint of the widget.
        """
        if self.is_paused:  # frozen by the user - keep the last numbers on screen
            return
        self.upload_speed = upload_mbps
        self.download_speed = download_mbps
        self.update() # Trigger a repaint
        if getattr(self, "_hover_card_pinned", False):
            self._refresh_pinned_usage()


    def _bar_shows(self, modes: Tuple[str, ...]) -> bool:
        """The widget's painted display mode is one of ``modes`` (see _CPU_BAR_MODES). The reading is
        stored either way - the hover card and the Monitor window use it."""
        return effective_display_mode(self.config) in modes

    def update_cpu_usage(self, usage: float) -> None:
        """Update CPU usage and trigger repaint."""
        if self.is_paused:
            return
        self.cpu_usage = usage
        self._refresh_hover_hardware()
        if self._bar_shows(_CPU_BAR_MODES):
            self.update()

    def update_gpu_usage(self, usage: float) -> None:
        """Update GPU usage and trigger repaint."""
        if self.is_paused:
            return
        self.gpu_usage = usage
        self._refresh_hover_hardware()
        if self._bar_shows(_GPU_BAR_MODES):
            self.update()

    def update_network_identity(self, identity: Optional[object]) -> None:
        """Update the connected network's identity (Wi-Fi band / SSID) and repaint if shown.

        `identity` is a NetworkIdentity (or None). Sub-polled ~5s in StatsMonitorThread, never on the
        hot path. The on-widget rendering + width reserve land with the identity element (KICKOFF §3);
        for now we store the value so the paint path can read it and repaint when the feature is on.
        """
        if self.is_paused:
            return
        self.network_identity = identity
        if not self.config.get("show_network_identity", False):
            return
        # The user asked for the network name but Windows Location is off (SSID came back blocked):
        # explain once why, and how, rather than silently showing nothing.
        self._maybe_prompt_location(identity)
        # The reserved identity slot depends on which band shows (alert_only flips on/off 2.4G) and on
        # the SSID text (variable width). Both change only on a network change (rare), so re-lay out on
        # that transition - keeping the widget tight - and otherwise just repaint.
        sig = self._identity_reserve_signature()
        if sig != self._identity_reserve_sig_last:
            self._identity_reserve_sig_last = sig
            try:
                self.layout_manager.resize_widget_for_font()
                return
            except Exception:
                pass
        self.update()

    def update_cpu_temp(self, temp: Optional[float]) -> None:
        """Update CPU temperature and trigger repaint."""
        if self.is_paused:
            return
        self.cpu_temp = temp
        if self._bar_shows(_CPU_BAR_MODES):
            self.update()

    def update_gpu_temp(self, temp: Optional[float]) -> None:
        """Update GPU temperature and trigger repaint."""
        if self.is_paused:
            return
        self.gpu_temp = temp
        if self._bar_shows(_GPU_BAR_MODES):
            self.update()

    def update_cpu_power(self, power: Optional[float]) -> None:
        """Update CPU power draw and trigger repaint."""
        if self.is_paused:
            return
        self.cpu_power = power
        if self._bar_shows(_CPU_BAR_MODES):
            self.update()

    def update_gpu_power(self, power: Optional[float]) -> None:
        """Update GPU power draw and trigger repaint."""
        if self.is_paused:
            return
        self.gpu_power = power
        if self._bar_shows(_GPU_BAR_MODES):
            self.update()

    def update_ram_info(self, used: float, total: float) -> None:
        """Update RAM info and trigger repaint."""
        if self.is_paused:
            return
        self.ram_used = used
        self.ram_total = total
        self._refresh_hover_hardware()
        if self._bar_shows(_CPU_BAR_MODES):
            self.update()

    def update_vram_info(self, used: float, total: float) -> None:
        """Update VRAM info and trigger repaint."""
        if self.is_paused:
            return
        self.vram_used = used
        self.vram_total = total if total >= 0 else None
        if self._bar_shows(_GPU_BAR_MODES):
            self.update()

    def _rotate_cycle(self) -> None:
        """Rotates the displayed metric forward when in 'cycle' mode (auto-timer)."""
        self._step_cycle(1)

    def _step_cycle(self, step: int) -> None:
        """
        Steps the cycle index by `step` (wrapping) and repaints with the new metric.
        `step` is +1 for the auto-timer / scroll-down, -1 for scroll-up. A no-op when
        only one metric is available (nothing to cycle through).
        """
        modes = ["network_only"]
        if self.config.get("graph_enabled", False) and self.config.get("graph_split_directions", False):
            # Separate upload/download graphs: each direction is a phase of its own, so the widget
            # keeps its size instead of widening every phase to two panels.
            modes = split_network_phases(bool(self.config.get("swap_upload_download", False)))
        if self.config.get("monitor_cpu_enabled", False): modes.append("cpu_only")
        if self.config.get("monitor_gpu_enabled", False): modes.append("gpu_only")

        if not modes: return

        self._cycle_index = (self._cycle_index + step) % len(modes)
        self._current_cycle_mode = modes[self._cycle_index]
        self.update() # Trigger repaint with new mode





    # _load_initial_config removed as it is now handled by ConfigController class


    def _on_taskbar_alignment_changed(self) -> None:
        """WM_SETTINGCHANGE saw TaskbarAl flip (rc.1 B3). On build 26200 this broadcast never arrives,
        so the side-bar watcher also reads the value on its own hook and on the 1 s tick; this is
        one more hint. The watcher re-decides once the re-laid bar has settled."""
        pm = getattr(self, "position_manager", None)
        if pm is None:
            return
        try:
            pm.note_taskbar_alignment_hint()
        except Exception as e:
            self.logger.debug("alignment change handling failed: %s", e)

    def _on_foreground_for_side_bar(self, _hwnd: int = 0) -> None:
        """A new foreground window often means a new app button on the taskbar (side-bar watcher)."""
        pm = getattr(self, "position_manager", None)
        if pm is None or self.config.get("free_move", False):
            return
        try:
            pm.note_foreground_change()
        except Exception as e:
            self.logger.debug("side-bar foreground hint failed: %s", e)

    def _on_theme_changed(self) -> None:
        """Delegates theme change handling."""
        # An open card (pinned ones last for minutes) keeps the old theme's colours: close it; the
        # next hover builds it in the new theme.
        self.dismiss_hover_card()
        self.theme_manager.on_theme_changed()   # refreshes the Windows-mode flag first
        dialog = getattr(self, "settings_dialog", None)
        if dialog is not None:
            try:
                dialog.on_taskbar_theme_changed()   # preview strip backdrop + colours
            except Exception as e:
                self.logger.debug("Settings preview theme refresh failed: %s", e)





    def _init_ui_components(self) -> None:
        """Initialize UI-related elements: icon, tray, event handler."""
        self.logger.debug("Initializing UI components...")

        self.tray_manager = TrayIconManager(self, self.i18n)
        self.tray_manager.initialize()
        
        # Input Handler must be initialized here, after tray_manager and position_manager exist
        self.input_handler = InputHandler(
            widget=self,
            position_manager=self.position_manager,
            tray_manager=self.tray_manager
        )
        
        self.system_event_handler = SystemEventHandler(self)






    def _setup_connections(self) -> None:
        """
        Connects signals from core components and initializes the WinEventHooks for
        stable, debounced visibility management.
        """
        self.logger.debug("Setting up signal connections and WinEventHooks...")
        if not all([self.widget_state, self.controller]):
            raise RuntimeError("Core components missing during signal connection setup.")
        try:
            # Connect core component signals
            self.monitor_thread.stats_ready.connect(self.controller.handle_stats)
            # AFTER the controller (queued slots run in connection order): the metrics endpoint's
            # snapshot is built from what the controller just computed. No-op while it is off.
            self.monitor_thread.stats_ready.connect(self._publish_metrics_snapshot)
            
            # NOTE: the display/cpu/gpu/temp/power/ram/vram slots are wired exactly
            # once in StatsController.set_view() (called at construction). Wiring any
            # of them again here ran the slot twice per tick, and the legacy
            # update_display_hardware lambda also repainted unconditionally even in
            # network_only mode. Do not re-add them.
            
            # One-time LHM notice
            self.monitor_thread.lhm_not_detected.connect(self._on_lhm_not_detected)

            # Degraded-monitor notice (recoverable circuit breaker fired). Previously wired to
            # nothing, so a stalled monitor died silently; now it surfaces a calm flyout.
            self.monitor_thread.error_occurred.connect(self._on_monitor_error)

            # Downgrade guard (DB written by a newer build): the worker refuses every write;
            # make that visible instead of log-only. The rolled-back build is the only one
            # that can ever show this message. Delivery is queued through WidgetState, so
            # a guard tripped during startup arrives once the event loop spins - after this
            # connect has run.
            self.widget_state.db_schema_incompatible.connect(self._on_db_schema_incompatible)
            self.widget_state.db_migration_deferred.connect(self._on_db_migration_deferred)
            self.widget_state.db_history_upgraded.connect(self._on_db_history_upgraded)

            # Start the monitoring thread
            self.monitor_thread.start()

            # Latency probe on its own thread (gateway by default; public anchor opt-in). A 1 s ICMP
            # timeout must never sit on the stats poll, so it's a separate QThread.
            # Started / stopped live with Settings > Connection > Latency (apply_latency_probe).
            self.apply_latency_probe()

            # 1. System Event Handler (replaces manual WinEventHooks)
            self.system_event_handler.foreground_app_changed.connect(self._execute_refresh)
            self.system_event_handler.foreground_app_changed.connect(self._on_foreground_for_hover_card)
            self.system_event_handler.foreground_app_changed.connect(self._on_foreground_for_side_bar)
            self.system_event_handler.taskbar_changed.connect(self.update_position)
            self._refresh_cached_layout_mode()
            self.system_event_handler.theme_changed.connect(self._on_theme_changed)
            self.system_event_handler.taskbar_alignment_changed.connect(self._on_taskbar_alignment_changed)
            
            # Emergency-hide on unambiguous fullscreen - but honor the user's
            # "keep visible over fullscreen" choice (issue #107). Without this guard
            # the immediate-hide path bypassed keep_visible_fullscreen, which
            # _execute_refresh() already respects, so the widget vanished anyway.
            #
            # Also skip the hide when free-floating (#188): a taskbar-less preferred
            # monitor falls back to the *primary* taskbar for obstruction checks, so a
            # fullscreen app on the primary would otherwise blink the off-taskbar widget
            # out until the next refresh re-shows it. _execute_refresh() already forces
            # it visible in this state; mirror that here so there's no transient flicker.
            self.system_event_handler.immediate_hide_requested.connect(self._on_immediate_hide_requested)

            # Immediately re-assert topmost when the taskbar gains focus, so the widget
            # never lingers behind the activating taskbar (the debounced refresh is too
            # slow and shows as a "hide and return"). Restores a410aae's _handle_taskbar_focus.
            self.system_event_handler.taskbar_focused.connect(self._ensure_win32_topmost)

            # Handle taskbar restarts - both the 3s validity poll and the instant
            # TaskbarCreated broadcast (nativeEvent) land in _recover_taskbar_restarted.
            self.system_event_handler.taskbar_restarted.connect(self._recover_taskbar_restarted)

            # Burial recovery, event-driven (#314, #238): the shell raised a window - possibly
            # the taskbar over the widget. Coalesced in SystemEventHandler; the check itself is
            # a short Z-order walk, and it re-seats only when the widget really is buried.
            self.system_event_handler.zorder_changed.connect(self._recover_if_buried)

            # React to monitor topology changes (add/remove/primary swap, KVM, dock/undock):
            # re-validate the position + re-assert the widget. A set-and-forget app must follow
            # its taskbar across these without the user nudging it.
            app = QApplication.instance()
            if app is not None:
                app.screenAdded.connect(lambda _s: self._on_environment_changed("screenAdded"))
                app.screenRemoved.connect(lambda _s: self._on_environment_changed("screenRemoved"))
                app.primaryScreenChanged.connect(lambda _s: self._on_environment_changed("primaryScreenChanged"))

            self.system_event_handler.start()

            # Last, so the mirrors' slots run after the widget's own on every shared signal.
            self.apply_mirrors()

            self.logger.debug("Signal connections and WinEventHooks established successfully.")
        except Exception as e:
            self.logger.error("Error setting up signal connections: %s", e, exc_info=True)
            raise RuntimeError("Failed to establish critical signal connections") from e

        



    def paintEvent(self, event: QPaintEvent) -> None:
        """
        Handles all painting for the widget via the shared `render_widget` path so the
        live widget and every preview draw identically (refactor C1).
        """
        if not self.isVisible():
            return

        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)

            # Base hit-test layer (nearly transparent) - widget-only, so the frameless
            # window still receives mouse events across its full rect.
            painter.fillRect(self.rect(), QColor(0, 0, 0, 1))

            if not self.renderer or not self.current_metrics:
                self._draw_paint_error(painter, "Render Error")
                return

            render_widget(
                painter, self.rect(), self.renderer, self.renderer.config,
                self._build_metrics(),
                layout_mode=self._cached_layout_mode,
                cycle_mode=self._current_cycle_mode,
                network_width=getattr(self.layout_manager, "_network_width", None),
                font=self.current_font,
                paused=self.is_paused,
            )
        except Exception as e:
            self.logger.error(f"Error in paintEvent: {e}", exc_info=True)
        finally:
            if painter.isActive():
                painter.end()

    def _live_graph_histories(self) -> Tuple[list, list, list]:
        ws = self.widget_state
        return (list(ws.get_aggregated_speed_history()), list(ws.cpu_history), list(ws.gpu_history))

    def _graph_histories(self) -> Tuple[list, list, list]:
        """The (net, cpu, gpu) histories the mini-graph draws. While paused, the ones frozen at
        pause(): the numbers are frozen, and the history keeps recording underneath, so any repaint
        (the Auto-Cycle tick, a hover, a resize) used to scroll a live graph under frozen numbers."""
        frozen = getattr(self, "_paused_histories", None)
        if getattr(self, "is_paused", False) and frozen is not None:
            return frozen
        return self._live_graph_histories()

    def _build_metrics(self) -> WidgetMetrics:
        """Snapshot the current live state into the shared paint path's metrics object."""
        identity_text, identity_color, identity_solid = self._identity_presentation()
        identity_ssid = self._identity_ssid_text()
        net_history, cpu_history, gpu_history = self._graph_histories()
        return WidgetMetrics(
            upload_mbps=self.upload_speed,
            download_mbps=self.download_speed,
            cpu_usage=self.cpu_usage,
            gpu_usage=self.gpu_usage,
            cpu_temp=self.cpu_temp,
            gpu_temp=self.gpu_temp,
            cpu_power=self.cpu_power,
            gpu_power=self.gpu_power,
            ram_used=self.ram_used,
            ram_total=self.ram_total,
            vram_used=self.vram_used,
            vram_total=self.vram_total,
            net_history=net_history,
            cpu_history=cpu_history,
            gpu_history=gpu_history,
            identity_band=identity_text,
            identity_band_color=identity_color,
            identity_band_solid=identity_solid,
            identity_ssid=identity_ssid,
        )

    def _identity_ssid_text(self) -> Optional[str]:
        """The truncated SSID / connection name to draw, or None (off, band-only mode, or unavailable).

        Location-gated: for Wi-Fi with Location off, `NetworkIdentity.name` is None (ssid_blocked) and
        this returns None - the band still shows and Settings explains why (never a blank/fake name).
        """
        if not self.config.get("show_network_identity", False):
            return None
        if self.config.get("identity_mode", "band") not in ("ssid", "both"):
            return None
        from netspeedtray.utils.network_utils import truncate_ssid
        ni = self.network_identity
        return truncate_ssid(getattr(ni, "name", None) if ni is not None else None)

    def _identity_presentation(self) -> "tuple[Optional[str], Optional[str], bool]":
        """(text, color_hex, solid) for the band pill, honoring show_network_identity + mode + band_display.

        Returns (None, None, False) when the feature is off, the mode hides the band, the band is
        unknown, or band_display == 'alert_only' and the current band is good (clean widget = you're fine).
        """
        if not self.config.get("show_network_identity", False):
            return None, None, False
        if self.config.get("identity_mode", "band") == "ssid":
            return None, None, False
        from netspeedtray.utils.network_utils import resolve_band_presentation
        ni = self.network_identity
        band = getattr(ni, "band", None) if ni is not None else None
        return resolve_band_presentation(band, self.config.get("band_display", "always"))

    def _identity_reserve_px(self, fm) -> int:
        """Total width to reserve for the identity badge (band pill / SSID pill / compound), given `fm`.

        Uses the exact `identity_layout` geometry the renderer draws with, so reserve == draw (no #106
        clip). The badge width tracks the ACTUAL band + truncated SSID; a network change (rare) re-lays
        out. Either element being absent (alert_only on a good band, blocked SSID, band-only) shrinks it.
        """
        from netspeedtray.utils.widget_renderer import IDENTITY_BAND_GAP_PX, identity_layout
        band_text, _c, _s = self._identity_presentation()
        ssid = self._identity_ssid_text()
        total, _parts = identity_layout(fm, ssid, band_text)
        return (IDENTITY_BAND_GAP_PX + total) if total else 0

    def _identity_reserve_signature(self) -> object:
        """What determines the badge width - re-layout only when this changes (a network change)."""
        band_text, _c, _s = self._identity_presentation()
        return (band_text, self._identity_ssid_text())

    # Number of app runs the gesture hint rides along in the hover card before it fades for good.
    _TOOLTIP_HINT_MAX_SHOWS = 8
    # How long the pointer must rest on the widget before the usage card appears.
    _HOVER_CARD_DELAY_MS = 350
    # Cache window for the (today, this-month) DB totals so rapid hovers don't hammer the DB.
    _HOVER_USAGE_TTL_SEC = 30.0
    # Hover-opened cards that carry the "Click the widget to keep this open" footer (once pinned: 0).
    _PIN_HINT_MAX_SHOWS = 5

    def invalidate_usage_cache(self) -> None:
        """Drop the hover card's cached today/this-month totals so the next hover re-reads them
        (after Delete all history - otherwise it shows deleted usage for up to the TTL)."""
        self._hover_usage_cache = None
        self._hover_usage_cache_ts = 0.0

    @staticmethod
    def _hover_card_enabled(config: Dict[str, Any]) -> bool:
        """Whether ANY section of the hover card can render - enterEvent's arm gate.

        Must stay in sync with _show_usage_hover_card, which decides what actually
        shows: the usage rows, the gesture hint, the battery section
        (power_display_enabled - the General tab's battery display) and the hardware
        section."""
        return (bool(config.get("show_usage_on_hover", True))
                or bool(config.get("show_hover_tips", True))
                or bool(config.get("power_display_enabled", False)))

    def enterEvent(self, event) -> None:
        """On hover, arm the usage card (shown after a short rest, positioned above the taskbar).
        Skipped entirely when the user has turned every hover section off in Settings."""
        try:
            hover_enabled = (self._hover_card_enabled(self.config)
                             or self._hover_hardware_wanted())
            if hover_enabled and self._hover_card is None and not self._hover_card_timer.isActive():
                self._hover_card_timer.start(self._HOVER_CARD_DELAY_MS)
        except Exception as e:
            self.logger.debug("hover card arm failed: %s", e)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        """When the pointer leaves the widget, cancel/hide the usage card - unless it is pinned."""
        self._on_pointer_left()
        super().leaveEvent(event)

    def _on_pointer_left(self) -> None:
        try:
            self._hover_card_timer.stop()
            if not self._hover_card_pinned:
                self._hide_usage_hover_card()
        except Exception as e:
            self.logger.debug("hover card hide failed: %s", e)

    # --- Click-to-pin (side-taskbar design §2, Phase B) -------------------------------------------

    def hover_card_state(self) -> Optional[str]:
        """"pinned", "hover" (shown by resting the pointer) or None (no card up)."""
        if self._hover_card is None:
            return None
        return "pinned" if self._hover_card_pinned else "hover"

    def pin_hover_card(self) -> None:
        """Keep the card open: pin the one showing from hover, or (a touch tap / a click before the
        hover delay) open it pinned. Pinning itself never activates anything: the card keeps its
        no-activate style and is only moved / restyled. The click that pins it, though, lands on the
        widget, and the widget only *shows* without activating (WA_ShowWithoutActivating) - a click
        still makes it the foreground window, as every click on it always has, so the app the user
        was typing in loses the keyboard. That foreground change is ours, so it does not close the
        card (``_on_foreground_for_hover_card``)."""
        self._hover_card_timer.stop()
        card = self._hover_card
        if card is None:
            self._show_usage_hover_card(pinned=True)
            return
        self._hover_card_pinned = True
        set_pinned = getattr(card, "set_pinned", None)
        if callable(set_pinned):
            set_pinned(True)
        self._retire_pin_hint()

    def _retire_pin_hint(self) -> None:
        """The user has pinned a card: they know the gesture - stop showing the footer line."""
        try:
            if int(self.config.get("pin_hint_shown_count", 0)) < self._PIN_HINT_MAX_SHOWS:
                self.config_controller.update_config(
                    {"pin_hint_shown_count": self._PIN_HINT_MAX_SHOWS}, apply_and_repaint=False)
        except Exception as e:
            self.logger.debug("pin hint retire failed: %s", e)

    def _pin_hint_text(self) -> Optional[str]:
        """The "Click the widget to keep this open" footer for a card opened by hover: on the first
        _PIN_HINT_MAX_SHOWS such cards (its own counter - independent of the gesture hint's
        graduation, which may already be over for an upgrader), and never again once pinned."""
        try:
            count = int(self.config.get("pin_hint_shown_count", 0))
            if count >= self._PIN_HINT_MAX_SHOWS:
                return None
            self.config_controller.update_config({"pin_hint_shown_count": count + 1},
                                                 apply_and_repaint=False)
            return self.i18n.HOVER_CARD_PIN_HINT
        except Exception:
            return None

    def dismiss_hover_card(self) -> None:
        """Close the card (pinned or not), cancel a pending hover show and a pending click-to-pin."""
        try:
            self._hover_card_timer.stop()
        except Exception:
            pass
        handler = getattr(self, "input_handler", None)
        cancel = getattr(handler, "cancel_pending_click", None)
        if callable(cancel):
            cancel()
        self._hide_usage_hover_card()

    def _reanchor_hover_card(self) -> None:
        """The widget moved without a dock change (tray grew, reposition): a pinned card follows it.
        A hover card needs nothing - the pointer leaving the moved widget closes it."""
        card = getattr(self, "_hover_card", None)    # moveEvent can fire mid-__init__
        if card is None or not getattr(self, "_hover_card_pinned", False):
            return
        try:
            card.reanchor(self.frameGeometry())
        except Exception as e:
            self.logger.debug("hover card re-anchor failed: %s", e)

    def _refresh_pinned_usage(self) -> None:
        """A pinned card stays up for minutes: keep its usage rows current, at the hardware rows'
        cadence (the totals themselves are TTL-cached, so this is not a DB read per tick)."""
        card = self._hover_card
        if card is None or not self._hover_card_pinned or not self.config.get("show_usage_on_hover", True):
            return
        now = time.monotonic()
        if now - getattr(self, "_hover_usage_refreshed", 0.0) < self._HOVER_HW_REFRESH_SEC:
            return
        self._hover_usage_refreshed = now
        try:
            today, month = self._hover_usage_totals()
            card.update_usage(today, month, self._hover_cap_info())
        except Exception as e:
            self.logger.debug("pinned card usage refresh failed: %s", e)

    def _on_foreground_for_hover_card(self, hwnd: int = 0) -> None:
        """Another app came to the front: a pinned card has done its job. Our own windows (the widget
        itself when clicked, the card, a menu) don't count."""
        if not self._hover_card_pinned or not hwnd:
            return
        try:
            import win32process
            _tid, pid = win32process.GetWindowThreadProcessId(int(hwnd))
            if pid == os.getpid():
                return
        except Exception:
            pass
        self.dismiss_hover_card()

    def _show_usage_hover_card(self, pinned: bool = False) -> None:
        """Build and show the usage card just above the widget (never under the taskbar). ``pinned``
        opens it already pinned (a click/tap, not a hover)."""
        if self._is_context_menu_visible or self._dragging or not self.isVisible():
            return
        # Don't pop an always-on-top card over a fullscreen app, even when the widget itself is
        # kept visible there (keep_visible_fullscreen).
        handler = getattr(self, "system_event_handler", None)
        if handler is not None and handler.is_fullscreen_active():
            return
        try:
            from netspeedtray.views.usage_flyout import UsageFlyout
            show_data = bool(self.config.get("show_usage_on_hover", True))
            show_tips = bool(self.config.get("show_hover_tips", True))
            show_power = bool(self.config.get("power_display_enabled", False))
            hint = self._hover_hint_text() if show_tips else None
            today, month = self._hover_usage_totals() if show_data else (None, None)
            cap = self._hover_cap_info() if show_data else None
            power = self._hover_power_summary() if show_power else None
            hardware = self._hover_hardware_snapshot() if self._hover_hardware_wanted() else None
            if hint is None and today is None and power is None and not hardware:
                return  # nothing to show (all toggles off, or tips graduated while data is off)
            note = (self.i18n.USAGE_RECORDED_HISTORY_NOTE
                    if today is not None and getattr(self, "_hover_usage_reconstructed", False) else None)
            month_label = (self.i18n.USAGE_THIS_PERIOD_LABEL
                           if today is not None and getattr(self, "_hover_usage_period_start", None)
                           else None)
            # "Click the widget to keep this open" footer: on a card opened by hover only (a pinned
            # card is already open), for the first few such cards.
            pin_hint = None if pinned else self._pin_hint_text()
            card = UsageFlyout(self.i18n, today, month, hint=hint, cap=cap, power=power,
                               history_note=note, hardware=hardware, month_label=month_label,
                               pin_hint=pin_hint)
            self._hover_card_pinned = bool(pinned)
            self._hover_hw_refreshed = time.monotonic()
            self._hover_usage_refreshed = time.monotonic()
            dismiss_signal = getattr(card, "dismiss_requested", None)
            if dismiss_signal is not None:
                dismiss_signal.connect(self.dismiss_hover_card)   # a click on the pinned card
            screen = self.screen() or QApplication.primaryScreen()
            avail = screen.availableGeometry() if screen else self.frameGeometry()
            # Never stack two cards - but show the new one BEFORE the old one goes: a rebuild
            # while the pointer rests on the widget (refresh_hover_power) then swaps cards with
            # no frame in between, instead of a visible blink.
            old, self._hover_card = self._hover_card, card
            card.show_for(self.frameGeometry(), avail, edge=self._hover_card_edge(), screen=screen)
            if pinned:
                card.set_pinned(True)    # opened by a click/tap: takes clicks (not pass-through)
                self._retire_pin_hint()
            if old is not None:
                try:
                    old.hide()
                    old.deleteLater()
                except Exception:
                    pass
        except Exception as e:
            self.logger.error("Error showing usage hover card: %s", e, exc_info=True)

    def _hide_usage_hover_card(self) -> None:
        """Tear down the usage card if one is up (pinned or not)."""
        self._hover_card_pinned = False
        card, self._hover_card = self._hover_card, None
        if card is not None:
            try:
                card.hide()
                # Qt deletes it on the GUI thread; dropped here, a Python-owned card could
                # otherwise be destroyed by the cyclic GC on a worker thread (utils/qt_lifetime).
                from netspeedtray.utils.qt_lifetime import hand_to_qt
                hand_to_qt(card)
                card.deleteLater()
            except Exception:
                pass

    def _usage_cap_set(self) -> bool:
        try:
            return bool(self.config.get("data_cap_enabled")) and float(self.config.get("data_cap_gb", 0) or 0) > 0
        except (TypeError, ValueError):
            return False

    def _usage_billing_period_start(self, month_start: datetime) -> Optional[datetime]:
        """Start of the data-cap billing period when a cap is set and the period does not begin on
        the 1st (else None - the calendar month IS the period). The "month" usage row follows it so
        it never disagrees with the cap row beneath it; the start comes from the odometer, which
        owns the period."""
        try:
            if not self.config.get("data_cap_enabled") or float(self.config.get("data_cap_gb", 0) or 0) <= 0:
                return None
            start = date.fromisoformat(self.widget_state.get_usage_period_key())
        except Exception:
            return None
        start_dt = datetime(start.year, start.month, start.day)
        return None if start_dt == month_start else start_dt

    def _hover_usage_totals(self) -> Tuple[Tuple[float, float], Tuple[float, float]]:
        """(today, this-month) bandwidth as (up, down) byte pairs, cached with a short TTL. "This
        month" is the billing period when a data cap with a reset day other than the 1st is set
        (``_hover_usage_period_start`` then holds its start, else None)."""
        now = time.monotonic()
        if self._hover_usage_cache is not None and (now - self._hover_usage_cache_ts) < self._HOVER_USAGE_TTL_SEC:
            return self._hover_usage_cache
        today: Tuple[float, float] = (0.0, 0.0)
        month: Tuple[float, float] = (0.0, 0.0)
        reconstructed = False
        period_start: Optional[datetime] = None
        try:
            now_dt = datetime.now()
            midnight = now_dt.replace(hour=0, minute=0, second=0, microsecond=0)
            month_start = midnight.replace(day=1)
            period_start = self._usage_billing_period_start(month_start)
            if period_start is not None:
                month_start = period_start
            # Only the adapters the cap's odometer meters: summing every adapter counts a VPN / WSL /
            # Hyper-V adapter's traffic twice and could read up to 2x the cap row beneath it.
            vt = self.widget_state.get_metered_volume_for_period(midnight, now_dt)
            today = (vt.up_bytes, vt.down_bytes)
            reconstructed = bool(vt.reconstructed)
            if self._usage_cap_set():
                # With a cap the period row IS the cap's count: the odometer, not a history read that
                # can only approximate it (auto mode meters one routing adapter history cannot replay;
                # the odometer also recovers bytes moved while the app was closed).
                month = tuple(self.widget_state.get_usage_this_period())
                # Today is history (in auto mode every physical adapter, not just the routing one the
                # cap meters), so it can read above the period's own count: never show Today > Period.
                today = (min(today[0], month[0]), min(today[1], month[1]))
            else:
                vm = self.widget_state.get_metered_volume_for_period(month_start, now_dt)
                month = (vm.up_bytes, vm.down_bytes)
                # D5: part of the month was estimated from speeds recorded before 2.2 -> label it.
                reconstructed = reconstructed or bool(vm.reconstructed)
        except Exception as e:
            self.logger.debug("hover usage fetch failed: %s", e)
        self._hover_usage_reconstructed = reconstructed
        self._hover_usage_period_start = period_start
        self._hover_usage_cache = (today, month)
        self._hover_usage_cache_ts = now
        return self._hover_usage_cache

    def _hover_power_summary(self) -> Optional[dict]:
        """Everything the hover card's battery section needs, in one dict.

        The live watts come from the monitor thread's battery poll (``self.system_power``
        while discharging, the signed ``self.battery_power`` while charging), the mode and
        charge percent from one GetSystemPowerStatus syscall, and today's average from the
        system_power stat history via the 30-second TTL cache below. None (section omitted)
        when the machine reports no battery or the state read failed - battery data is a
        nice-to-have, never worth an error card."""
        from netspeedtray.utils import power_utils
        try:
            state = power_utils.read_power_state()
            if state.mode == power_utils.MODE_NONE:
                return None
            mode = state.mode
            # Signed poll value: negative while charging (the card renders it as "+X W").
            # system_power is only a battery draw while the state says discharging: on
            # AC a whole-system reading (e.g. RAPL PSYS) must never be shown as drain
            # (#323 review). Charge detection still wins via the signed battery_power.
            if self.battery_power is not None:
                live_w = self.battery_power
            elif state.mode == power_utils.MODE_DISCHARGE:
                live_w = self.system_power
            else:
                live_w = None
            # The signed watts outrank the API's mode when they disagree: the charging bit can
            # lag the real flow around a plug-in. Watts are the ground truth for the state.
            # (With the AC line unknown, only the signed charge flow is trusted - a
            # whole-system reading is never guessed into a drain.)
            if live_w is not None and live_w < 0:
                mode = power_utils.MODE_CHARGE
            elif live_w:
                mode = power_utils.MODE_DISCHARGE
            else:
                mode = state.mode
            projected = None
            if mode == power_utils.MODE_DISCHARGE:
                # Windows' own estimate first; else design capacity x charge at the live draw.
                # The capacity comes from the cache the monitor thread fills - never WMI here,
                # on the GUI thread.
                projected = power_utils.windows_runtime_hours()
                if projected is None and live_w:
                    projected = power_utils.projected_runtime_hours(
                        live_w, state.charge_pct, power_utils.cached_design_capacity_mwh())
            return {
                "mode": mode,
                "live_w": live_w,
                "today_avg_w": self._hover_battery_today_avg(),
                "projected_hours": projected,
                "charge_pct": state.charge_pct,
            }
        except Exception as e:
            self.logger.debug("hover power summary failed: %s", e)
            return None

    def _hover_battery_today_avg(self) -> Optional[float]:
        """Today's average discharge in watts, from the battery_drain stat history
        (mirror of system_power written only while the machine is actually
        discharging, so an AC-side RAPL PSYS reading can never creep in).

        Cached with a short TTL like the bandwidth totals: rapid hovers shouldn't
        re-run the DB summary, but the figure only changes minute-to-minute."""
        now = time.monotonic()
        if self._hover_battery_avg_cache is not None and \
                (now - self._hover_battery_avg_cache_ts) < self._HOVER_USAGE_TTL_SEC:
            return self._hover_battery_avg_cache
        avg: Optional[float] = None
        try:
            now_dt = datetime.now()
            midnight = now_dt.replace(hour=0, minute=0, second=0, microsecond=0)
            summary = self.widget_state.summarize_hardware('battery_drain', midnight, now_dt,
                                                            poll_interval=resolve_poll_interval_seconds(
                                                                self.config.get("update_rate", 1.0)))
            # Only claim an average once real history exists - a fresh install (or the
            # first hours after the battery section was enabled) has no samples yet.
            if summary and summary.avg and summary.count:
                avg = float(summary.avg)
        except Exception as e:
            self.logger.debug("hover battery average fetch failed: %s", e)
        self._hover_battery_avg_cache = avg
        self._hover_battery_avg_cache_ts = now
        return avg

    def _hover_hint_text(self) -> Optional[str]:
        """The fading gesture hint - shown for the first few app runs, then retired. Counted at
        most once per run so it teaches without nagging."""
        try:
            count = int(self.config.get("tooltip_hint_shown_count", 0))
            if count >= self._TOOLTIP_HINT_MAX_SHOWS:
                return None
            if not getattr(self, "_tooltip_counted_this_session", False):
                self._tooltip_counted_this_session = True
                self.config_controller.update_config(
                    {"tooltip_hint_shown_count": count + 1}, apply_and_repaint=False)
            return hover_hint_text(self.i18n, self.config)
        except Exception:
            return None

    def _hover_cap_info(self) -> Optional[Tuple[float, float, float]]:
        """(used_gb, cap_gb, pct) when a data cap is set - preserves the live cap glance that
        used to live in the tray menu - else None (the card stays clean when no cap is set)."""
        try:
            cap = float(self.config.get("data_cap_gb", 0) or 0)
            if not self.config.get("data_cap_enabled") or cap <= 0:
                return None
            up, down = self.widget_state.get_usage_this_period()
            cnt = self.config.get("data_cap_count", "total")
            used = down if cnt == "download" else up if cnt == "upload" else (up + down)
            used_gb = used / (1000 ** 3)
            return used_gb, cap, (used_gb / cap) * 100.0
        except Exception:
            return None

    # The hover card's hardware rows refresh at most this often while it is open.
    _HOVER_HW_REFRESH_SEC = 0.9

    def _side_bar_fit(self):
        """The side-taskbar fit the widget was last sized for (widget_paint.SideBarFit), or None on a
        TOP/BOTTOM bar / free-float."""
        if self._cached_layout_mode != 'vertical':
            return None
        return getattr(getattr(self, "layout_manager", None), "_side_fit", None)

    def _hover_hardware_wanted(self) -> bool:
        """The hover card's hardware section has its own gate: the bar hides enabled hardware (or the
        Wi-Fi identity). Independent of show_usage_on_hover / show_hover_tips - turning the usage card
        off must not make CPU/GPU/memory unreachable on a side taskbar.

        "Show hardware in the hover card" (hardware_in_hover_card) opens the same gate on EVERY edge:
        the widget then paints network-only (helpers.effective_display_mode), and the card is where
        the enabled CPU/GPU/memory live."""
        if hardware_in_hover_card_active(self.config):
            return True
        fit = self._side_bar_fit()
        return bool(fit is not None and (fit.hides_hardware or fit.hides_identity))

    def _hover_hardware_snapshot(self) -> Optional[dict]:
        """Live values for the hover card's hardware section - only the stats the user turned on."""
        cfg = self.config
        hw: Dict[str, Any] = {}
        fit = self._side_bar_fit()
        in_card = hardware_in_hover_card_active(cfg)
        if in_card or (fit is not None and fit.hides_hardware):
            # Only what this display mode shows on the widget: cpu_only never lists the GPU/VRAM.
            # With the hover-card switch on, the user's OWN Display Mode decides (the widget paints
            # network_only then): side_by_side keeps its order and "None" slots, network_only and
            # Auto-Cycle list every enabled monitor.
            from netspeedtray.utils.widget_paint import side_hardware_stats
            from netspeedtray.utils.widget_renderer import RenderConfig
            rc = RenderConfig.from_dict(cfg)
            mode = (str(cfg.get("widget_display_mode", "network_only") or "network_only") if in_card
                    else rc.widget_display_mode)
            stats = side_hardware_stats(rc, mode)
            temps = bool(cfg.get("show_hardware_temps", False))
            power = bool(cfg.get("show_hardware_power", False))
            for key, usage, temp, watts in (("cpu", self.cpu_usage, self.cpu_temp, self.cpu_power),
                                            ("gpu", self.gpu_usage, self.gpu_temp, self.gpu_power)):
                if key.upper() in stats:
                    stat: Dict[str, Any] = {"usage": usage}
                    if temps:
                        stat["temp"] = temp   # Celsius; the card converts to temp_unit (rc.2 F8)
                        from netspeedtray.utils.helpers import temperature_unit
                        unit = temperature_unit(cfg)
                        if unit != constants.hardware.temperature.CELSIUS:
                            hw["temp_unit"] = unit   # absent = Celsius, the snapshot as before
                    if power:
                        stat["power"] = watts
                    hw[key] = stat
            if "CPU" in stats and cfg.get("monitor_ram_enabled", False):
                hw["ram"] = (self.ram_used, self.ram_total)
            if "GPU" in stats and cfg.get("monitor_vram_enabled", False):
                hw["vram"] = (self.vram_used, self.vram_total)
                if getattr(self, "vram_is_shared", False):
                    hw["vram_shared"] = True
        if fit is not None and fit.hides_identity:
            band, _color, _solid = self._identity_presentation()
            parts = [p for p in (self._identity_ssid_text(), band) if p]
            if parts:
                hw["identity"] = "  ".join(parts)
        return hw or None

    def _refresh_hover_hardware(self) -> None:
        """Push fresh values into an open hover card's hardware rows (~1 Hz)."""
        card = self._hover_card
        if card is None or not getattr(card, "has_hardware", False):
            return
        now = time.monotonic()
        if now - getattr(self, "_hover_hw_refreshed", 0.0) < self._HOVER_HW_REFRESH_SEC:
            return
        self._hover_hw_refreshed = now
        try:
            snapshot = self._hover_hardware_snapshot() if self._hover_hardware_wanted() else None
            if not snapshot:
                # Hardware turned off, display mode changed, or the bar now fits it: the rows would
                # only freeze on their last values - close the card (the next hover rebuilds it).
                self._hide_usage_hover_card()
                return
            card.update_hardware(snapshot)
        except Exception as e:
            self.logger.debug("hover card hardware refresh failed: %s", e)

    def refresh_hover_power(self) -> None:
        """Push a fresh battery summary into an open hover card (~1 Hz; the controller calls this on
        every poll). When the summary needs different rows - plugged in or unplugged mid-hover,
        today's history arrived - the card is rebuilt and swapped (the new one shown before the
        old one hides). Not closed like the hardware rows: the pointer is still resting on the
        widget, so no enterEvent would bring a closed card back, and a plug-in is exactly the
        moment the user is watching it. With nothing left to show it is closed."""
        card = self._hover_card
        if card is None or not getattr(card, "has_power", False):
            return
        now = time.monotonic()
        if now - getattr(self, "_hover_power_refreshed", 0.0) < self._HOVER_HW_REFRESH_SEC:
            return
        self._hover_power_refreshed = now
        try:
            power = (self._hover_power_summary()
                     if self.config.get("power_display_enabled", False) else None)
            if power is None or not card.update_power(power):
                # Builds the new card, then hides this one. A pinned card stays pinned across the
                # rebuild (a plug-in while the user reads the pinned card must not unpin it).
                if getattr(self, "_hover_card_pinned", False):
                    self._show_usage_hover_card(pinned=True)
                else:
                    self._show_usage_hover_card()
                if self._hover_card is card:           # nothing to show (or not now): close it
                    self._hide_usage_hover_card()
        except Exception as e:
            self.logger.debug("hover card power refresh failed: %s", e)

    def _hover_card_edge(self):
        """The edge of the taskbar the widget is docked to (the card opens away from it)."""
        try:
            if self.position_manager is not None and self.position_manager.is_free_float_active():
                return None
            return get_taskbar_info(preferred_screen_name=self.config.get("preferred_monitor")).get_edge_position()
        except Exception:
            return None

    def _draw_paint_error(self, painter: Optional[QPainter], text: str) -> None:
        """Draws a visual error indicator on the widget background."""
        try:
            if painter is None or not painter.isActive():
                p = QPainter(self)
                created_painter = True
            else:
                p = painter
                created_painter = False

            error_color = QColor(constants.color.RED)
            error_color.setAlpha(200) # Keep alpha for translucency
            p.fillRect(self.rect(), error_color)
            p.setPen(Qt.GlobalColor.white)
            if self.current_font:
                p.setFont(self.current_font)
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, text)

            if created_painter:
                p.end()

        except Exception as paint_err:
            self.logger.critical(f"CRITICAL: Failed to draw paint error indicator: {paint_err}", exc_info=True)






    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Delegates mouse press events to the InputHandler."""
        if self.input_handler:
            self.input_handler.handle_mouse_press(event)


    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """Delegates mouse move events to the InputHandler."""
        if self.input_handler:
            self.input_handler.handle_mouse_move(event)


    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """Delegates mouse release events to the InputHandler."""
        if self.input_handler:
            self.input_handler.handle_mouse_release(event)


    def changeEvent(self, event: QEvent) -> None:
        """
        This event is handled for proper superclass behavior, but all custom
        logic is now managed by the debounced WinEventHooks to prevent blinking.
        """
        super().changeEvent(event)


    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        """Delegates double-click events to the InputHandler."""
        if self.input_handler:
            self.input_handler.handle_double_click(event)


    def wheelEvent(self, event: QWheelEvent) -> None:
        """
        In 'cycle' display mode, scrolling over the widget flips through the metrics
        (network -> CPU -> GPU) instead of waiting for the auto-rotation. The auto
        timer is restarted so the metric the user lands on stays put for a full
        interval. In every other display mode the scroll is passed through untouched.
        """
        if effective_display_mode(self.config) != "cycle":
            super().wheelEvent(event)
            return
        delta = event.angleDelta().y()
        if delta == 0:
            event.ignore()
            return
        # Scroll up -> previous metric, scroll down -> next metric.
        self._step_cycle(1 if delta < 0 else -1)
        if self._cycle_timer.isActive():
            self._cycle_timer.start(constants.renderer.CYCLE_INTERVAL_MS)
        event.accept()


    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        """
        Shows the context menu. This handler is the primary mechanism for
        keyboard-invoked context menus and a fallback for mouse events.
        """
        try:
            if self.tray_manager:
                self.tray_manager.show_context_menu()
            event.accept()
        except Exception as e:
            self.logger.error(f"Error showing context menu: {e}", exc_info=True)
            event.ignore()


    def moveEvent(self, event) -> None:
        super().moveEvent(event)
        self._reanchor_hover_card()

    def showEvent(self, event: QShowEvent) -> None:
        self.logger.debug(f"Widget showEvent triggered. New visibility: {self.isVisible()}")
        super().showEvent(event)
        self._arm_shutdown_channel()
        # Windows broadcasts the theme change (WM_SETTINGCHANGE "ImmersiveColorSet") to top-level
        # windows, and an owned window that was hidden (fullscreen app) may not get it. One registry
        # read on re-show catches a flip made meanwhile; a no-op when nothing changed.
        handler = getattr(self, "system_event_handler", None)
        if handler is not None:
            try:
                handler.check_shell_theme()
            except Exception as e:
                self.logger.debug("theme re-check on show failed: %s", e)

    def event(self, event: QEvent) -> bool:
        # Qt sends WinIdChange whenever the native window is created or recreated (a window-flag
        # change, a re-parent, #327's re-dock after an explorer restart) - re-arm on the new HWND, so
        # --shutdown can always find us. internalWinId() never creates a window (it is 0 while the
        # HWND is being torn down, and arming is then skipped).
        if event.type() == QEvent.Type.WinIdChange:
            self._arm_shutdown_channel(create=False)
        return super().event(event)

    def _arm_shutdown_channel(self, create: bool = True) -> None:
        """
        Make this window the target of the installer's graceful close (setup.iss, or `--shutdown`):
        mark it and let the message through UIPI, so an unelevated sender reaches an elevated app.
        Idempotent per native window; re-armed when Qt hands us a new HWND. Called at the end of
        __init__ (create=True realizes the HWND, so a starting, not-yet-shown instance is already
        reachable), on every WinIdChange, and on show.
        """
        try:
            hwnd = int(self.winId() if create else (self.internalWinId() or 0))
            if not hwnd or hwnd == self._shutdown_armed_hwnd:
                return
            if shutdown_channel.arm_window(hwnd):
                self._shutdown_armed_hwnd = hwnd
        except Exception as e:
            self.logger.debug("Arming the shutdown channel failed: %s", e)


    def hideEvent(self, event: QHideEvent) -> None:
        self.logger.debug("Widget hideEvent triggered.")
        # Don't leave a usage card floating if the widget hides (e.g. a fullscreen app).
        try:
            self._hover_card_timer.stop()
            self._hide_usage_hover_card()
        except Exception:
            pass
        super().hideEvent(event)


    def closeEvent(self, event: QCloseEvent) -> None:
        """
        Handles widget closure.
        By default, closing the widget just hides it (standard Tray behavior).
        The application only exits if _will_quit_app is set to True.
        """
        if self._will_quit_app:
            self.logger.info("Application exit requested. Cleaning up...")
            try:
                self.cleanup()
                event.accept()
                
                # Explicitly ensure the app quits loop
                app = QApplication.instance()
                if app:
                    app.quit()
                    
            except Exception as e:
                self.logger.error(f"Error during shutdown cleanup: {e}", exc_info=True)
                event.accept()
        else:
            self.logger.debug("Close event received but not quitting app. Hiding widget.")
            self.setVisible(False)
            event.ignore() # Prevent destruction of the widget

    def fully_exit_application(self) -> None:
        """Helper to cleanly exit the entire application."""
        self.logger.info("Fully exiting application...")
        self._will_quit_app = True
        self.close()






 

    @property
    def position_manager_property(self):
        # Expose position manager for binding if needed, though self.position_manager exists
        return self.position_manager

    def _ensure_win32_topmost(self) -> None:
        """Delegates to PositionManager. No-op while hidden (e.g. over a fullscreen app)."""
        if not self.isVisible():
            pm = getattr(self, "position_manager", None)
            if pm is not None:
                pm.note_on_top_unverified()   # hidden time never counts as "held" on top
            return
        self._allow_taskbar_created()
        self.position_manager.ensure_topmost()
        # Burial / lost-topmost recovery, through the one guarded path (#327 review).
        self._recover_if_buried()

    def _allow_taskbar_created(self) -> None:
        """
        Allow "TaskbarCreated" through UIPI on our native window (ChangeWindowMessageFilterEx).

        Without this, a process running elevated silences the shell's broadcast and the
        instant re-dock only ever fires from the 3 s validity poll (the insta-recover
        from TaskbarCreated goes off). Idempotent per native window: re-applied when Qt
        hands us a fresh HWND after a native-window recreation.
        """
        if not _WM_TASKBARCREATED or _ChangeWindowMessageFilterEx is None:
            return
        try:
            hwnd = int(self.winId())
        except Exception:
            return
        if not hwnd or hwnd == getattr(self, "_taskbar_created_allowed", 0):
            return
        try:
            # Four arguments: the prototype declares a (nullable) FILTER_STATUS
            # pointer slot, so a 3-arg call raises TypeError and the ctypes
            # try/except would silently swallow it - leaving the UIPI filter
            # never applied (#327 review).
            ok = _ChangeWindowMessageFilterEx(hwnd, _WM_TASKBARCREATED, _MSGFLT_ALLOW, None)
            if not ok:
                # TRUE=1 on success. Do not cache: a later call retries.
                self.logger.debug(
                    "ChangeWindowMessageFilterEx(TaskbarCreated) returned FALSE on HWND %s (elevated=%s) - will retry.",
                    hwnd, is_process_elevated()
                )
                return
            self._taskbar_created_allowed = hwnd
            self.logger.debug(
                "TaskbarCreated allowed through UIPI on HWND %s (elevated=%s).", hwnd, is_process_elevated()
            )
        except Exception as e:
            self.logger.debug("ChangeWindowMessageFilterEx(TaskbarCreated) failed: %s", e)

    def _on_environment_changed(self, reason: str = "") -> None:
        """
        The display/power environment changed (monitor add/remove, primary swap, wake from
        sleep). Re-validate position and re-assert visibility/Z-order with a couple of
        staggered passes, since Windows needs a moment to settle taskbar + monitor geometry.
        """
        self.logger.debug("Environment changed (%s) - re-asserting widget.", reason)
        try:
            # PDH counter handles do not survive a suspend cycle or a GPU adapter coming and going:
            # they keep returning nothing, so VRAM reads a flat 0.0 after every wake. Nothing else
            # invalidated them - the only path that did was update_config, so opening Settings and
            # clicking Save was genuinely the user's sole recovery (#237). Flag them here; the
            # worker rebuilds on its own thread at the top of the next tick.
            if getattr(self, "monitor_thread", None):
                self.monitor_thread.invalidate_hardware_queries()
            self.update_position()
            for i in range(1, constants.timeouts.TASKBAR_RESTART_RETRIES + 1):
                QTimer.singleShot(i * constants.timeouts.TASKBAR_RESTART_RECOVERY_DELAY_MS,
                                  self._execute_refresh)
        except Exception as e:
            self.logger.error("Error handling environment change (%s): %s", reason, e, exc_info=True)

    def nativeEvent(self, eventType, message):
        """
        Observe WM_POWERBROADCAST so the widget re-asserts itself on wake from sleep/hibernate
        (monitors and the taskbar are often re-arranged across a suspend cycle). We only watch
        the message - we never consume it.

        The one message we DO consume is our own registered shutdown message (``--shutdown``, sent
        by the installer): it schedules ``fully_exit_application`` - the tray Exit path - and
        returns ``(True, 0)``. Nothing else ever carries that atom.

        CRITICAL: do NOT call ``super().nativeEvent(eventType, message)`` here. Re-dispatching the
        native message through the base implementation from a Python override access-violates
        inside Qt during window creation - it crashed the widget on its very first show() (a hard
        0xC0000005 in QtCore, no Python traceback). Since this handler only *observes*, returning
        ``(False, 0)`` - "not handled, let Qt continue normal processing" - is the correct and
        safe contract, and is equivalent to what a working base call would return for us.
        """
        try:
            if eventType == b"windows_generic_MSG" and message is not None:
                msg = _NativeMSG.from_address(int(message))
                if _WM_NST_SHUTDOWN and msg.message == _WM_NST_SHUTDOWN:
                    # Deferred out of the native dispatch, then exactly the tray Exit path.
                    self.logger.info("Shutdown requested by another process (installer/--shutdown) - exiting cleanly.")
                    QTimer.singleShot(0, self.fully_exit_application)
                    return True, 0
                if _WM_NST_ACTIVATE and msg.message == _WM_NST_ACTIVATE:
                    # A second launch (#234): deferred out of the native dispatch like the shutdown.
                    QTimer.singleShot(0, self.show_from_second_launch)
                    return True, 0
                if msg.message == _WM_QUERYENDSESSION or (msg.message == _WM_ENDSESSION and msg.wParam):
                    # Logoff / shutdown / restart. The normal quit path (closeEvent -> cleanup) never
                    # runs: Windows terminates us soon after WM_ENDSESSION returns, so the essential
                    # saves happen HERE, synchronously, inside its ~5 s budget. Observe only - Qt
                    # still answers (TRUE: never veto the logoff). Repeating it on ENDSESSION is
                    # cheap and catches anything changed in between.
                    self._save_session_essentials(
                        "query-end-session" if msg.message == _WM_QUERYENDSESSION else "end-session")
                    return False, 0
                if _WM_TASKBARCREATED and msg.message == _WM_TASKBARCREATED:
                    self.logger.info("TaskbarCreated received - re-docking to the new taskbar.")
                    self._recover_taskbar_restarted()
                elif msg.message == _WM_POWERBROADCAST and msg.wParam in (
                    _PBT_APMRESUMESUSPEND, _PBT_APMRESUMEAUTOMATIC
                ):
                    self.logger.info("Resume from sleep detected - re-asserting widget.")
                    QTimer.singleShot(constants.timeouts.TASKBAR_RESTART_RECOVERY_DELAY_MS,
                                      lambda: self._on_environment_changed("resume"))
                    invalidate = getattr(self, "invalidate_latency_gateway", None)
                    if invalidate is not None:
                        invalidate()   # the router may differ after sleep
        except Exception as e:
            self.logger.debug("nativeEvent power handling failed: %s", e)
        return False, 0

    def _recover_if_buried(self) -> None:
        """
        Put the widget back on top of its taskbar if the shell buried it (#314, #238).

        Ownership keeps the widget above the taskbar in theory, but the shell can raise the
        taskbar over it without breaking the owner link, and steady-state ensure_topmost()
        deliberately never touches Z-order (#200). Runs on every coalesced Z-order change
        (EVENT_OBJECT_REORDER / foreground) and on the 1 s safety-net tick; re-seats the
        widget directly above the taskbar only when it really is below it.

        Skipped while hidden, while our own menu is open or a drag is under way, and while a
        fullscreen app owns the screen - with Keep visible in fullscreen on, the widget's
        place over the video stays exactly as it was before this recovery existed.
        """
        try:
            pm = getattr(self, "position_manager", None)
            if pm is None:
                return
            handler = getattr(self, "system_event_handler", None)
            if (not self.isVisible() or self._is_context_menu_visible or self._dragging
                    or (handler is not None and handler.is_fullscreen_active())):
                pm.note_on_top_unverified()   # not a verified on-top tick (backoff stretch)
                return
            if pm.is_buried_behind_taskbar():
                pm.reseat_above_taskbar()
        except Exception as e:
            self.logger.debug("Burial recovery check failed: %s", e)

    def _recover_taskbar_restarted(self) -> None:
        """
        Explorer is (re)building the taskbar. Ride out the shell's startup with the
        established staggered refreshes: each pass re-detects the taskbar, re-docks
        the owner link and seats the widget back in the topmost band once detection
        succeeds. Fired by the 3s validity poll and - via nativeEvent - by the
        TaskbarCreated broadcast, which arrives the moment the new taskbar window
        exists instead of up to a poll interval later.
        """
        try:
            pm = getattr(self, "position_manager", None)
            if pm is not None:
                pm.reset_reseat_state()   # a new taskbar starts a new burial history
            for i in range(constants.timeouts.TASKBAR_RESTART_RETRIES):
                QTimer.singleShot(i * constants.timeouts.TASKBAR_RESTART_RECOVERY_DELAY_MS,
                                  self._execute_refresh)
        except Exception as e:
            self.logger.error("Error recovering from taskbar restart: %s", e, exc_info=True)

    def _enforce_topmost_status(self) -> None:
        """Delegates to PositionManager."""
        self.position_manager.enforce_topmost_status()

    def reset_to_default_position(self) -> None:
        """Right-click > Reset position: back to the automatic spot by the tray (or the side bar's
        chosen gap) - clears the free-move spot AND the docked drag offsets, then re-places."""
        self.logger.info("Resetting widget position to default.")
        self._hide_usage_hover_card()
        defaults = constants.config.defaults
        self.position_manager.reset_to_default()
        # Persist exactly what reset_to_default cleared in the (shared) config dict.
        self.update_config({'position_x': None, 'position_y': None,
                            'tray_offset_x': defaults.DEFAULT_TRAY_OFFSET_X,
                            'tray_offset_y': defaults.DEFAULT_TRAY_OFFSET_Y})

    def show_from_second_launch(self) -> None:
        """
        The app was launched again while running (#234, rc.2 W14; the second process posts
        ACTIVATE_MESSAGE_NAME and exits). Bring the widget back however it was lost: hidden -> shown;
        re-placed and re-docked to the current taskbar; still off-screen -> the docked spot for this
        session (nothing saved is cleared); re-seated above the taskbar if it was buried, with the
        burial backoff forgotten so nothing defers it. The 1 s refresh then applies the usual rules
        (an auto-hidden taskbar still hides it). A fullscreen app keeps it hidden, as always - unless
        Keep visible in fullscreen is on. Never opens a window: a duplicate Run-key start at sign-in
        must stay invisible.
        """
        try:
            pm = getattr(self, "position_manager", None)
            if pm is None:
                return
            self.logger.info("Another launch asked the widget to show itself (visible=%s).", self.isVisible())
            # #24, first: the app-button yield stands down for this session (it would hide the widget
            # again on the refresh below). Also under a fullscreen app - it comes back when that ends.
            # Its own try: nothing about the yield may stop the widget coming back.
            was_yielded = False
            try:
                was_yielded = self._stand_down_app_yield() is True
            except Exception as e:
                self.logger.debug("app-yield stand-down skipped: %s", e)
            handler = getattr(self, "system_event_handler", None)
            if (handler is not None and handler.is_fullscreen_active()
                    and not self.config.get("keep_visible_fullscreen", False)):
                self.logger.info("A fullscreen app is active - the widget comes back when it ends.")
                return
            self._taskbar_lost_count = 0
            if not self.isVisible():
                self.setVisible(True)
            pm.reset_reseat_state()
            pm.update_position()   # a saved spot on a monitor that is gone already falls back here
            if self._is_off_screen():
                # Still nowhere visible: the docked spot for THIS session only. Nothing saved is
                # cleared - a duplicate start at sign-in can beat a slow monitor that holds the
                # saved Free Move spot (#440 review L3); Reset position stays the explicit choice.
                self.logger.info("The widget was off-screen - showing it at its docked spot for now.")
                pm.move_to_default_for_session()
            pm.ensure_topmost()
            if pm.is_buried_behind_taskbar():
                pm.reseat_above_taskbar()
            QTimer.singleShot(0, self._execute_refresh)
            if was_yielded:
                # Last: only when the yield had hidden it (never over a fullscreen app - returned above).
                self._explain_app_yield_stand_down()
        except Exception as e:
            self.logger.error("Bringing the widget back for a second launch failed: %s", e, exc_info=True)

    def _is_off_screen(self) -> bool:
        """True when the widget's centre is on no connected screen (e.g. a saved spot on an unplugged
        monitor, or a stray position from elsewhere)."""
        try:
            return QApplication.screenAt(self.frameGeometry().center()) is None
        except Exception:
            return False

    def has_custom_position(self) -> bool:
        """Whether the widget sits where the user put it (a drag, or a saved free-move spot) - the
        tray menu offers Reset position only then."""
        defaults = constants.config.defaults
        cfg = self.config or {}
        offset_x = cfg.get('tray_offset_x', defaults.DEFAULT_TRAY_OFFSET_X)
        # An older release's default offset (10 / 1 / 3) is not a move the user made (rc.2 W6).
        default_x = offset_x == defaults.DEFAULT_TRAY_OFFSET_X or offset_x in defaults.LEGACY_DEFAULT_TRAY_OFFSETS_X
        return (cfg.get('position_x') is not None or cfg.get('position_y') is not None
                or not default_x
                or cfg.get('tray_offset_y', defaults.DEFAULT_TRAY_OFFSET_Y) != defaults.DEFAULT_TRAY_OFFSET_Y)

    def _maybe_show_side_no_room_notice(self) -> None:
        """One-time heads-up (ever): a side taskbar's app buttons leave no free spot, so the widget sits
        by the tray and may cover some of them - it can be dragged anywhere along the bar. Called by
        PositionManager when its placement ends in "tray_no_room" (never while the user's own drag
        is in force)."""
        try:
            if getattr(self, "_side_no_room_shown", False) or self.config.get("side_bar_no_room_notice_shown", False):
                return
            self._side_no_room_shown = True
            self.update_config({"side_bar_no_room_notice_shown": True})
            title = self.i18n.SIDE_BAR_NO_ROOM_TITLE
            body = self.i18n.SIDE_BAR_NO_ROOM_BODY
            # Deferred out of the reposition. A non-activating flyout, NOT a QMessageBox: this can fire
            # while the user types in another app (apps opened after placement, rc.2 W1), and a modal
            # message box took the keyboard away from it (rc.1 live test, W4).
            QTimer.singleShot(0, lambda: self._show_notice_flyout(title, body))
        except Exception as e:
            self.logger.debug("side-bar no-room notice failed: %s", e)

    def _on_app_yield_changed(self) -> None:
        """The #24 yield flipped (its probe landed): apply it through the authoritative refresh, out
        of the probe's poll callback. A hover card (pinned ones last minutes) goes with the widget."""
        try:
            if self.position_manager.is_yielding_to_apps() is True:
                self.dismiss_hover_card()
        except Exception as e:
            self.logger.debug("hover card dismiss on app yield failed: %s", e)
        QTimer.singleShot(0, self._execute_refresh)

    def _stand_down_app_yield(self) -> bool:
        """Step 1 of a second launch (show_from_second_launch): a yielded widget has no other way back
        (no tray icon), so the #24 yield stands down for the rest of this session - BEFORE the show /
        re-seat, so the refresh that follows can't hide it again. True when it was hidden by the yield."""
        try:
            pm = getattr(self, "position_manager", None)
            if pm is None:
                return False
            was_yielded = pm.suspend_app_yield() is True
            self.logger.info("Second launch: the app-button yield is off for this session (was yielded: %s).",
                             was_yielded)
            return was_yielded
        except Exception as e:
            self.logger.error("App-yield second-launch handling failed: %s", e, exc_info=True)
            return False

    def _explain_app_yield_stand_down(self) -> None:
        """The last step of a second launch that brought a YIELDED widget back: a non-activating flyout
        says why it had stepped aside and how to keep it visible for good. After the show / re-seat, so
        it is anchored to the widget's real spot."""
        try:
            title = self.i18n.SIDE_BAR_NO_ROOM_TITLE
            body = self._app_overlap_text(self.i18n.APP_OVERLAP_ACTIVATE_BODY)
            QTimer.singleShot(self._ACTIVATE_FLYOUT_DELAY_MS, lambda: self._show_notice_flyout(title, body))
        except Exception as e:
            self.logger.debug("app-yield second-launch notice failed: %s", e)

    # The flyout follows the show + the refresh it queues, so it sits next to the re-placed widget.
    _ACTIVATE_FLYOUT_DELAY_MS = 250

    def _app_overlap_text(self, template: str) -> str:
        """Fill a #24 notice with the exact Settings words it points at (the switch, the window and the
        page names), so it can never drift from the UI."""
        try:
            return template.format(setting=self.i18n.HIDE_WHEN_APPS_COVER_LABEL,
                                   settings=self.i18n.SETTINGS_WINDOW_TITLE,
                                   page=self.i18n.WIDGET_SETTINGS_GROUP)
        except Exception:
            return template

    def _maybe_show_app_overlap_notice(self) -> None:
        """One-time heads-up (ever), the first time the widget steps aside for app buttons on a
        horizontal taskbar (#24): why it is gone, and how to get it back (start the app again, or the
        Settings switch). Reuses the side-bar no-room title and flyout; non-activating."""
        try:
            if getattr(self, "_app_overlap_notice_shown", False) or self.config.get("app_overlap_notice_shown", False):
                return
            self._app_overlap_notice_shown = True
            self.update_config({"app_overlap_notice_shown": True})
            title = self.i18n.SIDE_BAR_NO_ROOM_TITLE
            body = self._app_overlap_text(self.i18n.APP_OVERLAP_YIELD_BODY)
            QTimer.singleShot(0, lambda: self._show_notice_flyout(title, body))
        except Exception as e:
            self.logger.debug("app-overlap notice failed: %s", e)

    # The no-room notice is longer than a usage alert: give it time to be read.
    _NOTICE_FLYOUT_DISMISS_MS = 30000

    def _show_notice_flyout(self, title: str, body: str, action_text: Optional[str] = None,
                            on_action=None) -> None:
        """A one-time notice next to the widget that never takes focus (the data-cap alerts' flyout).
        ``action_text`` / ``on_action``: an optional button (e.g. "Open folder")."""
        try:
            from netspeedtray.views.flyout import Flyout
            from PyQt6 import sip
            previous = getattr(self, "_notice_flyout", None)
            if previous is not None and not sip.isdeleted(previous):
                previous.close()
            self._notice_flyout = Flyout(title, body, action_text=action_text if on_action else None,
                                         auto_dismiss_ms=self._NOTICE_FLYOUT_DISMISS_MS)
            if on_action is not None and action_text:
                self._notice_flyout.action_clicked.connect(on_action)
            geo = self.frameGeometry()
            self._notice_flyout.show_at(QPoint(geo.left(), geo.top()))
        except Exception as e:
            self.logger.error("Error showing a notice: %s", e, exc_info=True)


    def show_backup_notice(self, title: str, body: str, path: Optional[str] = None) -> None:
        """Settings > Advanced > Back up history finished while Settings was closed (#334): say so in the
        non-focus flyout, with "Open folder" when a copy was written - never a modal box with no visible
        parent, which would take focus and block the Monitor."""
        from netspeedtray.utils.helpers import reveal_in_explorer
        self._show_notice_flyout(title, body, action_text=self.i18n.STATS_DETAIL_OPEN_FOLDER if path else None,
                                 on_action=(lambda: reveal_in_explorer(path)) if path else None)

    def apply_mirrors(self) -> None:
        """
        "Show on all taskbars" (#229): build the MirrorManager when the setting turns on, retire it
        when it turns off, and re-size the live mirrors on any other apply. Off (the default) is a
        plain dict lookup - the mirror module is not even imported.
        """
        want = bool(self.config.get("show_on_all_taskbars",
                                    constants.config.defaults.DEFAULT_SHOW_ON_ALL_TASKBARS))
        mgr = getattr(self, "_mirrors", None)
        try:
            if want and mgr is None:
                from netspeedtray.views.widget.mirror import MirrorManager
                self._mirrors = MirrorManager(self)
                self._mirrors.start()
            elif not want and mgr is not None:
                self._mirrors = None
                mgr.shutdown()
                mgr.deleteLater()
            elif mgr is not None:
                mgr.apply_settings()
        except Exception as e:
            self.logger.error("Applying 'Show on all taskbars' failed: %s", e, exc_info=True)

    def docked_taskbar(self) -> Optional[Tuple[int, Optional[str]]]:
        """(HWND, screen name) of the taskbar this widget is docked to - the one the mirrors leave
        alone; (0, None) while it free-floats on a taskbar-less display (#188); None when unknown
        (a fallback taskbar at logon).

        With Free Move the widget sits wherever it was dragged, so the screen is the one it is ON, not
        the configured one: dragged onto monitor 2, monitor 2 gets no mirror and the primary does."""
        pm = getattr(self, "position_manager", None)
        if pm is None:
            return None
        if pm.is_free_float_active():
            return 0, None
        if self.config.get("free_move", False):
            screen = QApplication.screenAt(self.frameGeometry().center()) or self.screen()
            return (0, screen.name()) if screen is not None else None
        info = pm._state.taskbar_info
        hwnd = int(info.hwnd) if info is not None and info.hwnd else 0
        return (hwnd, info.screen_name) if hwnd else None

    def apply_all_settings(self) -> None:
        """Delegates to ConfigController."""
        self.config_controller.apply_all_settings()


    def handle_settings_changed(self, updated_config: Dict[str, Any], save_to_disk: bool = True) -> None:
        """Delegates to ConfigController."""
        self.config_controller.handle_settings_changed(updated_config, save_to_disk)


    def open_data_usage_settings(self) -> None:
        """Open Settings straight on the Network page, where the data-cap 'Data usage' section lives -
        the Monitor's 'Set a monthly limit' hint's door (was dropping the user on General)."""
        try:
            self.show_settings()
            if self.settings_dialog is not None:
                self.settings_dialog.navigate_to_page(self.settings_dialog.PAGE_NETWORK)
        except Exception as e:
            self.logger.error("Error opening data-usage settings: %s", e, exc_info=True)

    def show_settings(self) -> None:
        """Creates and displays the settings dialog as a normal, non-modal window."""
        self.logger.debug("Showing settings dialog...")
        try:
            from netspeedtray.views.settings import SettingsDialog

            if self.settings_dialog is None:
                self.logger.debug("Creating new SettingsDialog instance.")
                # Create the dialog as a top-level window (parent=None)
                self.settings_dialog = SettingsDialog(
                    main_widget=self,
                    config=self.config.copy(),
                    version=constants.app.VERSION,
                    i18n=self.i18n,
                    available_interfaces=self.get_unified_interface_list(),
                    is_startup_enabled=self.is_startup_enabled(),
                    primary_interface=self._primary_interface_for_picker(),
                )
                # Connect signal for live preview updates (don't save to disk during preview)
                self.settings_dialog.settings_changed.connect(
                    lambda cfg: self.handle_settings_changed(cfg, save_to_disk=False)
                )

            if not self.settings_dialog.isVisible():
                # Also update the interface list when showing an existing dialog
                self.settings_dialog.update_interface_list(
                    self.get_unified_interface_list(), self._primary_interface_for_picker())
                self.settings_dialog.reset_with_config(
                    config=self.config.copy(),
                    is_startup_enabled=self.is_startup_enabled()
                )
                self.settings_dialog.show()
            else:
                self.logger.debug("Settings dialog already visible. Activating.")
                # Also update the interface list when re-activating the dialog
                self.settings_dialog.update_interface_list(
                    self.get_unified_interface_list(), self._primary_interface_for_picker())
                self.settings_dialog.raise_()
                self.settings_dialog.activateWindow()

        except Exception as e:
            self.logger.error(f"Error showing settings: {e}", exc_info=True)
            QMessageBox.critical(self, self.i18n.ERROR_TITLE, f"Could not open settings:\n\n{str(e)}")



    def _rollback_config(self, old_config: Dict[str, Any]) -> None:
        """Delegates to ConfigController."""
        self.config_controller.rollback_config(old_config)


    def update_config(self, updates: Dict[str, Any], save_to_disk: bool = True) -> None:
        """Delegates to ConfigController."""
        self.config_controller.update_config(updates, save_to_disk)


    def handle_graph_settings_update(self, updates: Dict[str, Any]) -> None:
        """
        Public method called by the GraphWindow to update and save configuration.
        This centralizes the saving logic and prevents race conditions.
        """
        self.logger.debug(f"Received settings update from graph window: {updates}")
        # The update_config method already updates the in-memory config and saves to disk.
        # We can just call it directly.
        self.update_config(updates)





    def open_monitor_window(self) -> None:
        """Open the unified Monitor window (Overview / Network / Hardware)."""
        try:
            from netspeedtray.views.monitor import MonitorWindow
            if self.monitor_window is None:
                self.monitor_window = MonitorWindow(self, self.config, self.i18n)
                self.monitor_window.window_closed.connect(self._on_monitor_window_closed)
                # If the user left it maximized, map straight to maximized - otherwise restore_window_geometry
                # sets the WindowMaximized flag but the window still maps at the normal size for one frame,
                # then expands (the "small window then grows" flash). showMaximized() skips that frame.
                if self.monitor_window.windowState() & Qt.WindowState.WindowMaximized:
                    self.monitor_window.showMaximized()
                else:
                    self.monitor_window.show()
            else:
                self.monitor_window.show()
                self.monitor_window.raise_()
                self.monitor_window.activateWindow()
        except Exception as e:
            self.logger.error(f"Error showing Monitor window: {e}", exc_info=True)

    def _on_monitor_window_closed(self) -> None:
        # Restore the widget's Z-order/visibility after the Monitor closes - via the authoritative
        # refresh, which RESPECTS fullscreen obstruction (so it won't flash the widget over a
        # fullscreen app). A second deferred pass lets the Windows focus transition settle.
        self.monitor_window = None
        self._execute_refresh()
        QTimer.singleShot(constants.timeouts.GRAPH_CLOSE_REFRESH_DELAY_MS, self._execute_refresh)


    def check_for_updates(self) -> None:
        """Manually trigger an update check (from menu)."""
        from netspeedtray.core.update_checker import update_check_disabled_by_policy
        if update_check_disabled_by_policy():
            # The menu item is hidden under the policy (U1); a stale menu opened just before the
            # policy landed must not wire the one-shot replies to a check that will never run.
            return
        if self.update_checker:
            # update_available is already wired to _on_update_available at construction
            # (persistent, see __init__). Do NOT connect a second handler here: on a manual
            # check both would fire and the update dialog shows twice - the first starts the
            # download, the second "pops back up" over it and races the install/quit, so the
            # installer never visibly runs. Only the up-to-date / failed messages are manual-
            # only (the automatic startup check stays silent on those).
            self.update_checker.up_to_date.connect(self._on_up_to_date_manual, Qt.ConnectionType.SingleShotConnection)
            self.update_checker.check_failed.connect(self._on_check_failed_manual, Qt.ConnectionType.SingleShotConnection)
            self.update_checker.check_now()

    def _on_update_available(self, latest_version: str, release_url: str, body: str = "",
                             installer_url: str = "", portable_url: str = "") -> None:
        """Handle update available - from either the automatic startup check or a manual
        menu check (both use this single persistent handler; see check_for_updates)."""
        self._show_update_dialog(latest_version, release_url, body, installer_url, portable_url)

    def _on_up_to_date_manual(self) -> None:
        """Show up-to-date message for manual check."""
        QMessageBox.information(
            None, self.i18n.UPDATE_UP_TO_DATE_TITLE,
            self.i18n.UPDATE_UP_TO_DATE_TEXT.format(current=constants.app.VERSION)
        )

    def _on_check_failed_manual(self, error: str) -> None:
        """Show error message for manual check."""
        QMessageBox.warning(self, self.i18n.UPDATE_CHECK_TITLE, self.i18n.UPDATE_CHECK_FAILED_TEXT)

    def _show_update_dialog(self, latest_version: str, release_url: str, body: str = "",
                            installer_url: str = "", portable_url: str = "") -> None:
        """Show the update-available dialog: version delta + inert release notes,
        with Download / Skip / Not Now."""
        from netspeedtray.views.update_dialog import UpdateDialog
        latest = latest_version.lstrip("vV")
        dlg = UpdateDialog(self.i18n, constants.app.VERSION, latest, body, parent=self)
        dlg.exec()

        if dlg.action == UpdateDialog.ACTION_DOWNLOAD:
            self._start_secure_update(installer_url, release_url,
                                      portable_url=portable_url, latest_version=latest)
        elif dlg.action == UpdateDialog.ACTION_SKIP:
            self.config["skipped_version"] = latest
            self.update_config({"skipped_version": latest})

    def _start_secure_update(self, installer_url: str, release_url: str,
                             *, portable_url: str = "", latest_version: str = "") -> None:
        """
        One-click update: download the signed installer, verify it (Authenticode +
        SignPath pin), and run it. Any failure falls back to opening the release page
        in the browser (the old behavior), so the worst case is never worse than before.

        On the portable build (detected via the packaged marker file) the installer can't update the
        unzipped folder in place, so this runs the guided portable flow instead: download + verify the
        portable ZIP and stage it for the user to copy over (#195).
        """
        try:
            from netspeedtray.core.update_installer import SecureUpdater
            from netspeedtray.utils.helpers import is_portable_install
            # In-flight guard: don't start a second download over a running one.
            existing = getattr(self, "_secure_updater", None)
            if existing is not None and existing.is_running():
                return
            portable = is_portable_install()
            # Parented to self (not GC'd); it self-destructs via deleteLater when done.
            self._secure_updater = SecureUpdater(
                self, installer_url, release_url, self.i18n,
                portable=portable, portable_url=portable_url, latest_version=latest_version)
            self._secure_updater.launching.connect(self._quit_for_update)
            self._secure_updater.start()
        except Exception as e:
            self.logger.error(f"Secure update failed to start: {e}", exc_info=True)
            try:
                import webbrowser
                webbrowser.open(release_url)
            except Exception:
                pass

    def _quit_for_update(self) -> None:
        """The verified installer was launched; quit so it can replace our files."""
        self.logger.info("Verified installer launched; quitting for update.")
        self._will_quit_app = True
        QApplication.instance().quit()

    def show_support_dialog(self) -> None:
        """Show the support/donate dialog - a Win11-styled custom dialog (replaces the old QMessageBox)."""
        try:
            from netspeedtray.views.support_dialog import SupportDialog
            SupportDialog(self.i18n, self, app_icon=self.windowIcon()).exec()
        except Exception as e:
            self.logger.error("Error showing support dialog: %s", e, exc_info=True)

    def _on_lhm_not_detected(self) -> None:
        """
        Temps/power are enabled but no sensor source was found. Show the actionable
        onboarding explainer (one click to LHM, clear that the app never runs as admin),
        unless the user has permanently dismissed it.
        """
        try:
            if self.config.get("temp_onboarding_dismissed", False):
                return
            from netspeedtray.views.temp_onboarding_dialog import (
                TempOnboardingDialog, LHM_RELEASES_URL,
            )
            # The port the user configured for LHM (Settings > Hardware), as the hint names it.
            lhm_port = getattr(self.monitor_thread, "lhm_port", constants.lhm.HTTP_PORT)
            dlg = TempOnboardingDialog(self.i18n, self, lhm_port=lhm_port)
            dlg.exec()
            if dlg.dismissed_forever():
                self.update_config({"temp_onboarding_dismissed": True})
            if dlg.action == TempOnboardingDialog.ACTION_GET_LHM:
                import webbrowser
                webbrowser.open(LHM_RELEASES_URL)
        except Exception as e:
            self.logger.error("Error showing temperature onboarding: %s", e, exc_info=True)

    def _maybe_prompt_location(self, identity: Optional[object]) -> None:
        """One-time, honest explainer when the user wants the SSID but Windows Location is off.

        Fires at most once per session and is suppressible forever. Only when the network name was
        actually requested (ssid/both mode) and actually blocked - never for band-only users.
        """
        try:
            if identity is None or not getattr(identity, "ssid_blocked", False):
                return
            if self.config.get("identity_mode", "band") not in ("ssid", "both"):
                return
            if self._location_prompt_shown or self.config.get("location_onboarding_dismissed", False):
                return
            self._location_prompt_shown = True
            from netspeedtray.views.location_onboarding_dialog import (
                LocationOnboardingDialog, LOCATION_SETTINGS_URI,
            )
            dlg = LocationOnboardingDialog(self.i18n, self)
            dlg.exec()
            if dlg.dismissed_forever():
                self.update_config({"location_onboarding_dismissed": True})
            if dlg.action == LocationOnboardingDialog.ACTION_OPEN_LOCATION:
                from PyQt6.QtGui import QDesktopServices
                from PyQt6.QtCore import QUrl
                QDesktopServices.openUrl(QUrl(LOCATION_SETTINGS_URI))
        except Exception as e:
            self.logger.error("Error showing location onboarding: %s", e, exc_info=True)


    def _maybe_nudge_widgets_overlap(self, taskbar_info) -> None:
        """One-time heads-up (#200): if the widget currently overlaps the Win11 Widgets/weather panel's
        visible content, tell the user once that they can drag the widget wherever they like and it's
        remembered. We deliberately never auto-move it - the panel's width changes with the weather, so
        repositioning would make the widget jitter, and the user may want it right where it is. Fires at
        most once, ever (session flag + persisted config flag)."""
        try:
            if self._widgets_nudge_shown or self.config.get("widgets_overlap_nudge_shown", False):
                # Nothing else reads the Widgets rect: stop probing for it (taskbar_utils).
                from netspeedtray.utils.taskbar_utils import set_widgets_probe_enabled
                set_widgets_probe_enabled(False)
                return
            widgets_rect = getattr(taskbar_info, "widgets_rect", None)
            if not widgets_rect:
                return
            from netspeedtray.utils.taskbar_utils import rect_overlaps_x, rect_overlaps_y
            dpi = getattr(taskbar_info, "dpi_scale", 1.0) or 1.0
            try:
                edge = taskbar_info.get_edge_position()
            except Exception:
                edge = None
            if edge in (constants.TaskbarEdge.LEFT, constants.TaskbarEdge.RIGHT):
                # A side bar: the widget and the Widgets button both span the bar's width, so only
                # their y-extents can tell an overlap apart (comparing x always "overlapped").
                overlaps = rect_overlaps_y(self.y(), self.y() + self.height(), widgets_rect, dpi)
            else:
                overlaps = rect_overlaps_x(self.x(), self.x() + self.width(), widgets_rect, dpi)
            if not overlaps:
                return
            # Latch immediately so a re-entrant refresh (or the deferred call below) can't double-fire.
            self._widgets_nudge_shown = True
            self.update_config({"widgets_overlap_nudge_shown": True})
            from netspeedtray.utils.taskbar_utils import set_widgets_probe_enabled
            set_widgets_probe_enabled(False)
            title = getattr(self.i18n, "WIDGETS_OVERLAP_NUDGE_TITLE", "NetSpeedTray overlaps the Widgets panel")
            body = getattr(
                self.i18n, "WIDGETS_OVERLAP_NUDGE_BODY",
                "NetSpeedTray is sitting over the Windows Widgets/weather panel on your taskbar. You can "
                "drag it anywhere you like - it'll remember the spot. (This message won't show again.)")
            # Defer out of the refresh tick so the modal doesn't run inside the timer callstack.
            QTimer.singleShot(0, lambda: QMessageBox.information(self, title, body))
        except Exception as e:
            self.logger.debug("widgets-overlap nudge check failed: %s", e)


    # update_config (redundant definition) removed


    def get_config(self) -> Dict[str, Any]:
        return self.config.copy() if self.config else {}


    def get_widget_size(self) -> QSize:
        return self.size()


    def set_app_version(self, version: str) -> None:
        self.app_version = version
        self.logger.debug(f"Application version set to: {version}")


    def update_position(self) -> None:
        """
        The single, authoritative method to reposition the widget based on its current state.
        """
        self.logger.debug("Authoritative request to update widget position.")
        self._refresh_cached_layout_mode()
        if self.position_manager:
            try:
                taskbar_info = get_taskbar_info(preferred_screen_name=self.config.get("preferred_monitor"))
                # A taskbar that changed thickness or edge needs a re-SIZE, not just a move (#322).
                # The resize ends in update_position() itself, so the move is already done.
                if self._resize_if_dock_changed(taskbar_info):
                    return
                self.position_manager.update_position(fresh_taskbar_info=taskbar_info)
            except Exception as e:
                self.logger.error(f"Error during position update: {e}", exc_info=True)

    def _resize_if_dock_changed(self, taskbar_info) -> bool:
        """Re-run the layout sizing when the taskbar's dock geometry changed since last seen (#322).

        The widget is sized once at startup to the taskbar strip; a live switch of Win11's native
        taskbar size (Default 48 px <-> Small 32 px) or edge only fired a reposition, so the widget kept
        its old height and hung off a thinner bar. Cheap tuple compare on every taskbar change; a plain
        move (same signature) resizes nothing. ``_dock_sig_seen`` is recorded by
        ``resize_widget_for_font`` from the taskbar it actually sized against - the same preferred-monitor
        taskbar read here - and is None when that was a fallback taskbar (Explorer not ready at logon)
        or a free-float, so the first real taskbar after either re-sizes. No loop: the resize's own
        update_position() sees the signature it just recorded. Returns True if it resized.
        """
        try:
            if self.position_manager is not None and self.position_manager.is_free_float_active():
                return False   # a free-floating widget isn't sized to a taskbar (#188)
            sig = dock_signature(taskbar_info)
            if sig is None:
                return False   # fallback taskbar now
            qt_scale = widget_qt_scale(self)
            if sig == self._dock_sig_seen:
                # Same dock - but a live display-scale change reaches the OS (the signature) and Qt (the
                # widget's own device pixel ratio, which the font metrics follow) at different moments: a
                # re-size run in between measured the text at the old scale (rc.2 live run: 87 px wide
                # at 100% after 100 -> 150 -> 100, a fresh start is 85). Re-size once Qt catches up.
                seen = getattr(self, "_qt_scale_seen", None)
                if qt_scale is None or seen is None or abs(qt_scale - seen) < 1e-3:
                    return False   # nothing changed
                self._qt_scale_seen = qt_scale   # a failed resize can't retry every tick
                self.logger.info("Display scale for the widget changed %s -> %s; re-sizing the widget.",
                                 seen, qt_scale)
                self.layout_manager.resize_widget_for_font()   # ends with update_position()
                return True
            prev, self._dock_sig_seen = self._dock_sig_seen, sig   # a failed resize can't retry every tick
            self.logger.info("Taskbar dock geometry changed %s -> %s; re-sizing the widget.", prev, sig)
            self.layout_manager.resize_widget_for_font()   # ends with update_position()
            return True
        except Exception as e:
            self.logger.error(f"Error checking taskbar dock geometry: {e}", exc_info=True)
            return False


    def _refresh_cached_layout_mode(self) -> None:
        """Update the cached paint ``layout_mode`` from the current taskbar edge.

        Naming follows the taskbar's *orientation*: a TOP/BOTTOM taskbar is 'horizontal', a LEFT/RIGHT
        taskbar is 'vertical'. This is the value passed to ``render_widget(layout_mode=...)`` and must
        match what ``PreviewWidget`` uses for the same scenario (it renders a horizontal taskbar strip,
        so it passes 'horizontal') - otherwise the live widget and the Settings/Overview preview of the
        same config diverge. In particular the side-by-side right-align probe only fires for 'horizontal'
        (the over-reserved, right-anchored-to-the-tray case); a side taskbar is docked differently and is
        left as-is. (Beyond the right-align decision and the live/preview parity, 'vertical' only stacks
        the side_by_side segments top-to-bottom, since a side taskbar has no room for them in a row.)
        """
        try:
            # A free-floating widget on a taskbar-less display has no taskbar edge; render it as a
            # compact horizontal readout (#188).
            from netspeedtray.utils.taskbar_utils import get_free_float_screen
            if self.config.get("free_float", True) and \
                    get_free_float_screen(self.config.get("preferred_monitor")) is not None:
                self._cached_layout_mode = 'horizontal'
                return
            # The widget's OWN taskbar (preferred monitor, #72) - the one it is sized and positioned
            # against. A bare get_taskbar_info() read the primary's edge, wrong for a preferred bar
            # on another edge (#229 design §3.7).
            taskbar_info = get_taskbar_info(preferred_screen_name=self.config.get("preferred_monitor"))
            self._cached_layout_mode = self._layout_mode_for_edge(taskbar_info.get_edge_position())
        except Exception:
            pass  # Keep previous cached value

    @staticmethod
    def _layout_mode_for_edge(edge) -> str:
        """Map a taskbar edge to the paint ``layout_mode`` - 'vertical' for a side (LEFT/RIGHT) taskbar,
        'horizontal' for TOP/BOTTOM. See ``_refresh_cached_layout_mode`` for why this drives the
        side-by-side right-align and live/preview parity."""
        return 'vertical' if edge in (
            constants.TaskbarEdge.LEFT, constants.TaskbarEdge.RIGHT
        ) else 'horizontal'

    def is_startup_enabled(self, force_check: bool = False) -> bool:
        """Checks if startup is enabled via StartupManager."""
        return self.startup_manager.is_startup_enabled(force_check)


    def toggle_startup(self, enable: bool) -> None:
        """Toggles startup via StartupManager."""
        try:
            self.startup_manager.toggle_startup(enable)
            self.config['start_with_windows'] = enable
            self.update_config({'start_with_windows': enable})
            self.logger.info(f"Application startup successfully {'enabled' if enable else 'disabled'}.")
        except Exception as e:
            self.logger.error(f"Failed to {'enable' if enable else 'disable'} startup: {e}", exc_info=True)
            QMessageBox.warning(
                self,
                "Startup Error",
                f"Could not {'enable' if enable else 'disable'} automatic startup.\n\n{e}"
            )


    def _synchronize_startup_task(self) -> None:
        """Synchronizes startup state using StartupManager."""
        should_be_enabled = self.config.get("start_with_windows", constants.config.defaults.DEFAULT_START_WITH_WINDOWS)
        self.startup_manager.synchronize_startup_task(should_be_enabled)


    def update_retention_period(self, days: int) -> None:
        """
        Public method called by child windows (like GraphWindow) to update
        the data retention period and trigger the necessary backend logic.
        
        Args:
            days: The new retention period in days.
        """
        self.logger.info("Request received to update data retention period to %d days.", days)
        if not self.widget_state:
            self.logger.error("Cannot update retention period: WidgetState is not available.")
            return
        
        # 1. Update the in-memory config dictionary.
        self.config["keep_data"] = days
        
        # 2. Persist the change immediately to the config file.
        self.update_config(self.config)
        
        # 3. Notify the WidgetState, which will trigger the grace period logic.
        self.widget_state.update_retention_period()

    def get_unified_interface_list(self) -> List[str]:
        """
        Returns a comprehensive, sorted list of network interfaces by combining
        currently active interfaces with all interfaces found in the history database.
        This serves as the single source of truth for all UI elements.
        """
        if not self.controller or not self.widget_state:
            self.logger.warning("Cannot get unified interface list: core components not initialized.")
            return []
        
        try:
            # Call the controller directly, as it is the true source of the live list.
            live_interfaces = set(self.controller.get_available_interfaces())
            
            # Get interfaces from the database history
            historical_interfaces = set(self.widget_state.get_distinct_interfaces())
            
            # Combine them, which automatically handles duplicates, then sort for a consistent UI.
            unified_list = sorted(list(live_interfaces.union(historical_interfaces)))
            
            self.logger.debug(f"Unified interface list created with {len(unified_list)} items.")
            return unified_list
        except Exception as e:
            self.logger.error(f"Error creating unified interface list: {e}", exc_info=True)
            return [] # Return an empty list on error
        

    def _primary_interface_for_picker(self) -> Optional[str]:
        """The routing interface, marked "Active connection" in the Settings picker. None if the
        controller isn't up or there is no route."""
        if not self.controller:
            return None
        try:
            return self.controller.current_primary_interface()
        except Exception as e:
            self.logger.debug("Primary interface lookup for the picker failed: %s", e)
            return None

    def get_active_interfaces(self) -> List[str]:
        """
        Provides a passthrough to the controller's method for getting a list
        of currently active network interfaces.
        """
        if self.controller:
            return self.controller.get_active_interfaces()
        return []


    def _capture_position_config(self) -> None:
        """Put the widget's current spot into self.config (not saved here).

        Persist the absolute spot when it's actively floating (Free Move or #188 free-float). Only CLEAR
        the saved coords when the widget is genuinely docked - i.e. BOTH free_move and free_float are
        OFF in config. Crucially we do NOT clear just because the free-float display is currently
        absent (asleep/unplugged): is_floating() is runtime state, so gating the clear on it would wipe
        a user's dragged spot every time the accessory panel sleeps. (config_controller clears on an
        explicit switch to docked.) (#188)"""
        if self.position_manager and self.position_manager.is_floating():
            pos = self.pos()
            self.update_config({"position_x": pos.x(), "position_y": pos.y()}, save_to_disk=False)
        elif not self.config.get("free_move", False) and not self.config.get("free_float", True):
            self.update_config({"position_x": None, "position_y": None}, save_to_disk=False)
        # else: free-float-capable but the display is temporarily absent -> keep the saved spot.

    def _save_session_essentials(self, reason: str) -> None:
        """The saves that must survive a logoff/shutdown, inside Windows' ~5 s WM_ENDSESSION budget:
        the widget position + config (an atomic file write) first, then the odometer tail and the
        last history batch, waited for at most _SESSION_END_DB_WAIT_S. Nothing here stops a thread or
        waits DB_SHUTDOWN_WAIT_MS; the database thread keeps draining best-effort until the process
        ends. Each step is guarded, so a failing one never skips the next."""
        self.logger.info("Windows session ending (%s): saving settings and the usage counter.", reason)

        def _odometer() -> None:
            if getattr(self, "widget_state", None):
                self.widget_state.persist_essentials(timeout=_SESSION_END_DB_WAIT_S)

        for name, fn in (
            ("position config", self._capture_position_config),
            ("config save", lambda: self.config_manager.save(self.config)),
            ("odometer + history", _odometer),
        ):
            try:
                fn()
            except Exception as e:
                self.logger.error("Session-end step '%s' failed: %s", name, e, exc_info=True)

    def cleanup(self, save_config: bool = True) -> None:
        """Performs necessary cleanup and a single, final save of the configuration.

        ``save_config=False`` (after a failed startup) stops everything the same way but writes
        nothing to the config file.

        Runs once. An exit reaches it twice - closeEvent calls it, then app.quit() fires aboutToQuit,
        which is connected here too - and a second pass would repeat the bounded waits (latency probe,
        and the database thread's DB_SHUTDOWN_WAIT_MS when a migration outlived the first one),
        doubling a slow quit past the installer's --shutdown timeout. The done-flag is set only after
        every step has had its turn; the running-flag stops a re-entrant call meanwhile.

        Every step runs in its own guard (log and continue), so one failing teardown - an event hook
        that will not unhook, a thread that raises on stop - can never skip the history flush or the
        final config save that come after it.
        """
        if getattr(self, "_cleanup_done", False) or getattr(self, "_cleanup_running", False):
            return
        self._cleanup_running = True
        self.logger.debug("Performing widget cleanup...")
        failed: List[str] = []

        def _mirrors() -> None:
            # "Show on all taskbars" (#229): close every mirror while the data they paint still exists.
            mgr = getattr(self, "_mirrors", None)
            if mgr is not None:
                self._mirrors = None
                mgr.shutdown()

        def _hover_card() -> None:
            # Tear down the hover usage card + its armed timer first, before the data layer
            # (widget_state / monitor thread) goes away - otherwise a pending 350ms singleShot
            # could fire _show_usage_hover_card into half-torn-down state, or leave an orphan
            # card window during shutdown.
            self._hover_card_timer.stop()
            self._hide_usage_hover_card()

        def _event_hooks() -> None:
            # self.foreground_hook is likely legacy, but keeping the check is harmless
            if getattr(self, 'system_event_handler', None):
                self.system_event_handler.stop()
            elif getattr(self, 'foreground_hook', None):
                self.foreground_hook.stop()

        def _position_manager() -> None:
            if getattr(self, 'position_manager', None):
                self.position_manager.stop_monitoring()
                self.position_manager.stop_side_watcher()
                self.position_manager.stop_app_overlap()

        def _state_watcher() -> None:
            timer = getattr(self, '_state_watcher_timer', None)
            if timer is not None and timer.isActive():
                timer.stop()

        def _monitor_thread() -> None:
            if getattr(self, 'monitor_thread', None):
                self.logger.debug("Stopping StatsMonitorThread...")
                self.monitor_thread.stop()

        def _latency_probe() -> None:
            probes = list(getattr(self, '_retired_latency_probes', None) or [])
            if getattr(self, 'latency_probe', None):
                probes.append(self.latency_probe)
            for p in probes:
                p.stop()
            for p in probes:   # all stopped first, so the waits overlap (one in-flight echo each)
                p.wait(1500)
            from netspeedtray.core import thread_reaper
            for p in probes:   # e.g. inside gethostbyname for the anchor - DNS cannot be cancelled
                thread_reaper.outlived(p, "A latency probe")

        def _update_checker() -> None:
            # A check in flight sits in urlopen for up to 10 s; it is not waited for.
            if getattr(self, 'update_checker', None):
                self.update_checker.stop()

        def _metrics_endpoint() -> None:
            # Joins its threads; nothing is left listening.
            if getattr(self, 'metrics_endpoint', None):
                self.metrics_endpoint.stop()

        def _controller() -> None:
            if getattr(self, 'controller', None):
                self.controller.cleanup()

        def _history_flush() -> None:
            # The final batch is written and the DB thread stopped (bounded by DB_SHUTDOWN_WAIT_MS).
            if getattr(self, 'widget_state', None):
                self.widget_state.cleanup()

        def _monitor_window() -> None:
            # Close the Monitor if it's open (it persists its own geometry + active tab on close).
            if getattr(self, 'monitor_window', None):
                try:
                    self.monitor_window.close()
                finally:
                    self.monitor_window = None

        def _position_config() -> None:
            self._capture_position_config()

        def _history_backup() -> None:
            # A running Settings > Advanced > Back up history (#334): interrupt its copy and give its
            # thread a moment to delete the unfinished file + -journal. It only ever reads the live
            # file, so a thread still running after the wait loses nothing (it is a daemon thread).
            from netspeedtray.core.db import history_backup
            history_backup.cancel_all(constants.timeouts.HISTORY_BACKUP_CANCEL_WAIT_MS / 1000.0)

        def _config_save() -> None:
            self.logger.debug("Performing final configuration save...")
            self.config_manager.save(self.config)

        # Order matters (2.2.0-beta.1 lifecycle review):
        # * "monitor window" before "history flush": closing the Monitor stops + joins its graph worker
        #   (bounded), which closes its own read connection - WidgetState.cleanup() must never find a
        #   reader mid-query. It also persists the Monitor's geometry into self.config.
        # * "config save" before "history flush": the DB step can wait DB_SHUTDOWN_WAIT_MS (15 s) for a
        #   busy database thread, and a logoff ends the process ~5 s in - the save must already be done.
        steps = (
            ("mirrors", _mirrors),
            ("hover card", _hover_card), ("event hooks", _event_hooks),
            ("position manager", _position_manager), ("state watcher", _state_watcher),
            ("monitor window", _monitor_window), ("history backup", _history_backup),
            ("monitor thread", _monitor_thread), ("latency probe", _latency_probe),
            ("update checker", _update_checker),
            ("metrics endpoint", _metrics_endpoint), ("controller", _controller),
            ("position config", _position_config), ("config save", _config_save),
            ("history flush", _history_flush),
        )
        if not save_config:
            steps = tuple(s for s in steps if s[0] not in ("position config", "config save"))
        try:
            for name, fn in steps:
                try:
                    fn()
                except Exception as e:
                    failed.append(name)
                    self.logger.error("Cleanup step '%s' failed: %s", name, e, exc_info=True)
        finally:
            self._cleanup_running = False
            self._cleanup_done = True
        if failed:
            self.logger.warning("Widget cleanup finished; failed steps: %s", ", ".join(failed))
        else:
            self.logger.debug("Widget cleanup finished successfully.")
