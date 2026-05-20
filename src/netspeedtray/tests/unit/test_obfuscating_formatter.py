"""
Probes ObfuscatingFormatter against synthetic PII to document what it
redacts, what it lets through, and where gaps exist. Findings feed into
Docs/v1.3.2_pii_audit.md.

Tests are written to PASS when the formatter behaves as currently implemented.
Tests for known gaps are marked with xfail so they document the gap without
failing the suite. When a gap is fixed, the corresponding xfail can be removed.
"""
import logging
import pytest
from pathlib import Path

from netspeedtray.utils.config import ObfuscatingFormatter


@pytest.fixture
def formatter():
    return ObfuscatingFormatter("%(message)s")


def _format(formatter: ObfuscatingFormatter, msg: str, *args) -> str:
    """Format a log record with the given message and return the obfuscated output."""
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname=__file__, lineno=1,
        msg=msg, args=args, exc_info=None,
    )
    return formatter.format(record)


# --- IPv4 ---------------------------------------------------------------------

class TestIPv4Redaction:
    def test_redacts_plain_ipv4(self, formatter):
        out = _format(formatter, "Connected to 192.168.1.1 successfully")
        assert "192.168.1.1" not in out
        assert "<REDACTED_IP>" in out

    def test_redacts_public_ipv4(self, formatter):
        out = _format(formatter, "DNS: 8.8.8.8")
        assert "8.8.8.8" not in out

    def test_redacts_broadcast(self, formatter):
        out = _format(formatter, "Broadcast at 255.255.255.255:67")
        assert "255.255.255.255" not in out

    def test_redacts_ipv4_in_parens(self, formatter):
        out = _format(formatter, "Failed (target: 10.0.0.1) retrying")
        assert "10.0.0.1" not in out

    def test_redacts_ipv4_in_url(self, formatter):
        out = _format(formatter, "GET https://192.168.1.1:8080/api/status")
        assert "192.168.1.1" not in out

    def test_does_not_redact_version_numbers(self, formatter):
        # 1.3.1 is a version, not an IP — only 3 octets, should not match.
        out = _format(formatter, "NetSpeedTray version 1.3.1 started")
        assert "1.3.1" in out

    def test_does_not_redact_decimals(self, formatter):
        out = _format(formatter, "Speed: 1234.5678 Mbps")
        assert "1234.5678" in out


# --- IPv6 ---------------------------------------------------------------------

class TestIPv6Redaction:
    def test_redacts_full_ipv6(self, formatter):
        out = _format(formatter, "Listening on 2001:0db8:0000:0000:0000:0000:0000:0001")
        assert "2001:0db8:0000:0000:0000:0000:0000:0001" not in out

    def test_redacts_compressed_ipv6(self, formatter):
        out = _format(formatter, "Connected to 2001:db8::1")
        assert "2001:db8::1" not in out, "compressed IPv6 leaked"

    def test_redacts_ipv6_loopback(self, formatter):
        out = _format(formatter, "Localhost is ::1")
        assert "::1" not in out

    def test_redacts_link_local_ipv6(self, formatter):
        out = _format(formatter, "Link-local fe80::abcd:1234 detected")
        assert "fe80::abcd:1234" not in out

    def test_redacts_link_local_ipv6_with_zone_id(self, formatter):
        out = _format(formatter, "Bound to fe80::abcd:1234%5 on adapter 5")
        assert "fe80::abcd:1234%5" not in out

    def test_redacts_ipv4_mapped_ipv6_partial(self, formatter):
        # Surprise — the IPv4 portion of an IPv4-mapped IPv6 IS caught by the IPv4 regex.
        # The "::ffff:" prefix is NOT removed, but the leak of the actual IPv4 is prevented.
        out = _format(formatter, "Bound to ::ffff:192.168.1.1")
        assert "192.168.1.1" not in out

    def test_does_not_redact_clock_time(self, formatter):
        # 12:34:56 has only 2 colons -> safe from current regex
        out = _format(formatter, "Event at 12:34:56 UTC")
        assert "12:34:56" in out


# --- Windows Paths ------------------------------------------------------------

class TestPathRedaction:
    def test_redacts_user_home_backslash(self, formatter):
        home = str(Path.home().resolve())
        out = _format(formatter, f"Config at {home}\\NetSpeedTray\\config.json")
        assert home not in out
        assert "<REDACTED_PATH>" in out

    def test_redacts_user_home_case_insensitive(self, formatter):
        home = str(Path.home().resolve())
        # Force uppercase drive letter — formatter uses normcase + IGNORECASE
        upper = home[0].upper() + home[1:]
        out = _format(formatter, f"Found file at {upper}\\Documents\\file.txt")
        assert upper.lower() not in out.lower()

    def test_redacts_forward_slash_paths(self, formatter):
        # Many libraries (Path repr, urllib) use forward slashes on Windows.
        home = str(Path.home().resolve()).replace("\\", "/")
        out = _format(formatter, f"Loading {home}/NetSpeedTray/config.json")
        assert home not in out

    def test_redacts_pathlib_repr(self, formatter):
        p = Path.home() / "NetSpeedTray" / "config.json"
        out = _format(formatter, f"Path object: {p!r}")
        import os
        username = os.environ.get("USERNAME") or os.environ.get("USER") or ""
        if username:
            assert username not in out, f"username '{username}' leaked via Path repr()"

    def test_redacts_path_in_exception_message(self, formatter):
        home = str(Path.home().resolve())
        # Simulate an exception with a path in its message
        try:
            raise FileNotFoundError(f"[Errno 2] No such file: '{home}\\foo.txt'")
        except FileNotFoundError as e:
            out = _format(formatter, "Failed: %s", e)
        assert home not in out


# --- Hostname / Computer Name -------------------------------------------------

class TestHostnameRedaction:
    def test_redacts_computer_name(self, formatter):
        import socket
        hostname = socket.gethostname()
        if not hostname or len(hostname) <= 3:
            pytest.skip("No usable hostname on this system")
        out = _format(formatter, f"Running on {hostname}")
        assert hostname not in out
        assert "<REDACTED_HOST>" in out


# --- MAC Addresses ------------------------------------------------------------

class TestMACAddress:
    def test_redacts_mac_dashed(self, formatter):
        out = _format(formatter, "Interface MAC: 00-1A-2B-3C-4D-5E")
        assert "00-1A-2B-3C-4D-5E" not in out
        assert "<REDACTED_MAC>" in out

    def test_redacts_mac_colon(self, formatter):
        out = _format(formatter, "Interface MAC: 00:1A:2B:3C:4D:5E")
        assert "00:1A:2B:3C:4D:5E" not in out
        assert "<REDACTED_MAC>" in out

    def test_does_not_redact_short_hex_groups(self, formatter):
        # Five hex groups is not a MAC — should not match.
        out = _format(formatter, "Status code: AB-CD-EF-12-34")
        assert "AB-CD-EF-12-34" in out


# --- Network Interface Identifiers -------------------------------------------

class TestInterfaceIdentifiers:
    def test_redacts_interface_guid(self, formatter):
        guid = "{12345678-1234-1234-1234-123456789012}"
        out = _format(formatter, f"Adapter ID: {guid}")
        assert guid not in out
        assert "<REDACTED_GUID>" in out

    def test_interface_friendly_names_pass_through(self, formatter):
        # Documenting current behavior: friendly names like "vEthernet (Default Switch)"
        # pass through unobfuscated. These can leak VPN/VLAN identifiers.
        out = _format(formatter, "Aggregating from 'vEthernet (WSL)'")
        assert "vEthernet (WSL)" in out  # currently passes through


# --- Logging Configuration ----------------------------------------------------

class TestLoggingSetupIntegrity:
    """Guards against regressions in how logging gets wired up."""

    def test_helpers_setup_logging_remains_deleted(self):
        """helpers.setup_logging was deleted in v1.3.2 to prevent accidental
        un-obfuscated logging. If a future contributor re-adds it, ConfigManager.
        setup_logging is no longer the unique entry point — flag immediately.
        """
        from netspeedtray.utils import helpers
        assert not hasattr(helpers, "setup_logging"), (
            "helpers.setup_logging is back. Either delete it, or ensure it uses "
            "ObfuscatingFormatter and update this test."
        )

    def test_config_setup_logging_uses_obfuscator_on_both_handlers(self):
        """File and console handlers must both use ObfuscatingFormatter."""
        import inspect
        from netspeedtray.utils import config
        src = inspect.getsource(config.ConfigManager.setup_logging)
        # Both handlers' formatter lines should reference ObfuscatingFormatter.
        # Looking for the substring rather than parsing AST keeps the test cheap.
        assert src.count("ObfuscatingFormatter") >= 2, (
            "Expected ObfuscatingFormatter on both file and console handlers"
        )
