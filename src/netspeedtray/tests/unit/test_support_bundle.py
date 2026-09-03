"""
Tests for the Support Bundle exporter.

Covers:
- Zip structure (expected file names, presence of manifest)
- Config sanitization (window position memory stripped)
- Log scrubbing (PII redaction applied to bundled log content)
- App Activity exclusion (no app-activity data ever included)
"""
import json
import logging
import re
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
        # Mildly-sensitive coordinate keys - all should be stripped from bundle.
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
        # v2.1.5 item 7 (words half): the promise must match what the code does -
        # NIC/display names are pseudonymized best-effort, not silently absent.
        assert "placeholder" in manifest.lower()
        assert "NIC-" in manifest
        assert "best-effort" in manifest.lower()


class TestConfigSanitization:
    def test_coordinate_keys_are_preserved_for_diagnostics(self, q_app, tmp_path, fake_config, fake_log_dir):
        """Window-position coordinates MUST survive into the bundle.

        position_x / position_y are exactly the diagnostic signal we need for
        multi-monitor placement bugs (#133, #138). A value of `position_x = -1920`
        literally tells us "user wants widget on the monitor to the left of
        primary". Stripping them throws away debugging data without any privacy
        benefit - coordinates are not PII.
        """
        dest = tmp_path / "bundle.zip"
        support_bundle.build_support_bundle(dest, fake_config)
        bundled_config = json.loads(_open_zip_entry(dest, "config.json"))
        # All four coordinate keys must round-trip through the bundle unchanged.
        for diagnostic_key in ("settings_window_pos", "graph_window_pos", "position_x", "position_y"):
            assert diagnostic_key in bundled_config, (
                f"{diagnostic_key!r} is missing from the bundle - was it accidentally "
                f"added back to _CONFIG_KEYS_TO_STRIP? These keys are diagnostic, not PII."
            )
        assert bundled_config["position_x"] == 1500
        assert bundled_config["position_y"] == 40

    def test_other_config_keys_preserved(self, q_app, tmp_path, fake_config, fake_log_dir):
        dest = tmp_path / "bundle.zip"
        support_bundle.build_support_bundle(dest, fake_config)
        bundled_config = json.loads(_open_zip_entry(dest, "config.json"))
        assert bundled_config["interface_mode"] == "auto"
        assert bundled_config["language"] == "en_US"
        assert bundled_config["free_move"] is True

    def test_interface_friendly_names_are_redacted(self, q_app, tmp_path, fake_log_dir):
        """The MANIFEST promises NIC friendly names are NOT included and the bundle is 'safe to attach',
        but users rename adapters to personal/company labels. The raw config must never ship those names -
        only the redacted shape (how many) survives. Regression for the empty-denylist PII leak."""
        cfg = {
            "language": "en_US", "interface_mode": "selected",
            "selected_interfaces": ["Office VPN", "ACME-Site-Ethernet"],
            "excluded_interfaces": ["MyCompany Secret Adapter"],
            "latency_public_host": "ping.my-isp.example.com",
        }
        dest = tmp_path / "bundle.zip"
        support_bundle.build_support_bundle(dest, cfg)
        raw = _open_zip_entry(dest, "config.json")
        for leak in ("Office VPN", "ACME-Site-Ethernet", "MyCompany Secret Adapter", "ping.my-isp.example.com"):
            assert leak not in raw, f"PII '{leak}' leaked into the support bundle config"
        bundled = json.loads(raw)
        assert bundled["selected_interfaces"] == ["<redacted-1>", "<redacted-2>"]   # count preserved
        assert bundled["excluded_interfaces"] == ["<redacted-1>"]
        assert bundled["latency_public_host"] == "<redacted>"
        assert bundled["interface_mode"] == "selected"                              # mode (not a name) kept


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

    # --- v2.1.5 item 7: NIC friendly names in logs -------------------------
    # controller.py's "Primary network interface changed" INFO line shipped the
    # raw adapter friendly name (94 lines on a real machine's live log). The
    # scrub must clean those HISTORICAL lines - a source-only fix cannot - by
    # replacing each name with a STABLE pseudonym so repeated changes still
    # correlate (#263's edge-triggered logging depends on that).

    def test_primary_interface_change_lines_are_pseudonymized(self, q_app, tmp_path, fake_config, fake_log_dir):
        from netspeedtray.utils.config import stable_pseudonym
        # Real message shapes as written by shipped builds (%r operands).
        (fake_log_dir / "NetSpeedTray_Log.log").write_text(
            "2026-06-12 10:23:45 - NetSpeedTray.StatsController - INFO - "
            "controller._update_primary_interface_name:392 - "
            "Primary network interface changed: None -> 'ACME-HQ Office VPN'\n"
            "2026-06-12 11:00:01 - NetSpeedTray.StatsController - INFO - "
            "controller._update_primary_interface_name:392 - "
            "Primary network interface changed: 'ACME-HQ Office VPN' -> 'Wi-Fi 3'\n"
            "2026-06-12 12:34:56 - NetSpeedTray.StatsController - INFO - "
            "controller._update_primary_interface_name:392 - "
            "Primary network interface changed: 'Wi-Fi 3' -> 'ACME-HQ Office VPN'\n",
            encoding="utf-8",
        )
        dest = tmp_path / "bundle.zip"
        support_bundle.build_support_bundle(dest, fake_config)
        log_content = _open_zip_entry(dest, "logs/NetSpeedTray_Log.log")

        # Zero occurrences of the raw names may remain.
        assert "ACME-HQ Office VPN" not in log_content
        assert "Wi-Fi 3" not in log_content
        # The None operand keeps its shape.
        assert "Primary network interface changed: None ->" in log_content
        # Stable pseudonyms: the SAME adapter maps to the SAME token every time.
        vpn = stable_pseudonym("ACME-HQ Office VPN")
        wifi = stable_pseudonym("Wi-Fi 3")
        assert re.fullmatch(r"NIC-[0-9a-f]{8}", vpn)
        found = re.findall(r"NIC-[0-9a-f]{8}", log_content)
        assert found == [vpn, vpn, wifi, wifi, vpn]

    def test_already_pseudonymized_lines_pass_through_unchanged(self, q_app, tmp_path, fake_config, fake_log_dir):
        """Lines the NEW build writes (already pseudonymized) must not be re-hashed."""
        (fake_log_dir / "NetSpeedTray_Log.log").write_text(
            "2026-08-20 09:00:00 - NetSpeedTray.StatsController - INFO - "
            "controller._update_primary_interface_name:392 - "
            "Primary network interface changed: NIC-00aabb11 -> None\n",
            encoding="utf-8",
        )
        dest = tmp_path / "bundle.zip"
        support_bundle.build_support_bundle(dest, fake_config)
        log_content = _open_zip_entry(dest, "logs/NetSpeedTray_Log.log")
        assert "NIC-00aabb11 -> None" in log_content

    def test_display_names_in_logs_are_pseudonymized(self, q_app, tmp_path, fake_config, fake_log_dir):
        """The MANIFEST promises no display names; `\\\\.\\DISPLAYn` is an
        OS-generated shape, so a global scrub rule is safe (unlike NIC names)."""
        (fake_log_dir / "NetSpeedTray_Log.log").write_text(
            "2026-08-20 09:00:00 - NetSpeedTray.Core.PositionManager - INFO - "
            "position_manager.restore:646 - "
            "Restored saved free-move position (10,20) on screen '\\\\.\\DISPLAY2'.\n",
            encoding="utf-8",
        )
        dest = tmp_path / "bundle.zip"
        support_bundle.build_support_bundle(dest, fake_config)
        log_content = _open_zip_entry(dest, "logs/NetSpeedTray_Log.log")
        assert "\\\\.\\DISPLAY" not in log_content
        assert re.search(r"DISPLAY-[0-9a-f]{8}", log_content)


class TestNicPseudonymization:
    """v2.1.5 item 7 - the pseudonym helper and the source half (controller.py)."""

    def test_stable_pseudonym_is_stable_and_distinct(self):
        from netspeedtray.utils.config import stable_pseudonym
        a1 = stable_pseudonym("Wi-Fi 3")
        a2 = stable_pseudonym("Wi-Fi 3")
        b = stable_pseudonym("Ethernet")
        assert a1 == a2, "pseudonym must be stable across calls"
        assert a1 != b, "different adapters must get different pseudonyms"
        assert re.fullmatch(r"NIC-[0-9a-f]{8}", a1)
        assert "Wi-Fi" not in a1
        # None keeps its printable shape so %s call sites stay readable.
        assert stable_pseudonym(None) == "None"

    def test_formatter_covers_resolved_debug_shape(self):
        """network_utils' 'Determined primary interface' debug shape is covered
        too - belt and suspenders if log levels ever change."""
        from netspeedtray.utils.config import ObfuscatingFormatter, stable_pseudonym
        fmt = ObfuscatingFormatter("%(message)s")
        record = logging.LogRecord(
            name="t", level=logging.DEBUG, pathname="", lineno=0,
            msg="Determined primary interface: 'ACME-HQ Office VPN' with IP 192.168.1.10",
            args=None, exc_info=None,
        )
        out = fmt.format(record)
        assert "ACME-HQ Office VPN" not in out
        assert stable_pseudonym("ACME-HQ Office VPN") in out
        assert "192.168.1.10" not in out

    def test_controller_logs_pseudonym_not_raw_name(self, q_app, caplog):
        """Source half: controller.py must log a stable pseudonym, never the raw
        friendly name - and repeated changes must still correlate."""
        import netspeedtray.core.controller as controller_mod
        from netspeedtray.utils.config import stable_pseudonym

        ctrl = controller_mod.StatsController({}, None)
        with caplog.at_level(logging.INFO, logger="NetSpeedTray.StatsController"):
            with patch.object(controller_mod, "get_primary_interface_name",
                              return_value="ACME-HQ Office VPN"):
                ctrl.last_primary_check_time = 0.0
                ctrl._update_primary_interface_name()
            with patch.object(controller_mod, "get_primary_interface_name",
                              return_value="Wi-Fi 3"):
                ctrl.last_primary_check_time = 0.0
                ctrl._update_primary_interface_name()
            with patch.object(controller_mod, "get_primary_interface_name",
                              return_value="ACME-HQ Office VPN"):
                ctrl.last_primary_check_time = 0.0
                ctrl._update_primary_interface_name()

        changes = [r.getMessage() for r in caplog.records
                   if "Primary network interface changed" in r.getMessage()]
        assert len(changes) == 3
        joined = "\n".join(changes)
        assert "ACME-HQ Office VPN" not in joined
        assert "Wi-Fi 3" not in joined
        vpn = stable_pseudonym("ACME-HQ Office VPN")
        wifi = stable_pseudonym("Wi-Fi 3")
        assert changes[0].endswith(f"None -> {vpn}")
        assert changes[1].endswith(f"{vpn} -> {wifi}")
        assert changes[2].endswith(f"{wifi} -> {vpn}")


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


def test_unknown_config_keys_are_redacted_in_bundle():
    """Review C8: item 4c preserves unknown (newer-build) keys, so the bundle key-allowlist
    sanitizer can never know them - their VALUES must not ship. The key name stays: that a
    newer-build setting exists is the useful diagnostic in a rollback bundle."""
    cfg = {"metrics_bind_host": "acme-nas.local", "config_version": "9.9", "font_size": 10}
    out = support_bundle._sanitize_config(cfg)
    assert out["metrics_bind_host"] == "<redacted-unknown-key>"
    assert out["config_version"] == "9.9", "config_version is schema-known and the key rollback diagnostic"
    assert out["font_size"] == 10
