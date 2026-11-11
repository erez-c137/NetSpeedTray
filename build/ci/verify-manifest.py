"""
Check a portable build the way the in-app updater will: the exe's signature, then every file
against the bundle manifest embedded in that exe (2.2.1). Read-only: never runs the exe.

    python build/ci/verify-manifest.py <portable zip | app folder> [--require-signed] [--tamper-test]

  --require-signed  fail unless NetSpeedTray.exe passes the updater's own gate (WinVerifyTrust +
                    the SignPath Foundation pin). Use on a SIGNED release zip (CI does, in
                    use-signed-exe.ps1). Without it the signature is only reported, so a local
                    unsigned build can be checked too.
  --tamper-test     also prove the check bites: in a scratch copy, change one byte of one
                    _internal file, then add one extra file, then delete one file - each must be
                    refused.

Exit code 0 = every check passed.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import time
import zipfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

from netspeedtray.utils import bundle_manifest as bm  # noqa: E402

EXE = "NetSpeedTray.exe"


def _extract(zip_path: str, dest: str) -> None:
    root = os.path.realpath(dest)
    with zipfile.ZipFile(zip_path) as zf:
        for m in zf.namelist():
            t = os.path.realpath(os.path.join(dest, m))
            if t != root and not t.startswith(root + os.sep):
                raise SystemExit(f"FAIL: unsafe path in archive: {m!r}")
        zf.extractall(dest)


def _find_app(root: str) -> str:
    for dirpath, _dirs, files in os.walk(root):
        if any(f.lower() == EXE.lower() for f in files):
            return dirpath
    raise SystemExit(f"FAIL: no {EXE} under {root}")


def _expect_refusal(label: str, app: str, exe: str) -> bool:
    try:
        bm.verify_app_folder(app, exe)
    except bm.ManifestError as e:
        print(f"  ok   {label}: refused ({e})")
        return True
    print(f"  FAIL {label}: ACCEPTED")
    return False


def tamper_test(app: str, scratch: str) -> bool:
    ok = True
    internal = os.path.join(app, "_internal")
    victim = None
    for dirpath, _d, files in os.walk(internal):
        for f in sorted(files):
            if f.lower().endswith((".pyd", ".dll")):
                victim = os.path.join(dirpath, f)
                break
        if victim:
            break
    if victim is None:
        print("  FAIL tamper test: no .pyd/.dll in _internal")
        return False

    def fresh() -> str:
        dst = os.path.join(scratch, f"copy-{time.time_ns()}")
        shutil.copytree(app, dst)
        return dst

    c = fresh()
    v = os.path.join(c, os.path.relpath(victim, app))
    with open(v, "r+b") as f:
        f.seek(os.path.getsize(v) // 2)
        b = f.read(1)
        f.seek(-1, os.SEEK_CUR)
        f.write(bytes([b[0] ^ 0xFF]))
    ok &= _expect_refusal(f"one byte changed in {os.path.relpath(victim, app)}", c, os.path.join(c, EXE))

    c = fresh()
    with open(os.path.join(c, "_internal", "version.dll"), "wb") as f:
        f.write(b"MZ")
    ok &= _expect_refusal("extra _internal\\version.dll", c, os.path.join(c, EXE))

    c = fresh()
    os.remove(os.path.join(c, os.path.relpath(victim, app)))
    ok &= _expect_refusal(f"{os.path.relpath(victim, app)} deleted", c, os.path.join(c, EXE))
    return ok


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("target", help="a NetSpeedTray-Portable-*.zip or an app folder")
    ap.add_argument("--require-signed", action="store_true")
    ap.add_argument("--tamper-test", action="store_true")
    args = ap.parse_args(argv)

    scratch = tempfile.mkdtemp(prefix="nst-verify-manifest-")
    try:
        if os.path.isfile(args.target) and args.target.lower().endswith(".zip"):
            _extract(args.target, os.path.join(scratch, "x"))
            app = _find_app(os.path.join(scratch, "x"))
        else:
            app = _find_app(os.path.abspath(args.target))
        exe = os.path.join(app, EXE)
        print(f"App folder: {app}")

        from netspeedtray.utils.signature_verifier import verify_file
        sig = verify_file(exe)
        print(f"Signature: {'trusted' if sig.trusted else 'NOT trusted'} ({sig.reason}; {sig.subject_cn})")
        if args.require_signed and not sig.trusted:
            print("FAIL: the exe does not pass the updater's signature gate.")
            return 1

        data = bm.read_from_exe(exe)
        if data is None:
            print(f"FAIL: {EXE} carries no {bm.RESOURCE_NAME} resource.")
            return 1
        manifest = bm.parse(data)
        print(f"Manifest: format v{manifest['v']}, {len(manifest['files'])} files, {len(data)} bytes")

        t0 = time.perf_counter()
        try:
            bm.verify_app_folder(app, exe)
        except bm.ManifestError as e:
            print(f"FAIL: the folder does not match the manifest: {e}")
            return 1
        print(f"Every file matches ({time.perf_counter() - t0:.2f} s to hash).")

        if args.tamper_test:
            print("Tamper test:")
            if not tamper_test(app, scratch):
                return 1
        print("PASS")
        return 0
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
