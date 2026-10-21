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
