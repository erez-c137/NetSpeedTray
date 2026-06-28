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


def test_settings_changed_rerenders_active_graph(q_app):
    from netspeedtray import constants
    from netspeedtray.views.monitor.window import MonitorWindow
    mw = MagicMock(); mw.config = {}
    win = MonitorWindow(mw, dict(constants.config.defaults.DEFAULT_CONFIG), I18nStrings("en_US"))
    win._graph_host = MagicMock()
    win._on_settings_changed()
    win._graph_host.update_graph.assert_called_once()            # live re-render path
    # and it's a no-op (no crash) when no chart tab has built the host yet
    win._graph_host = None
    win._on_settings_changed()
