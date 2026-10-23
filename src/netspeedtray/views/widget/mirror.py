"""
"Show on all taskbars" (#229): paint-only copies of the widget on every other taskbar.

A mirror is NOT a second NetworkSpeedWidget. It owns no config file, no monitor thread, no database,
no tray menu and no hover card; it paints the main widget's live state (``main._build_metrics()``)
through the same ``render_widget`` path, and forwards right-click / double-click / middle-click /
wheel to the main widget. What it does own is everything that is per WINDOW:

- its own ``WidgetRenderer`` (the renderer caches graph polylines by size and records the content
  bounds the context menu centres on - shared, it would thrash and misplace the main widget's menu);
- its own ``PositionManager`` (the burial / backoff machine is per instance, so a fight on one
  taskbar never escalates another window's backoff) and ``WidgetLayoutManager`` (sized for ITS bar);
- a config overlay (``_MirrorConfig``) that pins the placement keys to "docked on my screen, default
  offsets" and reads everything else live from the main config.

Lifecycle: ``MirrorManager`` exists only while the setting is on (``NetworkSpeedWidget.apply_mirrors``).
With the setting off nothing in this module is imported, constructed, connected or timed.

Mirrors are never the installer's shutdown target: they never call ``shutdown_channel.arm_window``
and never override ``nativeEvent`` - one receiver per process, the main widget, as before.

Known v1 limits (accepted):
- No hover card and no drag on a mirror.
- A mirror on a secondary SIDE taskbar does not get #406's side-bar watcher (W1-W4: re-placing as app
  buttons come and go, the live alignment flip): its PositionManager is built with
  side_watcher_enabled=False, so no hook thread exists per mirror. It keeps the static placement.
- A mirror does not yield to app buttons (#438, #24 "Hide while app buttons cover the widget"): its
  PositionManager is built with app_yield_enabled=False, so it never probes and never hides for them.
- Fullscreen is judged from the FOREGROUND window, as for the main widget: a fullscreen app on a
  monitor that is not in the foreground (a video left playing on monitor 2 while you work on 1)
  leaves that monitor's mirror showing.

Design: Docs/releases/v2.2/design-229-all-monitors.md.
"""
from __future__ import annotations

import collections.abc
import logging
from collections import Counter
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

import win32api
import win32con
import win32gui
from PyQt6.QtCore import QObject, QTimer, Qt
from PyQt6.QtGui import QColor, QContextMenuEvent, QMouseEvent, QPainter, QPaintEvent, QWheelEvent
from PyQt6.QtWidgets import QApplication, QWidget

from netspeedtray import constants
from netspeedtray.core.position_manager import (PositionManager, WindowState, _GWLP_HWNDPARENT,
                                                _GetWindowLongPtr, _SetWindowLongPtr)
from netspeedtray.utils import taskbar_utils
from netspeedtray.utils.config import stable_pseudonym
from netspeedtray.utils.helpers import effective_display_mode
from netspeedtray.utils.taskbar_utils import dock_signature, is_taskbar_obstructed, is_taskbar_visible
from netspeedtray.utils.widget_paint import render_widget
from netspeedtray.utils.widget_renderer import WidgetRenderer
from netspeedtray.views.widget.layout import WidgetLayoutManager

logger = logging.getLogger(f"{constants.app.APP_NAME}.Mirrors")


class _MirrorConfig(collections.abc.MutableMapping):
    """
    A mirror's view of the config: a few placement keys pinned, everything else read LIVE from the
    main widget's config. ``get_main_config`` is a callable because ``rollback_config`` REPLACES the
    main dict - a captured reference would go stale.

    Pinned: ``preferred_monitor`` = this mirror's screen (so every ``get_taskbar_info(preferred)``
    inside the position and layout managers finds ITS taskbar), docked (no free move, no #188 float,
    no saved spot) and the default tray offsets (the main widget's drag offset is the main widget's).
    Only the pinned keys can be written (PositionManager.reset_to_default writes them); any other
    write is dropped with a warning, so a stray write can neither reach the main config / the disk
    nor hide a main-config value from the mirror for good.
    """

    def __init__(self, get_main_config: Callable[[], Any], screen_name: str) -> None:
        defaults = constants.config.defaults
        self._get_main = get_main_config
        self._pinned: Dict[str, Any] = {
            "preferred_monitor": screen_name,
            "free_move": False,
            "free_float": False,
            "position_x": None,
            "position_y": None,
            "tray_offset_x": defaults.DEFAULT_TRAY_OFFSET_X,
            "tray_offset_y": defaults.DEFAULT_TRAY_OFFSET_Y,
        }

    def _main(self) -> Any:
        cfg = self._get_main()
        return cfg if cfg is not None else {}

    def __getitem__(self, key: str) -> Any:
        if key in self._pinned:
            return self._pinned[key]
        return self._main()[key]

    def __setitem__(self, key: str, value: Any) -> None:
        if key in self._pinned:
            self._pinned[key] = value
        else:
            logger.warning("Mirror config: ignored a write to %r (only placement keys are writable).", key)

    def __delitem__(self, key: str) -> None:
        raise TypeError("a mirror's config keys cannot be deleted (%r)" % (key,))

    def __iter__(self) -> Iterator[str]:
        seen = set()
        for source in (self._pinned, self._main()):
            for key in source:
                if key not in seen:
                    seen.add(key)
                    yield key

    def __len__(self) -> int:
        return sum(1 for _ in self)


class MirrorWidget(QWidget):
    """One paint-only copy of the widget, docked to one (secondary or primary) taskbar."""

    def __init__(self, main: Any, taskbar_info: Any) -> None:
        super().__init__(None)
        self._main = main
        self.screen_name: str = taskbar_info.screen_name
        self.taskbar_hwnd: int = int(taskbar_info.hwnd)
        self._tag = "mirror " + stable_pseudonym(self.screen_name, "DISPLAY")
        self.logger = logging.getLogger(f"{constants.app.APP_NAME}.Mirror")
        # Set when Windows destroyed the native window underneath us (its owner taskbar went away):
        # the manager then drops the mirror without another Win32 call on it.
        self.dead: bool = False

        # --- what WidgetLayoutManager / PositionManager read from their host -------------------
        self.config = _MirrorConfig(lambda: main.config, self.screen_name)
        self.i18n = main.i18n
        self.current_font = None
        self.current_metrics = None
        self._dock_sig_seen: Optional[tuple] = None
        self._tb = taskbar_info                      # the taskbar this mirror was last handed
        self._obstructed: bool = False               # a fullscreen window on ITS monitor
        self._layout_mode: str = self._mode_for(taskbar_info)
        self._identity_sig_seen = self._main_identity_signature()

        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
        self.setVisible(False)
        self.layout_manager = WidgetLayoutManager(self)
        self.layout_manager.setup_window_properties()
        # Own renderer; re-synced lazily from the main renderer's config identity (_sync_renderer).
        self.renderer = WidgetRenderer(main.config, main.i18n)
        self._src_render_cfg = getattr(getattr(main, "renderer", None), "config", None)
        # Born on its monitor's DPI, so its first move never crosses a DPI boundary.
        screen = taskbar_info.get_screen()
        if screen is not None:
            self.setScreen(screen)
        # Own PositionManager: per-window burial backoff. Its 10 s tray watcher is NOT started - it
        # re-fetches get_taskbar_info(preferred), which falls back to the PRIMARY bar the moment this
        # screen loses its taskbar; the manager's tick does that job from the enumeration instead.
        self.position_manager = PositionManager(
            WindowState(config=self.config, widget=self, taskbar_info=taskbar_info),
            parent=self, log_tag=self._tag, side_watcher_enabled=False, app_yield_enabled=False)
        self.layout_manager.set_font(resize=True)   # sizes for ITS bar, ends in update_position()
        self._dock_sig_seen = dock_signature(taskbar_info)
        logger.info("Mirror created on %s (taskbar HWND %s).", self._tag, self.taskbar_hwnd)

    # --- host protocol for WidgetLayoutManager -----------------------------------------------------
    @property
    def ram_total(self) -> Optional[float]:
        return getattr(self._main, "ram_total", None)

    @property
    def vram_total(self) -> Optional[float]:
        return getattr(self._main, "vram_total", None)

    def _identity_reserve_px(self, fm) -> int:
        return self._main._identity_reserve_px(fm)

    def update_position(self) -> None:
        """Called at the end of every resize: reposition from the CACHED taskbar the manager last
        handed in - never a fresh get_taskbar_info(), which falls back to the primary bar."""
        tb = self._tb
        if tb is not None and getattr(tb, "hwnd", 0):
            self.position_manager.update_position(fresh_taskbar_info=tb, _float_refreshed=True)

    # --- helpers -------------------------------------------------------------------------------------
    def _mode_for(self, tb: Any) -> str:
        try:
            return self._main._layout_mode_for_edge(tb.get_edge_position())
        except Exception:
            return "horizontal"

    def _main_identity_signature(self) -> object:
        try:
            return self._main._identity_reserve_signature()
        except Exception:
            return None

    def _hwnd(self) -> int:
        """Our native window, or 0 when none exists yet. Never creates one (internalWinId)."""
        try:
            return int(self.internalWinId() or 0)
        except Exception:
            return 0

    def native_alive(self) -> bool:
        """False once the native window we had is gone - Windows destroys the windows a taskbar OWNS
        when that taskbar is destroyed (monitor unplugged, "Show my taskbar on all displays" off,
        explorer restart). Not yet created counts as alive (Qt creates it on the first show)."""
        hwnd = self._hwnd()
        return not hwnd or bool(win32gui.IsWindow(hwnd))

    def owner_lost(self) -> bool:
        """True when we are owned by a taskbar window that no longer exists."""
        hwnd = self._hwnd()
        if not hwnd or not win32gui.IsWindow(hwnd):
            return False
        owner = int(_GetWindowLongPtr(hwnd, _GWLP_HWNDPARENT) or 0)
        return bool(owner) and not win32gui.IsWindow(owner)

    def detach_owner(self) -> None:
        """Cut the owner link (GWLP_HWNDPARENT = 0) so a taskbar being torn down cannot take our
        native window with it. The next ensure_topmost() re-docks if the mirror lives on."""
        hwnd = self._hwnd()
        try:
            if hwnd and win32gui.IsWindow(hwnd) and _GetWindowLongPtr(hwnd, _GWLP_HWNDPARENT):
                _SetWindowLongPtr(hwnd, _GWLP_HWNDPARENT, 0)
        except Exception as e:
            self.logger.debug("[%s] detaching the owner failed: %s", self._tag, e)

    def _mark_dead(self, why: str) -> None:
        if not self.dead:
            self.dead = True
            logger.info("Mirror on %s lost its window (%s); it will be rebuilt.", self._tag, why)

    def _sync_renderer(self) -> None:
        """Follow the main renderer: ``RenderConfig`` is a new object after every update_config, so
        one identity check catches Settings Apply, live preview, theme flips and rollback."""
        src = getattr(getattr(self._main, "renderer", None), "config", None)
        if src is not self._src_render_cfg:
            self.renderer.update_config(self._main.config)
            self._src_render_cfg = src

    # --- the per-tick body (a trimmed main._execute_refresh) ---------------------------------------
    def refresh(self, tb: Any, fg_hwnd: int) -> None:
        if self.dead:
            return
        if not self.native_alive():
            self._mark_dead("native window destroyed")
            return
        main = self._main
        if getattr(main, "_is_context_menu_visible", False):
            return   # the menu is modal and may sit over us: move nothing until it closes (#200)
        try:
            if self.owner_lost():
                # Our owner taskbar is gone but we survived: unlink before anything else touches us.
                self.detach_owner()
            self._tb = tb
            self._layout_mode = self._mode_for(tb)
            # Judged against THIS mirror's taskbar and monitor (is_taskbar_obstructed compares the
            # foreground window's monitor with the taskbar's), never the main widget's (§3.4).
            self._obstructed = bool(tb.hwnd) and is_taskbar_obstructed(tb, fg_hwnd)
            keep_visible = main.config.get("keep_visible_fullscreen",
                                           constants.config.defaults.DEFAULT_KEEP_VISIBLE_FULLSCREEN)
            visible = bool(tb.hwnd) and is_taskbar_visible(tb) and (keep_visible or not self._obstructed)
            if self.isVisible() != visible:
                self.setVisible(visible)
            if not visible:
                self.position_manager.note_on_top_unverified()
                return
            if not self._resize_if_needed(tb):
                self.position_manager.update_position(fresh_taskbar_info=tb, _float_refreshed=True)
            self.position_manager.ensure_topmost()
            self.recover_if_buried()
        except Exception as e:
            self.logger.error("[%s] refresh failed: %s", self._tag, e, exc_info=True)

    def _resize_if_needed(self, tb: Any) -> bool:
        """Re-size on a dock-signature change (Default <-> Small bar, edge, DPI - #322/#390) or a new
        identity-badge reserve (the main widget re-lays out on a network change). True if resized."""
        from netspeedtray.views.widget.main import widget_qt_scale   # deferred: main imports this module
        sig = dock_signature(tb)
        ident = self._main_identity_signature()
        if sig is None:
            return False
        if sig == self._dock_sig_seen and ident == self._identity_sig_seen:
            # Same dock - but after a live scale change Qt's ratio for this window can lag the OS one,
            # so a re-size in between measured at the old scale (main._resize_if_dock_changed). Once.
            qt_scale, seen = widget_qt_scale(self), getattr(self, "_qt_scale_seen", None)
            if qt_scale is None or seen is None or abs(qt_scale - seen) < 1e-3:
                return False
            self._qt_scale_seen = qt_scale   # a failed resize can't retry every tick
            self.logger.info("[%s] display scale changed %s -> %s; re-sizing.", self._tag, seen, qt_scale)
            self.layout_manager.resize_widget_for_font()
            return True
        self._identity_sig_seen = ident
        self.layout_manager.resize_widget_for_font()   # ends with update_position() from self._tb
        # Record the signature of the bar we were HANDED: the layout manager's own lookup can fall
        # back to the primary while this screen's bar is going away, and must not re-trigger a
        # resize every tick.
        self._dock_sig_seen = sig
        return True

    def recover_if_buried(self) -> None:
        """main._recover_if_buried with this window's guards: hidden, the (modal, shared) context
        menu open, or a fullscreen app on THIS mirror's monitor - with Keep visible in fullscreen on,
        the mirror's place over the video stays as it is."""
        if self.dead:
            return
        try:
            pm = self.position_manager
            if (not self.isVisible() or getattr(self._main, "_is_context_menu_visible", False)
                    or self._obstructed):
                pm.note_on_top_unverified()
                return
            if pm.is_buried_behind_taskbar():
                pm.reseat_above_taskbar()
        except Exception as e:
            self.logger.debug("[%s] burial recovery check failed: %s", self._tag, e)

    def apply_settings(self) -> None:
        """Settings Apply / live preview: follow the main widget's font, size and colours."""
        if self.dead:
            return
        self._sync_renderer()
        self.layout_manager.set_font(resize=True)
        self._dock_sig_seen = dock_signature(self._tb)
        self._identity_sig_seen = self._main_identity_signature()
        self.update()

    # --- paint -----------------------------------------------------------------------------------------
    def paintEvent(self, event: QPaintEvent) -> None:
        if not self.isVisible() or self.dead:
            return
        main = self._main
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            # Base hit-test layer (nearly transparent), as on the main widget.
            painter.fillRect(self.rect(), QColor(0, 0, 0, 1))
            self._sync_renderer()
            if not self.renderer or not self.current_metrics:
                return
            render_widget(
                painter, self.rect(), self.renderer, self.renderer.config,
                main._build_metrics(),
                layout_mode=self._layout_mode,                 # from ITS taskbar edge (§3.7)
                cycle_mode=main._current_cycle_mode,           # one shared cycle phase
                network_width=getattr(self.layout_manager, "_network_width", None),
                font=self.current_font,
                paused=main.is_paused,
            )
        except Exception as e:
            self.logger.error("[%s] paint failed: %s", self._tag, e, exc_info=True)
        finally:
            if painter.isActive():
                painter.end()

    # --- input: forwarded to the main widget (no drag, no hover card in v1) -------------------------
    def _run_click_action(self, key: str, default: str) -> None:
        handler = getattr(self._main, "input_handler", None)
        if handler is None:
            return
        action = self._main.config.get(key, default)
        handler._execute_click_action(action)
        self._bring_window_here(action)

    def _bring_window_here(self, action: str) -> None:
        """The Monitor / Settings window the action opened restores its saved spot - often on the
        main widget's monitor. Opened from a mirror, centre it on THIS mirror's monitor instead (the
        user is looking here). A maximized or full-screen window is left where it is."""
        defaults = constants.config.defaults
        attr = {defaults.CLICK_ACTION_OPEN_MONITOR: "monitor_window",
                defaults.CLICK_ACTION_SETTINGS: "settings_dialog"}.get(action)
        win = getattr(self._main, attr, None) if attr else None
        screen = self.screen()
        try:
            if (win is None or screen is None or not win.isVisible()
                    or win.isMaximized() or win.isFullScreen()):
                return
            frame = win.frameGeometry()
            if QApplication.screenAt(frame.center()) is screen:
                return
            avail = screen.availableGeometry()
            x = avail.left() + max(0, (avail.width() - frame.width()) // 2)
            y = avail.top() + max(0, (avail.height() - frame.height()) // 2)
            win.move(x, y)
        except Exception as e:
            self.logger.debug("[%s] moving the opened window failed: %s", self._tag, e)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.MiddleButton:
            self._run_click_action("middle_click_action",
                                   constants.config.defaults.DEFAULT_MIDDLE_CLICK_ACTION)
        event.accept()   # a left press starts nothing: mirrors are not draggable

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._run_click_action("double_click_action",
                                   constants.config.defaults.DEFAULT_DOUBLE_CLICK_ACTION)
        event.accept()

    def wheelEvent(self, event: QWheelEvent) -> None:
        """Cycle mode: scroll steps the SHARED cycle (every copy rotates together), as on main."""
        main = self._main
        if effective_display_mode(main.config) != "cycle":
            super().wheelEvent(event)
            return
        delta = event.angleDelta().y()
        if delta == 0:
            event.ignore()
            return
        main._step_cycle(1 if delta < 0 else -1)
        if main._cycle_timer.isActive():
            main._cycle_timer.start(constants.renderer.CYCLE_INTERVAL_MS)
        event.accept()

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        """The main widget's menu, opened over THIS mirror on its monitor."""
        try:
            tray = getattr(self._main, "tray_manager", None)
            if tray is not None:
                tray.show_context_menu(anchor=self)
            event.accept()
        except Exception as e:
            self.logger.error("[%s] context menu failed: %s", self._tag, e, exc_info=True)
            event.ignore()


def _window_class(hwnd: int) -> str:
    """The window class of a live ``hwnd``; "" when it is not (or no longer) a window."""
    try:
        if hwnd and win32gui.IsWindow(hwnd):
            return win32gui.GetClassName(hwnd) or ""
    except Exception:
        pass
    return ""


def _fullscreen_monitor(hwnd: int) -> int:
    """The HMONITOR a true-fullscreen ``hwnd`` covers (window rect == monitor rect), else 0. The
    cheap test SystemEventHandler._is_fullscreen_obstruction starts with; no taskbar query."""
    try:
        if not hwnd or not win32gui.IsWindow(hwnd):
            return 0
        monitor = win32api.MonitorFromWindow(hwnd, win32con.MONITOR_DEFAULTTONEAREST)
        info = win32api.GetMonitorInfo(monitor)
        return int(monitor) if win32gui.GetWindowRect(hwnd) == info.get("Monitor") else 0
    except Exception:
        return 0


class MirrorManager(QObject):
    """
    Owns the mirrors while "Show on all taskbars" is on: one per taskbar other than the main
    widget's, keyed by the taskbar's monitor (MirrorManager._key). Reconciled from ONE get_all_taskbar_info() a tick (1 s), however
    many mirrors exist. Constructed only by NetworkSpeedWidget.apply_mirrors when the setting is on.
    """

    def __init__(self, main: Any,
                 taskbars_provider: Optional[Callable[[], List[Any]]] = None,
                 mirror_factory: Optional[Callable[[Any], Any]] = None,
                 foreground_provider: Optional[Callable[[], int]] = None,
                 window_class: Optional[Callable[[int], str]] = None) -> None:
        super().__init__(main if isinstance(main, QObject) else None)
        self._main = main
        self._taskbars = taskbars_provider or taskbar_utils.get_all_taskbar_info
        self._factory = mirror_factory or (lambda tb: MirrorWidget(main, tb))
        self._foreground = foreground_provider or win32gui.GetForegroundWindow
        self._window_class = window_class or _window_class
        self.mirrors: Dict[Any, Any] = {}   # monitor key (see _key) -> mirror
        self._connections: List[Tuple[Any, Callable]] = []
        self._shut = False
        self._last_fs_monitor = 0
        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(constants.timeouts.STATE_WATCHER_INTERVAL_MS)
        self._tick_timer.timeout.connect(self.tick)
        # Fast fullscreen edge detection (an app going fullscreen while focused fires no foreground
        # event). Runs only while mirrors exist; cheap until the foreground window is fullscreen.
        self._fs_timer = QTimer(self)
        self._fs_timer.setInterval(constants.timeouts.FULLSCREEN_POLL_INTERVAL_MS)
        self._fs_timer.timeout.connect(self._poll_fullscreen)

    # --- lifecycle -------------------------------------------------------------------------------------
    def start(self) -> None:
        """Wire to the main widget's signals - connected AFTER main's own slots, so main's state is
        current when a mirror repaints - and run the first reconcile now."""
        main = self._main
        handler = getattr(main, "system_event_handler", None)
        monitor = getattr(main, "monitor_thread", None)
        if monitor is not None:
            self._connect(monitor.stats_ready, self._repaint_all)
        cycle = getattr(main, "_cycle_timer", None)
        if cycle is not None:
            self._connect(cycle.timeout, self._repaint_all)
        if handler is not None:
            self._connect(handler.zorder_changed, self._recover_all)
            self._connect(handler.taskbar_focused, self._recover_all)
            self._connect(handler.taskbar_restarted, self._on_taskbar_restarted)
            self._connect(handler.foreground_app_changed, self._on_foreground)
            self._connect(handler.theme_changed, self._repaint_all)
        app = QApplication.instance()
        if app is not None:
            self._connect(app.screenAdded, self._on_screen_added)
            self._connect(app.screenRemoved, self._on_screen_removed)
            self._connect(app.primaryScreenChanged, self._on_screen_added)
        self._tick_timer.start()
        self.tick()
        logger.info("Show on all taskbars: on.")

    def _connect(self, signal: Any, slot: Callable) -> None:
        signal.connect(slot)
        self._connections.append((signal, slot))

    def shutdown(self) -> None:
        """Stop the timers, disconnect everything and close every mirror. Idempotent."""
        if self._shut:
            return
        self._shut = True
        self._tick_timer.stop()
        self._fs_timer.stop()
        for signal, slot in self._connections:
            try:
                signal.disconnect(slot)
            except (TypeError, RuntimeError):
                pass
        self._connections.clear()
        for key in list(self.mirrors):
            self._destroy(key)
        logger.info("Show on all taskbars: off.")

    @property
    def connection_count(self) -> int:
        return len(self._connections)

    def timers_active(self) -> bool:
        return self._tick_timer.isActive() or self._fs_timer.isActive()

    # --- reconcile ---------------------------------------------------------------------------------
    def _main_dock(self) -> Optional[Tuple[int, Optional[str]]]:
        """(hwnd, screen name) of the taskbar the main widget sits on; (0, None) while it free-floats
        on a taskbar-less display (#188) - then every bar gets a mirror; None while unknown."""
        getter = getattr(self._main, "docked_taskbar", None)
        return getter() if callable(getter) else None

    @staticmethod
    def _key(tb: Any) -> Any:
        """A mirror's identity: the MONITOR its taskbar is on, from Win32 (GetMonitorInfo's physical
        rect) - not the Qt screen name, which a taskbar gets by overlap with Qt's screen list and
        which is wrong for a moment while a display is being added (#434 live run: display 2's mirror
        was rebuilt onto display 1's new taskbar). The screen name only when the rect is unknown."""
        rect = getattr(tb, "monitor_rect", None)
        return tuple(rect) if rect else tb.screen_name

    def reconcile(self, taskbars: List[Any]) -> Dict[Any, Any]:
        """Create / destroy mirrors so there is exactly one per taskbar other than main's. Returns
        the target {monitor key: TaskbarInfo}. Only a mirror whose taskbar HWND or screen changed is
        rebuilt (explorer restart, a re-created bar); one whose taskbar is unchanged is left alone."""
        dock = self._main_dock()
        if dock is None:
            return {}   # the main widget's own dock is unknown this tick: change nothing
        main_hwnd, main_screen = dock
        # The PRIMARY taskbar (Shell_TrayWnd) gets a mirror only while the main widget is provably
        # elsewhere: docked to a live secondary bar, or off any taskbar (free-float / Free Move, where
        # the screen check below decides). During an explorer restart main's HWND is dead for a tick
        # and the screen labels are in flux - a mirror then landed on the primary, over main (#434).
        primary_ok = not main_hwnd or self._window_class(main_hwnd) == "Shell_SecondaryTrayWnd"
        candidates = [tb for tb in taskbars if int(getattr(tb, "hwnd", 0) or 0)]
        # Two taskbars labelled with ONE Qt screen = a display is being added or removed and Qt's
        # screen list is behind: leave those monitors alone this tick (no create, no destroy).
        names = Counter(tb.screen_name for tb in candidates)
        targets: Dict[Any, Any] = {}
        held = set()
        for tb in candidates:
            hwnd = int(tb.hwnd)
            key = self._key(tb)
            if names[tb.screen_name] > 1:
                held.add(key)
                continue
            if hwnd == main_hwnd:
                continue
            # By screen too: just after an explorer restart main may still hold the OLD hwnd of a
            # bar on its own screen - never mirror onto the main widget's screen.
            if main_screen is not None and tb.screen_name == main_screen:
                continue
            if not primary_ok and self._window_class(hwnd) == "Shell_TrayWnd":
                continue
            targets.setdefault(key, tb)
        for key in list(self.mirrors):
            mirror = self.mirrors[key]
            if key in held and not getattr(mirror, "dead", False):
                continue
            tb = targets.get(key)
            if (tb is None or int(tb.hwnd) != int(mirror.taskbar_hwnd)
                    or tb.screen_name != mirror.screen_name or getattr(mirror, "dead", False)):
                self._destroy(key)
        for key, tb in targets.items():
            if key not in self.mirrors:
                try:
                    self.mirrors[key] = self._factory(tb)
                except Exception as e:
                    logger.error("Could not create a mirror for %s: %s",
                                 stable_pseudonym(tb.screen_name, "DISPLAY"), e, exc_info=True)
        if self.mirrors:
            if not self._fs_timer.isActive() and not self._shut:
                self._fs_timer.start()
        elif self._fs_timer.isActive():
            self._fs_timer.stop()
        return targets

    def _destroy(self, key: Any) -> None:
        mirror = self.mirrors.pop(key, None)
        if mirror is None:
            return
        key = getattr(mirror, "screen_name", key)   # for the log line (pseudonymised)
        try:
            # #406's side-bar watcher (a hook thread) must never outlive its mirror. Mirrors never
            # attach it (side_watcher_enabled=False); stop it anyway, in case that ever changes.
            # Same for #438's app-button yield (built inert for mirrors: app_yield_enabled=False).
            pm = getattr(mirror, "position_manager", None)
            if pm is not None and hasattr(pm, "stop_side_watcher"):
                pm.stop_side_watcher()
            if pm is not None and hasattr(pm, "stop_app_overlap"):
                pm.stop_app_overlap()
            if not getattr(mirror, "dead", False):
                # Unlink from the taskbar FIRST, so its teardown cannot destroy our window under Qt.
                mirror.detach_owner()
                mirror.hide()
            mirror.deleteLater()
        except Exception as e:
            logger.debug("Destroying the mirror for %s failed: %s", stable_pseudonym(key, "DISPLAY"), e)

    def tick(self, fg_hwnd: int = 0) -> None:
        """One enumeration, then reconcile, then each mirror's refresh against ITS taskbar."""
        if self._shut:
            return
        try:
            taskbars = self._taskbars()
            targets = self.reconcile(taskbars)
            fg = fg_hwnd or int(self._foreground() or 0)
            for key, mirror in list(self.mirrors.items()):
                tb = targets.get(key)
                if tb is not None:
                    mirror.refresh(tb, fg)
                if getattr(mirror, "dead", False):
                    self._destroy(key)   # rebuilt on the next tick if its taskbar is still there
        except Exception as e:
            logger.error("Mirror tick failed: %s", e, exc_info=True)

    # --- event slots ---------------------------------------------------------------------------------
    def _on_foreground(self, hwnd: int = 0) -> None:
        self.tick(int(hwnd or 0))

    def _repaint_all(self, *_args) -> None:
        for mirror in list(self.mirrors.values()):
            if not getattr(mirror, "dead", False) and mirror.isVisible():
                mirror.update()

    def _recover_all(self, *_args) -> None:
        for mirror in list(self.mirrors.values()):
            mirror.recover_if_buried()

    def _staggered_ticks(self) -> None:
        self.tick()
        for i in range(1, constants.timeouts.TASKBAR_RESTART_RETRIES + 1):
            QTimer.singleShot(i * constants.timeouts.TASKBAR_RESTART_RECOVERY_DELAY_MS, self.tick)

    def _on_taskbar_restarted(self) -> None:
        """Explorer is rebuilding the taskbars: drop the mirrors whose taskbar window is gone; the next
        ticks rebuild them on the new windows. A mirror whose taskbar is still the same live window
        is kept - the restart signal (the 3 s validity poll) often comes AFTER the ticks have already
        rebuilt the mirrors, and dropping them again rebuilt them twice on the same HWNDs (#434)."""
        if self._shut:
            return
        for key, mirror in list(self.mirrors.items()):
            if not self._window_class(int(mirror.taskbar_hwnd)):
                self._destroy(key)
        self._staggered_ticks()

    def _on_screen_added(self, *_args) -> None:
        if not self._shut:
            self._staggered_ticks()

    def _on_screen_removed(self, screen: Any = None) -> None:
        """A monitor is going away: retire its mirror NOW, before explorer tears that monitor's
        taskbar down (and with it the windows it owns)."""
        if self._shut:
            return
        try:
            name = screen.name() if screen is not None else None
        except Exception:
            name = None
        for key, mirror in list(self.mirrors.items()):
            if name is not None and getattr(mirror, "screen_name", None) == name:
                self._destroy(key)
        self._staggered_ticks()

    def _poll_fullscreen(self) -> None:
        """Re-evaluate at once when the foreground window becomes (or stops being) fullscreen on a
        monitor - the mirror on THAT monitor hides; the others are left alone."""
        if self._shut or not self.mirrors:
            return
        fg = int(self._foreground() or 0)
        fs_monitor = _fullscreen_monitor(fg)
        if fs_monitor != self._last_fs_monitor:
            self._last_fs_monitor = fs_monitor
            self.tick(fg)

    def apply_settings(self) -> None:
        """Settings Apply / live preview: re-font and re-size every mirror, then re-place them."""
        if self._shut:
            return
        for mirror in list(self.mirrors.values()):
            try:
                mirror.apply_settings()
            except Exception as e:
                logger.error("Applying settings to a mirror failed: %s", e, exc_info=True)
        self.tick()
