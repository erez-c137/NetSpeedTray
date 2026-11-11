"""
Embed the bundle manifest (a SHA-256 of every file of the onedir build) into NetSpeedTray.exe as an
RT_RCDATA resource named NST_MANIFEST, BEFORE the exe is signed. The signature then covers it, and
the in-app portable updater checks every file of a new version against it (2.2.1; see
src/netspeedtray/utils/bundle_manifest.py for the format and the threat it closes).

    python build/ci/embed-manifest.py dist\\NetSpeedTray [--exe NetSpeedTray.exe]

Why not just UpdateResource: a PyInstaller onedir exe is NOT a bare bootloader - PyInstaller appends
its PKG archive (the frozen entry script and the PYZ of every pure-Python module) to the exe as
overlay data, after the last PE section. EndUpdateResource rewrites the PE and silently DROPS any
overlay (spike, 2026-11-11: the exe shrank by the PKG's size and the bootloader could no longer find
its archive). PyInstaller itself writes the icon, version and manifest resources before it appends
the PKG for this reason. So this script cuts the overlay off, updates the resources of the bare PE,
appends the overlay again byte for byte and fixes the PE checksum - the same order PyInstaller
uses. The bootloader finds the PKG by searching back from the end of the file for its cookie, and
the cookie stores the PKG's length, not its offset, so moving it is safe.

Refuses an exe that is already signed (embedding would break the signature) and fails loudly on
anything unexpected: a release must never ship without the manifest.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def load_bundle_manifest():
    """Import src/netspeedtray/utils/bundle_manifest.py by path - one definition of the format,
    without importing the app package (and PyQt6) into the build."""
    path = os.path.join(ROOT, "src", "netspeedtray", "utils", "bundle_manifest.py")
    spec = importlib.util.spec_from_file_location("nst_bundle_manifest", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _pe_layout(exe: str):
    """(overlay start offset or None, has an Authenticode signature)."""
    import pefile
    pe = pefile.PE(exe, fast_load=True)
    try:
        sec = pe.OPTIONAL_HEADER.DATA_DIRECTORY[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_SECURITY"]]
        signed = sec.VirtualAddress != 0 and sec.Size != 0
        return pe.get_overlay_data_start_offset(), signed
    finally:
        pe.close()


def _fix_checksum(exe: str) -> None:
    import pefile
    pe = pefile.PE(exe, fast_load=True)
    try:
        pe.OPTIONAL_HEADER.CheckSum = pe.generate_checksum()
        data = pe.write()
    finally:
        pe.close()
    with open(exe, "wb") as f:
        f.write(data)


def embed(exe: str, payload: bytes, bm) -> None:
    import win32api

    overlay_at, signed = _pe_layout(exe)
    if signed:
        raise SystemExit(f"ERROR: {exe} is already signed; the manifest must go in BEFORE signing.")
    with open(exe, "rb") as f:
        blob = f.read()
    if overlay_at is None:
        head, overlay = blob, b""
    else:
        head, overlay = blob[:overlay_at], blob[overlay_at:]

    # The work file goes BESIDE the bundle (dist\), never inside it: the manifest is checked against
    # the folder right after this, and a work file left behind there (an AV holding it open) would
    # be an "unexpected file".
    bundle_dir = os.path.dirname(os.path.abspath(exe))
    fd, tmp = tempfile.mkstemp(suffix=".exe", prefix="nst-embed-",
                               dir=os.path.dirname(bundle_dir) or None)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(head)
        h = win32api.BeginUpdateResource(tmp, False)
        try:
            win32api.UpdateResource(h, bm.RESOURCE_TYPE, bm.RESOURCE_NAME, payload, bm.RESOURCE_LANG)
        except Exception:
            win32api.EndUpdateResource(h, True)   # discard
            raise
        win32api.EndUpdateResource(h, False)
        new_overlay_at, _ = _pe_layout(tmp)
        if new_overlay_at is not None:
            raise SystemExit("ERROR: the resource update left overlay data behind; refusing to guess.")
        with open(tmp, "ab") as f:
            f.write(overlay)
        _fix_checksum(tmp)
        shutil.copyfile(tmp, exe)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass

    # Self-checks: the overlay (the PKG) is the file's tail, unchanged, and the resource reads back.
    with open(exe, "rb") as f:
        out = f.read()
    if overlay and not out.endswith(overlay):
        raise SystemExit("ERROR: the PKG archive did not survive the resource update.")
    if bm.read_from_exe(exe) != payload:
        raise SystemExit("ERROR: the embedded manifest does not read back identically.")


def check_pyinstaller_archive(exe: str):
    """Open the exe's PKG the way the bootloader does (search back from the end for the cookie).
    Returns its entry count, or None when there is none to find (or PyInstaller is not installed)."""
    try:
        from PyInstaller.archive.readers import CArchiveReader
    except ImportError:
        return None
    try:
        return len(CArchiveReader(exe).toc)
    except Exception:
        return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("app_dir", help="the onedir output folder, e.g. dist\\NetSpeedTray")
    ap.add_argument("--exe", default="NetSpeedTray.exe")
    ap.add_argument("--allow-non-pyinstaller", action="store_true",
                    help="tests only: the exe need not carry a PyInstaller PKG archive")
    args = ap.parse_args(argv)

    bm = load_bundle_manifest()
    app_dir = os.path.abspath(args.app_dir)
    exe = os.path.join(app_dir, args.exe)
    if not os.path.isfile(exe):
        raise SystemExit(f"ERROR: no {exe}")

    entries_before = check_pyinstaller_archive(exe)
    if entries_before is None and not args.allow_non_pyinstaller:
        raise SystemExit(f"ERROR: no PyInstaller PKG archive found in {exe} (is PyInstaller installed?).")
    manifest = bm.build_manifest(app_dir, args.exe)
    payload = bm.serialize(manifest)
    bm.parse(payload)                       # the updater's own validation must accept it
    embed(exe, payload, bm)
    bm.verify_app_folder(app_dir, exe)      # and the folder must pass the updater's check as built
    entries_after = check_pyinstaller_archive(exe)
    if entries_before != entries_after:
        raise SystemExit(f"ERROR: PKG archive changed ({entries_before} -> {entries_after} entries).")

    print(f"Embedded {bm.RESOURCE_NAME}: {len(manifest['files'])} files, {len(payload)} bytes; "
          f"PKG intact ({entries_after} entries).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
