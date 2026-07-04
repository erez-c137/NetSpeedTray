from __future__ import annotations

# --- Standard Library Imports ---
import ctypes
import logging
import math
import os
import sys
import time
from ctypes import wintypes
from datetime import datetime
from collections import deque
from typing import Any, Deque, Dict, List, Optional, Tuple, TYPE_CHECKING

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
from netspeedtray.core.timer_manager import SpeedTimerManager
from netspeedtray.core.monitor_thread import StatsMonitorThread
from netspeedtray.core.tray_manager import TrayIconManager
from netspeedtray.core.widget_state import WidgetState as CoreWidgetState
from netspeedtray.utils.config import ConfigManager as CoreConfigManager
from netspeedtray.core.position_manager import PositionManager, WindowState
from netspeedtray.core.input_handler import InputHandler
from netspeedtray.utils.taskbar_utils import (
    get_taskbar_info, is_taskbar_obstructed, is_taskbar_visible,
    get_process_name_from_hwnd
)

from netspeedtray.utils.widget_renderer import WidgetRenderer as CoreWidgetRenderer, RenderConfig
from netspeedtray.utils.widget_paint import WidgetMetrics, render_widget
from netspeedtray.core.system_events import SystemEventHandler
from netspeedtray.views.widget.layout import WidgetLayoutManager
from netspeedtray.views.widget.theme import WidgetThemeManager
from netspeedtray.core.startup_manager import StartupManager
from netspeedtray.core.config_controller import ConfigController
from netspeedtray.core.update_checker import UpdateChecker

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
_PBT_APMRESUMESUSPEND = 0x0007
_PBT_APMRESUMEAUTOMATIC = 0x0012


class NetworkSpeedWidget(QWidget):
    """Main widget for displaying network speeds near the Windows system tray."""

    MIN_UPDATE_INTERVAL = constants.config.defaults.MINIMUM_UPDATE_RATE


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
        
        if i18n is None:
            raise ValueError("An i18n instance must be provided to NetworkSpeedWidget.")
        self.i18n = i18n
        
        # These MUST be initialized before _init_managers() because it checks self.current_metrics
        self.current_font: QFont = None
        self.current_metrics: QFontMetrics = None

        self._init_managers() # Initialize managers first
        self.theme_manager.apply_theme_aware_defaults()

        # --- Declare all instance attributes for clarity ---
        self.widget_state: CoreWidgetState
        self.timer_manager: SpeedTimerManager
        self.controller: CoreController
        self.renderer: CoreWidgetRenderer
        self.position_manager: PositionManager
        self.input_handler: InputHandler
        self.layout_manager: WidgetLayoutManager
        self.theme_manager: WidgetThemeManager
        self.tray_manager: TrayIconManager
        self.monitor_thread: StatsMonitorThread
        self._cached_layout_mode: str = 'horizontal'  # taskbar-edge orientation; updated on taskbar changes
        self.monitor_window = None  # unified Monitor (Overview / Network / Hardware), lazy
        self.update_checker: Optional[UpdateChecker] = None
        self.app_icon: QIcon
        # Note: self.current_font and self.current_metrics are initialized earlier before _init_managers()
        
        self.upload_speed: float = 0.0
        self.download_speed: float = 0.0
        self.cpu_usage: float = 0.0
        self.gpu_usage: float = 0.0
        self.network_identity: Optional[object] = None  # NetworkIdentity|None: Wi-Fi band/SSID, set by update_network_identity (v2.1)
        self._identity_reserve_sig_last: object = None  # last identity reserve signature (band-shown, ssid); re-layout on change
        self._location_prompt_shown: bool = False  # SSID Location nudge shown at most once per session
        self.gpu_present: bool = False  # LATCHES True once any poll detects GPU counters (never flips
        #                                 back - a GPU doesn't vanish), so the Monitor's GPU tile shows
        #                                 stably for a real/integrated GPU and stays hidden only on a
        #                                 genuinely GPU-less box. Default False => hidden until proven.
        self.cpu_temp: Optional[float] = None
        self.gpu_temp: Optional[float] = None
        self.cpu_power: Optional[float] = None
        self.gpu_power: Optional[float] = None
        self.system_power: Optional[float] = None   # true whole-system W (RAPL PSYS / battery), if available
        self.latency_gw: Optional[float] = None      # gateway RTT ms (LAN latency); None = timeout
        self.latency_anchor: Optional[float] = None  # public-anchor RTT ms (internet latency); opt-in
        self.latency_loss: float = 0.0               # rolling gateway loss% over recent probes
        self._latency_window: Deque[bool] = deque(maxlen=40)   # recent timed-out flags for live loss%
        self.ram_used: Optional[float] = None
        self.ram_total: Optional[float] = None
        self.vram_used: Optional[float] = None
        self.vram_total: Optional[float] = None
        
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
        self._is_context_menu_visible: bool = False
        self.last_tray_rect: Optional[Tuple[int, int, int, int]] = None
        self._taskbar_lost_count: int = 0
        self._will_quit_app: bool = False # Flag to distinguish hide vs exit
        
        # Hooks for system events
        self.system_event_handler: SystemEventHandler

        
        # Timers for periodic checks
        # self._tray_watcher_timer moved to PositionManager
        self._state_watcher_timer = QTimer(self) # The "Safety Net" timer

        # Hover usage card - a Win11 flyout we position ourselves (above the taskbar) so it is
        # never clipped, unlike Qt's built-in tooltip. Shown after a short rest on the widget.
        self._hover_card = None
        self._hover_card_timer = QTimer(self)
        self._hover_card_timer.setSingleShot(True)
        self._hover_card_timer.timeout.connect(self._show_usage_hover_card)
        self._hover_usage_cache: Optional[Tuple[Tuple[float, float], Tuple[float, float]]] = None
        self._hover_usage_cache_ts: float = 0.0

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
            
            QTimer.singleShot(0, self._delayed_initial_show)

            self.logger.debug("NetworkSpeedWidget initialized successfully.")

        except Exception as e:
            self.logger.critical("Initialization failed: %s", e, exc_info=True)
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

        # Cycle Timer
        if self.config.get("widget_display_mode") == "cycle":
            self._cycle_timer.start(constants.renderer.CYCLE_INTERVAL_MS)
            self.logger.debug("Cycle timer started.")


    def _init_core_components(self) -> None:
        """
        Initialize non-UI core logic components.

        Sets up WidgetState, SpeedTimerManager, StatsController, and WidgetRenderer.
        """
        self.logger.debug("Initializing core components...")
        if not self.config:
            raise RuntimeError("Cannot initialize core: Config missing")
        try:
            self.widget_state = CoreWidgetState(self.config)
            self.timer_manager = SpeedTimerManager(self.config, parent=self)
            
            # Background Monitoring Thread
            # Determine effective monitor interval. Support SMART sentinel (-1.0)
            cfg_rate = self.config.get("update_rate", constants.config.defaults.DEFAULT_UPDATE_RATE)

            # If the user is using "auto" scaling (unit auto-selection), running in
            # SMART adaptive mode produces very frequent UI changes that can be
            # visually jarring (e.g. rapid unit switching). Enforce a safe fallback:
            # when speed_display_mode == "auto" and update_rate signals SMART (<=0),
            # fall back to a sensible default fixed rate to avoid live-mode jitter.
            speed_mode = str(self.config.get("speed_display_mode", constants.config.defaults.DEFAULT_SPEED_DISPLAY_MODE))
            if isinstance(cfg_rate, (int, float)) and cfg_rate < 0:
                if speed_mode == "auto":
                    # Log and fallback to default fixed update rate (do not persist silently)
                    self.logger.warning(
                        "Incompatible settings: speed_display_mode='auto' with SMART update_rate. Falling back to default update rate %.1fs to avoid live-mode jitter.",
                        constants.config.defaults.DEFAULT_UPDATE_RATE
                    )
                    cfg_rate = constants.config.defaults.DEFAULT_UPDATE_RATE
                    effective_interval = max(constants.config.defaults.MINIMUM_UPDATE_RATE, min(float(cfg_rate), constants.timers.MAXIMUM_UPDATE_RATE_SECONDS))
                else:
                    # SMART mode (-1.0): Use adaptive interval
                    effective_interval = constants.timers.SMART_MODE_INTERVAL_MS / 1000.0
            else:
                # Fixed interval: Clamp to allowed min/max
                effective_interval = max(constants.config.defaults.MINIMUM_UPDATE_RATE, min(float(cfg_rate), constants.timers.MAXIMUM_UPDATE_RATE_SECONDS))

            self.monitor_thread = StatsMonitorThread(interval=effective_interval, config=self.config)
            
            self.controller = CoreController(config=self.config, widget_state=self.widget_state)
            self.controller.set_view(self)
            self.renderer = CoreWidgetRenderer(self.config, self.i18n)
            
            # Note: We no longer start timer_manager for speeds; monitor_thread drives them.
            # self.timer_manager.start_timer() 
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
            # Position Manager
            taskbar_info = get_taskbar_info()
            window_state = WindowState(
                config=self.config,
                widget=self,
                taskbar_info=taskbar_info,
                font_metrics=self.current_metrics
            )
            self.position_manager = PositionManager(window_state, parent=self)
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

            # Allow user override to keep widget visible even when a fullscreen window is present
            keep_visible = self.config.get("keep_visible_fullscreen", False)
            if free_float_active:
                # The accessory display has no taskbar to hide behind, and the visibility/obstruction
                # checks are judged against the (irrelevant) primary taskbar - so just keep it shown.
                should_be_visible = True
            else:
                should_be_visible = is_taskbar_visible(taskbar_info) and (keep_visible or not is_taskbar_obstructed(taskbar_info, hwnd))

            if self.isVisible() != should_be_visible:
                self.setVisible(should_be_visible)

            # Only update position if we are supposed to be visible.
            if self.isVisible():
                # Free-move keeps the user's exact saved spot (applied once); docked and free-float both
                # reposition on the refresh (free-float re-places on / re-detects its taskbar-less display).
                if not self.config.get("free_move", False):
                    # _float_refreshed=True: we already called refresh_float_state() above for the
                    # visibility check this tick, so don't re-enumerate taskbars inside (#188).
                    self.position_manager.update_position(fresh_taskbar_info=taskbar_info, _float_refreshed=True)
                
                # Always re-assert topmost status when visible to prevent falling behind taskbar (#77)
                self._ensure_win32_topmost()

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
        """First-run onboarding. A brand-new install gets the calm 'unfold' flyout that
        points at the features most users never find; an upgrader gets the one-time 2.0
        welcome dialog. The two gates are mutually exclusive - they never both fire."""
        # Brand-new install: the unfold flyout is DISABLED for this release - its only action opens the
        # Monitor, so there's no real guided tour to justify an interrupting pop-up yet (re-enable once
        # there's an actual walkthrough; _show_unfold_flyout() is kept for that). We still advance the
        # first-run flags so nothing pops later and the upgrade dialog stays correctly suppressed.
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

    def _show_unfold_flyout(self) -> None:
        """A one-time calm callout near the widget pointing at the hidden features."""
        try:
            from netspeedtray.views.flyout import Flyout
            self._unfold_flyout = Flyout(
                self.i18n.UNFOLD_FLYOUT_TITLE,
                self.i18n.UNFOLD_FLYOUT_BODY,
                action_text=self.i18n.SHOW_ME_AROUND_LABEL,
            )
            self._unfold_flyout.action_clicked.connect(self.open_monitor_window)
            geo = self.frameGeometry()
            self._unfold_flyout.show_at(QPoint(geo.left(), geo.top()))
        except Exception as e:
            self.logger.error(f"Error showing unfold flyout: {e}", exc_info=True)

    def _on_latency(self, gw_ms, anchor_ms, gw_timed_out: bool) -> None:
        """A latency sample arrived (main thread, queued from the probe thread). Update the live
        attributes + rolling loss%, and persist gateway/anchor RTT + the timeout flag so the Monitor's
        window loss% (the ISP-dispute figure) is honest."""
        self.latency_gw = gw_ms
        self.latency_anchor = anchor_ms
        self._latency_window.append(bool(gw_timed_out))
        if self._latency_window:
            self.latency_loss = round(sum(self._latency_window) / len(self._latency_window) * 100.0, 1)
        ws = getattr(self, "widget_state", None)
        if ws is not None:
            try:
                if gw_ms is not None:
                    ws.add_hardware_stat("latency_gw", float(gw_ms))
                if anchor_ms is not None:
                    ws.add_hardware_stat("latency_anchor", float(anchor_ms))
                ws.add_hardware_stat("latency_gw_timeout", 1.0 if gw_timed_out else 0.0)
            except Exception:
                pass

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

    def _show_usage_alert(self, title: str, message: str) -> None:
        """Show a data-cap usage alert via the flyout (no system-tray icon needed)."""
        try:
            from netspeedtray.views.flyout import Flyout
            self._usage_alert_flyout = Flyout(title, message, auto_dismiss_ms=15000)
            geo = self.frameGeometry()
            self._usage_alert_flyout.show_at(QPoint(geo.left(), geo.top()))
        except Exception as e:
            self.logger.error(f"Error showing usage alert: {e}", exc_info=True)

    def open_data_cap_dialog(self) -> None:
        """Open the data-cap settings dialog and apply any changes."""
        try:
            from netspeedtray.views.datacap_dialog import DataCapDialog
            up, down = self.widget_state.get_usage_this_period()
            cnt = self.config.get("data_cap_count", "total")
            used = down if cnt == "download" else up if cnt == "upload" else (up + down)
            dlg = DataCapDialog(self.config, used_bytes=used, parent=self, i18n=self.i18n)
            if dlg.exec():
                self.update_config(dlg.get_values())
        except Exception as e:
            self.logger.error(f"Error opening data cap dialog: {e}", exc_info=True)


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
        self.update()


    def resume(self) -> None:
        """Resume the live readout after pause()."""
        if not self.is_paused:
            self.logger.debug("Widget already running")
            return
        self.logger.info("Resuming widget updates")
        self.is_paused = False
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


    def update_cpu_usage(self, usage: float) -> None:
        """Update CPU usage and trigger repaint."""
        if self.is_paused:
            return
        self.cpu_usage = usage
        if self.config.get("widget_display_mode") in ["cpu_only", "combined", "side_by_side", "cycle"]:
            self.update()

    def update_gpu_usage(self, usage: float) -> None:
        """Update GPU usage and trigger repaint."""
        if self.is_paused:
            return
        self.gpu_usage = usage
        if self.config.get("widget_display_mode") in ["gpu_only", "combined", "side_by_side", "cycle"]:
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
        if self.config.get("widget_display_mode") in ["cpu_only", "combined", "side_by_side", "cycle"]:
            self.update()

    def update_gpu_temp(self, temp: Optional[float]) -> None:
        """Update GPU temperature and trigger repaint."""
        if self.is_paused:
            return
        self.gpu_temp = temp
        if self.config.get("widget_display_mode") in ["gpu_only", "combined", "side_by_side", "cycle"]:
            self.update()

    def update_cpu_power(self, power: Optional[float]) -> None:
        """Update CPU power draw and trigger repaint."""
        if self.is_paused:
            return
        self.cpu_power = power
        if self.config.get("widget_display_mode") in ["cpu_only", "combined", "side_by_side", "cycle"]:
            self.update()

    def update_gpu_power(self, power: Optional[float]) -> None:
        """Update GPU power draw and trigger repaint."""
        if self.is_paused:
            return
        self.gpu_power = power
        if self.config.get("widget_display_mode") in ["gpu_only", "combined", "side_by_side", "cycle"]:
            self.update()

    def update_ram_info(self, used: float, total: float) -> None:
        """Update RAM info and trigger repaint."""
        if self.is_paused:
            return
        self.ram_used = used
        self.ram_total = total
        if self.config.get("widget_display_mode") in ["cpu_only", "combined", "side_by_side", "cycle"]:
            self.update()

    def update_vram_info(self, used: float, total: float) -> None:
        """Update VRAM info and trigger repaint."""
        if self.is_paused:
            return
        self.vram_used = used
        self.vram_total = total if total >= 0 else None
        if self.config.get("widget_display_mode") in ["gpu_only", "combined", "side_by_side", "cycle"]:
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
        if self.config.get("monitor_cpu_enabled", False): modes.append("cpu_only")
        if self.config.get("monitor_gpu_enabled", False): modes.append("gpu_only")

        if not modes: return

        self._cycle_index = (self._cycle_index + step) % len(modes)
        self._current_cycle_mode = modes[self._cycle_index]
        self.update() # Trigger repaint with new mode





    # _load_initial_config removed as it is now handled by ConfigController class


    def _on_theme_changed(self) -> None:
        """Delegates theme change handling."""
        self.theme_manager.on_theme_changed()





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
        if not all([self.widget_state, self.timer_manager, self.controller]):
            raise RuntimeError("Core components missing during signal connection setup.")
        try:
            # Connect core component signals
            self.monitor_thread.stats_ready.connect(self.controller.handle_stats)
            
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

            # Start the monitoring thread
            self.monitor_thread.start()

            # Latency probe on its own thread (gateway by default; public anchor opt-in). A 1 s ICMP
            # timeout must never sit on the stats poll, so it's a separate QThread.
            self.latency_probe = None
            if self.config.get("latency_enabled", True):
                try:
                    from netspeedtray.core.latency_probe import LatencyProbe
                    self.latency_probe = LatencyProbe(self.config)
                    self.latency_probe.latency_ready.connect(self._on_latency)
                    self.latency_probe.start()
                except Exception as e:
                    self.logger.debug("LatencyProbe not started: %s", e)

            # 1. System Event Handler (replaces manual WinEventHooks)
            self.system_event_handler.foreground_app_changed.connect(self._execute_refresh)
            self.system_event_handler.taskbar_changed.connect(self.update_position)
            self._refresh_cached_layout_mode()
            self.system_event_handler.theme_changed.connect(self._on_theme_changed)
            
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

            # Handle taskbar restarts
            self.system_event_handler.taskbar_restarted.connect(lambda: [QTimer.singleShot(i * constants.timeouts.TASKBAR_RESTART_RECOVERY_DELAY_MS, self._execute_refresh) for i in range(constants.timeouts.TASKBAR_RESTART_RETRIES)])

            # React to monitor topology changes (add/remove/primary swap, KVM, dock/undock):
            # re-validate the position + re-assert the widget. A set-and-forget app must follow
            # its taskbar across these without the user nudging it.
            app = QApplication.instance()
            if app is not None:
                app.screenAdded.connect(lambda _s: self._on_environment_changed("screenAdded"))
                app.screenRemoved.connect(lambda _s: self._on_environment_changed("screenRemoved"))
                app.primaryScreenChanged.connect(lambda _s: self._on_environment_changed("primaryScreenChanged"))

            self.system_event_handler.start()

            
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
            )
        except Exception as e:
            self.logger.error(f"Error in paintEvent: {e}", exc_info=True)
        finally:
            if painter.isActive():
                painter.end()

    def _build_metrics(self) -> WidgetMetrics:
        """Snapshot the current live state into the shared paint path's metrics object."""
        identity_text, identity_color, identity_solid = self._identity_presentation()
        identity_ssid = self._identity_ssid_text()
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
            net_history=self.widget_state.get_aggregated_speed_history(),
            cpu_history=list(self.widget_state.cpu_history),
            gpu_history=list(self.widget_state.gpu_history),
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

    def enterEvent(self, event) -> None:
        """On hover, arm the usage card (shown after a short rest, positioned above the taskbar).
        Skipped entirely when the user has turned the hover card off in Settings."""
        try:
            hover_enabled = (self.config.get("show_usage_on_hover", True)
                             or self.config.get("show_hover_tips", True))
            if hover_enabled and self._hover_card is None and not self._hover_card_timer.isActive():
                self._hover_card_timer.start(self._HOVER_CARD_DELAY_MS)
        except Exception as e:
            self.logger.debug("hover card arm failed: %s", e)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        """When the pointer leaves the widget, cancel/hide the usage card."""
        try:
            self._hover_card_timer.stop()
            self._hide_usage_hover_card()
        except Exception as e:
            self.logger.debug("hover card hide failed: %s", e)
        super().leaveEvent(event)

    def _show_usage_hover_card(self) -> None:
        """Build and show the usage card just above the widget (never under the taskbar)."""
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
            hint = self._hover_hint_text() if show_tips else None
            today, month = self._hover_usage_totals() if show_data else (None, None)
            cap = self._hover_cap_info() if show_data else None
            if hint is None and today is None:
                return  # nothing to show (both toggles off, or tips graduated while data is off)
            self._hide_usage_hover_card()  # never stack two cards
            self._hover_card = UsageFlyout(self.i18n, today, month, hint=hint, cap=cap)
            screen = self.screen() or QApplication.primaryScreen()
            avail = screen.availableGeometry() if screen else self.frameGeometry()
            self._hover_card.show_for(self.frameGeometry(), avail)
        except Exception as e:
            self.logger.error("Error showing usage hover card: %s", e, exc_info=True)

    def _hide_usage_hover_card(self) -> None:
        """Tear down the usage card if one is up."""
        card, self._hover_card = self._hover_card, None
        if card is not None:
            try:
                card.hide()
                card.deleteLater()
            except Exception:
                pass

    def _hover_usage_totals(self) -> Tuple[Tuple[float, float], Tuple[float, float]]:
        """(today, this-month) bandwidth as (up, down) byte pairs, cached with a short TTL."""
        now = time.monotonic()
        if self._hover_usage_cache is not None and (now - self._hover_usage_cache_ts) < self._HOVER_USAGE_TTL_SEC:
            return self._hover_usage_cache
        today: Tuple[float, float] = (0.0, 0.0)
        month: Tuple[float, float] = (0.0, 0.0)
        try:
            now_dt = datetime.now()
            midnight = now_dt.replace(hour=0, minute=0, second=0, microsecond=0)
            month_start = midnight.replace(day=1)
            today = self.widget_state.get_total_bandwidth_for_period(midnight, now_dt)
            month = self.widget_state.get_total_bandwidth_for_period(month_start, now_dt)
        except Exception as e:
            self.logger.debug("hover usage fetch failed: %s", e)
        self._hover_usage_cache = (today, month)
        self._hover_usage_cache_ts = now
        return self._hover_usage_cache

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
            return self.i18n.WIDGET_HOVER_TOOLTIP
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
        if self.config.get("widget_display_mode") != "cycle":
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


    def showEvent(self, event: QShowEvent) -> None:
        self.logger.debug(f"Widget showEvent triggered. New visibility: {self.isVisible()}")
        super().showEvent(event)


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
            return
        self.position_manager.ensure_topmost()

    def _on_environment_changed(self, reason: str = "") -> None:
        """
        The display/power environment changed (monitor add/remove, primary swap, wake from
        sleep). Re-validate position and re-assert visibility/Z-order with a couple of
        staggered passes, since Windows needs a moment to settle taskbar + monitor geometry.
        """
        self.logger.debug("Environment changed (%s) - re-asserting widget.", reason)
        try:
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
                if msg.message == _WM_POWERBROADCAST and msg.wParam in (
                    _PBT_APMRESUMESUSPEND, _PBT_APMRESUMEAUTOMATIC
                ):
                    self.logger.info("Resume from sleep detected - re-asserting widget.")
                    QTimer.singleShot(constants.timeouts.TASKBAR_RESTART_RECOVERY_DELAY_MS,
                                      lambda: self._on_environment_changed("resume"))
        except Exception as e:
            self.logger.debug("nativeEvent power handling failed: %s", e)
        return False, 0

    def _enforce_topmost_status(self) -> None:
        """Delegates to PositionManager."""
        self.position_manager.enforce_topmost_status()

    def reset_to_default_position(self) -> None:
        """
        Resets the widget to its default position using PositionManager.
        """
        self.logger.info("Resetting widget position to default.")
        self.position_manager.reset_to_default()
        
        # Save the cleared config state
        self.update_config({'position_x': None, 'position_y': None})


    def apply_all_settings(self) -> None:
        """Delegates to ConfigController."""
        self.config_controller.apply_all_settings()


    def handle_settings_changed(self, updated_config: Dict[str, Any], save_to_disk: bool = True) -> None:
        """Delegates to ConfigController."""
        self.config_controller.handle_settings_changed(updated_config, save_to_disk)


    def open_hardware_settings(self) -> None:
        """Open Settings straight on the Hardware page (the tray 'Hardware monitor' row's door)."""
        try:
            self.show_settings()
            if self.settings_dialog is not None:
                self.settings_dialog.navigate_to_page(self.settings_dialog.PAGE_HARDWARE)
        except Exception as e:
            self.logger.error("Error opening hardware settings: %s", e, exc_info=True)

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
                    is_startup_enabled=self.is_startup_enabled()
                )
                # Connect signal for live preview updates (don't save to disk during preview)
                self.settings_dialog.settings_changed.connect(
                    lambda cfg: self.handle_settings_changed(cfg, save_to_disk=False)
                )

            if not self.settings_dialog.isVisible():
                # Also update the interface list when showing an existing dialog
                self.settings_dialog.update_interface_list(self.get_unified_interface_list())
                self.settings_dialog.reset_with_config(
                    config=self.config.copy(),
                    is_startup_enabled=self.is_startup_enabled()
                )
                self.settings_dialog.show()
            else:
                self.logger.debug("Settings dialog already visible. Activating.")
                # Also update the interface list when re-activating the dialog
                self.settings_dialog.update_interface_list(self.get_unified_interface_list())
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
        if self.update_checker:
            self.update_checker.update_available.connect(self._on_update_available_manual, Qt.ConnectionType.SingleShotConnection)
            self.update_checker.up_to_date.connect(self._on_up_to_date_manual, Qt.ConnectionType.SingleShotConnection)
            self.update_checker.check_failed.connect(self._on_check_failed_manual, Qt.ConnectionType.SingleShotConnection)
            self.update_checker.check_now()

    def _on_update_available(self, latest_version: str, release_url: str, body: str = "",
                             installer_url: str = "", portable_url: str = "") -> None:
        """Handle update available from automatic startup check."""
        self._show_update_dialog(latest_version, release_url, body, installer_url)

    def _on_update_available_manual(self, latest_version: str, release_url: str, body: str = "",
                                    installer_url: str = "", portable_url: str = "") -> None:
        """Handle update available from manual menu check."""
        self._show_update_dialog(latest_version, release_url, body, installer_url)

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
                            installer_url: str = "") -> None:
        """Show the update-available dialog: version delta + inert release notes,
        with Download / Skip / Not Now."""
        from netspeedtray.views.update_dialog import UpdateDialog
        latest = latest_version.lstrip("vV")
        dlg = UpdateDialog(self.i18n, constants.app.VERSION, latest, body, parent=self)
        dlg.exec()

        if dlg.action == UpdateDialog.ACTION_DOWNLOAD:
            self._start_secure_update(installer_url, release_url)
        elif dlg.action == UpdateDialog.ACTION_SKIP:
            self.config["skipped_version"] = latest
            self.update_config({"skipped_version": latest})

    def _start_secure_update(self, installer_url: str, release_url: str) -> None:
        """
        One-click update: download the signed installer, verify it (Authenticode +
        SignPath pin), and run it. Any failure falls back to opening the release page
        in the browser (the old behavior), so the worst case is never worse than before.
        """
        try:
            from netspeedtray.core.update_installer import SecureUpdater
            # In-flight guard: don't start a second download over a running one.
            existing = getattr(self, "_secure_updater", None)
            if existing is not None and existing.is_running():
                return
            # Parented to self (not GC'd); it self-destructs via deleteLater when done.
            self._secure_updater = SecureUpdater(self, installer_url, release_url, self.i18n)
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
            dlg = TempOnboardingDialog(self.i18n, self)
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
                self.position_manager.update_position()
            except Exception as e:
                self.logger.error(f"Error during position update: {e}", exc_info=True)


    def _refresh_cached_layout_mode(self) -> None:
        """Update the cached paint ``layout_mode`` from the current taskbar edge.

        Naming follows the taskbar's *orientation*: a TOP/BOTTOM taskbar is 'horizontal', a LEFT/RIGHT
        taskbar is 'vertical'. This is the value passed to ``render_widget(layout_mode=...)`` and must
        match what ``PreviewWidget`` uses for the same scenario (it renders a horizontal taskbar strip,
        so it passes 'horizontal') - otherwise the live widget and the Settings/Overview preview of the
        same config diverge. In particular the side-by-side right-align probe only fires for 'horizontal'
        (the over-reserved, right-anchored-to-the-tray case); a side taskbar is docked differently and is
        left as-is. (The renderer's draw methods accept layout_mode but don't branch on it today, so this
        only drives the right-align decision and the live/preview parity.)
        """
        try:
            # A free-floating widget on a taskbar-less display has no taskbar edge; render it as a
            # compact horizontal readout (#188).
            from netspeedtray.utils.taskbar_utils import get_free_float_screen
            if self.config.get("free_float", True) and \
                    get_free_float_screen(self.config.get("preferred_monitor")) is not None:
                self._cached_layout_mode = 'horizontal'
                return
            taskbar_info = get_taskbar_info()
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
        

    def get_active_interfaces(self) -> List[str]:
        """
        Provides a passthrough to the controller's method for getting a list
        of currently active network interfaces.
        """
        if self.controller:
            return self.controller.get_active_interfaces()
        return []


    def cleanup(self) -> None:
        """Performs necessary cleanup and a single, final save of the configuration."""
        self.logger.debug("Performing widget cleanup...")
        try:
            # Tear down the hover usage card + its armed timer first, before the data layer
            # (widget_state / monitor thread) goes away - otherwise a pending 350ms singleShot
            # could fire _show_usage_hover_card into half-torn-down state, or leave an orphan
            # card window during shutdown.
            self._hover_card_timer.stop()
            self._hide_usage_hover_card()

            # --- Stop all external event listeners and timers ---
            # self.foreground_hook and movesize_hook are likely legacy, but keeping check is harmless
            if hasattr(self, 'system_event_handler') and self.system_event_handler:
                self.system_event_handler.stop()
            elif hasattr(self, 'foreground_hook') and self.foreground_hook: 
                self.foreground_hook.stop()
            
            # Stop PositionManager monitoring
            if self.position_manager:
                self.position_manager.stop_monitoring()
            
            if self._state_watcher_timer.isActive(): self._state_watcher_timer.stop()
            
            # --- Stop the background monitor thread ---
            if hasattr(self, 'monitor_thread') and self.monitor_thread:
                self.logger.debug("Stopping StatsMonitorThread...")
                self.monitor_thread.stop()

            # --- Stop the latency probe thread ---
            if getattr(self, 'latency_probe', None):
                try:
                    self.latency_probe.stop()
                    self.latency_probe.wait(1500)
                except Exception:
                    pass

            # --- Clean up core components ---
            if self.timer_manager: self.timer_manager.cleanup()
            if self.controller: self.controller.cleanup()
            if self.widget_state: self.widget_state.cleanup()

            # Close the Monitor if it's open (it persists its own geometry + active tab on close).
            if self.monitor_window:
                try:
                    self.monitor_window.close()
                except Exception:
                    pass
                self.monitor_window = None

            # Persist the widget's absolute spot when it's actively floating (Free Move or #188
            # free-float). Only CLEAR the saved coords when the widget is genuinely docked - i.e. BOTH
            # free_move and free_float are OFF in config. Crucially we do NOT clear just because the
            # free-float display is currently absent (asleep/unplugged): is_floating() is runtime state,
            # so gating the clear on it would wipe a user's dragged spot every time the accessory panel
            # sleeps. (config_controller clears on an explicit switch to docked.) (#188)
            if self.position_manager and self.position_manager.is_floating():
                pos = self.pos()
                self.update_config({"position_x": pos.x(), "position_y": pos.y()}, save_to_disk=False)
            elif not self.config.get("free_move", False) and not self.config.get("free_float", True):
                self.update_config({"position_x": None, "position_y": None}, save_to_disk=False)
            # else: free-float-capable but the display is temporarily absent -> keep the saved spot.
            
            self.logger.debug("Performing final configuration save...")
            self.config_manager.save(self.config)

            self.logger.debug("Widget cleanup finished successfully.")
        except Exception as e:
            self.logger.error(f"Unexpected error during cleanup: %s", e, exc_info=True)
