"""
MonitorWindow shell — the unified Monitor (Overview / Network / Hardware): construction, lazy tab
building, and Hardware-tab config gating. Plus the import firewall (the whole point of the lazy
design): the shell must not pull matplotlib at module scope, or a glance at Overview would regress
the idle-RAM win.
"""
import os
import subprocess
import sys
from unittest.mock import MagicMock

import pytest

from netspeedtray import constants
from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.views.monitor.window import MonitorWindow


@pytest.fixture(scope="session")
def q_app():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _main_widget():
    m = MagicMock()
    m.config = {}
    m.config_manager = MagicMock()
    return m


def _cfg(**over):
    c = dict(constants.config.defaults.DEFAULT_CONFIG)
    c.update(over)
    return c


def test_constructs_and_lazily_builds_tabs(q_app):
    w = MonitorWindow(_main_widget(), _cfg(monitor_cpu_enabled=True), I18nStrings("en_US"))
    # Overview is the default tab and is built eagerly on open.
    assert w._descriptors[0].tab_id == "overview"
    assert w._descriptors[0].page is not None
    # Network starts as a placeholder; activating it builds the real page.
    assert w._descriptors[1].page is None
    w._on_tab_changed(1)
    assert w._descriptors[1].page is not None


def test_hardware_tab_visible_only_with_monitoring(q_app):
    off = MonitorWindow(_main_widget(), _cfg(monitor_cpu_enabled=False, monitor_gpu_enabled=False,
                                             monitor_ram_enabled=False, monitor_vram_enabled=False),
                        I18nStrings("en_US"))
    assert off._tab_bar._buttons["hardware"].isHidden()

    on = MonitorWindow(_main_widget(), _cfg(monitor_gpu_enabled=True), I18nStrings("en_US"))
    assert not on._tab_bar._buttons["hardware"].isHidden()


def test_import_firewall_no_matplotlib_at_module_scope():
    """Importing the Monitor shell modules must NOT pull matplotlib (the lazy-loading contract)."""
    src = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))  # .../src
    code = (
        "import sys\n"
        "import netspeedtray.views.monitor.window\n"
        "import netspeedtray.views.monitor.tab_bar\n"
        "import netspeedtray.views.monitor.lazy\n"
        "leaked = [m for m in sys.modules if m == 'matplotlib' or m.startswith('matplotlib.')]\n"
        "assert not leaked, 'matplotlib leaked into the Monitor shell: %r' % leaked\n"
        "print('FIREWALL_OK')\n"
    )
    env = dict(os.environ, PYTHONPATH=src, QT_QPA_PLATFORM="offscreen")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    assert r.returncode == 0, (r.stdout + "\n" + r.stderr)
    assert "FIREWALL_OK" in r.stdout
