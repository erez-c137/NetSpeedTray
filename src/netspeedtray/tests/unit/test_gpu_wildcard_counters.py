"""
Regression tests for the widget's GPU counter query (#236).

`\\GPU Engine` instances are per-PROCESS - `pid_1234_luid_..._engtype_3D`. The old code enumerated
them once at startup and added one PDH handle each, so it only ever sampled processes that already
existed when NetSpeedTray launched. Anything started afterwards got a PID we had never subscribed
to, and its load never showed up.

On a single-GPU machine that is invisible: the desktop compositor's own instance tracks total load
closely enough that nobody notices. On a hybrid laptop it means the discrete GPU is never sampled
at all, because nothing driving it was running at launch - the reporter could only ever see their
integrated AMD, never the NVIDIA.

The second half of this is the `present` flag. `AddCounter` on a wildcard path succeeds whenever
the counter OBJECT exists, which it does on machines with no usable GPU at all, so deriving
presence from "did AddCounter work" would report a confident 0% on RDP sessions and VMs.
"""

import pytest
from unittest.mock import MagicMock, patch

from netspeedtray.core.monitor_thread import StatsMonitorThread


@pytest.fixture
def thread(q_app):
    t = StatsMonitorThread(interval=0.1)
    t.logger = MagicMock()
    t._gpu_query = 123
    t._gpu_util_counter = 1
    t._gpu_vram_counter = None
    t._nvidia_smi_path = None
    t._wmi_ohm = False
    return t


def _poll(thread, util_array, vram_array=None):
    """Run one GPU poll with the wildcard arrays the PDH layer would return."""
    arrays = [util_array] if vram_array is None else [util_array, vram_array]
    if vram_array is not None:
        thread._gpu_vram_counter = 2
    with patch("win32pdh.CollectQueryData"), \
         patch("win32pdh.GetFormattedCounterArray", side_effect=arrays):
        return thread._poll_gpu_hybrid(include_temp=False, include_power=False)


class TestProcessesStartedAfterLaunchAreSampled:

    def test_a_process_that_did_not_exist_at_startup_is_counted(self, thread):
        """The #236 bug in one assertion: the wildcard re-expands every collection, so a PID we
        never subscribed to still contributes. Under the old snapshot this returned 0."""
        result = _poll(thread, {
            "pid_9999_luid_0x00000000_0x0000A1B2_phys_0_eng_0_engtype_3D": 87.0,
        })
        assert result.util == 87.0

    def test_the_busiest_engine_across_both_adapters_wins(self, thread):
        """Hybrid laptop: the iGPU idles driving the desktop while the dGPU does the work."""
        result = _poll(thread, {
            "pid_100_luid_0x00000000_0x0000AAAA_phys_0_eng_0_engtype_3D": 4.0,    # iGPU, desktop
            "pid_700_luid_0x00000000_0x0000BBBB_phys_0_eng_0_engtype_3D": 91.0,   # dGPU, the game
        })
        assert result.util == 91.0

    def test_instances_with_a_blank_engtype_still_count(self, thread):
        """A large share of instances report no engtype, and there is no "Compute" engtype at all -
        filtering to 3D would zero out exactly the CUDA/compute workloads this should surface."""
        result = _poll(thread, {
            "pid_100_luid_0x00000000_0x0000AAAA_phys_0_eng_3_engtype_": 76.0,
        })
        assert result.util == 76.0

    def test_vram_sums_across_adapters(self, thread):
        result = _poll(
            thread,
            {"pid_1_luid_0x0_0xA_phys_0_eng_0_engtype_3D": 10.0},
            {"luid_0x0_0xA_phys_0": 1073741824.0, "luid_0x0_0xB_phys_0": 536870912.0},
        )
        assert result.vram_used == 1536.0        # (1024 + 512) MiB

    def test_an_idle_gpu_reads_zero_not_none(self, thread):
        result = _poll(thread, {"pid_1_luid_0x0_0xA_phys_0_eng_0_engtype_3D": 0.0})
        assert result.util == 0.0


class TestPresenceIsEvidenceBased:
    """`present` gates the Monitor's GPU tiles, so a false positive fabricates a whole readout."""

    def test_an_empty_array_does_not_claim_a_gpu(self, thread):
        """RDP sessions, VMs and GPU-less boxes: the counter object exists and AddCounter succeeds,
        but no instance is ever returned. Deriving presence from the handle would report 0% here."""
        assert thread._gpu_engine_seen is False
        result = _poll(thread, {})
        assert result.present is False
        assert result.util == 0.0

    def test_one_real_instance_establishes_presence(self, thread):
        result = _poll(thread, {"pid_1_luid_0x0_0xA_phys_0_eng_0_engtype_3D": 3.0})
        assert result.present is True

    def test_presence_is_latched_across_an_idle_tick(self, thread):
        """The array is legitimately empty when nothing touches the GPU. Presence must not flap,
        or the Monitor's GPU tiles blink out whenever the machine goes quiet."""
        assert _poll(thread, {"pid_1_luid_0x0_0xA_phys_0_eng_0_engtype_3D": 40.0}).present is True
        assert _poll(thread, {}).present is True

    def test_presence_survives_a_query_rebuild(self, thread):
        """A settings change or a resume closes and reopens the query; forgetting that this machine
        has a GPU would blank the tiles until the next non-idle tick."""
        assert _poll(thread, {"pid_1_luid_0x0_0xA_phys_0_eng_0_engtype_3D": 40.0}).present is True

        with patch("win32pdh.CloseQuery"):
            thread._cleanup_gpu_query()
        assert thread._gpu_engine_seen is True

        thread._gpu_query = 123
        thread._gpu_util_counter = 1
        assert _poll(thread, {}).present is True


class TestFailuresDegrade:

    def test_a_pdh_read_error_does_not_crash_the_poll(self, thread):
        with patch("win32pdh.CollectQueryData"), \
             patch("win32pdh.GetFormattedCounterArray", side_effect=Exception("PDH_INVALID_HANDLE")):
            result = thread._poll_gpu_hybrid(include_temp=False, include_power=False)
        assert result.util == 0.0
        assert result.present is False

    def test_non_numeric_values_are_ignored(self, thread):
        result = _poll(thread, {
            "pid_1_luid_0x0_0xA_phys_0_eng_0_engtype_3D": None,
            "pid_2_luid_0x0_0xA_phys_0_eng_1_engtype_Copy": 33.0,
        })
        assert result.util == 33.0


class TestPerEngineAggregation:
    """#328: each instance is ONE process on ONE engine. Task Manager's GPU figure is the busiest
    engine with its processes SUMMED; the old max() reported the busiest single process instead."""

    def test_two_processes_on_one_engine_add_up(self, thread):
        result = _poll(thread, {
            "pid_100_luid_0x00000000_0x0000A1B2_phys_0_eng_0_engtype_3D": 40.0,
            "pid_200_luid_0x00000000_0x0000A1B2_phys_0_eng_0_engtype_3D": 40.0,
        })
        assert result.util == 80.0

    def test_the_reporters_three_processes(self, thread):
        """Browser 44 + VM 34 + dwm 13 on one 3D engine: Task Manager read ~91, we read 44."""
        result = _poll(thread, {
            "pid_100_luid_0x0_0xA_phys_0_eng_0_engtype_3D": 44.0,
            "pid_200_luid_0x0_0xA_phys_0_eng_0_engtype_3D": 34.0,
            "pid_300_luid_0x0_0xA_phys_0_eng_0_engtype_3D": 13.0,
        })
        assert result.util == pytest.approx(91.0)

    def test_different_engines_are_not_added(self, thread):
        result = _poll(thread, {
            "pid_100_luid_0x0_0xA_phys_0_eng_0_engtype_3D": 30.0,
            "pid_100_luid_0x0_0xA_phys_0_eng_3_engtype_VideoDecode": 50.0,
        })
        assert result.util == 50.0

    def test_busiest_engine_sum_beats_a_bigger_single_process_elsewhere(self, thread):
        result = _poll(thread, {
            "pid_100_luid_0x0_0xA_phys_0_eng_0_engtype_3D": 35.0,
            "pid_200_luid_0x0_0xA_phys_0_eng_0_engtype_3D": 35.0,
            "pid_300_luid_0x0_0xA_phys_0_eng_1_engtype_Copy": 50.0,
        })
        assert result.util == 70.0

    def test_engtype_label_does_not_split_an_engine(self, thread):
        """The engine is (luid, phys, eng); engtype is only a label and is often blank."""
        result = _poll(thread, {
            "pid_100_luid_0x0_0xA_phys_0_eng_0_engtype_3D": 30.0,
            "pid_200_luid_0x0_0xA_phys_0_eng_0_engtype_": 25.0,
        })
        assert result.util == 55.0

    def test_sum_over_100_clamps(self, thread):
        result = _poll(thread, {
            "pid_100_luid_0x0_0xA_phys_0_eng_0_engtype_3D": 70.0,
            "pid_200_luid_0x0_0xA_phys_0_eng_0_engtype_3D": 60.0,
        })
        assert result.util == 100.0

    def test_two_adapters_are_not_added_busiest_engine_wins(self, thread):
        """dGPU + iGPU. Nothing in the app picks an adapter (VRAM sums all of them), so the figure
        is the busiest engine on ANY adapter - never a cross-adapter sum."""
        result = _poll(thread, {
            "pid_100_luid_0x00000000_0x0000AAAA_phys_0_eng_0_engtype_3D": 30.0,   # iGPU
            "pid_200_luid_0x00000000_0x0000AAAA_phys_0_eng_0_engtype_3D": 30.0,   # iGPU
            "pid_300_luid_0x00000000_0x0000BBBB_phys_0_eng_0_engtype_3D": 45.0,   # dGPU
            "pid_400_luid_0x00000000_0x0000BBBB_phys_0_eng_0_engtype_3D": 25.0,   # dGPU
        })
        assert result.util == 70.0

    def test_same_low_luid_different_high_luid_is_a_different_adapter(self, thread):
        result = _poll(thread, {
            "pid_100_luid_0x00000000_0x0000AAAA_phys_0_eng_0_engtype_3D": 40.0,
            "pid_200_luid_0x00000001_0x0000AAAA_phys_0_eng_0_engtype_3D": 40.0,
        })
        assert result.util == 40.0

    def test_hex_case_and_padding_do_not_split_an_engine(self, thread):
        result = _poll(thread, {
            "pid_100_luid_0x00000000_0x0000aaaa_phys_0_eng_0_engtype_3D": 20.0,
            "pid_200_LUID_0X0_0XAAAA_PHYS_0_ENG_0_ENGTYPE_3D": 20.0,
        })
        assert result.util == 40.0

    @pytest.mark.parametrize("bad_name", [
        "", "garbage", "pid_100", "pid_x_luid_0x0_0xA_phys_0_eng_0_engtype_3D",
        "pid_100_luid_zz_0xA_phys_0_eng_0_engtype_3D", "luid_0x0_0xA_phys_0_eng_0",
    ])
    def test_a_malformed_name_counts_as_its_own_engine(self, thread, bad_name):
        """An unparseable instance cannot be attributed to an engine: it is neither dropped nor
        summed into one. It counts alone, and the poll does not crash."""
        result = _poll(thread, {
            bad_name: 60.0,
            "pid_100_luid_0x0_0xA_phys_0_eng_0_engtype_3D": 20.0,
            "pid_200_luid_0x0_0xA_phys_0_eng_0_engtype_3D": 15.0,
        })
        assert result.util == 60.0

    def test_malformed_names_do_not_absorb_or_hide_parsed_engines(self, thread):
        result = _poll(thread, {
            "garbage_a": 30.0,
            "garbage_b": 30.0,
            "pid_100_luid_0x0_0xA_phys_0_eng_0_engtype_3D": 40.0,
            "pid_200_luid_0x0_0xA_phys_0_eng_0_engtype_3D": 30.0,
        })
        assert result.util == 70.0

    @pytest.mark.parametrize("bad_value", [None, "50", True, -30.0, float("nan"), float("inf")])
    def test_invalid_values_do_not_poison_an_engine_sum(self, thread, bad_value):
        result = _poll(thread, {
            "pid_100_luid_0x0_0xA_phys_0_eng_0_engtype_3D": 40.0,
            "pid_200_luid_0x0_0xA_phys_0_eng_0_engtype_3D": bad_value,
            "pid_300_luid_0x0_0xA_phys_0_eng_0_engtype_3D": 30.0,
        })
        assert result.util == 70.0

    def test_a_non_string_instance_key_does_not_crash(self, thread):
        result = _poll(thread, {
            None: 10.0,
            "pid_100_luid_0x0_0xA_phys_0_eng_0_engtype_3D": 40.0,
        })
        assert result.util == 40.0
