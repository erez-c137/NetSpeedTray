"""
Unit tests for the hardware monitoring logic in StatsMonitorThread.
"""

import pytest
from unittest.mock import MagicMock, patch
from netspeedtray.core.monitor_thread import StatsMonitorThread, GpuPollResult

class TestHardwareMonitoring:

    @pytest.fixture
    def monitor_thread(self, q_app):
        """Creates a thread instance for testing."""
        return StatsMonitorThread(interval=0.1)

    # ------------------------------------------------------------------
    # GPU hybrid polling
    # ------------------------------------------------------------------

    @patch('win32pdh.GetFormattedCounterValue')
    @patch('win32pdh.CollectQueryData')
    def test_poll_gpu_hybrid_success(self, mock_collect, mock_get_val, monitor_thread):
        """Test successful hybrid GPU stats polling (PDH util/VRAM + nvidia-smi for temp+total+power)."""
        monitor_thread._gpu_query = 123
        monitor_thread._gpu_util_counters = [1]
        monitor_thread._gpu_vram_counters = [2]
        monitor_thread._nvidia_smi_path = "nvidia-smi"
        monitor_thread._wmi_ohm = False  # LHM not available; fall back to nvidia-smi

        # Counter 1 (Util): 45.0%, Counter 2 (VRAM used): 1073741824 bytes = 1024 MiB
        mock_get_val.side_effect = [(None, 45.0), (None, 1073741824.0)]

        # nvidia-smi returns: temperature, memory.total (MiB), power.draw (W)
        mock_smi_output = "52, 8192, 75.3\n"

        with patch('subprocess.check_output') as mock_sub:
            mock_sub.return_value = mock_smi_output

            result = monitor_thread._poll_gpu_hybrid()

            assert isinstance(result, GpuPollResult)
            assert result.util == 45.0
            assert result.vram_used == 1024.0   # 1073741824 bytes / (1024*1024)
            assert result.vram_total == 8192.0   # MiB, from nvidia-smi memory.total
            assert result.temp == 52.0

            # Ensure the nvidia-smi call fetches temp, memory.total, and power.draw
            args, _ = mock_sub.call_args
            cmd = args[0]
            assert any("temperature.gpu,memory.total,power.draw" in arg for arg in cmd)

    @patch('win32pdh.GetFormattedCounterValue')
    @patch('win32pdh.CollectQueryData')
    def test_poll_gpu_hybrid_lhm_temp(self, mock_collect, mock_get_val, monitor_thread):
        """LHM/OHM GPU temp should be used before nvidia-smi and works for all vendors."""
        monitor_thread._gpu_query = 123
        monitor_thread._gpu_util_counters = [1]
        monitor_thread._gpu_vram_counters = []
        monitor_thread._nvidia_smi_path = "nvidia-smi"

        mock_get_val.return_value = (None, 55.0)

        # Set up a mock LHM WMI object
        mock_ohm = MagicMock()
        mock_sensor = MagicMock()
        mock_sensor.Value = 68.0
        mock_sensor.Identifier = "/nvidiagpu/0/temperature/0"
        mock_sensor.Name = "GPU Core"
        mock_ohm.ExecQuery.return_value = [mock_sensor]
        monitor_thread._wmi_ohm = mock_ohm

        with patch('subprocess.check_output') as mock_sub:
            result = monitor_thread._poll_gpu_hybrid()

            assert result.temp == 68.0            # From LHM, not nvidia-smi
            mock_sub.assert_not_called()   # nvidia-smi not reached when LHM provides temp

    @patch('win32pdh.GetFormattedCounterValue')
    @patch('win32pdh.CollectQueryData')
    def test_poll_gpu_hybrid_no_temp_flag(self, mock_collect, mock_get_val, monitor_thread):
        """With include_temp=False and include_power=False, nvidia-smi is not called."""
        monitor_thread._gpu_query = 123
        monitor_thread._gpu_util_counters = [1]
        monitor_thread._gpu_vram_counters = []
        monitor_thread._nvidia_smi_path = "nvidia-smi"

        mock_get_val.return_value = (None, 30.0)

        with patch('subprocess.check_output') as mock_sub:
            result = monitor_thread._poll_gpu_hybrid(include_temp=False)

            assert result.temp is None
            assert result.power is None
            mock_sub.assert_not_called()  # No subprocess call when neither temp nor power needed

    def test_poll_gpu_hybrid_no_smi(self, monitor_thread):
        """AMD/Intel with no nvidia-smi and no LHM: total, temp, and power are None."""
        monitor_thread._gpu_query = 123
        monitor_thread._gpu_util_counters = [1]
        monitor_thread._nvidia_smi_path = None
        monitor_thread._wmi_ohm = False  # LHM not available

        with patch('win32pdh.CollectQueryData'):
            with patch('win32pdh.GetFormattedCounterValue', return_value=(None, 10.0)):
                result = monitor_thread._poll_gpu_hybrid()
                assert result.util == 10.0
                assert result.vram_total is None
                assert result.temp is None
                assert result.power is None

    def test_poll_gpu_hybrid_smi_error(self, monitor_thread):
        """nvidia-smi subprocess failure should not crash polling; temp, total, and power stay None."""
        monitor_thread._gpu_query = 123
        monitor_thread._gpu_util_counters = [1]
        monitor_thread._gpu_vram_counters = []
        monitor_thread._nvidia_smi_path = "nvidia-smi"
        monitor_thread._wmi_ohm = False  # LHM not available

        with patch('win32pdh.CollectQueryData'):
            with patch('win32pdh.GetFormattedCounterValue', return_value=(None, 20.0)):
                with patch('subprocess.check_output', side_effect=Exception("SMI Error")):
                    result = monitor_thread._poll_gpu_hybrid()
                    assert result.util == 20.0
                    assert result.vram_total is None
                    assert result.temp is None
                    assert result.power is None

    # ------------------------------------------------------------------
    # GPU power polling
    # ------------------------------------------------------------------

    @patch('win32pdh.GetFormattedCounterValue')
    @patch('win32pdh.CollectQueryData')
    def test_poll_gpu_hybrid_power_from_smi(self, mock_collect, mock_get_val, monitor_thread):
        """nvidia-smi should provide GPU power when include_power=True and LHM unavailable."""
        monitor_thread._gpu_query = 123
        monitor_thread._gpu_util_counters = [1]
        monitor_thread._gpu_vram_counters = []
        monitor_thread._nvidia_smi_path = "nvidia-smi"
        monitor_thread._wmi_ohm = False

        mock_get_val.return_value = (None, 50.0)
        mock_smi_output = "65, 8192, 120.5\n"

        with patch('subprocess.check_output') as mock_sub:
            mock_sub.return_value = mock_smi_output
            result = monitor_thread._poll_gpu_hybrid(include_power=True)

            assert result.power == 120.5
            assert result.temp == 65.0

    @patch('win32pdh.GetFormattedCounterValue')
    @patch('win32pdh.CollectQueryData')
    def test_poll_gpu_hybrid_power_from_lhm(self, mock_collect, mock_get_val, monitor_thread):
        """LHM/OHM GPU power sensor should be preferred over nvidia-smi."""
        monitor_thread._gpu_query = 123
        monitor_thread._gpu_util_counters = [1]
        monitor_thread._gpu_vram_counters = []
        monitor_thread._nvidia_smi_path = "nvidia-smi"

        mock_get_val.return_value = (None, 40.0)

        mock_ohm = MagicMock()
        # Temperature sensor
        mock_temp_sensor = MagicMock()
        mock_temp_sensor.Value = 72.0
        mock_temp_sensor.Identifier = "/nvidiagpu/0/temperature/0"
        mock_temp_sensor.Name = "GPU Core"
        # Power sensor
        mock_power_sensor = MagicMock()
        mock_power_sensor.Value = 95.2
        mock_power_sensor.Identifier = "/nvidiagpu/0/power/0"
        mock_power_sensor.Name = "GPU Power"

        def mock_exec_query(query):
            if "Temperature" in query:
                return [mock_temp_sensor]
            elif "Power" in query:
                return [mock_power_sensor]
            return []

        mock_ohm.ExecQuery.side_effect = mock_exec_query
        monitor_thread._wmi_ohm = mock_ohm

        with patch('subprocess.check_output') as mock_sub:
            result = monitor_thread._poll_gpu_hybrid(include_temp=True, include_power=True)

            assert result.temp == 72.0
            assert result.power == 95.2
            mock_sub.assert_not_called()  # nvidia-smi not needed

    # ------------------------------------------------------------------
    # CPU power polling
    # ------------------------------------------------------------------

    @patch('win32pdh.GetFormattedCounterValue')
    @patch('win32pdh.CollectQueryData')
    def test_poll_cpu_power_rapl(self, mock_collect, mock_get_val, monitor_thread):
        """CPU power should come from RAPL PKG counter (milliwatts → watts)."""
        monitor_thread._power_query = 123
        monitor_thread._power_pkg_counter = 1
        monitor_thread._wmi_ohm = False

        # 15000 mW = 15.0 W
        mock_get_val.return_value = (None, 15000.0)

        power = monitor_thread._poll_cpu_power()
        assert power == 15.0

    def test_poll_cpu_power_no_rapl(self, monitor_thread):
        """No RAPL and no LHM: CPU power should be None."""
        monitor_thread._power_query = None
        monitor_thread._power_pkg_counter = None
        monitor_thread._wmi_ohm = False

        with patch.object(monitor_thread, '_init_power_query', return_value=False):
            power = monitor_thread._poll_cpu_power()
            assert power is None

    # ------------------------------------------------------------------
    # OHM/LHM WMI probe
    # ------------------------------------------------------------------

    def test_init_ohm_wmi_rejects_empty_namespace(self, monitor_thread):
        """LHM namespace with 0 sensors (not running as admin) should not be cached."""
        monitor_thread._wmi_ohm = None

        mock_obj = MagicMock()
        mock_obj.ExecQuery.return_value = []  # 0 sensors

        with patch('win32com.client.GetObject', return_value=mock_obj), \
             patch('pythoncom.CoInitialize'):
            monitor_thread._init_ohm_wmi()
            # Should NOT have cached the connection — still None for retry
            assert monitor_thread._wmi_ohm is None

    # ------------------------------------------------------------------
    # CPU temperature polling
    # ------------------------------------------------------------------

    @patch('win32com.client.GetObject')
    def test_poll_cpu_temperature_wmi_success(self, mock_get_obj, monitor_thread):
        """Test successful CPU temperature polling via WMI ACPI fallback."""
        mock_wmi = MagicMock()
        mock_get_obj.return_value = mock_wmi

        # (310.2 K - 273.15) = 37.05 C; value stored as tenths of Kelvin → 3102
        mock_temp = MagicMock()
        mock_temp.CurrentTemperature = 3102
        mock_wmi.ExecQuery.return_value = [mock_temp]

        # Skip PDH thermal zone and OHM/LHM to isolate the ACPI WMI fallback.
        # Use truthy sentinel (-1) for _thermal_query to prevent _init_thermal_query().
        monitor_thread._thermal_query = -1
        monitor_thread._thermal_counters = []
        monitor_thread._thermal_hp_counters = []
        monitor_thread._wmi_ohm = False

        with patch('pythoncom.CoInitialize'):
            temp = monitor_thread._poll_cpu_temperature()
            assert pytest.approx(temp, 0.1) == 37.05
            assert monitor_thread._wmi is not None

    def test_poll_cpu_temperature_wmi_reconnection(self, monitor_thread):
        """Test that WMI client is reset on critical RPC errors."""
        monitor_thread._wmi = MagicMock()
        monitor_thread._wmi.ExecQuery.side_effect = Exception("RPC server is unavailable (0x800706ba)")
        # Skip PDH thermal zone and OHM/LHM to isolate the ACPI WMI path
        monitor_thread._thermal_query = -1
        monitor_thread._thermal_counters = []
        monitor_thread._thermal_hp_counters = []
        monitor_thread._wmi_ohm = False

        temp = monitor_thread._poll_cpu_temperature()
        assert temp is None
        assert monitor_thread._wmi is None  # Should have been reset for reconnection
