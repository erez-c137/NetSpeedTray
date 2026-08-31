"""LibreHardwareMonitor HTTP sensor source.

LibreHardwareMonitor 0.9.5 and newer expose sensor data through the built-in
web server instead of WMI.  This module intentionally depends only on the
Python standard library and quietly returns ``None`` when the endpoint is not
available, allowing callers to retain their existing WMI/native fallbacks.
"""

from __future__ import annotations

import json
import math
import re
import time
import urllib.request
from dataclasses import dataclass
from typing import Any, Iterator, Optional, Tuple


DEFAULT_URL = "http://localhost:8085/data.json"
DEFAULT_TIMEOUT_SEC = 0.35
DEFAULT_CACHE_SEC = 0.75

_NUMBER_RE = re.compile(r"[-+]?(?:\d+(?:[.,]\d+)?|[.,]\d+)")
_CPU_PREFIXES = ("/amdcpu/", "/intelcpu/")
_GPU_PREFIXES = (
    "/gpu-nvidia/", "/gpu-amd/", "/gpu-intel/",
    "/nvidiagpu/", "/amdgpu/", "/intelgpu/",
)


@dataclass(frozen=True)
class LhmSensor:
    name: str
    sensor_type: str
    sensor_id: str
    hardware_id: str
    hardware_name: str
    value: float


@dataclass(frozen=True)
class LhmGpu:
    hardware_id: str
    hardware_name: str
    temperature: Optional[float] = None
    temperature_sensor: Optional[str] = None
    power: Optional[float] = None
    power_sensor: Optional[str] = None


@dataclass(frozen=True)
class LhmSnapshot:
    cpu_temp: Optional[float] = None
    cpu_temp_sensor: Optional[str] = None
    cpu_power: Optional[float] = None
    cpu_power_sensor: Optional[str] = None
    gpus: Tuple[LhmGpu, ...] = ()

    @property
    def preferred_gpu(self) -> Optional[LhmGpu]:
        return min(self.gpus, key=_gpu_rank) if self.gpus else None


def parse_lhm_value(value: Any) -> Optional[float]:
    """Parse values such as ``58,9 °C`` and ``56.8 W`` safely."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        result = float(value)
    else:
        match = _NUMBER_RE.search(str(value).strip())
        if not match:
            return None
        try:
            result = float(match.group(0).replace(",", "."))
        except ValueError:
            return None
    return result if math.isfinite(result) else None


def _walk_nodes(
    node: Any, hardware_id: str = "", hardware_name: str = ""
) -> Iterator[Tuple[dict, str, str]]:
    if not isinstance(node, dict):
        return
    current_id = str(node.get("HardwareId") or hardware_id).strip().lower()
    current_name = hardware_name
    if node.get("HardwareId"):
        current_name = str(node.get("Text") or "").strip()
    yield node, current_id, current_name
    children = node.get("Children")
    if isinstance(children, list):
        for child in children:
            yield from _walk_nodes(child, current_id, current_name)


def _iter_sensors(data: Any) -> Iterator[LhmSensor]:
    for node, hardware_id, hardware_name in _walk_nodes(data):
        sensor_id = str(node.get("SensorId") or "").strip().lower()
        sensor_type = str(node.get("Type") or "").strip().lower()
        value = parse_lhm_value(node.get("Value"))
        if not sensor_id or not sensor_type or value is None:
            continue
        yield LhmSensor(
            name=str(node.get("Text") or "").strip(),
            sensor_type=sensor_type,
            sensor_id=sensor_id,
            hardware_id=hardware_id,
            hardware_name=hardware_name,
            value=value,
        )


def _belongs_to(sensor: LhmSensor, prefixes: Tuple[str, ...]) -> bool:
    return sensor.sensor_id.startswith(prefixes) or sensor.hardware_id.startswith(prefixes)


def _pick(sensors: list[LhmSensor], priorities: Tuple[str, ...]) -> Optional[LhmSensor]:
    if not sensors:
        return None
    names = {name.casefold(): index for index, name in enumerate(priorities)}

    def rank(sensor: LhmSensor) -> Tuple[int, str]:
        name = sensor.name.casefold()
        exact = names.get(name)
        if exact is not None:
            return exact, sensor.sensor_id
        for index, preferred in enumerate(priorities):
            if preferred.casefold() in name:
                return len(priorities) + index, sensor.sensor_id
        return len(priorities) * 2, sensor.sensor_id

    return min(sensors, key=rank)


def _gpu_rank(gpu: LhmGpu) -> Tuple[int, str]:
    ident = gpu.hardware_id.casefold()
    name = gpu.hardware_name.casefold()
    if "nvidia" in ident or "nvidia" in name:
        return 0, ident
    integrated = any(token in name for token in ("integrated", " igpu", "radeon(tm) graphics"))
    if not integrated and ("amd" in ident or "radeon" in name):
        return 1, ident
    if "intel" in ident or "intel" in name:
        return 2, ident
    return 3, ident


def parse_snapshot(data: Any) -> LhmSnapshot:
    """Convert LHM's nested JSON tree into the readings NetSpeedTray uses."""
    sensors = list(_iter_sensors(data))
    cpu_temps = [
        sensor for sensor in sensors
        if sensor.sensor_type == "temperature"
        and _belongs_to(sensor, _CPU_PREFIXES)
        and 0.0 < sensor.value < 150.0
    ]
    cpu_powers = [
        sensor for sensor in sensors
        if sensor.sensor_type == "power"
        and _belongs_to(sensor, _CPU_PREFIXES)
        and 0.0 < sensor.value < 1000.0
    ]
    cpu_temp = _pick(cpu_temps, (
        "Core (Tctl/Tdie)", "CPU Package", "Tctl/Tdie", "Tdie",
        "Tctl", "Package", "CCD1 (Tdie)", "CCD", "Core",
    ))
    cpu_power = _pick(cpu_powers, ("CPU Package", "Package", "CPU Total", "Total"))

    grouped: dict[Tuple[str, str], list[LhmSensor]] = {}
    for sensor in sensors:
        if not _belongs_to(sensor, _GPU_PREFIXES):
            continue
        hardware_id = sensor.hardware_id or sensor.sensor_id.rsplit("/", 2)[0]
        grouped.setdefault((hardware_id, sensor.hardware_name), []).append(sensor)

    gpus = []
    for (hardware_id, hardware_name), gpu_sensors in grouped.items():
        temps = [s for s in gpu_sensors if s.sensor_type == "temperature" and 0.0 < s.value < 150.0]
        powers = [s for s in gpu_sensors if s.sensor_type == "power" and 0.0 < s.value < 1000.0]
        temp = _pick(temps, ("GPU Core", "Core", "GPU Hot Spot", "Hot Spot"))
        power = _pick(powers, ("GPU Package", "GPU Total", "Total Board Power", "Board Power", "GPU Core"))
        if temp is not None or power is not None:
            gpus.append(LhmGpu(
                hardware_id=hardware_id,
                hardware_name=hardware_name,
                temperature=temp.value if temp else None,
                temperature_sensor=temp.name if temp else None,
                power=power.value if power else None,
                power_sensor=power.name if power else None,
            ))

    return LhmSnapshot(
        cpu_temp=cpu_temp.value if cpu_temp else None,
        cpu_temp_sensor=cpu_temp.name if cpu_temp else None,
        cpu_power=cpu_power.value if cpu_power else None,
        cpu_power_sensor=cpu_power.name if cpu_power else None,
        gpus=tuple(sorted(gpus, key=_gpu_rank)),
    )


class LhmHttpClient:
    """Small, failure-tolerant client with a per-poll-sized cache."""

    def __init__(
        self,
        url: str = DEFAULT_URL,
        timeout: float = DEFAULT_TIMEOUT_SEC,
        cache_seconds: float = DEFAULT_CACHE_SEC,
    ) -> None:
        self.url = url
        self.timeout = timeout
        self.cache_seconds = cache_seconds
        self._cached_at = 0.0
        self._cached: Optional[LhmSnapshot] = None

    def get_snapshot(self) -> Optional[LhmSnapshot]:
        now = time.monotonic()
        if self._cached is not None and now - self._cached_at < self.cache_seconds:
            return self._cached
        try:
            with urllib.request.urlopen(self.url, timeout=self.timeout) as response:
                data = json.loads(response.read().decode("utf-8-sig"))
            snapshot = parse_snapshot(data)
        except (OSError, ValueError, TypeError, UnicodeError):
            return None
        self._cached = snapshot
        self._cached_at = now
        return snapshot

