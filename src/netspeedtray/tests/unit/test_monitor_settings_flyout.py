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


def test_gear_persistent_enabled_only_on_hardware(q_app):
    win = _window()
    # The gear is PERSISTENT (always visible - hiding it made it flicker on every pivot); it's only
    # *enabled* on Hardware, where it has an effect, and dimmed/disabled elsewhere.
    win._on_tab_changed(1)                       # Network - gear has no effect here
    assert not win._gear.isHidden()
    assert not win._gear.isEnabled()
    win._on_tab_changed(2)                       # Hardware - gear is relevant
    assert not win._gear.isHidden()
    assert win._gear.isEnabled()


def test_flyout_reused_not_rebuilt(q_app):
    win = _window()
    win._open_settings_flyout()
    first = win._settings_flyout
    assert first is not None
    first.hide()
    win._open_settings_flyout()
    assert win._settings_flyout is first         # one cached instance, not a per-click leak
    win._settings_flyout.hide()


# --- 6.2b: graph mode (combined/separate/toggle) + smoothing + axis ---------------

def test_mode_segmented_writes_config_and_emits(q_app):
    config = {}
    f, mw = _flyout(config)
    seen = []
    f.changed.connect(lambda: seen.append(1))
    f._mode.setValue("separate")
    assert config["monitor_hw_graph_mode"] == "separate"
    assert seen
    f._mode.setValue("toggle")
    assert config["monitor_hw_graph_mode"] == "toggle"


def test_smoothing_and_axis_controls_write_config(q_app):
    config = {}
    f, _ = _flyout(config)
    f._smooth.toggled.emit(True)
    assert config["monitor_graph_smoothing"] is True
    f._axis.setValue(False)                                  # Auto
    assert config["monitor_graph_fixed_axis"] is False
    f._axis.setValue(True)                                   # back to fixed 0-100%
    assert config["monitor_graph_fixed_axis"] is True


def test_refresh_resyncs_mode_smoothing_axis(q_app):
    config = {"monitor_hw_graph_mode": "combined", "monitor_graph_smoothing": False,
              "monitor_graph_fixed_axis": True}
    f, _ = _flyout(config)
    # Another surface (the 6.2c Settings page) changes the config behind the cached flyout.
    config.update(monitor_hw_graph_mode="separate", monitor_graph_smoothing=True,
                  monitor_graph_fixed_axis=False)
    f.refresh()
    assert f._mode.value() == "separate"
    assert f._smooth.isChecked() is True
    assert f._axis.value() is False
    # Re-sync must not echo back as a write storm - values still match config (no flip-flop).
    assert config["monitor_hw_graph_mode"] == "separate"


def _hardware_tab(config):
    from unittest.mock import MagicMock
    from netspeedtray.views.monitor.hardware.tab import HardwareTab
    host = MagicMock()
    host._current_stat = None
    return HardwareTab(host, MagicMock(), config, I18nStrings("en_US")), host


def test_hardware_tab_resolves_stat_per_mode(q_app):
    from netspeedtray import constants
    base = dict(constants.config.defaults.DEFAULT_CONFIG)

    tab, _ = _hardware_tab({**base, "monitor_hw_graph_mode": "combined"})
    assert tab._resolve_stat() == "hwcombined"

    tab, _ = _hardware_tab({**base, "monitor_hw_graph_mode": "separate"})
    assert tab._resolve_stat() == "hwseparate"

    tab, _ = _hardware_tab({**base, "monitor_hw_graph_mode": "toggle"})
    assert tab._resolve_stat() == "cpu"            # default toggle role
    tab._cpu_gpu.setValue("gpu")
    assert tab._resolve_stat() == "gpu"


def test_hardware_tab_toggle_switch_visibility_and_set_stat(q_app):
    from netspeedtray import constants
    base = dict(constants.config.defaults.DEFAULT_CONFIG)

    # combined: the CPU|GPU switch is hidden; on settings-change we re-render in place (no set_stat).
    tab, host = _hardware_tab({**base, "monitor_hw_graph_mode": "combined"})
    host._current_stat = "hwcombined"
    tab.on_settings_changed()
    assert tab._cpu_gpu.isHidden()
    host.set_stat.assert_not_called()
    host.update_graph.assert_called()

    # toggle: the switch is shown; switching to GPU drives set_stat("gpu").
    tab, host = _hardware_tab({**base, "monitor_hw_graph_mode": "toggle"})
    host._current_stat = "cpu"
    tab.on_settings_changed()
    assert not tab._cpu_gpu.isHidden()
    tab._cpu_gpu.setValue("gpu")
    host.set_stat.assert_called_with("gpu")


def test_flyout_width_fits_segmented_labels_all_locales(q_app):
    """Regression for the review finding: the Layout + Y-axis Win11Segmented controls hard-clip
    (no elide), so the panel must be wide enough for the longest localized labels after adjustSize()
    (what _open_settings_flyout does before showing). Guards against silent truncation in any locale."""
    from netspeedtray import constants
    base = dict(constants.config.defaults.DEFAULT_CONFIG)
    margins = 16 + 16  # root contentsMargins L + R
    for loc in ("en_US", "de_DE", "es_ES", "fr_FR", "nl_NL", "pl_PL", "ru_RU", "ko_KR", "sl_SI", "ja_JP"):
        f = MonitorSettingsFlyout(MagicMock(), dict(base), I18nStrings(loc))
        f.adjustSize()
        content = f.width() - margins
        assert content + 1 >= f._mode.sizeHint().width(), f"{loc}: Layout segmented clipped"
        assert content + 1 >= f._axis.sizeHint().width(), f"{loc}: Y-axis segmented clipped"
