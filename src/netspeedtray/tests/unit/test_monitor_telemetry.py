"""
TelemetryStrip - the Hardware tab's live band of temp/power/RAM/VRAM tiles. The Monitor forces
hardware collection while it's open, so the strip always builds all four tiles and shows whatever was
collected: CPU/GPU always have a usage%; temp/power append when present; the memory tiles hide
themselves when their reading is unavailable.
"""
from types import SimpleNamespace

import pytest

from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.views.monitor.hardware.telemetry import TelemetryStrip


@pytest.fixture(scope="session")
def q_app():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _strip():
    return TelemetryStrip({}, I18nStrings("en_US"))


def _widget(**kw):
    base = dict(cpu_usage=45.0, gpu_usage=30.0, gpu_present=True, cpu_temp=62.0, gpu_temp=55.0,
                cpu_power=35.0, gpu_power=80.0, ram_used=12.4, ram_total=32.0,
                vram_used=3.1, vram_total=8.0)
    base.update(kw)
    return SimpleNamespace(**base)


def test_all_four_tiles_created(q_app):
    s = _strip()
    assert s._cpu and s._gpu and s._ram and s._vram   # always - collection is forced while open


def test_proc_text_shows_only_present_metrics(q_app):
    s = _strip()
    assert s._proc_text(45.0, 62.0, 35.0) == "45%  ·  62°C  ·  35 W"   # all present
    assert s._proc_text(45.0, 62.0, None) == "45%  ·  62°C"            # power not collected
    assert s._proc_text(45.0, None, None) == "45%"                     # no sensors -> just usage


def test_mem_text_formats(q_app):
    s = _strip()
    assert s._mem_text(12.4, 32.0) == "12.4 / 32.0 GB"
    assert s._mem_text(3.1, None) == "3.1 GB"             # VRAM total often absent (no nvidia-smi)
    assert s._mem_text(None, None) == "-"


def test_update_from_populates_tiles(q_app):
    s = _strip()
    s.update_from(_widget())
    assert s._cpu._value.text() == "45%  ·  62°C  ·  35 W"
    assert s._gpu._value.text() == "30%  ·  55°C  ·  80 W"
    assert s._ram._value.text() == "12.4 / 32.0 GB"
    assert s._vram._value.text() == "3.1 / 8.0 GB"


def test_mem_tile_hidden_when_no_data(q_app):
    # A memory tile with no reading (e.g. VRAM with no PDH counter) hides itself rather than "-".
    s = _strip()
    s.update_from(_widget(vram_used=None))
    assert not s._vram.isVisibleTo(s)
    s.update_from(_widget(vram_used=2.0))                # data returns -> tile reappears
    assert s._vram.isVisibleTo(s)


def test_update_from_handles_missing_sensors(q_app):
    s = _strip()
    s.update_from(_widget(cpu_temp=None, cpu_power=None, vram_total=None))
    assert s._cpu._value.text() == "45%"                 # both CPU sensors absent
    assert s._vram._value.text() == "3.1 GB"             # no total, but used present -> shown
    s.update_from(None)                                  # must not raise


def test_gpu_tile_hidden_on_no_gpu(q_app):
    # Review fix: a confirmed no-GPU box hides the GPU tile rather than showing a permanent 0%.
    s = _strip()
    s.update_from(_widget(gpu_present=False))
    assert not s._gpu.isVisibleTo(s)
    s.update_from(_widget(gpu_present=True))             # a GPU appears (or default) -> tile returns
    assert s._gpu.isVisibleTo(s)
