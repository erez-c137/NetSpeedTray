"""
MonitorSettingsFlyout (the gear panel) + the live-apply wiring: each control writes its config key,
persists via the widget's config_controller, and emits `changed` so the Monitor re-renders. The
"Auto" reset stores None (vendor-auto) while showing the vendor hue.
"""
from unittest.mock import MagicMock

import pytest

from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.utils import hardware_vendors as hv
from netspeedtray.views.monitor.settings_flyout import MonitorSettingsFlyout


@pytest.fixture(scope="session")
def q_app():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _flyout(config):
    mw = MagicMock()
    return MonitorSettingsFlyout(mw, config, I18nStrings("en_US")), mw


def test_color_change_writes_config_persists_and_emits(q_app):
    config = {"monitor_graph_legend": True}
    f, mw = _flyout(config)
    seen = []
    f.changed.connect(lambda: seen.append(1))
    f._cpu.setColor("#123456")
    assert config["monitor_cpu_graph_color"] == "#123456"
    mw.config_controller.update_config.assert_called()           # persisted
    assert seen                                                  # Monitor told to re-render


def test_auto_stores_none_but_shows_vendor_hue(q_app):
    config = {}
    f, mw = _flyout(config)
    f._cpu.setColor("#123456")
    f._reset("monitor_cpu_graph_color", "cpu", f._cpu)
    assert config["monitor_cpu_graph_color"] is None            # None == auto
    assert f._cpu.color().upper() == hv.default_color("cpu", f._dark).upper()  # field shows vendor hue


def test_legend_toggle_writes_bool(q_app):
    config = {"monitor_graph_legend": True}
    f, _ = _flyout(config)
    f._legend.toggled.emit(False)
    assert config["monitor_graph_legend"] is False


def test_refresh_rereads_config_into_swatches(q_app):
    config = {"monitor_cpu_graph_color": "#111111", "dark_mode": True}
    f, _ = _flyout(config)
    assert f._cpu.color().upper() == "#111111"
    config["monitor_cpu_graph_color"] = "#222222"   # changed elsewhere while the cached flyout lived
    f.refresh()
    assert f._cpu.color().upper() == "#222222"


def _window():
    from netspeedtray import constants
    from netspeedtray.views.monitor.window import MonitorWindow
    mw = MagicMock(); mw.config = {}
    cfg = dict(constants.config.defaults.DEFAULT_CONFIG); cfg["monitor_cpu_enabled"] = True
    return MonitorWindow(mw, cfg, I18nStrings("en_US"))


def test_settings_changed_rerenders_active_graph(q_app):
    win = _window()
    win._graph_host = MagicMock()
    win._on_settings_changed()
    win._graph_host.update_graph.assert_called_once()            # live re-render path
    win._graph_host = None
    win._on_settings_changed()                                   # no-op (no crash) when no host yet


def test_gear_only_on_hardware_tab(q_app):
    win = _window()
    win._on_tab_changed(1)                       # Network — the gear has no effect here in 6.2a
    assert win._gear.isHidden()
    win._on_tab_changed(2)                       # Hardware — gear is relevant
    assert not win._gear.isHidden()


def test_flyout_reused_not_rebuilt(q_app):
    win = _window()
    win._open_settings_flyout()
    first = win._settings_flyout
    assert first is not None
    first.hide()
    win._open_settings_flyout()
    assert win._settings_flyout is first         # one cached instance, not a per-click leak
    win._settings_flyout.hide()
