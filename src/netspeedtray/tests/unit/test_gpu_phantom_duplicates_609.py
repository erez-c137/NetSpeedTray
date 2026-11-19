"""
#609 (2.2.1-beta.2): a laptop with ONE Intel Arc B390 (Core Ultra X7 358H) listed it three times in
the hover card - "#1 35 %", then "#2" and "#3" at 0 % with no memory. DXGI enumerated three adapters
with the same maker and Description but different LUIDs; only one of those LUIDs has PDH
\\GPU Adapter Memory instances.

Phantom duplicates are dropped only against PDH evidence: two identical real cards each have their own
PDH instances and stay two (#1 / #2); with no PDH data nothing is dropped; a hybrid's iGPU + dGPU
(different makers) are never grouped.
"""
import types
from unittest.mock import MagicMock, patch

import pytest

from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.core import gpu_adapters
from netspeedtray.core.gpu_adapters import AdapterInfo
from netspeedtray.core.monitor_thread import StatsMonitorThread

EN = I18nStrings("en_US")
MIB = 1024.0 * 1024.0
GIB = 1024 ** 3

ARC_NAME = "Intel(R) Arc(TM) B390 GPU"
ARC_LUIDS = (0xA001, 0xA002, 0xA003)
ARC = [AdapterInfo(ARC_NAME, 0x8086, (0, low), 128 * 2 ** 20, int(15.8 * GIB), False,
                   outputs=1 if low == 0xA001 else 0, device_id=0xB0A0)
       for low in ARC_LUIDS]
BRD = AdapterInfo("Microsoft Basic Render Driver", 0x1414, (0, 0xDD11), 0, int(15 * GIB), True)
RTX_A = AdapterInfo("NVIDIA GeForce RTX 4090", 0x10DE, (0, 0xC001), 24 * GIB, int(16 * GIB), False,
                    outputs=1, device_id=0x2684)
RTX_B = RTX_A._replace(luid=(0, 0xC002), outputs=0)
INTEL_IGPU = AdapterInfo("Intel(R) UHD Graphics", 0x8086, (0, 0xD9C1), 128 * 2 ** 20, int(8 * GIB), False,
                         outputs=1)
RTX_MOBILE = AdapterInfo("NVIDIA GeForce RTX 4060 Laptop GPU", 0x10DE, (0, 0xAB01), 8 * GIB, int(8 * GIB),
                         False)


def _mem(luid_low: int) -> str:
    return f"luid_0x00000000_0x{luid_low:08X}_phys_0"


def _eng(luid_low: int, pid: int) -> str:
    return f"pid_{pid}_luid_0x00000000_0x{luid_low:08X}_phys_0_eng_0_engtype_3D"


@pytest.fixture
def thread(q_app, monkeypatch):
    monkeypatch.setattr(gpu_adapters, "enumerate_adapters", lambda: None)
    t = StatsMonitorThread(interval=0.1)
    t.logger = MagicMock()
    t._gpu_query = 123
    t._gpu_util_counter, t._gpu_vram_counter, t._gpu_shared_counter = 1, 2, 3
    t._nvidia_smi_path = None
    t._wmi_ohm = False
    t._lhm_http = MagicMock()
    t._lhm_http.get_snapshot.return_value = None
    t._init_power_query = MagicMock()
    t._power_query = None
    t.config = dict(t.config or {}, gpu_adapter="")
    return t


def _poll(t, util, dedicated, shared):
    with patch("win32pdh.CollectQueryData"), \
         patch("win32pdh.GetFormattedCounterArray", side_effect=[util, dedicated, shared]), \
         patch("netspeedtray.core.monitor_thread.psutil.virtual_memory",
               return_value=types.SimpleNamespace(total=32 * GIB)), \
         patch.object(StatsMonitorThread, "_init_ohm_wmi"):
        return t._poll_gpu_hybrid(include_temp=True, include_power=True)


def _bober_poll(t):
    """The #609 machine: PDH names only the first Arc LUID (and Basic Render)."""
    return _poll(t, {_eng(0xA001, 10): 35.0},
                 {_mem(0xA001): 0.0, _mem(0xDD11): 0.0},
                 {_mem(0xA001): 3.0 * GIB, _mem(0xDD11): 0.0})


def _phantom_logs(t):
    return [c for c in t.logger.info.call_args_list if "duplicate adapter" in str(c.args[0])]


# --- the hover card / Automatic / picker (monitor thread) ----------------------------------------

def test_three_same_identity_intel_adapters_with_pdh_for_one_are_one_gpu(thread):
    thread._gpu_adapters = [*ARC, BRD]
    r = _bober_poll(thread)
    real = gpu_adapters.hardware_adapters(thread._gpu_adapters)
    assert [a.luid for a in real] == [(0, 0xA001)]
    assert thread._gpu_cards(True, True) == ()          # one GPU: no per-GPU list at all
    assert r.vram_is_shared and r.vram_used == pytest.approx(3.0 * 1024)
    assert r.util == pytest.approx(35.0)
    logs = _phantom_logs(thread)
    assert len(logs) == 1 and logs[0].args[1:] == (2, "0x8086")
    assert ARC_NAME not in str(logs[0])                  # count and maker only, no names


def test_phantoms_beside_a_real_dgpu_leave_two_unnumbered_rows(thread):
    thread._gpu_adapters = [*ARC, RTX_A, BRD]
    _poll(thread, {_eng(0xA001, 10): 35.0, _eng(0xC001, 11): 5.0},
          {_mem(0xA001): 0.0, _mem(0xC001): 2048 * MIB},
          {_mem(0xA001): 3.0 * GIB, _mem(0xC001): 0.0})
    cards = thread._gpu_cards(True, True)
    assert [c.name for c in cards] == ["NVIDIA GeForce RTX 4090", "Intel Arc B390 GPU"]   # no "#1"


def test_the_picked_arc_is_the_one_pdh_reads(thread):
    """The DXGI-first Arc is the phantom here: the picker's name match (first wins) must land on the
    one PDH reads, not on an adapter with no load and no memory."""
    thread._gpu_adapters = [*ARC, RTX_A, BRD]
    thread.config = dict(thread.config, gpu_adapter=ARC_NAME)
    r = _poll(thread, {_eng(0xA002, 10): 35.0, _eng(0xC001, 11): 5.0},
              {_mem(0xA002): 0.0, _mem(0xC001): 2048 * MIB},
              {_mem(0xA002): 3.0 * GIB, _mem(0xC001): 0.0})
    assert r.util == pytest.approx(35.0) and r.vram_is_shared
    assert r.vram_used == pytest.approx(3.0 * 1024)


def test_two_identical_discrete_cards_with_their_own_pdh_stay_two(thread):
    thread._gpu_adapters = [RTX_A, RTX_B, BRD]
    _poll(thread, {_eng(0xC001, 10): 40.0, _eng(0xC002, 11): 7.0},
          {_mem(0xC001): 4096 * MIB, _mem(0xC002): 1024 * MIB, _mem(0xDD11): 0.0},
          {_mem(0xC001): 0.0, _mem(0xC002): 0.0, _mem(0xDD11): 0.0})
    cards = thread._gpu_cards(True, True)
    assert [c.name for c in cards] == ["NVIDIA GeForce RTX 4090 #1", "NVIDIA GeForce RTX 4090 #2"]
    assert [c.util for c in cards] == [40.0, 7.0]
    assert not _phantom_logs(thread)


def test_without_pdh_data_nothing_is_dropped(thread):
    thread._gpu_adapters = [*ARC, BRD]
    _poll(thread, {}, {}, {})
    assert thread._gpu_adapters == [*ARC, BRD]
    assert len(thread._gpu_cards(True, True)) == 3       # as before: no evidence, no collapse
    assert not _phantom_logs(thread)


def test_optimus_igpu_and_sleeping_dgpu_are_unchanged(thread):
    """A hybrid's dGPU is another maker: never grouped with the iGPU - even on a poll where PDH would
    not name it (PDH does list an idle adapter; this pins that the rule could not drop it anyway)."""
    thread._gpu_adapters = [INTEL_IGPU, RTX_MOBILE, BRD]
    _poll(thread, {_eng(0xD9C1, 10): 12.0}, {_mem(0xD9C1): 0.0}, {_mem(0xD9C1): 900 * MIB})
    assert thread._gpu_adapters == [INTEL_IGPU, RTX_MOBILE, BRD]
    assert [c.name for c in thread._gpu_cards(True, True)] == [
        "NVIDIA GeForce RTX 4060 Laptop GPU", "Intel UHD Graphics"]


def test_logged_once_across_a_query_rebuild(thread, monkeypatch):
    thread._gpu_adapters = [*ARC, BRD]
    _bober_poll(thread)
    # A rebuild (resume / display change) re-reads DXGI: the phantoms come back and are dropped again.
    thread._gpu_adapters, thread._gpu_pdh_luids_checked = [*ARC, BRD], None
    _bober_poll(thread)
    assert len(gpu_adapters.hardware_adapters(thread._gpu_adapters)) == 1
    assert len(_phantom_logs(thread)) == 1


def test_g4_reread_stays_collapsed_and_quiet(thread):
    """A G4 re-read (a new LUID in PDH) collapses the fresh list before comparing it, so the phantoms
    never read as an adapter change."""
    thread._gpu_adapters = [*ARC, BRD]
    _bober_poll(thread)
    with patch.object(gpu_adapters, "enumerate_adapters", return_value=[*ARC, RTX_A, BRD]):
        _poll(thread, {}, {_mem(0xA001): 0.0, _mem(0xC001): 0.0, _mem(0xDD11): 0.0},
              {_mem(0xA001): 0.0, _mem(0xC001): 0.0, _mem(0xDD11): 0.0})
    assert [a.luid for a in gpu_adapters.hardware_adapters(thread._gpu_adapters)] == [(0, 0xA001), (0, 0xC001)]


# --- the pure rule ---------------------------------------------------------------------------------

def test_rule_keeps_whichever_duplicate_pdh_names_and_the_order():
    collapse = gpu_adapters.collapse_phantom_duplicates
    assert collapse([BRD, *ARC], {(0, 0xA002)}) == [BRD, ARC[1]._replace(outputs=1)]   # ARC[0]'s display
    assert collapse([*ARC, BRD], {(0, 0xA001), (0, 0xA003)}) == [ARC[0], ARC[2]._replace(outputs=1), BRD]


@pytest.mark.parametrize("luids", [None, frozenset(), {(0, 0xDD11)}, {(0, 0x9999)}])
def test_rule_without_evidence_for_the_group_drops_nothing(luids):
    assert gpu_adapters.collapse_phantom_duplicates([*ARC, BRD], luids) == [*ARC, BRD]


def test_kept_member_inherits_the_groups_display_outputs():
    """A dropped phantom that owned the display outputs must not take them with it: the hybrid check
    (an NVIDIA card with outputs is awake, not Optimus) reads ``outputs``."""
    a = RTX_A._replace(outputs=2)
    b = RTX_A._replace(luid=(0, 0xC002), outputs=0)
    assert gpu_adapters.collapse_phantom_duplicates([a, b, BRD], {(0, 0xC002)}) == [b._replace(outputs=2), BRD]


def test_a_phantom_owning_the_outputs_does_not_turn_a_desktop_into_a_hybrid(thread):
    nv_out = RTX_MOBILE._replace(outputs=1)
    nv_real = RTX_MOBILE._replace(luid=(0, 0xAB02), outputs=0)
    thread._gpu_adapters = [INTEL_IGPU, nv_out, nv_real, BRD]
    thread._mobile_battery = True
    _poll(thread, {}, {_mem(0xD9C1): 0.0, _mem(0xAB02): 0.0}, {_mem(0xD9C1): 0.0, _mem(0xAB02): 0.0})
    assert [a.luid for a in gpu_adapters.hardware_adapters(thread._gpu_adapters)] == [(0, 0xD9C1), (0, 0xAB02)]
    assert thread._is_hybrid_laptop() is False      # the display on the NVIDIA card keeps it awake


def test_a_later_drop_logs_again_after_a_clean_set(thread):
    thread._gpu_adapters = [*ARC, BRD]
    _bober_poll(thread)
    # PDH names every Arc (nothing dropped), then the phantoms return: logged again.
    _poll(thread, {}, {_mem(l): 0.0 for l in ARC_LUIDS}, {_mem(l): 0.0 for l in ARC_LUIDS})
    thread._gpu_adapters, thread._gpu_pdh_luids_checked = [*ARC, BRD], None
    _bober_poll(thread)
    assert len(_phantom_logs(thread)) == 2


def test_gpu_query_cleanup_forgets_the_pdh_set(thread):
    thread._gpu_adapters = [*ARC, BRD]
    _bober_poll(thread)
    assert gpu_adapters.last_pdh_luids()
    with patch("win32pdh.CloseQuery"):
        thread._cleanup_gpu_query()
    assert gpu_adapters.last_pdh_luids() is None


def test_rule_never_touches_different_names_or_makers():
    adapters = [INTEL_IGPU, RTX_MOBILE, *ARC[:1], BRD]
    assert gpu_adapters.collapse_phantom_duplicates(adapters, {(0, 0xD9C1)}) == adapters


# --- Settings > Hardware ---------------------------------------------------------------------------

@pytest.fixture
def page(qt_owned):
    from netspeedtray.views.settings.pages.hardware import HardwarePage
    return qt_owned(HardwarePage(EN, lambda: None))


def test_settings_hides_the_list_switch_once_the_poll_has_seen_pdh(page):
    with patch.object(gpu_adapters, "enumerate_adapters", return_value=[*ARC, BRD]):
        page.load_settings({})
        assert not page._hover_gpus_card.isHidden()     # no PDH set yet: unchanged
        gpu_adapters.remember_pdh_luids({(0, 0xA001), (0, 0xDD11)})
        page.load_settings({})
    assert page._hover_gpus_card.isHidden()
    assert page._gpu_adapter_card.isHidden()            # one name, as before
    assert page.get_settings()["gpu_adapter"] == ""
