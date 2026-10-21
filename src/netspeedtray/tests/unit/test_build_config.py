"""
Release-build configuration that no runtime test can see: what PyInstaller packs, what the
installer runs, what CI checks before it publishes. Each of these failed silently once.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]


def _read(*parts: str) -> str:
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


# --- no UPX: packing strips the vendors' Authenticode signatures ----------------------------------

def test_the_spec_packs_nothing_with_upx():
    """The 2.2.0-beta.3 zip shipped _socket.pyd, _sqlite3.pyd, sqlite3.dll, _ctypes.pyd and
    libffi-8.dll unsigned, although the build Python's copies are PSF-signed: UPX rewrote them.
    Smart App Control evaluates every DLL the process loads."""
    spec = _read("build", "netspeedtray.spec")
    assert re.search(r"^_UPX = False$", spec, re.M)
    upx_args = re.findall(r"\bupx\s*=\s*([^,\n]+)", spec)
    assert upx_args and all(a.strip() == "_UPX" for a in upx_args), upx_args
    assert "b'UPX0'" in spec, "the post-COLLECT guard against packed binaries is gone"


def test_the_build_scripts_do_not_fetch_or_pass_upx():
    for bat in ("build.bat", "build-exe-only.bat"):
        text = _read("build", bat).lower()
        assert "fetch_upx" not in text and "--upx-dir" not in text, bat


# --- the installer never starts the app with its own token ------------------------------------

def _run_section(iss: str) -> list:
    m = re.search(r"^\[Run\]\s*$(.*?)^\[", iss, re.M | re.S)
    assert m, "no [Run] section"
    return [ln for ln in m.group(1).splitlines() if ln.strip() and not ln.lstrip().startswith(";")]


def test_no_silent_run_entry_starts_the_exe_directly():
    """A silent install's direct `{app}\\NetSpeedTray.exe` launch ran with Setup's token: SYSTEM in
    session 0 under Intune/SCCM (holding the Global\\ mutex against every user until a reboot), or
    elevated in the in-app update path whenever Explorer was briefly missing."""
    iss = _read("build", "setup.iss")
    for line in _run_section(iss):
        if line.startswith('Filename: "{app}\\{#MyAppExeName}"'):
            assert "skipifsilent" in line and "postinstall" in line, line
    assert "LaunchSilentDirect" not in iss


def test_the_silent_relaunch_goes_through_the_shell_and_skips_service_contexts():
    iss = _read("build", "setup.iss")
    shell = [ln for ln in _run_section(iss) if "Check: LaunchSilentViaShell" in ln]
    assert len(shell) == 1 and shell[0].startswith('Filename: "{win}\\explorer.exe"')
    body = re.search(r"function LaunchSilentViaShell\(\): Boolean;(.*?)\nend;", iss, re.S).group(1)
    assert "InServiceContext()" in body and "ShellIsRunning()" in body
    ctx = re.search(r"function InServiceContext\(\): Boolean;(.*?)\nend;", iss, re.S).group(1)
    assert "SessionId = 0" in ctx and "'SYSTEM'" in ctx
