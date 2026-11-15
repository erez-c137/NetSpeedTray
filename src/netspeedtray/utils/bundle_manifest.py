"""
The bundle manifest: a SHA-256 for every file of the portable app, carried INSIDE the signed exe.

A portable update is a PyInstaller onedir folder - ``NetSpeedTray.exe`` plus ``_internal\\`` (Python,
Qt, PyQt6, numpy, base_library.zip, ...). SignPath signs the exe only, and most of ``_internal`` is
upstream builds we cannot sign, so the exe's Authenticode signature alone does not vouch for the code
that actually runs. Before 2.2.1 the rest was covered by the release's ``checksums.txt`` alone, so
someone able to replace both the zip and ``checksums.txt`` on a release could swap a ``.pyd`` and keep
the genuine signed exe.

Since 2.2.1 the build (``build/ci/embed-manifest.py``) hashes every file of the folder and embeds the
result in the exe as an ``RT_RCDATA`` resource named ``NST_MANIFEST``, BEFORE SignPath signs it.
Authenticode hashes the whole PE except the checksum field, the certificate-table directory entry and
the certificate table itself, so the signature covers the manifest. The updater verifies the new exe's
signature first, then reads the manifest out of that exe (as data, never executed) and requires the
folder to match it exactly: no missing, extra or changed file.

What the manifest covers: every regular file under the app folder except the exe itself (covered by
its own signature) and the top-level ``portable.marker`` (a presence flag the zip adds after the exe is
built, and that the installer build must not carry; its content is never read, and it is capped at
``_MARKER_MAX_BYTES``). Directories are not listed: an empty one runs nothing, and any file in one is
an extra file.

FORMAT CONTRACT - read before changing anything here. The resource is ``{"v": 1, "exe": ...,
"files": {...}}`` and every shipped updater (2.2.1 on) accepts ONLY ``v == 1`` and refuses an exe
without it. So every future build MUST keep embedding a v1-readable ``NST_MANIFEST``, forever: a
release that replaced it with a v2 (or dropped it) would have every installed portable copy refuse
the update. Need more? Add a SECOND resource (e.g. ``NST_MANIFEST_V2``) beside the v1 one, and
teach newer readers to prefer it. ``FORMAT_VERSION`` is what the build writes; a build-config test
pins it to 1.

This module is stdlib-only (ctypes for the resource read): the update applier imports it, and so does
the build script, by file path, without the rest of the package.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
from typing import Callable, Dict, Optional

FORMAT_VERSION = 1
RESOURCE_TYPE = 10                  # RT_RCDATA
RESOURCE_NAME = "NST_MANIFEST"
RESOURCE_LANG = 0                   # LANG_NEUTRAL / SUBLANG_NEUTRAL
MARKER_NAME = "portable.marker"
_MARKER_MAX_BYTES = 1024
# The real manifest is ~25-40 KB (about 300-500 files). Anything near this cap is not ours.
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
_HEX = frozenset("0123456789abcdef")
_CHUNK = 1024 * 1024

# Windows file attribute bit for reparse points (junctions, symlinks, cloud placeholders), and the
# reparse-tag bit that marks a tag as a NAME SURROGATE - one that names another location.
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_REPARSE_NAME_SURROGATE = 0x20000000


class ManifestError(ValueError):
    """The manifest is missing, malformed, or the folder does not match it."""


# --- the canonical form -----------------------------------------------------------------------

def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def _reparse_is_link(attrs: int, tag: int) -> bool:
    """A reparse point that NAMES another location (symlink, junction, mount point), or one whose
    tag cannot be read. Cloud-file placeholders (OneDrive, Dropbox: IO_REPARSE_TAG_CLOUD_*) are
    reparse points WITHOUT the name-surrogate bit: their content is the file's own and is hashed
    like any other, so they pass. The same rule as update_applier._reparse_is_link (duplicated:
    this module is loaded by file path, without the package)."""
    if not attrs & _FILE_ATTRIBUTE_REPARSE_POINT:
        return False
    return not tag or bool(tag & _REPARSE_NAME_SURROGATE)


def _is_link_like(entry: "os.DirEntry") -> bool:
    """A symlink, junction or mount point (never followed); unreadable counts as one (fail-safe).
    A 2.1.7 build stages a hands-off update in Downloads, which may sit in a cloud-synced folder
    whose files are placeholders - refusing every reparse point refused those genuine copies."""
    try:
        if entry.is_symlink() or getattr(entry, "is_junction", lambda: False)():
            return True
        st = entry.stat(follow_symlinks=False)
        return _reparse_is_link(getattr(st, "st_file_attributes", 0),
                                getattr(st, "st_reparse_tag", 0))
    except OSError:
        return True


def list_files(app_dir: str, exe_name: str) -> Dict[str, str]:
    """``{posix relpath: absolute path}`` for every regular file the manifest covers.

    Raises ManifestError for anything that is not a plain file or directory (a link, a junction, a
    device) - an update folder never contains one, and following one could hash something that is
    not in the folder."""
    out: Dict[str, str] = {}
    exe_lower = exe_name.lower()

    def walk(abs_dir: str, rel_parts: tuple) -> None:
        with os.scandir(abs_dir) as it:
            entries = sorted(it, key=lambda e: e.name)
        for e in entries:
            if _is_link_like(e):
                raise ManifestError(f"link or reparse point in the app folder: {'/'.join(rel_parts + (e.name,))}")
            if e.is_dir(follow_symlinks=False):
                walk(e.path, rel_parts + (e.name,))
                continue
            if not e.is_file(follow_symlinks=False):
                raise ManifestError(f"not a regular file: {'/'.join(rel_parts + (e.name,))}")
            if not rel_parts and e.name.lower() in (exe_lower, MARKER_NAME):
                continue
            out["/".join(rel_parts + (e.name,))] = e.path

    walk(app_dir, ())
    return out


def build_manifest(app_dir: str, exe_name: str) -> dict:
    """Hash every covered file of ``app_dir`` (build time)."""
    files = {rel: sha256_file(path) for rel, path in list_files(app_dir, exe_name).items()}
    if not files:
        raise ManifestError(f"no files to cover in {app_dir}")
    return {"v": FORMAT_VERSION, "exe": exe_name, "files": files}


def serialize(manifest: dict) -> bytes:
    """Canonical bytes: sorted keys, no whitespace, UTF-8."""
    return json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _check_relpath(rel: str) -> None:
    if not isinstance(rel, str) or not rel:
        raise ManifestError("empty path in manifest")
    if "\\" in rel or ":" in rel or "\x00" in rel or rel.startswith("/"):
        raise ManifestError(f"unsafe path in manifest: {rel!r}")
    for part in rel.split("/"):
        if part in ("", ".", ".."):
            raise ManifestError(f"unsafe path in manifest: {rel!r}")


def parse(data: bytes) -> dict:
    """Validate manifest bytes and return the manifest. Raises ManifestError."""
    if not data:
        raise ManifestError("empty manifest")
    if len(data) > MAX_MANIFEST_BYTES:
        raise ManifestError("manifest is too large")
    try:
        m = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise ManifestError(f"manifest is not valid JSON: {e}") from None
    if not isinstance(m, dict):
        raise ManifestError("manifest is not an object")
    if m.get("v") != FORMAT_VERSION:
        raise ManifestError(f"unsupported manifest format: {m.get('v')!r}")
    files = m.get("files")
    if not isinstance(files, dict) or not files:
        raise ManifestError("manifest lists no files")
    exe = m.get("exe")
    if not isinstance(exe, str) or not exe:
        raise ManifestError("manifest names no exe")
    seen_lower = set()
    for rel, digest in files.items():
        _check_relpath(rel)
        if not isinstance(digest, str) or len(digest) != 64 or not set(digest) <= _HEX:
            raise ManifestError(f"bad hash for {rel!r}")
        low = rel.lower()
        if low in seen_lower:
            raise ManifestError(f"duplicate path in manifest: {rel!r}")
        seen_lower.add(low)
        if "/" not in rel and low in (exe.lower(), MARKER_NAME):
            raise ManifestError(f"manifest lists an uncovered file: {rel!r}")
    return m


# --- verification -----------------------------------------------------------------------------

def verify_tree(app_dir: str, manifest: dict, exe_name: str) -> None:
    """Require ``app_dir`` to match ``manifest`` exactly. Raises ManifestError naming the first
    problem (missing, extra or changed file; link; oversized marker)."""
    if exe_name.lower() != str(manifest.get("exe", "")).lower():
        raise ManifestError(f"manifest is for {manifest.get('exe')!r}, not {exe_name!r}")
    expected: Dict[str, str] = manifest["files"]
    actual = list_files(app_dir, exe_name)
    missing = sorted(set(expected) - set(actual))
    if missing:
        raise ManifestError(f"{len(missing)} file(s) missing, e.g. {missing[0]}")
    extra = sorted(set(actual) - set(expected))
    if extra:
        raise ManifestError(f"{len(extra)} unexpected file(s), e.g. {extra[0]}")
    for rel in sorted(expected):
        if sha256_file(actual[rel]) != expected[rel].lower():
            raise ManifestError(f"file changed: {rel}")
    marker = os.path.join(app_dir, MARKER_NAME)
    if os.path.lexists(marker):
        st = os.lstat(marker)
        if not stat.S_ISREG(st.st_mode) or st.st_size > _MARKER_MAX_BYTES:
            raise ManifestError(f"unexpected {MARKER_NAME}")


# --- reading the resource ---------------------------------------------------------------------

def read_from_exe(exe_path: str) -> Optional[bytes]:
    """The raw manifest resource of ``exe_path``, or None if it carries none.

    The file is mapped as a resource-only data file (LOAD_LIBRARY_AS_DATAFILE |
    LOAD_LIBRARY_AS_IMAGE_RESOURCE): nothing in it runs, no imports are resolved. Raises OSError if
    the file cannot be mapped at all."""
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.LoadLibraryExW.restype = wintypes.HMODULE
    k32.LoadLibraryExW.argtypes = [wintypes.LPCWSTR, wintypes.HANDLE, wintypes.DWORD]
    k32.FindResourceW.restype = wintypes.HRSRC
    k32.FindResourceW.argtypes = [wintypes.HMODULE, wintypes.LPCWSTR, wintypes.LPCWSTR]
    k32.SizeofResource.restype = wintypes.DWORD
    k32.SizeofResource.argtypes = [wintypes.HMODULE, wintypes.HRSRC]
    k32.LoadResource.restype = wintypes.HGLOBAL
    k32.LoadResource.argtypes = [wintypes.HMODULE, wintypes.HRSRC]
    k32.LockResource.restype = ctypes.c_void_p
    k32.LockResource.argtypes = [wintypes.HGLOBAL]
    k32.FreeLibrary.argtypes = [wintypes.HMODULE]

    LOAD_LIBRARY_AS_DATAFILE = 0x00000002
    LOAD_LIBRARY_AS_IMAGE_RESOURCE = 0x00000020
    hmod = k32.LoadLibraryExW(exe_path, None, LOAD_LIBRARY_AS_DATAFILE | LOAD_LIBRARY_AS_IMAGE_RESOURCE)
    if not hmod:
        err = ctypes.get_last_error()
        raise OSError(err, f"could not map {exe_path} as data (Win32 error {err})")
    try:
        # MAKEINTRESOURCE(RT_RCDATA): the integer type id passed as the pointer value.
        hres = k32.FindResourceW(hmod, RESOURCE_NAME, ctypes.cast(ctypes.c_void_p(RESOURCE_TYPE),
                                                                 wintypes.LPCWSTR))
        if not hres:
            return None
        size = k32.SizeofResource(hmod, hres)
        if size == 0 or size > MAX_MANIFEST_BYTES:
            raise OSError(0, f"manifest resource has an implausible size ({size} bytes)")
        hglob = k32.LoadResource(hmod, hres)
        ptr = k32.LockResource(hglob) if hglob else None
        if not ptr:
            err = ctypes.get_last_error()
            raise OSError(err, f"could not load the manifest resource (Win32 error {err})")
        return ctypes.string_at(ptr, size)
    finally:
        k32.FreeLibrary(hmod)


def verify_app_folder(app_dir: str, exe_path: str,
                      reader: Optional[Callable[[str], Optional[bytes]]] = None) -> None:
    """Fail-closed check of a whole portable folder against the manifest in its (already
    signature-verified) exe. Returns None on a match; raises ManifestError otherwise - including
    when the exe carries no manifest: every build since 2.2.1 embeds one, and the updater only ever
    moves forward, so an exe without one is not a release this code can vouch for."""
    try:
        data = (reader or read_from_exe)(exe_path)
    except OSError as e:
        raise ManifestError(f"could not read the manifest: {e}") from None
    if data is None:
        raise ManifestError("the exe carries no file manifest")
    manifest = parse(data)
    try:
        verify_tree(app_dir, manifest, os.path.basename(exe_path))
    except OSError as e:
        raise ManifestError(f"could not read the app folder: {e}") from None
