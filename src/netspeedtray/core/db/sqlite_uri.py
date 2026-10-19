"""
The one way to open a SQLite file read-only by path: ``sqlite3.connect(sqlite_ro_uri(p), uri=True)``.

Why a helper: ``f"file:{path}?mode=ro"`` is an UNENCODED URI. A ``#`` in the path (a Windows
username like ``Bob#2`` puts one into ``%APPDATA%``) starts the fragment, so SQLite truncates the
path there, never sees ``?mode=ro``, and opens - creating it - the truncated path READ-WRITE.
``%`` and ``?`` break the same way. Here every path component is percent-encoded, and the URI
form is the one SQLite documents for Windows: ``file:///C:/...`` for a drive path and
``file:////server/share/...`` (empty authority, ``//server`` path) for a UNC path.

Qt-free (imported by the migrator, which must stay importable without Qt).
"""
import os
from urllib.parse import quote


def sqlite_uri(path: "os.PathLike[str] | str", mode: str = "ro") -> str:
    """An encoded ``file:`` URI for ``path`` with ``?mode=<mode>`` (``ro``, ``rw``, ``rwc``)."""
    p = os.path.abspath(os.fspath(path)).replace("\\", "/")
    if p.startswith("//"):          # UNC: \\server\share\... -> file:////server/share/...
        prefix = "file://"
    elif p.startswith("/"):         # POSIX absolute path
        prefix = "file://"
    else:                           # drive path C:/... -> file:///C:/...
        prefix = "file:///"
    return f"{prefix}{quote(p, safe='/:')}?mode={mode}"


def sqlite_ro_uri(path: "os.PathLike[str] | str") -> str:
    """``sqlite_uri(path, 'ro')`` - read-only, and never creates the file."""
    return sqlite_uri(path, "ro")
