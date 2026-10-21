"""
2.2 release-candidate checkup - Settings dialog fixes (C1, S1-S5, S8).

Each test reproduces a reviewer harness finding (rc-check-settings/harness/{config,settings}) and
fails without its fix.
"""
from unittest.mock import MagicMock

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QMessageBox

from netspeedtray import constants
from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.utils.config import ConfigManager
from netspeedtray.views.settings.dialog import SettingsDialog

DEFAULTS = constants.config.defaults.DEFAULT_CONFIG
# Keys a Save legitimately rewrites (window position, the startup mirror).
_VOLATILE = {"settings_window_pos", "start_with_windows"}


class _Main:
    """The parts of NetworkSpeedWidget the dialog touches, with ConfigController's merge semantics."""

    def __init__(self, cfg):
        self.config = dict(cfg)
        self.config_manager = MagicMock()
        self.saved = []
        self.calls = []
        self.metrics_endpoint = None

    def handle_settings_changed(self, updated, save_to_disk=True):
        self.calls.append((dict(updated), save_to_disk))
        self.config.update(updated)
        if save_to_disk:
            self.saved.append(dict(self.config))

    def toggle_startup(self, on):
        pass

    def update(self):
        pass

    def width(self):
        return 200


@pytest.fixture(autouse=True)
def _quiet_boxes(monkeypatch):
    for n in ("critical", "information", "warning"):
        monkeypatch.setattr(QMessageBox, n, lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)


def _make(qtbot, cfg=None, ifaces=("Ethernet", "Wi-Fi")):
    cfg = dict(DEFAULTS if cfg is None else cfg)
    main = _Main(cfg)
    dlg = SettingsDialog(main_widget=main, config=cfg, version="2.2.0", i18n=I18nStrings("en_US"),
                         available_interfaces=list(ifaces), is_startup_enabled=False)
    dlg.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    dlg.settings_changed.connect(lambda c: main.handle_settings_changed(c, save_to_disk=False))
    qtbot.addWidget(dlg)
    return dlg, main


def _flush(dlg):
    if dlg._update_timer.isActive():
        dlg._update_timer.stop()
    dlg._emit_settings_changed_throttled()


def _changed(before, after):
    return sorted(k for k in before if k not in _VOLATILE and before.get(k) != after.get(k))


# --- C1: a decimal graph_opacity must not blank out the rest of the dialog --------------------------

@pytest.mark.parametrize("opacity", [55.0, 10.5])
def test_float_graph_opacity_loads_and_a_noop_save_changes_nothing(qtbot, opacity):
    cfg = dict(DEFAULTS, graph_opacity=opacity, keep_data=365, font_family="Consolas",
               record_hardware_history=True)
    dlg, _ = _make(qtbot, cfg)
    assert dlg._failed_pages == set()
    got = dlg.get_settings()
    others = [k for k in _changed(cfg, got) if k != "graph_opacity"]
    assert others == [], f"a no-op Save would rewrite {others}"
    assert got["graph_opacity"] == round(opacity)


def test_a_page_that_fails_to_load_never_writes_its_keys(qtbot, monkeypatch):
    """Whatever the next load bug is: the failed page's keys keep their loaded values on Save,
    and the pages after it still load."""
    from netspeedtray.views.settings.pages.hardware import HardwarePage

    def boom(self, config):
        raise TypeError("simulated load failure")
    monkeypatch.setattr(HardwarePage, "load_settings", boom)
    cfg = dict(DEFAULTS, monitor_cpu_enabled=True, cpu_load_high_threshold=91,
               keep_data=365, metrics_endpoint_port=9555)
    dlg, main = _make(qtbot, cfg)
    assert dlg._failed_pages == {"hardware"}
    got = dlg.get_settings()
    assert _changed(cfg, got) == []
    # The page after Hardware (Advanced) still loaded its real values.
    assert dlg.advanced_page.get_settings()["metrics_endpoint_port"] == 9555
    dlg.save_button.click()
    assert main.saved and main.saved[-1]["cpu_load_high_threshold"] == 91
    assert main.saved[-1]["monitor_cpu_enabled"] is True


def test_config_validation_stores_graph_opacity_as_int(tmp_path):
    cm = ConfigManager(tmp_path / "c.json")
    v = cm._validate_config({"graph_opacity": 55.6})
    assert v["graph_opacity"] == 56 and isinstance(v["graph_opacity"], int)
    assert cm._validate_config({"graph_opacity": 40})["graph_opacity"] == 40
