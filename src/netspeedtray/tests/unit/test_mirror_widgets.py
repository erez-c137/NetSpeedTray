"""
"Show on all taskbars" (#229): paint-only mirrors of the widget on every other taskbar.

Design: Docs/releases/v2.2/design-229-all-monitors.md §4.1. What is pinned here:

1. the config overlay - pinned placement keys, live fall-through (also after rollback REPLACES the
   main dict), writes never reaching the main config;
2. reconcile - one mirror per taskbar other than the main widget's, rebuilt on an HWND change,
   none for a fallback taskbar, and with the setting OFF: no manager, no timers, no connections;
3. a mirror's refresh - judged against ITS taskbar and monitor, positioned from the TaskbarInfo it
   is handed (never a bare get_taskbar_info());
4. positioning through the overlay ignores the main widget's drag offset;
5. burial backoff is per window;
6. the renderer follows the main renderer's config;
7. the context-menu anchor;
8. mirrors are never the installer's shutdown target;
9. cleanup closes the mirrors first; a tick after shutdown is a no-op.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from PyQt6.QtCore import QObject, QPoint, QRect, QTimer, pyqtSignal
from PyQt6.QtWidgets import QApplication, QWidget

from netspeedtray import constants
from netspeedtray.views.widget import main as widget_main
from netspeedtray.views.widget import mirror as mirror_mod
from netspeedtray.views.widget.mirror import MirrorManager, MirrorWidget, _MirrorConfig


@pytest.fixture(scope="module", autouse=True)
def _app():
    return QApplication.instance() or QApplication([])


def _tb(hwnd, screen, primary=False):
    return SimpleNamespace(hwnd=hwnd, screen_name=screen, is_primary=primary,
                           get_edge_position=lambda: constants.TaskbarEdge.BOTTOM)


class _FakeMirror:
    """Stands in for MirrorWidget in the reconcile tests (no window, records what happens)."""

    def __init__(self, tb):
        self.taskbar_hwnd = tb.hwnd
        self.screen_name = tb.screen_name
        self.dead = False
        self.refreshed = []
        self.detached = self.hidden = self.deleted = False

    def refresh(self, tb, fg):
        self.refreshed.append((tb, fg))

    def detach_owner(self):
        self.detached = True

    def hide(self):
        self.hidden = True

    def deleteLater(self):
        self.deleted = True

    def isVisible(self):
        return not self.hidden

    def recover_if_buried(self):
        pass

    def update(self):
        pass


def _manager(taskbars, main_dock=(100, "PRIMARY"), fg=0):
    main = SimpleNamespace(docked_taskbar=lambda: main_dock)
    holder = {"taskbars": taskbars}
    mgr = MirrorManager(main, taskbars_provider=lambda: holder["taskbars"],
                        mirror_factory=_FakeMirror, foreground_provider=lambda: fg)
    return mgr, holder


# --- 1. the overlay --------------------------------------------------------------------------------

class TestOverlay:
    def test_pinned_keys_win_and_the_rest_falls_through(self):
        main = {"preferred_monitor": None, "free_move": True, "free_float": True, "position_x": 5,
                "position_y": 6, "tray_offset_x": -200, "tray_offset_y": 40, "font_size": 11}
        cfg = _MirrorConfig(lambda: main, "DISPLAY2")
        d = constants.config.defaults
        assert cfg["preferred_monitor"] == "DISPLAY2"
        assert cfg.get("free_move") is False and cfg.get("free_float") is False
        assert cfg.get("position_x") is None and cfg.get("position_y") is None
        assert cfg.get("tray_offset_x") == d.DEFAULT_TRAY_OFFSET_X
        assert cfg.get("tray_offset_y") == d.DEFAULT_TRAY_OFFSET_Y
        assert cfg.get("font_size") == 11
        assert cfg.get("missing", "dflt") == "dflt"
        assert "font_size" in cfg and set(main) <= set(cfg)

    def test_reads_follow_a_replaced_main_config(self):
        """rollback_config() REPLACES widget.config - the overlay must read the new dict."""
        host = SimpleNamespace(config={"font_size": 9})
        cfg = _MirrorConfig(lambda: host.config, "D2")
        host.config = {"font_size": 14}
        assert cfg["font_size"] == 14

    def test_writes_stay_in_the_overlay(self):
        main = {"position_x": 7, "font_size": 9}
        cfg = _MirrorConfig(lambda: main, "D2")
        cfg["position_x"] = 1
        cfg["font_size"] = 30
        assert main == {"position_x": 7, "font_size": 9}
        assert cfg["font_size"] == 30
        del cfg["font_size"]
        assert cfg["font_size"] == 9


# --- 2. reconcile ----------------------------------------------------------------------------------

class TestReconcile:
    def test_main_on_primary_mirrors_the_two_secondaries(self):
        mgr, _ = _manager([_tb(100, "PRIMARY", True), _tb(200, "B"), _tb(300, "C")])
        mgr.tick()
        assert sorted(mgr.mirrors) == ["B", "C"]
        mgr.shutdown()

    def test_main_on_secondary_mirrors_primary_and_the_other(self):
        mgr, _ = _manager([_tb(100, "PRIMARY", True), _tb(200, "B"), _tb(300, "C")], main_dock=(200, "B"))
        mgr.tick()
        assert sorted(mgr.mirrors) == ["C", "PRIMARY"]
        mgr.shutdown()

    def test_main_free_floating_mirrors_every_bar(self):
        mgr, _ = _manager([_tb(100, "PRIMARY", True), _tb(200, "B")], main_dock=(0, None))
        mgr.tick()
        assert sorted(mgr.mirrors) == ["B", "PRIMARY"]
        mgr.shutdown()

    def test_unknown_main_dock_changes_nothing(self):
        mgr, _ = _manager([_tb(100, "PRIMARY", True), _tb(200, "B")], main_dock=None)
        mgr.tick()
        assert mgr.mirrors == {}
        mgr.shutdown()

    def test_main_screen_never_mirrored_even_with_a_stale_hwnd(self):
        """Just after an explorer restart main may still hold the old HWND of its own bar."""
        mgr, _ = _manager([_tb(101, "PRIMARY", True), _tb(200, "B")], main_dock=(100, "PRIMARY"))
        mgr.tick()
        assert sorted(mgr.mirrors) == ["B"]
        mgr.shutdown()

    def test_removed_screen_destroys_its_mirror_owner_first(self):
        mgr, holder = _manager([_tb(100, "PRIMARY", True), _tb(200, "B")])
        mgr.tick()
        b = mgr.mirrors["B"]
        holder["taskbars"] = [_tb(100, "PRIMARY", True)]
        mgr.tick()
        assert mgr.mirrors == {}
        assert b.detached and b.hidden and b.deleted
        mgr.shutdown()

    def test_changed_hwnd_recreates_the_mirror(self):
        mgr, holder = _manager([_tb(100, "PRIMARY", True), _tb(200, "B")])
        mgr.tick()
        old = mgr.mirrors["B"]
        holder["taskbars"] = [_tb(100, "PRIMARY", True), _tb(201, "B")]
        mgr.tick()
        assert mgr.mirrors["B"] is not old and mgr.mirrors["B"].taskbar_hwnd == 201
        assert old.deleted
        mgr.shutdown()

    def test_fallback_taskbar_gets_no_mirror(self):
        mgr, _ = _manager([_tb(0, "PRIMARY", True)], main_dock=(0, None))
        mgr.tick()
        assert mgr.mirrors == {}
        mgr.shutdown()

    def test_dead_mirror_is_dropped_without_win32_calls_and_rebuilt(self):
        mgr, _ = _manager([_tb(100, "PRIMARY", True), _tb(200, "B")])
        mgr.tick()
        b = mgr.mirrors["B"]
        b.dead = True
        mgr.tick()
        assert b.deleted and not b.detached and not b.hidden   # its window is already gone
        assert mgr.mirrors["B"] is not b                         # rebuilt on the live taskbar
        mgr.shutdown()

    def test_each_mirror_is_refreshed_with_its_own_taskbar(self):
        bars = [_tb(100, "PRIMARY", True), _tb(200, "B"), _tb(300, "C")]
        mgr, _ = _manager(bars, fg=4242)
        mgr.tick()
        assert mgr.mirrors["B"].refreshed == [(bars[1], 4242)]
        assert mgr.mirrors["C"].refreshed == [(bars[2], 4242)]
        mgr.shutdown()

    def test_screen_removed_retires_that_mirror_at_once(self):
        mgr, _ = _manager([_tb(100, "PRIMARY", True), _tb(200, "B")])
        mgr._taskbars = lambda: [_tb(100, "PRIMARY", True), _tb(200, "B")]
        mgr.tick()
        b = mgr.mirrors["B"]
        mgr._staggered_ticks = lambda: None
        mgr._on_screen_removed(SimpleNamespace(name=lambda: "B"))
        assert b.detached and b.deleted and "B" not in mgr.mirrors
        mgr.shutdown()

    def test_tick_after_shutdown_is_a_noop(self):
        calls = []
        mgr, _ = _manager([_tb(100, "PRIMARY", True), _tb(200, "B")])
        mgr.tick()
        b = mgr.mirrors["B"]
        mgr.shutdown()
        assert b.deleted and mgr.mirrors == {} and not mgr.timers_active()
        mgr._taskbars = lambda: calls.append(1) or []
        mgr.tick()
        assert calls == []


# --- 2b. the setting OFF runs none of it ----------------------------------------------------------

class _Signals(QObject):
    stats_ready = pyqtSignal(dict)
    zorder_changed = pyqtSignal()
    taskbar_focused = pyqtSignal()
    taskbar_restarted = pyqtSignal()
    foreground_app_changed = pyqtSignal(int)
    theme_changed = pyqtSignal()


def _receiver_counts(sig, cycle):
    app = QApplication.instance()
    return (sig.receivers(sig.stats_ready), sig.receivers(sig.zorder_changed),
            sig.receivers(sig.taskbar_focused), sig.receivers(sig.taskbar_restarted),
            sig.receivers(sig.foreground_app_changed), sig.receivers(sig.theme_changed),
            cycle.receivers(cycle.timeout), app.receivers(app.screenAdded),
            app.receivers(app.screenRemoved), app.receivers(app.primaryScreenChanged))


def _fake_main(enabled):
    sig = _Signals()
    cycle = QTimer()
    w = SimpleNamespace(
        config={"show_on_all_taskbars": enabled}, monitor_thread=sig, system_event_handler=sig,
        _cycle_timer=cycle, logger=MagicMock(), _mirrors=None,
        docked_taskbar=lambda: (100, "PRIMARY"))
    w.apply_mirrors = lambda: widget_main.NetworkSpeedWidget.apply_mirrors(w)
    return w, sig, cycle


class TestSettingOff:
    def test_default_is_off(self):
        assert constants.config.defaults.DEFAULT_SHOW_ON_ALL_TASKBARS is False
        assert constants.config.defaults.DEFAULT_CONFIG["show_on_all_taskbars"] is False
        assert constants.config.defaults.VALIDATION_SCHEMA["show_on_all_taskbars"]["type"] is bool

    def test_off_builds_nothing_connects_nothing(self, monkeypatch):
        def boom(*a, **k):
            raise AssertionError("MirrorManager constructed with the setting off")
        monkeypatch.setattr(mirror_mod, "MirrorManager", boom)
        w, sig, cycle = _fake_main(False)
        before = _receiver_counts(sig, cycle)
        widget_main.NetworkSpeedWidget.apply_mirrors(w)   # startup (_setup_connections)
        widget_main.NetworkSpeedWidget.apply_mirrors(w)   # a settings Apply
        assert w._mirrors is None
        assert _receiver_counts(sig, cycle) == before
        w.logger.error.assert_not_called()

    def test_off_never_queries_the_taskbars(self, monkeypatch):
        from netspeedtray.utils import taskbar_utils
        monkeypatch.setattr(taskbar_utils, "get_all_taskbar_info",
                            MagicMock(side_effect=AssertionError("enumerated with the setting off")))
        w, _sig, _cycle = _fake_main(False)
        widget_main.NetworkSpeedWidget.apply_mirrors(w)
        assert w._mirrors is None

    def test_on_then_off_restores_every_connection_and_timer(self, monkeypatch):
        monkeypatch.setattr(mirror_mod.taskbar_utils, "get_all_taskbar_info", lambda: [])
        w, sig, cycle = _fake_main(True)
        before = _receiver_counts(sig, cycle)
        widget_main.NetworkSpeedWidget.apply_mirrors(w)
        mgr = w._mirrors
        assert isinstance(mgr, MirrorManager) and mgr.timers_active()
        assert _receiver_counts(sig, cycle) != before
        w.config["show_on_all_taskbars"] = False
        widget_main.NetworkSpeedWidget.apply_mirrors(w)
        assert w._mirrors is None and not mgr.timers_active() and mgr.connection_count == 0
        assert _receiver_counts(sig, cycle) == before

    def test_cleanup_closes_mirrors_first(self):
        """'mirrors' is the first cleanup step: the mirrors paint main's data, which goes next."""
        import inspect
        src = inspect.getsource(widget_main.NetworkSpeedWidget.cleanup)
        assert src.index('("mirrors", _mirrors)') < src.index('("hover card", _hover_card)')
        w = SimpleNamespace(_cleanup_done=False, _cleanup_running=False, logger=MagicMock())
        mgr = MagicMock()
        w._mirrors = mgr
        order = []
        mgr.shutdown.side_effect = lambda: order.append("mirrors")
        # Every later step fails fast on the bare namespace; only the mirrors step matters here.
        widget_main.NetworkSpeedWidget.cleanup(w, save_config=False)
        assert order == ["mirrors"] and w._mirrors is None


# --- 3. a mirror's refresh ------------------------------------------------------------------------

def _refresh_host(keep_visible=False, visible=False, menu=False):
    """A MagicMock 'self' for MirrorWidget.refresh with the main widget behind it."""
    m = MagicMock()
    m.dead = False
    m.native_alive.return_value = True
    m.owner_lost.return_value = False
    m._main = SimpleNamespace(config={"keep_visible_fullscreen": keep_visible},
                              _is_context_menu_visible=menu)
    m.isVisible.return_value = visible
    m._resize_if_needed.return_value = False
    m._mode_for.return_value = "horizontal"
    return m


@pytest.fixture
def per_monitor(monkeypatch):
    """Two monitors: a fullscreen window (hwnd 9) sits on the monitor of taskbar 200 only."""
    monkeypatch.setattr(mirror_mod, "is_taskbar_visible", lambda tb: tb.hwnd != 300)  # 300 auto-hidden
    monkeypatch.setattr(mirror_mod, "is_taskbar_obstructed", lambda tb, fg: fg == 9 and tb.hwnd == 200)

    def no_bare_lookup(*a, **k):
        raise AssertionError("a mirror must never fetch a taskbar itself")
    monkeypatch.setattr(mirror_mod.taskbar_utils, "get_taskbar_info", no_bare_lookup)


class TestMirrorRefresh:
    def test_hidden_by_a_fullscreen_window_on_its_monitor(self, per_monitor):
        m = _refresh_host(visible=True)
        MirrorWidget.refresh(m, _tb(200, "B"), 9)
        m.setVisible.assert_called_once_with(False)
        m.position_manager.update_position.assert_not_called()

    def test_stays_for_a_fullscreen_window_on_another_monitor(self, per_monitor):
        m = _refresh_host(visible=False)
        tb = _tb(400, "C")
        MirrorWidget.refresh(m, tb, 9)
        m.setVisible.assert_called_once_with(True)

    def test_hidden_when_its_bar_auto_hides(self, per_monitor):
        m = _refresh_host(visible=True)
        MirrorWidget.refresh(m, _tb(300, "D"), 0)
        m.setVisible.assert_called_once_with(False)

    def test_keep_visible_fullscreen_is_respected(self, per_monitor):
        m = _refresh_host(keep_visible=True, visible=True)
        tb = _tb(200, "B")
        MirrorWidget.refresh(m, tb, 9)
        m.setVisible.assert_not_called()
        assert m._obstructed is True     # the burial guard still sees the fullscreen app

    def test_positions_from_the_taskbar_it_was_handed(self, per_monitor):
        m = _refresh_host(visible=True)
        tb = _tb(400, "C")
        MirrorWidget.refresh(m, tb, 0)
        m.position_manager.update_position.assert_called_once_with(fresh_taskbar_info=tb,
                                                                   _float_refreshed=True)
        m.position_manager.ensure_topmost.assert_called_once()
        m.recover_if_buried.assert_called_once()
        assert m._tb is tb

    def test_skipped_while_the_shared_menu_is_open(self, per_monitor):
        m = _refresh_host(visible=True, menu=True)
        MirrorWidget.refresh(m, _tb(400, "C"), 0)
        m.setVisible.assert_not_called()
        m.position_manager.update_position.assert_not_called()

    def test_dead_native_window_marks_the_mirror_and_touches_nothing(self, per_monitor):
        m = _refresh_host(visible=True)
        m.native_alive.return_value = False
        m._mark_dead = lambda why: setattr(m, "dead", True)
        MirrorWidget.refresh(m, _tb(400, "C"), 0)
        assert m.dead is True
        m.setVisible.assert_not_called()

    def test_lost_owner_is_detached_first(self, per_monitor):
        m = _refresh_host(visible=True)
        m.owner_lost.return_value = True
        MirrorWidget.refresh(m, _tb(400, "C"), 0)
        m.detach_owner.assert_called_once()

    def test_burial_guard_skips_when_its_bar_is_obstructed_or_menu_open(self):
        for obstructed, menu in ((True, False), (False, True)):
            m = MagicMock()
            m.dead = False
            m.isVisible.return_value = True
            m._obstructed = obstructed
            m._main = SimpleNamespace(_is_context_menu_visible=menu)
            MirrorWidget.recover_if_buried(m)
            m.position_manager.note_on_top_unverified.assert_called_once()
            m.position_manager.is_buried_behind_taskbar.assert_not_called()

    def test_burial_recovery_reseats_when_buried(self):
        m = MagicMock()
        m.dead = False
        m.isVisible.return_value = True
        m._obstructed = False
        m._main = SimpleNamespace(_is_context_menu_visible=False)
        m.position_manager.is_buried_behind_taskbar.return_value = True
        MirrorWidget.recover_if_buried(m)
        m.position_manager.reseat_above_taskbar.assert_called_once()


# --- 4. positioning through the overlay ----------------------------------------------------------

class TestOverlayPositioning:
    def test_secondary_mirror_ignores_the_main_drag_offset(self):
        from netspeedtray.core.position_manager import PositionCalculator

        class Screen:
            def geometry(self):
                return QRect(1920, 0, 1920, 1080)

            def availableGeometry(self):
                return QRect(1920, 0, 1920, 1032)

            def name(self):
                return "B"

        screen = Screen()
        tb = SimpleNamespace(
            hwnd=200, rect=(1920, 1032, 3840, 1080), dpi_scale=1.0, tasklist_rect=None,
            get_screen=lambda: screen, get_edge_position=lambda: constants.TaskbarEdge.BOTTOM,
            get_tray_rect=lambda: None, work_area=(1920, 0, 3840, 1032), height=48)
        main_cfg = {"tray_offset_x": -200, "secondary_clock_reserve_px": 75}
        cfg = _MirrorConfig(lambda: main_cfg, "B")
        pos = PositionCalculator().calculate_position(tb, (120, 40), cfg)
        d = constants.config.defaults
        assert pos.x == 3840 - 75 - 120 - d.DEFAULT_TRAY_OFFSET_X
        assert main_cfg["tray_offset_x"] == -200


# --- 5. burial backoff is per window ---------------------------------------------------------------

class TestBurialIndependence:
    def test_a_fight_on_one_window_never_escalates_another(self, monkeypatch):
        from netspeedtray.core import position_manager as pmod
        from netspeedtray.core.position_manager import PositionManager, WindowState

        def pm():
            widget = MagicMock()
            widget.winId.return_value = 0x10
            return PositionManager(WindowState(config={}, widget=widget))

        a, b = pm(), pm()
        monkeypatch.setattr(pmod.win32gui, "IsWindow", lambda h: True)
        monkeypatch.setattr(pmod.win32gui, "GetWindowLong", lambda h, i: pmod.win32con.WS_EX_TOPMOST)
        monkeypatch.setattr(pmod.win32gui, "GetWindow", lambda h, c: 0x99)
        monkeypatch.setattr(pmod.win32gui, "SetWindowPos", lambda *a: None)
        monkeypatch.setattr(pmod, "_window_band", lambda h: None)
        monkeypatch.setattr(a, "_docked_taskbar_hwnd", lambda h: 0x20)
        for _ in range(3):
            a._burial_fresh = False   # a fight: charged
            a._last_reseat_ts = 0.0   # out of the window, so each attempt acts
            assert a.reseat_above_taskbar() is True
        assert a._reseat_attempts == 3
        assert b._reseat_attempts == 0 and b._reseat_delay() == 0.0

    def test_tagged_position_manager_prefixes_its_log_lines(self, caplog):
        import logging
        from netspeedtray.core.position_manager import PositionManager, WindowState
        tagged = PositionManager(WindowState(config={}, widget=MagicMock()), log_tag="mirror DISPLAY-ab")
        plain = PositionManager(WindowState(config={}, widget=MagicMock()))
        with caplog.at_level(logging.INFO, logger="NetSpeedTray.Core.PositionManager"):
            tagged._logger.info("Widget recovered")
            plain._logger.info("Widget recovered")
        msgs = [r.getMessage() for r in caplog.records]
        assert msgs == ["[mirror DISPLAY-ab] Widget recovered", "Widget recovered"]


# --- real mirror windows (offscreen Qt) ------------------------------------------------------------

def _real_main(qt_owned):
    """Enough of a NetworkSpeedWidget for a real MirrorWidget: config, i18n, renderer, metrics."""
    from netspeedtray.utils.widget_renderer import WidgetRenderer
    from netspeedtray.utils.widget_paint import WidgetMetrics
    i18n = constants.i18n.I18nStrings()
    cfg = dict(constants.config.defaults.DEFAULT_CONFIG)
    main = qt_owned(QWidget())
    main.config = cfg
    main.i18n = i18n
    main.renderer = WidgetRenderer(cfg, i18n)
    main.ram_total = main.vram_total = None
    main.is_paused = False
    main._is_context_menu_visible = False
    main._current_cycle_mode = "network_only"
    main._identity_reserve_px = lambda fm: 0
    main._identity_reserve_signature = lambda: (None, None)
    main._layout_mode_for_edge = widget_main.NetworkSpeedWidget._layout_mode_for_edge
    main._build_metrics = lambda: WidgetMetrics(upload_mbps=1.5, download_mbps=42.0)
    main.tray_manager = MagicMock()
    main.input_handler = MagicMock()
    return main


def _secondary_tb():
    screen = QApplication.primaryScreen()
    g = screen.geometry()
    return SimpleNamespace(
        hwnd=0x200, screen_name=screen.name(), rect=(g.left(), g.bottom() - 47, g.right() + 1, g.bottom() + 1),
        dpi_scale=1.0, tasklist_rect=None, tray_hwnd=None, is_primary=False, height=48,
        screen_geometry=(g.left(), g.top(), g.width(), g.height()),
        work_area=(g.left(), g.top(), g.right() + 1, g.bottom() - 47),
        get_screen=lambda: screen, get_edge_position=lambda: constants.TaskbarEdge.BOTTOM,
        get_tray_rect=lambda: None)


@pytest.fixture
def real_mirror(qt_owned, monkeypatch):
    from netspeedtray.views.widget import layout as layout_mod
    from netspeedtray.utils import taskbar_utils
    tb = _secondary_tb()
    monkeypatch.setattr(layout_mod, "get_taskbar_info", lambda preferred_screen_name=None: tb)
    monkeypatch.setattr(taskbar_utils, "get_free_float_screen", lambda name: None)
    armed = []
    from netspeedtray.core import shutdown_channel
    monkeypatch.setattr(shutdown_channel, "arm_window", lambda hwnd: armed.append(hwnd) or True)
    main = _real_main(qt_owned)
    m = qt_owned(MirrorWidget(main, tb))
    return SimpleNamespace(main=main, mirror=m, tb=tb, armed=armed)


class TestRealMirror:
    def test_sized_and_placed_on_its_bar_left_of_the_clock_reserve(self, real_mirror):
        m, tb = real_mirror.mirror, real_mirror.tb
        assert m.width() > 0 and m.height() > 0
        g = tb.get_screen().geometry()
        reserve = constants.config.defaults.DEFAULT_SECONDARY_CLOCK_RESERVE_PX
        assert m.x() + m.width() <= g.right() + 1 - reserve
        assert m.config["preferred_monitor"] == tb.screen_name

    def test_renders_the_main_widgets_numbers(self, real_mirror):
        m = real_mirror.mirror
        m.show()
        img = m.grab().toImage()
        opaque = sum(1 for x in range(0, img.width(), 2) for y in range(0, img.height(), 2)
                     if img.pixelColor(x, y).alpha() > 40)
        assert opaque > 20      # text was painted, not just the 1-alpha hit-test layer

    def test_renderer_follows_the_main_renderer(self, real_mirror):
        main, m = real_mirror.main, real_mirror.mirror
        assert m.renderer is not main.renderer
        main.config["default_color"] = "#123456"
        main.renderer.update_config(main.config)
        m._sync_renderer()
        assert m.renderer.config.default_color.lower() == "#123456"

    def test_never_armed_as_the_shutdown_target(self, real_mirror):
        m = real_mirror.mirror
        m.show()
        m.hide()
        m.show()
        assert real_mirror.armed == []
        # No receiver for the shutdown message: no Python class above QWidget defines nativeEvent.
        assert not any("nativeEvent" in vars(c) for c in type(m).__mro__ if c is not QWidget
                       and c.__module__.startswith("netspeedtray"))
        from netspeedtray.core import shutdown_channel
        assert not shutdown_channel._has_target_prop(m._hwnd())

    def test_right_click_opens_the_main_menu_over_the_mirror(self, real_mirror):
        from PyQt6.QtGui import QContextMenuEvent
        m = real_mirror.mirror
        m.contextMenuEvent(QContextMenuEvent(QContextMenuEvent.Reason.Mouse, QPoint(1, 1)))
        real_mirror.main.tray_manager.show_context_menu.assert_called_once_with(anchor=m)

    def test_double_click_runs_the_configured_action(self, real_mirror):
        from PyQt6.QtCore import QPointF, Qt
        from PyQt6.QtGui import QMouseEvent
        main, m = real_mirror.main, real_mirror.mirror
        main.config["double_click_action"] = constants.config.defaults.CLICK_ACTION_SETTINGS
        ev = QMouseEvent(QMouseEvent.Type.MouseButtonDblClick, QPointF(1, 1), QPointF(1, 1),
                         Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        m.mouseDoubleClickEvent(ev)
        main.input_handler._execute_click_action.assert_called_once_with(
            constants.config.defaults.CLICK_ACTION_SETTINGS)


# --- 7. menu anchor ---------------------------------------------------------------------------------

class TestMenuAnchor:
    def _tray(self, qt_owned):
        from netspeedtray.core.tray_manager import TrayIconManager
        main = qt_owned(QWidget())
        main.setGeometry(100, 900, 120, 40)
        main.renderer = None
        tray = TrayIconManager.__new__(TrayIconManager)
        QObject.__init__(tray)
        tray.widget = main
        tray.logger = MagicMock()
        tray.context_menu = MagicMock()
        tray.context_menu.sizeHint.return_value = SimpleNamespace(width=lambda: 200, height=lambda: 300)
        return tray, main

    def test_no_anchor_is_unchanged(self, qt_owned):
        tray, main = self._tray(qt_owned)
        assert tray._calculate_menu_position() == tray._calculate_menu_position(anchor=main)

    def test_anchor_centres_on_the_mirror(self, qt_owned):
        tray, main = self._tray(qt_owned)
        other = qt_owned(QWidget())
        other.setGeometry(600, 900, 120, 40)
        other.renderer = None
        a = tray._calculate_menu_position()
        b = tray._calculate_menu_position(anchor=other)
        assert b.x() - a.x() == other.mapToGlobal(other.rect().center()).x() - main.mapToGlobal(main.rect().center()).x()
