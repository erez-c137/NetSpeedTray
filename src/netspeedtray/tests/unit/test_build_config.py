"""
Release-build configuration that no runtime test can see: what PyInstaller packs, what the
installer runs, what CI checks before it publishes. Each of these failed silently once.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

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


def test_no_relaunch_under_an_over_the_shoulder_administrator():
    """A standard user who types an admin's credentials: the in-app updater starts Setup elevated
    as THAT admin, so 'original user' is the admin - the app must not be started under it."""
    iss = _read("build", "setup.iss")
    body = re.search(r"function LaunchSilentViaShell\(\): Boolean;(.*?)\nend;", iss, re.S).group(1)
    assert body.index("SetupRunsAsAnotherUser()") < body.index("Result := True")
    other = re.search(r"function SetupRunsAsAnotherUser\(\): Boolean;(.*?)\nend;", iss, re.S).group(1)
    # DOMAIN\user on both sides (a local and a domain account may share a name), logged each time.
    assert "SessionAccount()" in other and "SetupAccount()" in other and "Log(" in other
    assert "GetUserNameString" not in other
    assert "WTSQuerySessionInformationW@wtsapi32.dll" in iss and "GetUserNameExW@secur32.dll" in iss
    account = re.search(r"function SessionAccount\(\): string;(.*?)\nend;", iss, re.S).group(1)
    assert "SessionInfoString(7)" in account and "SessionInfoString(5)" in account


def test_release_pins_inno_setup():
    """setup.iss declares 32-bit pointers; an unpinned Inno could change Setup under a release."""
    yml = _read(".github", "workflows", "release.yml")
    assert re.search(r"choco install innosetup --version=\d+\.\d+\.\d+", yml)


def test_setup_installs_in_64_bit_mode_wherever_it_is_allowed_to_run():
    """ArchitecturesAllowed=x64compatible lets Setup run on Windows 11 on ARM (x64 emulation), but
    ArchitecturesInstallIn64BitMode=x64 (an alias of x64os since Inno 6.3) does not match ARM64, so
    there Setup installed in 32-bit mode: Program Files (x86), the WOW6432Node uninstall key."""
    iss = _read("build", "setup.iss")
    allowed = re.search(r"^ArchitecturesAllowed=(\S+)\s*$", iss, re.M).group(1)
    mode = re.search(r"^ArchitecturesInstallIn64BitMode=(\S+)\s*$", iss, re.M).group(1)
    assert allowed == "x64compatible"
    assert mode == allowed


def test_a_previous_folder_on_a_missing_drive_falls_back_to_the_default():
    iss = _read("build", "setup.iss")
    wiz = re.search(r"procedure InitializeWizard\(\);(.*?)\nend;", iss, re.S).group(1)
    assert "PreviousDriveMissing(Dir)" in wiz and "{autopf}\\{#MyAppName}" in wiz
    check = re.search(r"function PreviousDriveMissing\(Dir: string\): Boolean;(.*?)\nend;", iss,
                      re.S).group(1)
    assert "ExtractFileDrive(Dir)" in check and "DirExists" in check


def test_an_upgrade_installs_into_the_existing_folder():
    """UsePreviousAppDir=no moved every silent upgrade of a custom-folder install to Program Files
    and left the old copy (and its shortcuts) running the old version against the migrated DB."""
    iss = _read("build", "setup.iss")
    assert re.search(r"^UsePreviousAppDir=yes\s*$", iss, re.M)
    assert re.search(r"^DisableDirPage=auto\s*$", iss, re.M)


def test_the_in_app_update_never_overrides_the_install_folder():
    from netspeedtray.core.update_installer import INSTALLER_SILENT_ARGS
    assert "/DIR" not in INSTALLER_SILENT_ARGS.upper()


# --- a release tag must name the version the code carries --------------------------------------

def test_release_checks_the_tag_against_version_before_building():
    yml = _read(".github", "workflows", "release.yml")
    check = yml.index("Check the tag matches __version__")
    assert check < yml.index("Run unit tests") < yml.index("build.bat"), (
        "the tag check must run before anything is built or sent for signing")
    step = yml[check:yml.index("- name:", check)]
    assert "GITHUB_REF_NAME" in step and "-cne" in step and '"v" +' in step
    # The step's own pattern must find the real version line (PowerShell and Python agree on it).
    pattern = re.search(r"Match\(\$init, '([^']+)'\)", step).group(1)
    init = _read("src", "netspeedtray", "__init__.py")
    import netspeedtray
    assert re.search(pattern, init).group(1) == netspeedtray.__version__


def test_release_publishes_tracked_notes_with_the_release():
    """The release went public (and Latest) with an empty body; notes came by hand later."""
    yml = _read(".github", "workflows", "release.yml")
    step = yml[yml.index("Resolve the release notes"):]
    step = step[:step.index("- name:")]
    assert '.github/release-notes/$($env:GITHUB_REF_NAME).md' in step
    assert r"<!--.*?(-->|\z)" in step, "draft comments, closed or not, must be stripped"
    assert yml.index("Resolve the release notes") < yml.index("build.bat")
    create = yml[yml.index("- name: Create Release"):]
    assert "body_path: ${{ steps.notes.outputs.path }}" in create[:create.index("submit-to-winget")]
    assert (ROOT / ".github" / "release-notes" / "README.md").is_file()


def test_setup_checks_the_session_users_own_instance_mutex():
    """If WMI cannot list processes and another user holds the legacy name, Setup must still see
    THIS user's 2.2.0+ instance - by its per-user mutex name."""
    iss = _read("build", "setup.iss")
    assert r'#define MyAppMutexPrefix "Global\NetSpeedTray_Instance_"' in iss
    import netspeedtray.constants as c
    assert c.app.MUTEX_NAME_PREFIX == r"Global\NetSpeedTray_Instance_"
    running = re.search(r"function IsAppRunning\(\): Boolean;(.*?)\nend;", iss, re.S).group(1)
    assert "SessionUserInstanceRunning()" in running
    exists = re.search(r"function MutexExists\(Name: string\): Boolean;(.*?)\nend;", iss, re.S).group(1)
    assert "DLLGetLastError() = 5" in exists, "access denied means it exists"
    assert "LookupAccountNameW@advapi32.dll" in iss and "ConvertSidToStringSidW@advapi32.dll" in iss


def test_a_network_previous_folder_is_kept_not_split():
    iss = _read("build", "setup.iss")
    wiz = re.search(r"procedure InitializeWizard\(\);(.*?)\nend;", iss, re.S).group(1)
    assert wiz.index("IsNetworkLocation(Dir)") < wiz.index("WizardForm.DirEdit.Text := Fallback")
    net = re.search(r"function IsNetworkLocation\(Dir: string\): Boolean;(.*?)\nend;", iss, re.S).group(1)
    assert r"'\\'" in net and "SessionUserMappedDrive(Drive[1])" in net
    # The mapping is read from the SESSION user's hive when Setup runs as another account (an admin's
    # HKCU under over-the-shoulder elevation doesn't hold the user's mapped drives), else its own HKCU.
    mapped = re.search(r"function SessionUserMappedDrive\(Letter: string\): Boolean;(.*?)\nend;", iss, re.S).group(1)
    assert "SetupRunsAsAnotherUser()" in mapped and "AccountSid(SessionAccount())" in mapped
    assert r"RegKeyExists(HKU, Sid + '\Network\' + Letter)" in mapped
    assert r"RegKeyExists(HKCU, 'Network\' + Letter)" in mapped
    finish = re.search(r"procedure CurPageChanged\(CurPageID: Integer\);(.*?)\nend;", iss, re.S).group(1)
    assert "wpFinished" in finish and "MovedFromDir" in finish


# --- NST_VENV: a worktree can build with another checkout's venv ---------------------------------

def test_the_build_scripts_take_the_venv_from_nst_venv_else_root_venv():
    r"""Builds from a worktree needed a .venv junction because both scripts hard-coded ROOT\.venv.
    NST_VENV overrides it; unset (CI's release.yml build step sets only NST_SKIP_INSTALLER), the
    venv is ROOT\.venv exactly as before."""
    for bat in ("build.bat", "build-exe-only.bat"):
        text = _read("build", bat)
        assert re.search(r'^set "VENV_DIR=%ROOT_DIR%\\\.venv"$', text, re.M), bat
        assert re.search(r'^if defined NST_VENV set "VENV_DIR=%NST_VENV:"=%"$', text, re.M), bat
        assert re.search(r"^if defined NST_VENV \(echo Using venv from NST_VENV:.*\) else "
                         r"\(echo Using venv:", text, re.M), f"{bat} does not say which venv it uses"
        assert "%ROOT_DIR%\\.venv\\" not in text, f"{bat} still hard-codes ROOT\\.venv somewhere"
        default = text.index(r'set "VENV_DIR=%ROOT_DIR%\.venv"')
        first_use = text.index(r'"%VENV_DIR%\Scripts\python.exe" -c')
        assert default < text.index("if defined NST_VENV set") < first_use, bat


def _run_venv_block(bat, tmp_path, nst_venv):
    """Run the scripts' own venv-resolution block under cmd (delayed expansion on, as in the
    scripts) with ROOT_DIR at tmp_path/root, and return (exit code, resolved VENV_DIR)."""
    text = _read("build", bat).replace("\r\n", "\n")
    start = text.index('set "VENV_DIR=%ROOT_DIR%\\.venv"')
    end = text.index("\n", text.index("echo Resolving Python from:", start)) + 1
    probe = tmp_path / f"probe-{bat}"
    body = ("@echo off\nsetlocal EnableDelayedExpansion\n"
            f'set "ROOT_DIR={tmp_path / "root"}"\n' + text[start:end]
            + "echo VENV_DIR=[%VENV_DIR%]\n")
    probe.write_bytes(body.replace("\n", "\r\n").encode("ascii"))
    env = {k: v for k, v in os.environ.items() if k.upper() != "NST_VENV"}
    if nst_venv is not None:
        env["NST_VENV"] = nst_venv
    result = subprocess.run(["cmd", "/d", "/v:on", "/c", str(probe)],
                            capture_output=True, text=True, env=env, timeout=30)
    found = re.search(r"^VENV_DIR=\[(.*)\]$", result.stdout, re.M)
    return result.returncode, found.group(1) if found else result.stdout + result.stderr


@pytest.mark.skipif(sys.platform != "win32", reason="runs the batch block under cmd.exe")
@pytest.mark.parametrize("bat", ["build.bat", "build-exe-only.bat"])
def test_the_venv_block_resolves_nst_venv_with_quotes_and_trailing_backslash(tmp_path, bat):
    r"""`set NST_VENV="C:\some path\.venv"` is how a path with spaces is usually written; the quotes
    used to end up inside VENV_DIR and break every "%VENV_DIR%\Scripts\..." that follows."""
    venv = tmp_path / "my venv"
    for scripts in (venv / "Scripts", tmp_path / "root" / ".venv" / "Scripts"):
        scripts.mkdir(parents=True)
        (scripts / "python.exe").write_bytes(b"")

    assert _run_venv_block(bat, tmp_path, None) == (0, str(tmp_path / "root" / ".venv"))
    for value in (str(venv), str(venv) + "\\", f'"{venv}"', f'"{venv}\\"'):
        assert _run_venv_block(bat, tmp_path, value) == (0, str(venv)), value
    code, _ = _run_venv_block(bat, tmp_path, f'"{tmp_path / "missing"}"')
    assert code == 1, "a venv without python.exe must stop the build"


def test_the_release_build_step_does_not_set_nst_venv():
    release = _read(".github", "workflows", "release.yml")
    assert "NST_VENV" not in release
    assert "python -m venv .venv" in release


def test_the_spec_fails_the_build_on_a_bundled_foreign_icu_dll():
    """#425 keeps PATH from shadowing System32's ICU; this guard catches it if anything slips past."""
    spec = _read("build", "netspeedtray.spec")
    guard = spec.index("_foreign_icu = ")
    assert spec.index("_bundled_basenames = ") < guard < spec.index("pyz = PYZ(")
    assert "'icuuc.dll', 'icuin.dll'" in spec[guard:guard + 200]
    assert "raise SystemExit(" in spec[guard:guard + 300]
