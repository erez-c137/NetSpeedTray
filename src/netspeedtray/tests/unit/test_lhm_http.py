import json
import unittest
from unittest.mock import patch

from netspeedtray.core.lhm_http import LhmHttpClient, parse_lhm_value, parse_snapshot


def hardware(name, hardware_id, *sensors):
    return {"Text": name, "HardwareId": hardware_id, "Children": list(sensors)}


def sensor(name, sensor_id, sensor_type, value):
    return {
        "Text": name, "SensorId": sensor_id, "Type": sensor_type,
        "Value": value, "Children": [],
    }


def tree(*children):
    return {"Text": "Sensor", "Children": list(children)}


class FakeResponse:
    def __init__(self, data):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.data


class TestLhmHttp(unittest.TestCase):
    def test_decimal_comma_and_point(self):
        self.assertEqual(parse_lhm_value("58,9 °C"), 58.9)
        self.assertEqual(parse_lhm_value("56.8 W"), 56.8)
        self.assertIsNone(parse_lhm_value("-"))
        self.assertIsNone(parse_lhm_value(True))

    def test_ryzen_and_real_multi_gpu_case(self):
        data = tree(
            hardware("Nuvoton NCT6686D", "/lpc/nct6686d/0",
                sensor("CPU Socket", "/lpc/nct6686d/0/temperature/4", "Temperature", "84,0 °C")),
            hardware("AMD Ryzen 7 9700X", "/amdcpu/0",
                sensor("CCD1 (Tdie)", "/amdcpu/0/temperature/0", "Temperature", "48,3 °C"),
                sensor("Core (Tctl/Tdie)", "/amdcpu/0/temperature/2", "Temperature", "58,9 °C"),
                sensor("Package", "/amdcpu/0/power/0", "Power", "56,8 W")),
            hardware("AMD Radeon(TM) Graphics", "/gpu-amd/0",
                sensor("GPU Core", "/gpu-amd/0/temperature/0", "Temperature", "44,0 °C"),
                sensor("GPU Core", "/gpu-amd/0/power/0", "Power", "21,0 W")),
            hardware("NVIDIA GeForce RTX 5060 Ti", "/gpu-nvidia/0",
                sensor("GPU Core", "/gpu-nvidia/0/temperature/0", "Temperature", "50,3 °C"),
                sensor("GPU Package", "/gpu-nvidia/0/power/0", "Power", "24,2 W")),
        )
        snapshot = parse_snapshot(data)
        self.assertEqual(snapshot.cpu_temp, 58.9)
        self.assertEqual(snapshot.cpu_power, 56.8)
        self.assertEqual(snapshot.cpu_temp_sensor, "Core (Tctl/Tdie)")
        self.assertEqual(snapshot.preferred_gpu.hardware_id, "/gpu-nvidia/0")
        self.assertEqual(snapshot.preferred_gpu.temperature, 50.3)
        self.assertEqual(snapshot.preferred_gpu.power, 24.2)

    def test_intel_cpu_package(self):
        snapshot = parse_snapshot(tree(
            hardware("Intel Core", "/intelcpu/0",
                sensor("CPU Package", "/intelcpu/0/temperature/0", "Temperature", "61.5 °C"),
                sensor("CPU Package", "/intelcpu/0/power/0", "Power", "72,4 W"))))
        self.assertEqual(snapshot.cpu_temp, 61.5)
        self.assertEqual(snapshot.cpu_power, 72.4)

    def test_gpu_core_cannot_become_cpu_sensor(self):
        snapshot = parse_snapshot(tree(
            hardware("NVIDIA GPU", "/gpu-nvidia/0",
                sensor("GPU Core", "/gpu-nvidia/0/temperature/0", "Temperature", "77 °C"))))
        self.assertIsNone(snapshot.cpu_temp)
        self.assertEqual(snapshot.preferred_gpu.temperature, 77.0)

    def test_sensor_id_is_authoritative_even_without_hardware_id(self):
        snapshot = parse_snapshot(tree(
            sensor("CPU Package", "/lpc/nct6686d/0/temperature/0", "Temperature", "84 °C"),
            sensor("GPU Core", "/gpu-nvidia/0/temperature/0", "Temperature", "50 °C"),
            sensor("Package", "/amdcpu/0/power/0", "Power", "56 W")))
        self.assertIsNone(snapshot.cpu_temp)
        self.assertEqual(snapshot.cpu_power, 56.0)

    @patch("netspeedtray.core.lhm_http.urllib.request.urlopen")
    def test_short_cache_fetches_once(self, urlopen):
        payload = tree(hardware("AMD CPU", "/amdcpu/0",
            sensor("Package", "/amdcpu/0/power/0", "Power", "42 W")))
        urlopen.return_value = FakeResponse(json.dumps(payload).encode())
        client = LhmHttpClient(cache_seconds=10)
        self.assertEqual(client.get_snapshot().cpu_power, 42.0)
        self.assertEqual(client.get_snapshot().cpu_power, 42.0)
        urlopen.assert_called_once()

    @patch("netspeedtray.core.lhm_http.urllib.request.urlopen", side_effect=OSError("offline"))
    def test_unreachable_endpoint_returns_none(self, _urlopen):
        self.assertIsNone(LhmHttpClient().get_snapshot())

    @patch("netspeedtray.core.lhm_http.urllib.request.urlopen")
    def test_broken_json_returns_none(self, urlopen):
        urlopen.return_value = FakeResponse(b"{broken")
        self.assertIsNone(LhmHttpClient().get_snapshot())


if __name__ == "__main__":
    unittest.main()

