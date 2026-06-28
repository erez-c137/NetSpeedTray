"""
TelemetryStrip — the Hardware tab's live band of temp/power/RAM/VRAM tiles. It reads readings the
stats pipeline already collects off the main widget's attributes, gates tiles by the enabled sources,
and gracefully omits a reading that's unavailable (no sensor, or the temp/power gate off).
"""
from types import SimpleNamespace

import pytest

from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.views.monitor.hardware.telemetry import TelemetryStrip


@pytest.fixture(scope="session")
def q_app():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _strip(cpu=True, gpu=True, temps=True, power=False, ram=False, vram=False):
    cfg = {"monitor_cpu_enabled": cpu, "monitor_gpu_enabled": gpu,
           "show_hardware_temps": temps, "show_hardware_power": power,
           "monitor_ram_enabled": ram, "monitor_vram_enabled": vram}
    return TelemetryStrip(cfg, I18nStrings("en_US"))


def _widget(**kw):
    base = dict(cpu_usage=45.0, gpu_usage=30.0, cpu_temp=62.0, gpu_temp=55.0,
                cpu_power=35.0, gpu_power=80.0, ram_used=12.4, ram_total=32.0,
                vram_used=3.1, vram_total=8.0)
    base.update(kw)
    return SimpleNamespace(**base)


def test_tiles_gated_by_enabled_sources(q_app):
    s = _strip(cpu=True, gpu=True)
    assert s._cpu and s._gpu and s._ram and s._vram      # RAM rides CPU, VRAM rides GPU (data reality)
    s2 = _strip(cpu=True, gpu=False)
    assert s2._cpu and s2._ram and s2._gpu is None and s2._vram is None
    s3 = _strip(cpu=False, gpu=False)
    assert all(t is None for t in (s3._cpu, s3._gpu, s3._ram, s3._vram))


def test_proc_text_respects_temp_power_gates(q_app):
    s = _strip(temps=True, power=False)
    assert s._proc_text(45.0, 62.0, 35.0) == "45%  ·  62°C"          # power gate off -> no watts
    s2 = _strip(temps=True, power=True)
    assert s2._proc_text(45.0, 62.0, 35.0) == "45%  ·  62°C  ·  35 W"
    s3 = _strip(temps=False, power=False)
    assert s3._proc_text(45.0, 62.0, 35.0) == "45%"                  # temps gate off -> just usage
    assert s._proc_text(45.0, None, 35.0) == "45%"                   # temp sensor absent -> omitted


def test_mem_text_formats(q_app):
    s = _strip()
    assert s._mem_text(12.4, 32.0) == "12.4 / 32.0 GB"
    assert s._mem_text(3.1, None) == "3.1 GB"             # VRAM total often absent (no nvidia-smi)
    assert s._mem_text(None, None) == "—"


def test_update_from_populates_tiles(q_app):
    s = _strip(temps=True, power=True)
    s.update_from(_widget())
    assert s._cpu._value.text() == "45%  ·  62°C  ·  35 W"
    assert s._gpu._value.text() == "30%  ·  55°C  ·  80 W"
    assert s._ram._value.text() == "12.4 / 32.0 GB"
    assert s._vram._value.text() == "3.1 / 8.0 GB"


def test_update_from_handles_missing_sensors(q_app):
    s = _strip(temps=True, power=True)
    s.update_from(_widget(cpu_temp=None, cpu_power=None, vram_total=None))
    assert s._cpu._value.text() == "45%"                 # both sensors absent
    assert s._vram._value.text() == "3.1 GB"             # no total, but used present -> shown
    assert s._vram.isVisibleTo(s)
    s.update_from(None)                                  # must not raise


def test_mem_tile_hidden_when_no_data(q_app):
    # Review fix: a memory tile with no reading (e.g. VRAM with no PDH counter) hides itself
    # rather than showing a permanent "—".
    s = _strip()
    s.update_from(_widget(vram_used=None))
    assert not s._vram.isVisibleTo(s)
    s.update_from(_widget(vram_used=2.0))                # data returns -> tile reappears
    assert s._vram.isVisibleTo(s)


def test_strip_empty_and_hidden_when_no_cpu_gpu(q_app):
    # Review fix: a RAM-only/VRAM-only config (CPU+GPU off) has no collectable telemetry, so the
    # strip is empty and collapses instead of leaving a blank band.
    from PyQt6.QtWidgets import QWidget, QVBoxLayout
    empty = _strip(cpu=False, gpu=False)
    full = _strip(cpu=True, gpu=False)
    assert empty.is_empty() and not full.is_empty()
    p = QWidget()
    lay = QVBoxLayout(p)
    lay.addWidget(empty)
    lay.addWidget(full)
    assert not empty.isVisibleTo(p)         # explicitly collapsed, not just "never shown"
    assert full.isVisibleTo(p)              # a non-empty strip stays visible
