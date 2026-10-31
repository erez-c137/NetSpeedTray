"""LibreHardwareMonitor HTTP sensor source.

LibreHardwareMonitor 0.9.5 and newer expose sensor data through the built-in
web server instead of WMI.  This module intentionally depends only on the
Python standard library and quietly returns ``None`` when the endpoint is not
available, allowing callers to retain their existing WMI/native fallbacks.
"""

from __future__ import annotations

import base64
import http.client
import json
import logging
import math
import re
import socket
import time
from dataclasses import dataclass
from typing import Any, Iterator, Optional, Tuple

from netspeedtray import constants


logger = logging.getLogger("NetSpeedTray.LhmHttp")

_NUMBER_RE = re.compile(r"[-+]?(?:\d+(?:[.,]\d+)?|[.,]\d+)")
_CPU_PREFIXES = ("/amdcpu/", "/intelcpu/")
_GPU_PREFIXES = (
    "/gpu-nvidia/", "/gpu-amd/", "/gpu-intel/", "/gpu-intel-integrated/",
    "/nvidiagpu/", "/amdgpu/", "/intelgpu/",
)
# AMD APU graphics: "AMD Radeon(TM) Graphics", "Radeon Vega 8 Graphics", "Radeon 780M Graphics",
# "Radeon 890M". Discrete cards are "RX ...", "Pro ...", "VII" and never end in a model number + M.
_AMD_IGPU_MODEL_RE = re.compile(r"\b\d{3,4}m\b")
_AMD_DGPU_TOKENS = ("rx", "pro", "vii")
_CPU_TEMP_PRIORITY = (
    "Core (Tctl/Tdie)", "CPU Package", "Tctl/Tdie", "Tdie",
    "Tctl", "Package", "Core Max", "CCD1 (Tdie)", "CCD",
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

    def gpu_for(self, vendor_id: int, name: str = "",
                integrated: Optional[bool] = None) -> Optional[LhmGpu]:
        """The GPU group for the adapter the user picked (#236), or None - "no sensor of THIS GPU",
        never another card's reading.

        Same maker only (hardware id or name). Among those: the one LHM names exactly as DXGI does;
        else the one on the same side of integrated / discrete (``integrated``, from DXGI's dedicated
        memory; LHM says so in its id - /gpu-intel-integrated/ - or the AMD APU name). Two or more
        candidates left (two identical cards, two same-maker GPUs LHM cannot tell apart): None."""
        from netspeedtray.core.gpu_adapters import sensor_matches_vendor
        same = [g for g in self.gpus
                if sensor_matches_vendor(g.hardware_id, vendor_id)
                or sensor_matches_vendor(g.hardware_name, vendor_id)]
        wanted = (name or "").strip().casefold()
        named = [g for g in same if wanted and g.hardware_name.strip().casefold() == wanted]
        if len(named) == 1:
            return named[0]
        if named:
            return None
        if integrated is not None:
            same = [g for g in same if lhm_gpu_is_integrated(g) == integrated]
        return same[0] if len(same) == 1 else None


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


def _raw_number(value: Any) -> Optional[float]:
    """A JSON number only (bools, strings, null and non-finite values are not readings)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _is_fahrenheit(raw: str) -> bool:
    text = raw.strip().casefold()
    return text.endswith("°f") or text.endswith("℉") or text.endswith(" f")


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
        if not sensor_id or not sensor_type:
            continue
        # Current LHM also publishes RawValue: the unformatted reading as a JSON number (always
        # Celsius/Watts, no locale). Prefer it; older builds only have the display string.
        value = _raw_number(node.get("RawValue"))
        if value is None:
            raw_value = node.get("Value")
            value = parse_lhm_value(raw_value)
            if value is None:
                continue
            if sensor_type == "temperature" and isinstance(raw_value, str) and _is_fahrenheit(raw_value):
                # LHM can be set to display Fahrenheit, and Value carries the display string.
                value = (value - 32.0) * 5.0 / 9.0
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


def _pick(sensors: list[LhmSensor], priorities: Tuple[str, ...],
          require_match: bool = False) -> Optional[LhmSensor]:
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

    best = min(sensors, key=rank)
    if require_match and rank(best)[0] >= len(priorities) * 2:
        return None
    return best


def _pick_cpu_temp(sensors: list[LhmSensor]) -> Optional[LhmSensor]:
    """Package/Tctl-style die sensor first, then Core Max, then the HOTTEST core - never Core #1 by
    accident of ordering, and never a "Distance to TjMax" (a headroom, not a temperature)."""
    sensors = [s for s in sensors if "distance to tjmax" not in s.name.casefold()]
    named = _pick(sensors, _CPU_TEMP_PRIORITY, require_match=True)
    if named is not None:
        return named
    cores = [s for s in sensors
             if "core" in s.name.casefold() and "average" not in s.name.casefold()]
    if cores:
        return max(cores, key=lambda s: (s.value, s.sensor_id))
    return _pick(sensors, _CPU_TEMP_PRIORITY)


def _is_amd_igpu(name: str) -> bool:
    if "radeon" not in name:
        return False
    if any(token in name for token in ("integrated", " igpu", "radeon(tm) graphics")):
        return True
    # Discrete words win over the model-number pattern: laptop dGPUs carry an "M" model too
    # ("RX 6800M", "RX 7600M XT", "Pro W6600M").
    words = set(re.findall(r"[a-z0-9]+", name))
    if words.intersection(_AMD_DGPU_TOKENS):
        return False
    if _AMD_IGPU_MODEL_RE.search(name):
        return True
    return "graphics" in words


def lhm_gpu_is_integrated(gpu: LhmGpu) -> bool:
    """LHM's own word that a GPU group is integrated: its id (/gpu-intel-integrated/) or name."""
    ident = gpu.hardware_id.casefold()
    name = gpu.hardware_name.casefold()
    return "integrated" in ident or " igpu" in name or "integrated" in name or _is_amd_igpu(name)


def _gpu_rank(gpu: LhmGpu) -> Tuple[int, str]:
    ident = gpu.hardware_id.casefold()
    name = gpu.hardware_name.casefold()
    if "nvidia" in ident or "nvidia" in name:
        return 0, ident
    integrated = ("integrated" in ident or " igpu" in name or "integrated" in name
                  or _is_amd_igpu(name))
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
    cpu_temp = _pick_cpu_temp(cpu_temps)
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
        power = _pick(powers, ("GPU Package", "GPU Total", "Total Board Power", "Board Power", "GPU PPT", "GPU Core"))
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


class LhmHttpStatusError(ValueError):
    """The port answered HTTP, but not with data.json (LHM auth on, wrong interface, another app).

    The message is the status only - never a header, so a failure line cannot carry credentials."""

    def __init__(self, status: int) -> None:
        super().__init__(f"HTTP {status}")
        self.status = status


def _require_lhm_root(data: Any) -> Any:
    """LHM's data.json root is {"Text": "Sensor", "Children": [...]}. Anything else on the port is
    not LHM, and must back off like any other failure rather than parse as an empty success."""
    if (not isinstance(data, dict) or data.get("Text") != "Sensor"
            or not isinstance(data.get("Children"), list)):
        raise ValueError("not a LibreHardwareMonitor data.json")
    return data


class _DeadlineSocket(socket.socket):
    """A TCP socket whose every read gets only what is left of one overall deadline.

    A plain socket timeout applies per read, so a server that trickles one byte at a time can
    hold the caller for (bytes x timeout). http.client reads through ``makefile()``, which calls
    ``recv_into`` on this object - re-arming the timeout there bounds the status line, headers
    and body together.
    """

    deadline: float = 0.0

    def _arm(self) -> None:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("LHM HTTP request exceeded its deadline")
        self.settimeout(remaining)

    def recv_into(self, *args: Any, **kwargs: Any) -> int:
        self._arm()
        return super().recv_into(*args, **kwargs)

    def recv(self, *args: Any, **kwargs: Any) -> bytes:
        self._arm()
        return super().recv(*args, **kwargs)


class _DeadlineHTTPConnection(http.client.HTTPConnection):
    """HTTPConnection on a _DeadlineSocket. http.client never consults proxy settings, so a
    system or corporate proxy can never see (or slow down) a loopback sensor read."""

    def __init__(self, host: str, port: int, connect_timeout: float, deadline: float) -> None:
        super().__init__(host, port, timeout=connect_timeout)
        self._deadline = deadline

    def connect(self) -> None:
        sock = _DeadlineSocket(socket.AF_INET, socket.SOCK_STREAM)
        sock.deadline = self._deadline
        try:
            sock.settimeout(max(0.001, min(self.timeout, self._deadline - time.monotonic())))
            sock.connect((self.host, self.port))
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except BaseException:
            sock.close()
            raise
        self.sock = sock


class LhmHttpClient:
    """Small client with a per-poll cache and a quiet failure backoff.

    ``get_snapshot()`` never raises and never blocks longer than ``total_deadline`` seconds.

    Optional HTTP Basic credentials (LHM's Options > Remote Web Server > Authentication) are sent only
    when ``host`` is loopback (``constants.lhm.AUTH_ALLOWED_HOSTS``), and are never logged. ``status``
    says what the last attempt found (``constants.lhm.STATUS_*``) for Settings > Hardware.
    """

    def __init__(
        self,
        host: str = constants.lhm.HTTP_HOST,
        port: int = constants.lhm.HTTP_PORT,
        path: str = constants.lhm.HTTP_PATH,
        connect_timeout: float = constants.lhm.CONNECT_TIMEOUT_SEC,
        total_deadline: float = constants.lhm.TOTAL_DEADLINE_SEC,
        cache_seconds: float = constants.lhm.CACHE_SEC,
        failure_backoff_seconds: float = constants.lhm.FAILURE_BACKOFF_SEC,
        max_response_bytes: int = constants.lhm.MAX_RESPONSE_BYTES,
        username: str = "",
        password: str = "",
    ) -> None:
        self.host = host
        self.port = port
        self._username = username or ""
        self._password = password or ""
        self.path = path
        self.connect_timeout = connect_timeout
        self.total_deadline = total_deadline
        self.cache_seconds = cache_seconds
        self.failure_backoff_seconds = failure_backoff_seconds
        self.max_response_bytes = max_response_bytes
        self._cached_at = 0.0
        self._cached: Optional[LhmSnapshot] = None
        self._retry_after = 0.0
        self._last_failure: Optional[str] = None
        self.status: str = constants.lhm.STATUS_UNKNOWN

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}{self.path}"

    @property
    def has_credentials(self) -> bool:
        return bool(self._username or self._password)

    def configure(self, port: int, username: str = "", password: str = "") -> None:
        """Apply Settings > Hardware's port and credentials. Call on the polling thread (run() does).

        A change drops the cached reading and the backoff, so the next poll tries the new settings
        at once. The failure-log dedupe is kept: retyping a wrong password logs nothing new."""
        username, password = username or "", password or ""
        if (port, username, password) == (self.port, self._username, self._password):
            return
        self.port = port
        self._username, self._password = username, password
        self._cached = None
        self._retry_after = 0.0
        self.status = constants.lhm.STATUS_UNKNOWN

    def reset_backoff(self) -> None:
        """Forget a remembered failure so the next call tries the endpoint again."""
        self._retry_after = 0.0

    def _request_headers(self) -> dict:
        headers = {"Accept": "application/json"}
        # Loopback only: the password must never leave the machine (G2).
        if self.has_credentials and self.host in constants.lhm.AUTH_ALLOWED_HOSTS:
            # RFC 7617 with UTF-8; LHM (.NET HttpListener) compares the decoded name and password.
            token = base64.b64encode(f"{self._username}:{self._password}".encode("utf-8"))
            headers["Authorization"] = "Basic " + token.decode("ascii")
        return headers

    def _fetch_json(self) -> Any:
        conn = _DeadlineHTTPConnection(
            self.host, self.port, self.connect_timeout,
            deadline=time.monotonic() + self.total_deadline)
        try:
            conn.request("GET", self.path, headers=self._request_headers())
            response = conn.getresponse()
            if response.status != 200:
                raise LhmHttpStatusError(response.status)
            body = response.read(self.max_response_bytes + 1)
            if len(body) > self.max_response_bytes:
                raise ValueError("response too large")
            return _require_lhm_root(json.loads(body.decode("utf-8-sig")))
        finally:
            conn.close()

    def get_snapshot(self) -> Optional[LhmSnapshot]:
        now = time.monotonic()
        if self._cached is not None and now - self._cached_at < self.cache_seconds:
            return self._cached
        if now < self._retry_after:
            return None
        try:
            snapshot = parse_snapshot(self._fetch_json())
        except Exception as e:
            # Deliberately broad: this is an optional, best-effort source polled from the monitor
            # thread, where an escaping error drops the whole tick (network speeds included). Besides
            # OSError (refused, timeout, deadline) and ValueError (bad JSON, non-200), whatever else
            # answers on the port can raise http.client.HTTPException (BadStatusLine, IncompleteRead,
            # LineTooLong...), and a pathological tree can raise RecursionError.
            # CPU temperature, CPU power and GPU polling share this client. Remember the failure so
            # an unavailable/foreign endpoint costs one bounded attempt per backoff window, not one
            # per metric on every poll.
            self._retry_after = now + self.failure_backoff_seconds
            self._cached = None
            self.status = self._status_for(e)
            # Port in the key: moving to another port that fails the same way is a new line in the log.
            reason = f"{type(e).__name__}: {e} ({self.status}, port {self.port})"
            if reason != self._last_failure:
                self._last_failure = reason
                self._log_failure(e, reason)
            return None
        if self._last_failure is not None or self._cached is None:
            # Once per transition to "available" - a line the support bundle should show.
            logger.info("Hardware monitor: reading the LibreHardwareMonitor web server at %s.", self.url)
        self._last_failure = None
        self._cached = snapshot
        self._cached_at = now
        self._retry_after = 0.0
        self.status = constants.lhm.STATUS_OK
        return snapshot

    def _status_for(self, e: BaseException) -> str:
        L = constants.lhm
        if isinstance(e, LhmHttpStatusError):
            if e.status == 401:
                return L.STATUS_AUTH_FAILED if self.has_credentials else L.STATUS_AUTH_REQUIRED
            return L.STATUS_OTHER
        if isinstance(e, OSError):   # refused, unreachable, timed out, deadline: nothing answered
            return L.STATUS_NOT_FOUND
        return L.STATUS_OTHER        # bad JSON, not LHM's tree, a broken HTTP reply, too large

    def _log_failure(self, e: BaseException, reason: str) -> None:
        """One line per distinct failure. Something serving HTTP there and refusing us is worth an
        INFO line in the support log; nothing listening stays at DEBUG. Never logs credentials -
        not even the user name."""
        L = constants.lhm
        if self.status == L.STATUS_AUTH_REQUIRED:
            logger.info(
                "Hardware monitor: %s asks for a user name and password (HTTP 401). If that is "
                "LibreHardwareMonitor with web-server authentication on, enter the same user name and "
                "password in Settings > Hardware.", self.url)
        elif self.status == L.STATUS_AUTH_FAILED:
            # LHM 0.9.5/0.9.6 save the password's SHA-256 and hash it AGAIN when they start, so after
            # every LHM restart the password the user chose stops working (fixed on LHM master).
            logger.info(
                "Hardware monitor: %s rejected the configured user name or password (HTTP 401). "
                "LibreHardwareMonitor 0.9.6 and older change their stored password every time they "
                "restart - set it again in LHM (Options > Remote Web Server > Authentication).",
                self.url)
        elif isinstance(e, LhmHttpStatusError):
            # One bound to a single LAN address answers 400 (http.sys) for 127.0.0.1.
            logger.info(
                "Hardware monitor: %s answered HTTP %d. If that is LibreHardwareMonitor, check its "
                "Remote Web Server port; up to v0.9.6 it must listen on all interfaces to answer "
                "127.0.0.1 (do not open port %d in the firewall), and once a newer LHM offers "
                "127.0.0.1, choose that.", self.url, e.status, self.port)
        else:
            logger.debug("LHM HTTP source unavailable at %s (%s); retrying in %.0f s.",
                         self.url, reason, self.failure_backoff_seconds)
