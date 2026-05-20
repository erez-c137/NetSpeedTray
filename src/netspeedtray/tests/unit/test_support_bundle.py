"""
Tests for the Support Bundle exporter.

Covers:
- Zip structure (expected file names, presence of manifest)
- Config sanitization (window position memory stripped)
- Log scrubbing (PII redaction applied to bundled log content)
- App Activity exclusion (no app-activity data ever included)
"""
import json
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from netspeedtray.utils import support_bundle


@pytest.fixture
def fake_config():
    return {
        "language": "en_US",
        "update_rate": 1.0,
        "interface_mode": "auto",
        "free_move": True,
        "selected_interfaces": ["Ethernet", "Wi-Fi"],
        "default_color": "#FFFFFF",
        # Mildly-sensitive coordinate keys — all should be stripped from bundle.
        "settings_window_pos": {"x": 100, "y": 200},
        "graph_window_pos": {"x": 300, "y": 400},
        "position_x": 1500,
        "position_y": 40,
    }


@pytest.fixture
def fake_log_dir(tmp_path, monkeypatch):
    """Create a fake AppData with a log file, and patch get_app_data_path."""
    log_dir = tmp_path / "AppData" / "NetSpeedTray"
    log_dir.mkdir(parents=True)
    (log_dir / "NetSpeedTray_Log.log").write_text(
        "2026-05-20 10:00:00 - INFO - Connected to 192.168.1.42\n"
        "2026-05-20 10:00:01 - INFO - User home is C:\\Users\\Erez\\Documents\n"
        "2026-05-20 10:00:02 - INFO - Adapter GUID {12345678-1234-1234-1234-123456789012}\n"
        "2026-05-20 10:00:03 - INFO - MAC 00-1A-2B-3C-4D-5E\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(support_bundle, "get_app_data_path", lambda: log_dir)
    return log_dir


def _open_zip_entry(zip_path: Path, name: str) -> str:
    with zipfile.ZipFile(zip_path, "r") as zf:
        return zf.read(name).decode("utf-8")


class TestStructure:
    def test_bundle_contains_expected_files(self, q_app, tmp_path, fake_config, fake_log_dir):
        dest = tmp_path / "bundle.zip"
        support_bundle.build_support_bundle(dest, fake_config)
        with zipfile.ZipFile(dest, "r") as zf:
            names = set(zf.namelist())
        assert "system_info.txt" in names
        assert "config.json" in names
        assert "MANIFEST.txt" in names
        assert any(n.startswith("logs/") for n in names), names

    def test_manifest_documents_exclusions(self, q_app, tmp_path, fake_config, fake_log_dir):
        dest = tmp_path / "bundle.zip"
        support_bundle.build_support_bundle(dest, fake_config)
        manifest = _open_zip_entry(dest, "MANIFEST.txt")
        # The manifest must explicitly call out what's NOT included so a user
        # can verify privacy claims without reading source.
        assert "App Activity" in manifest
        assert "Hostname" in manifest
        assert "MAC" in manifest


class TestConfigSanitization:
    def test_all_coordinate_keys_are_stripped(self, q_app, tmp_path, fake_config, fake_log_dir):
        """All window-position / widget-coord keys must be removed from the bundle.

        Coordinates fingerprint where the user places windows on their monitors.
        They have weak identifying value but zero diagnostic value, so we strip them.
        """
        dest = tmp_path / "bundle.zip"
        support_bundle.build_support_bundle(dest, fake_config)
        bundled_config = json.loads(_open_zip_entry(dest, "config.json"))
        for stripped_key in ("settings_window_pos", "graph_window_pos", "position_x", "position_y"):
            assert stripped_key not in bundled_config, (
                f"{stripped_key!r} leaked into the support bundle"
            )

    def test_other_config_keys_preserved(self, q_app, tmp_path, fake_config, fake_log_dir):
        dest = tmp_path / "bundle.zip"
        support_bundle.build_support_bundle(dest, fake_config)
        bundled_config = json.loads(_open_zip_entry(dest, "config.json"))
        assert bundled_config["interface_mode"] == "auto"
        assert bundled_config["language"] == "en_US"
        assert bundled_config["selected_interfaces"] == ["Ethernet", "Wi-Fi"]
        # free_move and language should survive — they're diagnostic, not PII.
        assert bundled_config["free_move"] is True


class TestLogScrubbing:
    def test_ipv4_in_logs_is_redacted(self, q_app, tmp_path, fake_config, fake_log_dir):
        dest = tmp_path / "bundle.zip"
        support_bundle.build_support_bundle(dest, fake_config)
        log_content = _open_zip_entry(dest, "logs/NetSpeedTray_Log.log")
        assert "192.168.1.42" not in log_content
        assert "<REDACTED_IP>" in log_content

    def test_mac_in_logs_is_redacted(self, q_app, tmp_path, fake_config, fake_log_dir):
        dest = tmp_path / "bundle.zip"
        support_bundle.build_support_bundle(dest, fake_config)
        log_content = _open_zip_entry(dest, "logs/NetSpeedTray_Log.log")
        assert "00-1A-2B-3C-4D-5E" not in log_content
        assert "<REDACTED_MAC>" in log_content

    def test_guid_in_logs_is_redacted(self, q_app, tmp_path, fake_config, fake_log_dir):
        dest = tmp_path / "bundle.zip"
        support_bundle.build_support_bundle(dest, fake_config)
        log_content = _open_zip_entry(dest, "logs/NetSpeedTray_Log.log")
        assert "{12345678-1234-1234-1234-123456789012}" not in log_content
        assert "<REDACTED_GUID>" in log_content


class TestSystemInfo:
    def test_no_display_names_leaked(self, q_app, tmp_path, fake_config, fake_log_dir):
        """System info must include monitor resolutions but not display device names."""
        dest = tmp_path / "bundle.zip"
        support_bundle.build_support_bundle(dest, fake_config)
        sysinfo = _open_zip_entry(dest, "system_info.txt")
        # Must include known-safe fields
        assert "NetSpeedTray version" in sysinfo
        assert "OS:" in sysinfo
        # Must NOT include leaky fields
        assert "\\\\.\\DISPLAY" not in sysinfo  # Windows internal display device path
        # Hostname should not appear (we don't add it)
        import socket
        hostname = socket.gethostname()
        if hostname and len(hostname) > 3:
            assert hostname not in sysinfo


class TestAppActivityExclusion:
    def test_no_app_activity_files_in_bundle(self, q_app, tmp_path, fake_config, fake_log_dir):
        """Per the v1.3.2 PII audit, App Activity data must never be bundled."""
        dest = tmp_path / "bundle.zip"
        support_bundle.build_support_bundle(dest, fake_config)
        with zipfile.ZipFile(dest, "r") as zf:
            names = zf.namelist()
        forbidden = ("app_activity", "AppActivity", "connections", "psutil_net")
        for name in names:
            for token in forbidden:
                assert token not in name, f"forbidden token '{token}' in bundled path '{name}'"
