"""
Every copy of the app must carry the GPLv3 text (section 4) and the bundled libraries' notices (Qt's
LGPLv3, the BSD/MIT/Apache packages). Before this, the installed app and the portable zip shipped
neither: nothing in the spec or the installer script referenced a licence file.
"""
import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
DOCS = ("LICENSE.txt", "THIRD-PARTY-NOTICES.txt")


def _read(*parts: str) -> str:
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def notices_mod():
    path = ROOT / "build" / "third_party_notices.py"
    spec = importlib.util.spec_from_file_location("nst_third_party_notices_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _summary_names(text: str) -> set:
    """Normalized package names from the Summary bullets ('* name version')."""
    summary = text.split("Summary\n-------\n", 1)[1].split("\n\n", 1)[0]
    return {re.sub(r"[-_.]+", "-", m.group(1)).lower()
            for m in re.finditer(r"^\* (\S+)", summary, re.M)}


def test_the_spec_writes_both_files_next_to_the_exe_in_both_build_modes():
    spec = _read("build", "netspeedtray.spec")
    assert "third_party_notices.py" in spec
    for doc in DOCS:
        assert f"'{doc}'" in spec, doc
    # onefile: next to dist\NetSpeedTray.exe; onedir: inside dist\NetSpeedTray (before the manifest).
    assert "_write_release_docs(DISTPATH)" in spec
    assert "_write_release_docs(os.path.join(DISTPATH, 'NetSpeedTray'))" in spec


def test_the_installer_installs_both_files_explicitly():
    iss = _read("build", "setup.iss")
    for doc in DOCS:
        assert re.search(rf'^Source: "\.\.\\dist\\NetSpeedTray\\{re.escape(doc)}"; DestDir: "{{app}}"', iss, re.M), doc
    # The wildcard must not pick them up a second time.
    wildcard = re.search(r'^Source: "\.\.\\dist\\NetSpeedTray\\\*";.*$', iss, re.M).group(0)
    assert r'Excludes: "\LICENSE.txt,\THIRD-PARTY-NOTICES.txt"' in wildcard


def test_the_exe_only_build_keeps_both_files_with_the_exe():
    bat = _read("build", "build-exe-only.bat")
    assert "for %%f in (LICENSE.txt THIRD-PARTY-NOTICES.txt) do (" in bat


def test_the_installed_copy_check_in_ci_looks_for_both_files():
    ps = _read("build", "ci", "verify-installed-exe.ps1")
    assert '@("LICENSE.txt", "THIRD-PARTY-NOTICES.txt")' in ps


def test_every_runtime_requirement_has_a_licence_entry(notices_mod):
    reqs = notices_mod.runtime_requirements()
    assert len(reqs) >= 20, reqs   # parsed the pip-compile file, not an empty list
    assert {"pyqt6", "pyqt6-qt6", "numpy", "matplotlib", "psutil", "pywin32", "wmi"} <= set(reqs)
    missing = sorted(set(reqs) - set(notices_mod.RUNTIME_PACKAGES))
    assert not missing, f"add these to RUNTIME_PACKAGES in build/third_party_notices.py: {missing}"


def test_the_generated_notices_cover_every_runtime_requirement(notices_mod):
    text = notices_mod.generate()
    names = _summary_names(text)
    missing = sorted(set(notices_mod.runtime_requirements()) - names)
    assert not missing, missing
    for must in ("Python (CPython runtime)", "OpenSSL", "PyInstaller bootloader", "GNU LESSER GENERAL PUBLIC LICENSE"):
        assert must in text, must


def test_the_committed_notices_cover_every_runtime_requirement(notices_mod):
    """The repo-root copy is for readers of the source tree; the build regenerates its own. After a
    dependency change, run: python build/third_party_notices.py"""
    names = _summary_names(_read("THIRD-PARTY-NOTICES.txt"))
    missing = sorted(set(notices_mod.runtime_requirements()) - names)
    assert not missing, f"regenerate THIRD-PARTY-NOTICES.txt (python build/third_party_notices.py): {missing}"


def test_write_release_docs_writes_the_gpl_and_the_notices(notices_mod, tmp_path):
    written = notices_mod.write_release_docs(str(tmp_path))
    assert sorted(Path(p).name for p in written) == sorted(DOCS)
    lic = (tmp_path / "LICENSE.txt").read_text(encoding="utf-8")
    assert lic.replace("\r\n", "\n") == _read("LICENSE").replace("\r\n", "\n")
    assert "GNU GENERAL PUBLIC LICENSE" in lic and "Version 3" in lic
    assert (tmp_path / "THIRD-PARTY-NOTICES.txt").stat().st_size > 100_000
