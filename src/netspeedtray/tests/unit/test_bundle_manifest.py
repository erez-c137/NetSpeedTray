"""
2.2.1: the bundle manifest - a SHA-256 of every file of the portable app, embedded in the exe
before SignPath signs it, so the exe's signature vouches for ``_internal`` too.

Real directories and real PE files throughout: the embed step's whole risk is what a resource update
does to a PyInstaller exe (it drops the appended PKG archive unless handled), which no mock can show.
"""
import importlib.util
import os
import shutil
import sys
from pathlib import Path

import pytest

from netspeedtray.utils import bundle_manifest as bm

ROOT = Path(__file__).resolve().parents[4]
EXE = "NetSpeedTray.exe"


def _app(root: Path) -> Path:
    d = root / "NetSpeedTray"
    (d / "_internal" / "PyQt6").mkdir(parents=True)
    (d / EXE).write_bytes(b"MZ-exe")
    (d / bm.MARKER_NAME).write_text("portable\n", encoding="utf-8")
    (d / "_internal" / "python313.dll").write_bytes(b"dll")
    (d / "_internal" / "base_library.zip").write_bytes(b"PK")
    (d / "_internal" / "PyQt6" / "QtCore.pyd").write_bytes(b"pyd")
    return d


def _manifest(d: Path) -> dict:
    return bm.parse(bm.serialize(bm.build_manifest(str(d), EXE)))


# ----------------------------------------------------------------------------- format

def test_round_trip_covers_every_file_but_the_exe_and_the_marker(tmp_path):
    d = _app(tmp_path)
    m = _manifest(d)
    assert m["v"] == bm.FORMAT_VERSION and m["exe"] == EXE
    assert sorted(m["files"]) == ["_internal/PyQt6/QtCore.pyd", "_internal/base_library.zip",
                                  "_internal/python313.dll"]
    bm.verify_tree(str(d), m, EXE)


def test_serialization_is_canonical(tmp_path):
    d = _app(tmp_path)
    a = bm.serialize(bm.build_manifest(str(d), EXE))
    b = bm.serialize(bm.build_manifest(str(d), EXE))
    assert a == b and b" " not in a


@pytest.mark.parametrize("damage, expect", [
    (lambda d: (d / "_internal" / "python313.dll").write_bytes(b"evil"), "file changed"),
    (lambda d: (d / "_internal" / "python313.dll").unlink(), "missing"),
    (lambda d: (d / "_internal" / "extra.pyd").write_bytes(b"x"), "unexpected"),
    (lambda d: (d / "version.dll").write_bytes(b"MZ"), "unexpected"),          # DLL planting
    (lambda d: (d / "_internal" / "new").mkdir() or (d / "_internal" / "new" / "a.pyc").write_bytes(b""),
     "unexpected"),
    (lambda d: (d / bm.MARKER_NAME).write_bytes(b"x" * 4096), bm.MARKER_NAME),
])
def test_a_changed_missing_or_extra_file_fails(tmp_path, damage, expect):
    d = _app(tmp_path)
    m = _manifest(d)
    damage(d)
    with pytest.raises(bm.ManifestError, match=expect):
        bm.verify_tree(str(d), m, EXE)


def test_the_marker_may_be_absent_and_an_empty_folder_is_harmless(tmp_path):
    d = _app(tmp_path)
    m = _manifest(d)
    (d / bm.MARKER_NAME).unlink()          # the installed build has none
    (d / "_internal" / "empty").mkdir()
    bm.verify_tree(str(d), m, EXE)


def test_a_junction_in_the_folder_is_refused(tmp_path):
    import _winapi
    d = _app(tmp_path)
    m = _manifest(d)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    _winapi.CreateJunction(str(elsewhere), str(d / "_internal" / "link"))
    with pytest.raises(bm.ManifestError, match="link"):
        bm.verify_tree(str(d), m, EXE)


@pytest.mark.parametrize("rel", ["../evil.dll", "_internal/../../evil.dll", "/abs.dll",
                                 "C:/Windows/evil.dll", "_internal\\evil.dll", "", "a//b",
                                 "./a", "_internal/./x"])
def test_a_crafted_path_is_rejected(rel):
    data = bm.serialize({"v": 1, "exe": EXE, "files": {rel: "0" * 64}})
    with pytest.raises(bm.ManifestError):
        bm.parse(data)


@pytest.mark.parametrize("m", [
    {"v": 2, "exe": EXE, "files": {"a": "0" * 64}},                       # unknown format
    {"v": 1, "exe": EXE, "files": {}},                                     # covers nothing
    {"v": 1, "exe": EXE, "files": {"a": "0" * 63}},                        # bad hash
    {"v": 1, "exe": EXE, "files": {"a": "G" * 64}},
    {"v": 1, "exe": EXE, "files": {"A.dll": "0" * 64, "a.dll": "1" * 64}},  # case duplicate
    {"v": 1, "exe": EXE, "files": {EXE: "0" * 64}},                        # lists the exe
    {"v": 1, "files": {"a": "0" * 64}},                                    # no exe
    ["not", "an", "object"],
])
def test_a_malformed_manifest_is_rejected(m):
    with pytest.raises(bm.ManifestError):
        bm.parse(bm.serialize(m))


def test_garbage_and_oversize_are_rejected():
    for data in (b"", b"\xff\xfe", b"{not json", b" " * (bm.MAX_MANIFEST_BYTES + 1)):
        with pytest.raises(bm.ManifestError):
            bm.parse(data)


def test_verify_app_folder_fails_closed_without_a_readable_manifest(tmp_path):
    d = _app(tmp_path)
    with pytest.raises(bm.ManifestError, match="no file manifest"):
        bm.verify_app_folder(str(d), str(d / EXE), reader=lambda p: None)

    def boom(p):
        raise OSError(193, "not a PE")
    with pytest.raises(bm.ManifestError, match="could not read"):
        bm.verify_app_folder(str(d), str(d / EXE), reader=boom)
    # The real reader on a file that is not a PE: a refusal, not a crash and not a pass.
    with pytest.raises(bm.ManifestError, match="could not read"):
        bm.verify_app_folder(str(d), str(d / EXE))


def test_a_manifest_for_another_exe_name_is_refused(tmp_path):
    d = _app(tmp_path)
    m = _manifest(d)
    m["exe"] = "Other.exe"
    with pytest.raises(bm.ManifestError, match="Other.exe"):
        bm.verify_tree(str(d), m, EXE)


# ------------------------------------------------------- the build step, on real PE files

def _embed_module():
    path = ROOT / "build" / "ci" / "embed-manifest.py"
    spec = importlib.util.spec_from_file_location("nst_embed_manifest", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _unsigned_pe(dest: Path) -> Path:
    """A real, unsigned PE to embed into: a copy of a system DLL with its signature cut off
    (Windows' own files are catalog-signed, so most carry no embedded signature at all)."""
    pefile = pytest.importorskip("pefile")
    src = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "version.dll")
    pe = pefile.PE(src, fast_load=True)
    sec = pe.OPTIONAL_HEADER.DATA_DIRECTORY[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_SECURITY"]]
    data = bytearray(pe.write())
    if sec.VirtualAddress:
        del data[sec.VirtualAddress:]
        pe2 = pefile.PE(data=bytes(data), fast_load=True)
        d2 = pe2.OPTIONAL_HEADER.DATA_DIRECTORY[4]
        d2.VirtualAddress = d2.Size = 0
        data = bytearray(pe2.write())
    pe.close()
    dest.write_bytes(bytes(data))
    return dest


def test_embed_keeps_the_appended_archive_and_reads_back(tmp_path):
    """The spike's finding, pinned: a plain UpdateResource drops everything after the last section
    - in a PyInstaller exe that is the PKG archive, and the exe no longer starts. The embed step
    must put the overlay back byte for byte, and the resource must read back through the same
    data-file mapping the updater uses."""
    emb = _embed_module()
    exe = _unsigned_pe(tmp_path / "app.exe")
    overlay = b"PKG-ARCHIVE-STAND-IN" * 500 + b"MEI\x0c\x0b\x0a\x0b\x0e-cookie"
    with open(exe, "ab") as f:
        f.write(overlay)
    payload = bm.serialize({"v": 1, "exe": "app.exe", "files": {"a": "0" * 64}})

    emb.embed(str(exe), payload, bm)

    data = exe.read_bytes()
    assert data.endswith(overlay), "the appended archive must survive, at the end of the file"
    assert bm.read_from_exe(str(exe)) == payload
    import pefile
    pe = pefile.PE(str(exe), fast_load=True)
    assert pe.OPTIONAL_HEADER.CheckSum == pe.generate_checksum()
    pe.close()


def test_a_plain_resource_update_would_drop_the_archive(tmp_path):
    """Why embed() exists: documents the Windows behavior the spike found (2026-11-11)."""
    import win32api
    exe = _unsigned_pe(tmp_path / "app.exe")
    with open(exe, "ab") as f:
        f.write(b"OVERLAY" * 100)
    h = win32api.BeginUpdateResource(str(exe), False)
    win32api.UpdateResource(h, bm.RESOURCE_TYPE, bm.RESOURCE_NAME, b"{}", bm.RESOURCE_LANG)
    win32api.EndUpdateResource(h, False)
    assert not exe.read_bytes().endswith(b"OVERLAY" * 100)


def test_a_pe_without_the_resource_reads_as_none(tmp_path):
    assert bm.read_from_exe(str(_unsigned_pe(tmp_path / "plain.exe"))) is None


def test_embed_refuses_a_signed_exe(tmp_path):
    """Embedding after signing would break the signature; the order is the whole point."""
    emb = _embed_module()
    signed = Path(sys.executable)
    import pefile
    pe = pefile.PE(str(signed), fast_load=True)
    is_signed = pe.OPTIONAL_HEADER.DATA_DIRECTORY[4].VirtualAddress != 0
    pe.close()
    if not is_signed:
        pytest.skip("this interpreter's exe carries no embedded signature")
    copy = tmp_path / "signed.exe"
    shutil.copyfile(signed, copy)
    with pytest.raises(SystemExit, match="already signed"):
        emb.embed(str(copy), b"{}", bm)


def test_the_build_script_end_to_end_on_a_folder(tmp_path):
    """main(): hash the folder, embed, and the folder then passes the updater's own check."""
    emb = _embed_module()
    d = _app(tmp_path)
    _unsigned_pe(d / EXE)
    with pytest.raises(SystemExit, match="no PyInstaller PKG"):
        emb.main([str(d)])                     # the real build insists on a PyInstaller exe
    assert emb.main([str(d), "--allow-non-pyinstaller"]) == 0
    bm.verify_app_folder(str(d), str(d / EXE))
    (d / "_internal" / "python313.dll").write_bytes(b"evil")
    with pytest.raises(bm.ManifestError, match="file changed"):
        bm.verify_app_folder(str(d), str(d / EXE))
