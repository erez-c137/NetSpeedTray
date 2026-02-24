"""
Unit tests for the hardware monitoring logic in StatsMonitorThread.
"""

import pytest
from unittest.mock import MagicMock, patch
from netspeedtray.core.monitor_thread import StatsMonitorThread

class TestHardwareMonitoring:
    
    @pytest.fixture
    def monitor_thread(self, q_app):
        """Creates a thread instance for testing."""
        return StatsMonitorThread(interval=0.1)

    def test_poll_gpu_stats_success(self, monitor_thread):
        """Test successful batched GPU stats polling."""
        monitor_thread._nvidia_smi_path = "nvidia-smi"
        
        # Mocking nvidia-smi output: temperature, memory.used, memory.total
        mock_output = "50, 2048, 8192\n"
        
        with patch('subprocess.check_output') as mock_sub:
            mock_sub.return_value = mock_output
            
            temp, used, total = monitor_thread._poll_gpu_stats()
            
            assert temp == 50.0
            assert used == 2048.0
            assert total == 8192.0
            # Ensure correct arguments were passed
            args, kwargs = mock_sub.call_args
            # The list of arguments is args[0]
            cmd_args = args[0]
            assert any("temperature.gpu,memory.used,memory.total" in arg for arg in cmd_args)
            assert any("--format=csv,noheader,nounits" in arg for arg in cmd_args)

    def test_poll_gpu_stats_error_handling(self, monitor_thread):
        """Test error handling in GPU stats polling."""
        monitor_thread._nvidia_smi_path = "nvidia-smi"
        
        with patch('subprocess.check_output', side_effect=Exception("SMI Error")):
            temp, used, total = monitor_thread._poll_gpu_stats()
            assert temp is None
            assert used is None
            assert total is None

    def test_poll_gpu_stats_missing_binary(self, monitor_thread):
        """Test that polling returns None if nvidia-smi is not found."""
        monitor_thread._nvidia_smi_path = None
        temp, used, total = monitor_thread._poll_gpu_stats()
        assert temp is None
        assert used is None
        assert total is None

    @patch('win32pdh.GetFormattedCounterValue')
    @patch('win32pdh.CollectQueryData')
    def test_poll_gpu_hybrid_success(self, mock_collect, mock_get_val, monitor_thread):
        """Test successful hybrid GPU stats polling (Universal PDH + optional SMI)."""
        monitor_thread._gpu_query = 123
        monitor_thread._gpu_util_counters = [1]
        monitor_thread._gpu_vram_counters = [2]
        monitor_thread._nvidia_smi_path = "nvidia-smi"
        
        # Mock PDH values
        # Counter 1 (Util): 45.0%
        # Counter 2 (VRAM): 1024 MiB (1073741824 bytes)
        mock_get_val.side_effect = [(None, 45.0), (None, 1073741824.0)]
        
        # Mock SMI output for temperature
        mock_smi_output = "52\n"
        
        with patch('subprocess.check_output') as mock_sub:
            mock_sub.return_value = mock_smi_output
            
            util, used, total, temp = monitor_thread._poll_gpu_hybrid()
            
            assert util == 45.0
            assert used == 1024.0 # 1073741824 / (1024*1024)
            assert total is None  # PDH doesn't easily give total
            assert temp == 52.0

    def test_poll_gpu_hybrid_no_smi(self, monitor_thread):
        """Test that hybrid polling works for AMD/Intel (no nvidia-smi)."""
        monitor_thread._gpu_query = 123
        monitor_thread._gpu_util_counters = [1]
        monitor_thread._nvidia_smi_path = None
        
        with patch('win32pdh.CollectQueryData'):
            with patch('win32pdh.GetFormattedCounterValue', return_value=(None, 10.0)):
                util, used, total, temp = monitor_thread._poll_gpu_hybrid()
                assert util == 10.0
                assert temp is None # No temperature for non-NVIDIA without SMI

    @patch('win32com.client.GetObject')
    def test_poll_cpu_temperature_wmi_success(self, mock_get_obj, monitor_thread):
        """Test successful CPU temperature polling via WMI."""
        # Setup WMI mock
        mock_wmi = MagicMock()
        mock_get_obj.return_value = mock_wmi
        
        # Mock thermal zone data (tenths of Kelvin)
        # (310.2 K - 273.15) = 37.05 C
        mock_temp = MagicMock()
        mock_temp.CurrentTemperature = 3102 
        mock_wmi.ExecQuery.return_value = [mock_temp]
        
        # We need to mock pythoncom for thread initialization
        with patch('pythoncom.CoInitialize'):
            temp = monitor_thread._poll_cpu_temperature()
            assert pytest.approx(temp, 0.1) == 37.05
            assert monitor_thread._wmi is not None

    def test_poll_cpu_temperature_wmi_reconnection(self, monitor_thread):
        """Test that WMI client is reset on critical RPC errors."""
        monitor_thread._wmi = MagicMock()
        monitor_thread._wmi.ExecQuery.side_effect = Exception("RPC server is unavailable (0x800706ba)")
        
        temp = monitor_thread._poll_cpu_temperature()
        assert temp is None
        assert monitor_thread._wmi is None # Should have been reset for reconnection
