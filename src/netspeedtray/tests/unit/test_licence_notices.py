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


def test_the_committed_notices_match_the_requirements_pins(notices_mod):
    """A stale root copy (a dependency bumped without regenerating) fails here."""
    pins = {}
    for line in _read("requirements.txt").splitlines():
        m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==(\S+)", line.split("#", 1)[0].strip())
        if m:
            pins[re.sub(r"[-_.]+", "-", m.group(1)).lower()] = m.group(2)
    summary = _read("THIRD-PARTY-NOTICES.txt").split("Summary\n-------\n", 1)[1].split("\n\n", 1)[0]
    listed = {re.sub(r"[-_.]+", "-", m.group(1)).lower(): m.group(2)
              for m in re.finditer(r"^\* (\S+) (\S+)$", summary, re.M)}
    stale = {n: (listed.get(n), v) for n, v in pins.items() if listed.get(n) != v}
    assert not stale, f"regenerate THIRD-PARTY-NOTICES.txt (python build/third_party_notices.py): {stale}"


def test_the_notices_reproduce_the_texts_the_wheels_do_not_ship(notices_mod):
    text = notices_mod.generate()
    for must in ("The FreeType Project LICENSE", "HarfBuzz is licensed under", "PNG Reference Library",
                 "PCRE2 Licence", "Copyright 2006-2011, the V8 project authors", "md4c",
                 "Copyright (c) Microsoft Corporation", "Copyright Tim Golden",
                 "Licenses and Acknowledgements for Incorporated Software", "mimalloc", "libmpdec",
                 "HACL* Contributors", "XZ Utils Licensing", "Apache License",
                 "https://docs.python.org/3.", "/license.html"):
        assert must in text, must
    start = text.index("=" * 100 + "\nOpenSSL (bundled with Python")
    openssl = text[start:text.index("\n\n\n" + "=" * 100, start)]
    assert "Apache License" in openssl and "Version 2.0" in openssl, "OpenSSL section must carry its own text"
    assert "ships no licence file in its wheel. Its licence is" not in text   # no link-only entries


def test_the_notices_give_exact_source_pointers_for_the_copyleft_parts(notices_mod):
    import importlib.metadata as md
    text = notices_mod.generate()
    qt = md.version("pyqt6-qt6")
    mm = ".".join(qt.split(".")[:2])
    for url in (f"https://download.qt.io/archive/qt/{mm}/{qt}/",
                f"/PyQt6/pyqt6-{md.version('pyqt6')}.tar.gz",
                f"/python_bidi-{md.version('python-bidi')}.tar.gz",
                f"/pyinstaller-{md.version('pyinstaller')}.tar.gz",
                f"https://github.com/erez-c137/NetSpeedTray/tree/v{notices_mod.app_version()}"):
        assert url in text, url


# --- the portable updater accepts a build that adds the two files at the top level ---------------

def _app_tree(root: Path, name: str, *, docs: bool) -> Path:
    from netspeedtray.core import update_applier as ua
    d = root / name
    (d / "_internal").mkdir(parents=True)
    (d / ua.APP_EXE).write_text("exe", encoding="utf-8")
    (d / "_internal" / "data.bin").write_text("payload", encoding="utf-8")
    (d / ua.PORTABLE_MARKER).write_text("portable\n", encoding="utf-8")
    if docs:
        for doc in DOCS:
            (d / doc).write_text(f"{doc} text", encoding="utf-8")
    return d


def test_validate_accepts_an_update_that_adds_the_docs(tmp_path):
    from netspeedtray.core import update_applier as ua
    install = _app_tree(tmp_path, "NetSpeedTray", docs=False)      # 2.2.0 / 2.2.1-beta.1
    staged = _app_tree(tmp_path, "staged", docs=True)               # this build
    assert ua.validate(str(install), str(staged)) is None


def test_validate_checks_the_top_level_names_it_accepts(tmp_path):
    """Control for the test above: the same check refuses a top-level name the staged copy lacks
    (the downgrade direction, which the updater never offers)."""
    from netspeedtray.core import update_applier as ua
    install = _app_tree(tmp_path, "NetSpeedTray", docs=True)
    staged = _app_tree(tmp_path, "staged", docs=False)
    assert "LICENSE.txt" in (ua.validate(str(install), str(staged)) or "")


def test_cleanup_accepts_a_staged_folder_with_the_docs(tmp_path):
    from netspeedtray.core import update_applier as ua
    install = _app_tree(tmp_path, "NetSpeedTray", docs=True)
    staged = _app_tree(tmp_path, "staged", docs=True)
    allowed = [p.name for p in install.iterdir()]
    assert ua._portable_tree_refusal(str(staged), allowed_extra=allowed) is None
    # Control: without the install's names the docs are unknown items, and the folder is kept.
    assert "LICENSE.txt" in (ua._portable_tree_refusal(str(staged)) or "")


def test_the_bundle_manifest_covers_the_docs(tmp_path):
    from netspeedtray.core import update_applier as ua
    from netspeedtray.utils import bundle_manifest as bm
    app = _app_tree(tmp_path, "NetSpeedTray", docs=True)
    manifest = bm.build_manifest(str(app), ua.APP_EXE)
    assert set(DOCS) <= set(manifest["files"])
    bm.verify_tree(str(app), manifest, ua.APP_EXE)                  # passes: no exception
    # Control: a manifest built without them refuses them as extra files.
    bare = _app_tree(tmp_path / "bare", "NetSpeedTray", docs=False)
    with pytest.raises(bm.ManifestError, match="unexpected"):
        bm.verify_tree(str(app), bm.build_manifest(str(bare), ua.APP_EXE), ua.APP_EXE)
