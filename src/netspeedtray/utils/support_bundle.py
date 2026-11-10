"""
Support Bundle generator.

Bundles the user's log files + config + system info into a single zip that
they can attach to a GitHub issue. Designed to make bug reports self-
contained so triage doesn't stall waiting for log/config follow-ups.

Privacy:
- Log files are passed through ObfuscatingFormatter one final time before
  zipping (belt-and-suspenders -- the file handler already obfuscates as it
  writes, but a second pass catches any future logging-setup mistakes).
- Config is bundled as-is. It contains only user preferences -- no PII.
  Window-position coordinates (`position_x`, `position_y`, etc.) ARE
  included; they're exactly the diagnostic data we need for multi-monitor
  bug reports (#133, #138). The picked GPU (`gpu_adapter`) is redacted.
- GPU model strings in logs are replaced with <gpu-N> placeholders at export
  time (the local log keeps them - it is the user's own).
- App Activity data is NEVER included -- it's the highest-PII data the
  app touches (per-process names + remote IPs/ports).
- System info contains versions and monitor sizes only -- no display names,
  no hostname, no GPU model.
"""

from __future__ import annotations

import ast
import json
import logging
import platform
import re
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PyQt6.QtGui import QGuiApplication

from netspeedtray import constants
from netspeedtray.utils.config import ObfuscatingFormatter
from netspeedtray.utils.helpers import get_app_data_path

logger = logging.getLogger("NetSpeedTray.SupportBundle")


# Config keys to strip from the bundled config.
#
# Currently empty. We deliberately do NOT strip window-position coordinates
# (`position_x`, `position_y`, `graph_window_pos`, `settings_window_pos`):
# they are exactly the diagnostic signal we need for multi-monitor / placement
# bugs (e.g. #133, #138 - `position_x = -1920` literally means "user wants
# widget on the monitor to the left of primary"). Window coordinates are not
# PII; they don't identify a user.
#
# Actual PII (paths, IPs, hostnames, MACs) lives in logs, not config - and
# that's handled by ObfuscatingFormatter in the logs/ section of the bundle.
#
# Add keys here only if a future config field genuinely contains identifying
# data (e.g. an authentication token, a username, a remote URL).
_CONFIG_KEYS_TO_STRIP: tuple = ()

# Keys whose VALUES can carry a user's own labels and so MUST NOT ship verbatim:
#   - selected_interfaces / excluded_interfaces hold Windows NIC *friendly names*, which users freely
#     rename ("Office VPN", a site/company label, a hostname-bearing virtual adapter). The bundle's
#     MANIFEST promises those names never ship verbatim: config values are redacted outright here,
#     and log lines are pseudonymized by ObfuscatingFormatter's message-shape rules (v2.1.5 item 7).
#   - latency_public_host is a user-chosen ping target (a public hostname/IP) - reveals network choices.
#   - lhm_username / lhm_password_protected are the LibreHardwareMonitor web-server credentials (G2). The
#     password is only ever a per-user DPAPI blob, useless off this account - but a credential never
#     ships in any form. "<redacted>" still says that one is set (useful when triaging a 401).
#   - gpu_adapter is the picked GPU's model string ("" = Automatic). The MANIFEST promises no GPU model;
#     "<redacted-gpu>" still tells triage that a specific card was picked.
# We keep the diagnostic SHAPE (how many interfaces, that a host was set) but never the literal name.
# IMPORTANT: any NEW config field that can hold a name/path/host/ID must be added here.
_CONFIG_LIST_KEYS_TO_REDACT: tuple = ("selected_interfaces", "excluded_interfaces")
_CONFIG_VALUE_KEYS_TO_REDACT: tuple = ("latency_public_host", "lhm_username", "lhm_password_protected")
_CONFIG_GPU_KEY = "gpu_adapter"


def _sanitize_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """Returns a copy of the config safe to attach to a public issue.

    Drops `_CONFIG_KEYS_TO_STRIP` outright and redacts the user-label-bearing keys
    (`_CONFIG_LIST_KEYS_TO_REDACT` / `_CONFIG_VALUE_KEYS_TO_REDACT`) to placeholders - honoring the
    MANIFEST's promise that NIC friendly names never leave the machine, while preserving the count/shape
    that's actually useful for diagnosing interface-mode and latency bugs.
    """
    sanitized = dict(config)
    for key in _CONFIG_KEYS_TO_STRIP:
        sanitized.pop(key, None)
    for key in _CONFIG_LIST_KEYS_TO_REDACT:
        value = sanitized.get(key)
        if isinstance(value, list) and value:
            sanitized[key] = [f"<redacted-{i + 1}>" for i in range(len(value))]
    for key in _CONFIG_VALUE_KEYS_TO_REDACT:
        if sanitized.get(key):
            sanitized[key] = "<redacted>"
    if sanitized.get(_CONFIG_GPU_KEY):
        sanitized[_CONFIG_GPU_KEY] = "<redacted-gpu>"
    # A rollback carries keys written by a NEWER build (preserved by config item 4c); the
    # allowlists above structurally cannot know them, so their VALUES must not ship (review
    # C8). The key name stays - THAT a newer-build setting exists is the useful diagnostic.
    known_keys = set(constants.config.defaults.VALIDATION_SCHEMA)
    for key in list(sanitized):
        if key not in known_keys:
            sanitized[key] = "<redacted-unknown-key>"
    return sanitized


def _collect_system_info(config: Dict[str, Any]) -> str:
    """Builds a human-readable system info report.

    Intentionally excludes: hostname, full display names, GPU model string,
    network interface names, MAC addresses. Includes only what's useful for
    diagnosing layout / behavior bugs.
    """
    lines: List[str] = []
    lines.append(f"NetSpeedTray version: {constants.app.VERSION}")
    lines.append(f"Bundle generated:     {datetime.now().isoformat(timespec='seconds')}")
    lines.append("")
    lines.append(f"OS:                   {platform.platform()}")
    lines.append(f"Architecture:         {platform.machine()}")
    lines.append(f"Python:               {sys.version.split()[0]}")
    lines.append(f"Frozen build:         {getattr(sys, 'frozen', False)}")
    # #308: an exe flagged "Run as administrator" is silently skipped at sign-in, and an elevated
    # run is how a config ends up writable only by admins (#307). Both belong in every bundle.
    from netspeedtray.core.startup_manager import (get_compat_layers, get_startup_approved_state,
                                                   is_process_elevated)
    lines.append(f"Process elevated:     {is_process_elevated()}")
    lines.append(f"Compatibility flags:  {get_compat_layers(sys.executable) or '<none>'}")
    lines.append(f"Task Manager startup: {get_startup_approved_state(constants.app.APP_NAME)}")
    lines.append("")
    lines.append(f"Configured language:  {config.get('language', '<unset>')}")
    lines.append(f"Configured update_rate: {config.get('update_rate', '<unset>')}s")
    lines.append(f"Interface mode:       {config.get('interface_mode', '<unset>')}")
    lines.append(f"Free move enabled:    {config.get('free_move', False)}")
    lines.append("")

    # Monitor layout - count + resolution + DPI only, no display names.
    app = QGuiApplication.instance()
    if app is not None:
        screens = app.screens()
        lines.append(f"Monitors detected:    {len(screens)}")
        for i, screen in enumerate(screens, start=1):
            geom = screen.geometry()
            avail = screen.availableGeometry()
            dpr = screen.devicePixelRatio()
            primary_tag = " (primary)" if screen == app.primaryScreen() else ""
            # geometry() is in Qt LOGICAL pixels; scale by the DPI ratio for the native
            # panel resolution so a high-DPI display isn't mislabeled (issue #152).
            native_w, native_h = round(geom.width() * dpr), round(geom.height() * dpr)
            lines.append(
                f"  Monitor {i}{primary_tag}: "
                f"{native_w}x{native_h} native ({geom.width()}x{geom.height()} logical) "
                f"@ DPI scale {dpr:.2f}, "
                f"available {avail.width()}x{avail.height()} logical"
            )
        lines.extend(_taskbar_geometry_lines(config))
    else:
        lines.append("Monitors detected:    <Qt application not running>")

    return "\n".join(lines) + "\n"


def _taskbar_geometry_lines(config: Dict[str, Any]) -> List[str]:
    """The docked taskbar's geometry as Windows and Qt report it - what placement bugs turn on (#322).

    Rects and sizes only (no window titles or display names): the Shell_TrayWnd rect, its monitor and
    work area (physical px), Qt's available-geometry inset, the detected edge and DPI, and the strip
    the widget is sized and centred in. Never raises - a failure becomes one line.
    """
    try:
        from netspeedtray.utils.taskbar_utils import get_taskbar_info, horizontal_taskbar_strip
        tb = get_taskbar_info(config.get("preferred_monitor"))
        edge = tb.get_edge_position()
        out = [
            f"Taskbar edge:         {edge.name if hasattr(edge, 'name') else edge}"
            f" (fallback: {tb.hwnd == 0}, primary: {tb.is_primary})",
            f"  window rect (phys): {tb.rect}  DPI scale {tb.dpi_scale:.2f}  logical height {tb.height}",
            f"  monitor (phys):     {tb.monitor_rect}  work area (phys): {tb.work_area}",
            f"  tray rect (phys):   {tb.get_tray_rect()}",
        ]
        screen = tb.get_screen()
        if screen is not None:
            g, a = screen.geometry(), screen.availableGeometry()
            out.append(f"  Qt insets (logical): top {a.top() - g.top()}, bottom {g.bottom() - a.bottom()}, "
                       f"left {a.left() - g.left()}, right {g.right() - a.right()}")
        if edge in (constants.TaskbarEdge.TOP, constants.TaskbarEdge.BOTTOM):
            top, height = horizontal_taskbar_strip(tb, edge, tb.dpi_scale)
            out.append(f"  dock strip (logical): top {top}, height {height}")
        return out
    except Exception as e:  # noqa: BLE001 - diagnostics must never break the bundle
        return [f"Taskbar geometry:     <unavailable: {type(e).__name__}>"]


def _scrub_log_text(text: str) -> str:
    """Run log text through ObfuscatingFormatter as a second pass.

    The file handler already runs this on every record at write time, but
    a final scrub before zipping protects against:
    - Future contributors wiring up a non-obfuscating handler by mistake
    - Old log lines written by an earlier version with weaker obfuscation
      (relevant after the v1.3.2 PII audit that closed 6 gaps)
    """
    formatter = ObfuscatingFormatter("%(message)s")
    scrubbed_lines: List[str] = []
    for line in text.splitlines(keepends=True):
        # Wrap each line in a synthetic LogRecord so the formatter runs on it
        # without us having to replicate its internal substitution loop.
        record = logging.LogRecord(
            name="bundle.scrub",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg=line.rstrip("\r\n"),
            args=None,
            exc_info=None,
        )
        scrubbed_lines.append(formatter.format(record))
        if line.endswith("\r\n"):
            scrubbed_lines.append("\r\n")
        elif line.endswith("\n"):
            scrubbed_lines.append("\n")
    return "".join(scrubbed_lines)


# The INFO lines monitor_thread writes with GPU model names in them. Their names are harvested from the
# logs themselves too, so a card that is gone now (an unplugged eGPU, a swapped card) is still
# redacted from the older lines that named it.
_LOG_GPU_LIST_RE = re.compile(r"GPU adapters changed: (\[[^\r\n]*\])")
_LOG_PICKED_GPU_RE = re.compile(
    r"""Picked GPU ('(?:[^'\\\r\n]|\\.)*'|"(?:[^"\\\r\n]|\\.)*") is not present""")
# A real adapter name is never this short; a hand-edited one-letter `gpu_adapter` must not shred
# every log line that happens to contain that letter.
_GPU_NAME_MIN_LEN = 4


def _gpu_names_in_logs(texts: List[str]) -> List[str]:
    """GPU names from the "GPU adapters changed: [...]" and "Picked GPU '...'" log lines. A line
    that does not parse as the Python literal it was logged as is skipped (never raises)."""
    names: List[str] = []
    for text in texts:
        for match in _LOG_GPU_LIST_RE.finditer(text):
            try:
                value = ast.literal_eval(match.group(1))
            except (ValueError, SyntaxError, MemoryError, RecursionError):
                continue
            if isinstance(value, list):
                names.extend(v for v in value if isinstance(v, str))
        for match in _LOG_PICKED_GPU_RE.finditer(text):
            try:
                value = ast.literal_eval(match.group(1))
            except (ValueError, SyntaxError, MemoryError, RecursionError):
                continue
            if isinstance(value, str):
                names.append(value)
    return names


def _gpu_names_to_redact(config: Dict[str, Any]) -> List[str]:
    """Every GPU model string the logs can carry: the real DXGI adapters present now plus the
    config's picked GPU (which may be a card no longer installed). Never raises - a failed
    enumeration falls back to the config value alone."""
    names: List[str] = []
    try:
        from netspeedtray.core import gpu_adapters
        adapters = gpu_adapters.enumerate_adapters()
        if adapters:
            names.extend(a.name for a in gpu_adapters.hardware_adapters(adapters))
    except Exception as e:  # noqa: BLE001 - diagnostics must never break the bundle
        logger.debug("GPU enumeration for bundle redaction failed: %s", type(e).__name__)
    picked = config.get(_CONFIG_GPU_KEY)
    if isinstance(picked, str):
        names.append(picked)
    return names


def _build_gpu_redactor(names: List[str]):
    """A text -> text function replacing each GPU name with a stable <gpu-N> token.

    Same name (case and surrounding space ignored) -> same token; longest names are matched
    first so a name that is a substring of another ("Radeon Graphics" inside "AMD Radeon
    Graphics") cannot split the longer one. Numbering follows first appearance in `names`.
    """
    tokens: Dict[str, str] = {}
    for name in names:
        key = str(name).strip().lower()
        if len(key) >= _GPU_NAME_MIN_LEN and key not in tokens:
            tokens[key] = f"<gpu-{len(tokens) + 1}>"
    if not tokens:
        return lambda text: text
    by_length = sorted(tokens, key=len, reverse=True)
    pattern = re.compile("|".join(re.escape(k) for k in by_length), re.IGNORECASE)
    return lambda text: pattern.sub(lambda m: tokens.get(m.group(0).lower(), "<gpu>"), text)


def _list_log_files() -> List[Path]:
    """Returns all NetSpeedTray log files (current + rotated backups), plus the in-app updater's
    installer log when one exists - 2.1.6 started writing it precisely so bundles would carry it."""
    base = get_app_data_path()
    main = base / constants.logs.LOG_FILENAME
    files: List[Path] = []
    if main.exists():
        files.append(main)
    # RotatingFileHandler creates exactly .1 .. .LOG_BACKUP_COUNT backups.
    for i in range(1, constants.logs.LOG_BACKUP_COUNT + 1):
        rotated = base / f"{constants.logs.LOG_FILENAME}.{i}"
        if rotated.exists():
            files.append(rotated)
    installer_log = base / constants.logs.INSTALLER_LOG_SUBDIR / constants.logs.INSTALLER_LOG_FILENAME
    if installer_log.exists():
        files.append(installer_log)
    # The crash record (faulthandler + unhandled exceptions, armed before logging starts) and its
    # one rotated generation - the only trace a crash at logon leaves.
    from netspeedtray.crash_log import CRASH_LOG_FILENAME
    for name in (CRASH_LOG_FILENAME, CRASH_LOG_FILENAME + ".1"):
        crash = base / name
        if crash.exists():
            files.append(crash)
    return files


def build_support_bundle(
    destination_zip: Path,
    config: Dict[str, Any],
    config_source_path: Optional[Path] = None,
) -> Path:
    """Build a support bundle zip at `destination_zip`.

    Args:
        destination_zip: Where to write the .zip file.
        config: The current in-memory config dict (will be sanitized).
        config_source_path: Optional path to the on-disk config file for the
            bundled-config filename only. Not read directly - we use the
            in-memory dict so we get the user's *current* settings, not what
            they last saved.

    Returns the destination path (echo-back for the caller).
    """
    destination_zip = Path(destination_zip)
    sanitized_config = _sanitize_config(config)
    system_info = _collect_system_info(config)

    log_texts: List[Tuple[str, str]] = []
    for log_path in _list_log_files():
        try:
            log_texts.append((log_path.name, log_path.read_text(encoding="utf-8", errors="replace")))
        except OSError as e:
            logger.warning("Could not read log %s: %s", log_path.name, e)
    redact_gpu_names = _build_gpu_redactor(
        _gpu_names_to_redact(config) + _gpu_names_in_logs([text for _, text in log_texts]))

    with zipfile.ZipFile(destination_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        # 1. System info
        zf.writestr("system_info.txt", system_info)

        # 2. Sanitized config (pretty-printed for human readability)
        zf.writestr(
            "config.json",
            json.dumps(sanitized_config, indent=2, ensure_ascii=False, sort_keys=True),
        )

        # 3. Log files (each scrubbed through ObfuscatingFormatter again)
        for log_name, raw in log_texts:
            scrubbed = _scrub_log_text(redact_gpu_names(raw))
            zf.writestr(f"logs/{log_name}", scrubbed)

        # 4. Manifest
        manifest = (
            "NetSpeedTray Support Bundle\n"
            f"Generated: {datetime.now().isoformat(timespec='seconds')}\n"
            "\n"
            "Contents:\n"
            "  system_info.txt   - App version, OS, monitor layout (no display names),\n"
            "                      whether the app runs as admin, its Windows compatibility\n"
            "                      flags, and its Task Manager startup state\n"
            "  config.json       - Your settings (interface names, ping host, the picked\n"
            "                      GPU and the LibreHardwareMonitor user name/password\n"
            "                      redacted)\n"
            "  logs/             - Log files, including the in-app updater's installer log,\n"
            "                      scrubbed for paths/usernames/IPs/MACs/GUIDs/hostnames\n"
            "\n"
            "NOT included:\n"
            "  - App Activity per-process / per-connection data\n"
            "  - Hostname, MAC addresses, your Windows username\n"
            "  - Full GPU model strings, raw device IDs\n"
            "\n"
            "Replaced with placeholders (best-effort scrubbing):\n"
            "  - Network interface friendly names: redacted from the bundled config,\n"
            "    and replaced in logs with a stable placeholder (e.g. NIC-1a2b3c4d).\n"
            "    The placeholder is a one-way hash, so repeated log lines still\n"
            "    correlate but never contain the name.\n"
            "  - Display device names in logs (e.g. DISPLAY-1a2b3c4d).\n"
            "  - GPU model names in logs (e.g. <gpu-1>; the same GPU keeps the\n"
            "    same number within one bundle).\n"
            "\n"
            "It is safe to attach this file to a GitHub issue.\n"
        )
        zf.writestr("MANIFEST.txt", manifest)

    logger.info(
        "Support bundle built: %s (%d log files included)",
        destination_zip.name, len(log_texts),
    )
    return destination_zip
