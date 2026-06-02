"""
Ensure UPX is available locally for PyInstaller compression.

Idempotent: no-op if upx.exe already exists under build/tools/. Called by
build.bat and build-exe-only.bat before invoking PyInstaller, so the spec
file's `upx_dir` lookup finds it.

UPX is open-source (https://upx.github.io/) but we don't ship the binary
in the repo; this script fetches the official Windows x64 release. Failure
to download is non-fatal — PyInstaller proceeds without compression.
"""
from __future__ import annotations

import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path


UPX_VERSION = "5.0.2"
UPX_URL = f"https://github.com/upx/upx/releases/download/v{UPX_VERSION}/upx-{UPX_VERSION}-win64.zip"


def main() -> int:
    build_dir = Path(__file__).resolve().parent
    tools_dir = build_dir / "tools"
    upx_dir = tools_dir / f"upx-{UPX_VERSION}-win64"
    upx_exe = upx_dir / "upx.exe"

    if upx_exe.exists():
        print(f"UPX already present at {upx_exe.relative_to(build_dir)}")
        return 0

    tools_dir.mkdir(parents=True, exist_ok=True)
    zip_path = tools_dir / f"upx-{UPX_VERSION}.zip"

    print(f"Downloading UPX {UPX_VERSION} from GitHub...")
    try:
        with urllib.request.urlopen(UPX_URL, timeout=30) as resp:
            zip_path.write_bytes(resp.read())
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"WARNING: Could not download UPX ({e}). Build will proceed without compression.")
        return 0  # Non-fatal — PyInstaller skips UPX gracefully.

    print(f"Extracting to {tools_dir.relative_to(build_dir)}...")
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(tools_dir)
    zip_path.unlink(missing_ok=True)

    if not upx_exe.exists():
        print(f"WARNING: UPX was extracted but {upx_exe.name} is missing — build will proceed without compression.")
        return 0

    print(f"UPX ready at {upx_exe.relative_to(build_dir)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
