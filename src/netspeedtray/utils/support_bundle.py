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
  bug reports (#133, #138).
- App Activity data is NEVER included -- it's the highest-PII data the
  app touches (per-process names + remote IPs/ports).
- System info contains versions and monitor sizes only -- no display names,
  no hostname, no GPU model.
"""

from __future__ import annotations

import json
import logging
import platform
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

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
#     MANIFEST explicitly promises "network interface friendly names" are NOT included.
#   - latency_public_host is a user-chosen ping target (a public hostname/IP) - reveals network choices.
# We keep the diagnostic SHAPE (how many interfaces, that a host was set) but never the literal name.
# IMPORTANT: any NEW config field that can hold a name/path/host/ID must be added here.
_CONFIG_LIST_KEYS_TO_REDACT: tuple = ("selected_interfaces", "excluded_interfaces")
_CONFIG_VALUE_KEYS_TO_REDACT: tuple = ("latency_public_host",)


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
    else:
        lines.append("Monitors detected:    <Qt application not running>")

    return "\n".join(lines) + "\n"


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


def _list_log_files() -> List[Path]:
    """Returns all NetSpeedTray log files (current + rotated backups)."""
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

    log_files = _list_log_files()

    with zipfile.ZipFile(destination_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        # 1. System info
        zf.writestr("system_info.txt", system_info)

        # 2. Sanitized config (pretty-printed for human readability)
        zf.writestr(
            "config.json",
            json.dumps(sanitized_config, indent=2, ensure_ascii=False, sort_keys=True),
        )

        # 3. Log files (each scrubbed through ObfuscatingFormatter again)
        for log_path in log_files:
            try:
                raw = log_path.read_text(encoding="utf-8", errors="replace")
            except OSError as e:
                logger.warning("Could not read log %s: %s", log_path.name, e)
                continue
            scrubbed = _scrub_log_text(raw)
            zf.writestr(f"logs/{log_path.name}", scrubbed)

        # 4. Manifest
        manifest = (
            "NetSpeedTray Support Bundle\n"
            f"Generated: {datetime.now().isoformat(timespec='seconds')}\n"
            "\n"
            "Contents:\n"
            "  system_info.txt   - App version, OS, monitor layout (no display names)\n"
            "  config.json       - Your settings (preferences only, no PII)\n"
            "  logs/             - Log files, scrubbed for paths/IPs/MACs/GUIDs/hostnames\n"
            "\n"
            "NOT included:\n"
            "  - App Activity per-process / per-connection data\n"
            "  - Hostname, MAC addresses, network interface friendly names\n"
            "  - Full GPU model strings, raw device IDs\n"
            "\n"
            "It is safe to attach this file to a GitHub issue.\n"
        )
        zf.writestr("MANIFEST.txt", manifest)

    logger.info(
        "Support bundle built: %s (%d log files included)",
        destination_zip.name, len(log_files),
    )
    return destination_zip
