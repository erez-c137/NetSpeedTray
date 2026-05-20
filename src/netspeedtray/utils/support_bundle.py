"""
Support Bundle generator.

Bundles the user's log files + sanitized config + system info into a single
zip that they can attach to a GitHub issue. Designed to make bug reports
self-contained so triage doesn't stall waiting for log/config follow-ups.

Privacy:
- Log files are passed through ObfuscatingFormatter one final time before
  zipping (belt-and-suspenders -- the file handler already obfuscates as it
  writes, but a second pass catches any future logging-setup mistakes).
- Config has its window position memory stripped (settings_window_pos)
  since position coordinates are mildly fingerprinting.
- App Activity data is NEVER included (per v1.3.2 PII audit) -- it's the
  highest-PII data the app touches.
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


# Config keys that get stripped during sanitization. These are not strictly
# PII but they reveal coordinate-level placement of the user's windows on
# their monitors, which has weak fingerprinting value and zero diagnostic
# value for most bug reports.
# Mildly-fingerprinting coordinate keys. Stripped from the bundled config
# because they reveal exactly where the user placed each window on their
# monitor — interesting for a fingerprinter, zero diagnostic value for us.
_CONFIG_KEYS_TO_STRIP: tuple = (
    "settings_window_pos",
    "graph_window_pos",
    "position_x",
    "position_y",
)


def _sanitize_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """Returns a copy of the config with mildly-sensitive keys removed."""
    sanitized = dict(config)
    for key in _CONFIG_KEYS_TO_STRIP:
        sanitized.pop(key, None)
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

    # Monitor layout — count + resolution + DPI only, no display names.
    app = QGuiApplication.instance()
    if app is not None:
        screens = app.screens()
        lines.append(f"Monitors detected:    {len(screens)}")
        for i, screen in enumerate(screens, start=1):
            geom = screen.geometry()
            avail = screen.availableGeometry()
            primary_tag = " (primary)" if screen == app.primaryScreen() else ""
            lines.append(
                f"  Monitor {i}{primary_tag}: "
                f"{geom.width()}x{geom.height()} @ DPI scale {screen.devicePixelRatio():.2f}, "
                f"available {avail.width()}x{avail.height()}"
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
            bundled-config filename only. Not read directly — we use the
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
            "  config.json       - Your settings, with window position memory stripped\n"
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
