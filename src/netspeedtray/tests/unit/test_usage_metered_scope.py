"""
F5 (2.2 RC checkup): the hover card's "Today" / "This Period" rows and the Overview usage tile summed
EVERY adapter's recorded history, while the data-cap row beneath them (the odometer) counts only the
adapters the interface mode meters. A VPN / WSL / Hyper-V adapter's bytes also ride the real NIC, so
with #401 labelling both as the billing period they disagreed by up to 2x. Those reads are now scoped
to the odometer's adapters (``WidgetState.metered_scope`` -> ``HistoryReader.metered_volume``).
"""
import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from netspeedtray.core.db import schema_v8 as S
from netspeedtray.core.db import writer_v8 as W
from netspeedtray.core.db.history_reader import MeteredScope, V8HistoryReader, VolumeResult
from netspeedtray.core.widget_state import WidgetState
from netspeedtray.views.widget.main import NetworkSpeedWidget

NOW = int(datetime(2026, 10, 21, 12, 0).timestamp())
PHYSICAL = {"Ethernet", "Wi-Fi"}


def _db(path: Path, physical=PHYSICAL) -> V8HistoryReader:
    c = sqlite3.connect(path)
    S.create_v8_schema(c)
    c.execute("INSERT INTO metadata (key, value) VALUES ('db_version', '8')")
    c.commit()
    w = W.WriterV8(c, version="t", poll_ms=1000, is_physical=lambda n: n in physical)
    # One hour of traffic: the VPN adapter carries the same bytes the Ethernet NIC does (tunnelled).
    for i in range(60):
        ts = NOW - 3600 + i * 60
        w.add_net_sample(W.NetSample(ts=ts, dt_ms=1000, byte_deltas={
            "Ethernet": (1_000, 10_000), "ProtonVPN": (1_000, 10_000), "Wi-Fi": (0, 500)}))
    w.flush(NOW)
    c.close()
    return V8HistoryReader(lambda: sqlite3.connect(path), lambda: path, clock=lambda: NOW)


def _win():
    return datetime.fromtimestamp(NOW - 7200), datetime.fromtimestamp(NOW)


def test_physical_scope_leaves_out_the_vpn_adapter(tmp_path):
    r = _db(tmp_path / "h.db")
    every = r.volume(*_win(), None, 1.0)
    metered = r.metered_volume(*_win(), MeteredScope(physical_only=True), 1.0)
    assert every.down_bytes == 60 * (10_000 + 10_000 + 500)          # the old, double-counted figure
    assert metered.down_bytes == 60 * (10_000 + 500)                  # Ethernet + Wi-Fi only
    assert metered.up_bytes == 60 * 1_000


def test_selected_scope_counts_exactly_the_selected_adapters(tmp_path):
    r = _db(tmp_path / "h.db")
    assert r.metered_volume(*_win(), MeteredScope(names=("ProtonVPN",)), 1.0).down_bytes == 60 * 10_000
    assert r.metered_volume(*_win(), MeteredScope(names=()), 1.0).down_bytes == 0.0
    assert r.metered_volume(*_win(), None, 1.0).down_bytes == r.volume(*_win(), None, 1.0).down_bytes


def test_no_physical_adapter_at_all_counts_every_adapter(tmp_path):
    """A VM guest NIC the OS does not flag as hardware: the odometer falls back to every non-pseudo
    adapter, so a physical-only history read must not read zero forever."""
    r = _db(tmp_path / "h.db", physical=set())
    assert r.metered_volume(*_win(), MeteredScope(physical_only=True), 1.0).down_bytes == \
        r.volume(*_win(), None, 1.0).down_bytes


class _Classifier:
    """The controller's classifier, faked: kind() honours exclusions; fallback_eligible() never a VPN."""
    PHYS = {"Ethernet", "Wi-Fi"}

    def kind(self, name, exclusions=()):
        from netspeedtray import constants
        iface = constants.network.interface
        if any(x.lower() in name.lower() for x in exclusions):
            return iface.KIND_PSEUDO
        return iface.KIND_PHYSICAL if name in self.PHYS else iface.KIND_VIRTUAL

    def fallback_eligible(self, name, exclusions=()):
        return "vpn" not in name.lower() and not any(x.lower() in name.lower() for x in exclusions)


NAMES = ["Ethernet", "Wi-Fi", "ProtonVPN", "vEthernet (WSL)"]


@pytest.mark.parametrize("config, expected", [
    ({"interface_mode": "auto", "excluded_interfaces": []}, MeteredScope(names=("Ethernet", "Wi-Fi"))),
    ({"interface_mode": "all_physical", "excluded_interfaces": []}, MeteredScope(names=("Ethernet", "Wi-Fi"))),
    ({"interface_mode": "all_physical", "excluded_interfaces": ["wi-fi"]}, MeteredScope(names=("Ethernet",))),
    ({"interface_mode": "selected", "selected_interfaces": ["Wi-Fi"]}, MeteredScope(names=("Wi-Fi",))),
    ({"interface_mode": "all_virtual"}, None),
    ({"interface_mode": "virtual"}, None),
])
def test_scope_follows_the_controllers_selection(config, expected):
    """Same classifier, same exclusions as the controller (S2: history's stored is_physical ignored
    excluded_interfaces)."""
    assert WidgetState.metered_scope(config, NAMES, _Classifier()) == expected


def test_no_physical_adapter_falls_back_like_the_controller():
    """The controller's fallback is fallback_eligible (never a known VPN / tunnel), not every adapter."""
    scope = WidgetState.metered_scope({"interface_mode": "all_physical", "excluded_interfaces": []},
                                      ["ProtonVPN", "Ethernet 2 (VM)"], type("C", (_Classifier,), {"PHYS": set()})())
    assert scope == MeteredScope(names=("Ethernet 2 (VM)",))


def test_only_auto_mode_is_inexact():
    assert not WidgetState.metered_scope_is_exact({"interface_mode": "auto"})
    assert not WidgetState.metered_scope_is_exact({})
    for mode in ("all_physical", "selected", "all_virtual"):
        assert WidgetState.metered_scope_is_exact({"interface_mode": mode})


class _WS:
    """History with a VPN double count: every-adapter reads 2x what the metered adapters moved."""
    def get_usage_period_key(self):
        return "2026-10-15"

    def get_history_volume_for_period(self, start, end, interface_name=None):
        return VolumeResult(2e9, 20e9, 2e9, 20e9, False)

    def get_metered_volume_for_period(self, start, end):
        return VolumeResult(1e9, 10e9, 1e9, 10e9, False)

    def get_usage_this_period(self):
        return (0.5e9, 9e9)                    # the odometer: what the cap row shows


class _W:
    _HOVER_USAGE_TTL_SEC = NetworkSpeedWidget._HOVER_USAGE_TTL_SEC
    _hover_usage_totals = NetworkSpeedWidget._hover_usage_totals
    _usage_billing_period_start = NetworkSpeedWidget._usage_billing_period_start
    _usage_cap_set = NetworkSpeedWidget._usage_cap_set

    def __init__(self):
        self.config = {"data_cap_enabled": True, "data_cap_gb": 100, "data_cap_reset_day": 15}
        self.widget_state = _WS()
        self.logger = logging.getLogger("test")
        self._hover_usage_cache = None
        self._hover_usage_cache_ts = 0.0


def test_hover_usage_rows_count_what_the_cap_counts():
    """The rows the hover card and the Overview usage tile show (both read _hover_usage_totals)."""
    today, period = _W()._hover_usage_totals()
    assert period == (0.5e9, 9e9)              # with a cap set: exactly the cap row's count
    # Today is history (1 / 10 GB here); with a cap set it never reads above the period (L-a).
    assert today == (0.5e9, 9e9)


def test_today_below_the_period_is_left_alone():
    w = _W()
    w.widget_state.get_usage_this_period = lambda: (5e9, 50e9)
    today, _period = w._hover_usage_totals()
    assert today == (1e9, 10e9)


def test_widget_state_metered_volume_reads_through_the_scope(tmp_path):
    r = _db(tmp_path / "h.db")
    ws = SimpleNamespace(db_worker=object(), config={"interface_mode": "auto", "excluded_interfaces": []},
                         history_reader=r)
    v = WidgetState.get_metered_volume_for_period(ws, *_win())
    assert v.down_bytes == 60 * (10_000 + 500)


def test_strict_read_returns_none_on_a_failed_read_not_zeros(tmp_path):
    """S1: the v8 reader turns every error into a zero VolumeResult; a re-key read that as "nothing
    moved" and wrote 0 GB. The strict read says None instead."""
    r = _db(tmp_path / "h.db")
    ok = r.metered_volume_strict(*_win(), MeteredScope(names=("Ethernet",)))
    assert ok is not None and ok.down_bytes == 60 * 10_000
    broken = V8HistoryReader(lambda: sqlite3.connect(tmp_path / "missing" / "x.db"), lambda: tmp_path, clock=lambda: NOW)
    assert broken.metered_volume(*_win(), MeteredScope(names=("Ethernet",)), 1.0).down_bytes == 0.0
    assert broken.metered_volume_strict(*_win(), MeteredScope(names=("Ethernet",))) is None
    assert sorted(r.interface_names()) == ["Ethernet", "ProtonVPN", "Wi-Fi"]
