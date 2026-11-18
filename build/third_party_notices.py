"""
Generate THIRD-PARTY-NOTICES.txt: every component the frozen app bundles, with its version, licence,
home page and the licence text its own package ships.

    python build/third_party_notices.py [--output PATH]

Run it with the BUILD venv's Python: versions and licence texts are read from the installed
distributions' metadata (importlib.metadata), so the result describes exactly what PyInstaller packs.
The package list is requirements.txt (the pinned runtime closure); each package also needs an entry in
RUNTIME_PACKAGES below, so a new dependency fails here until someone has looked at its licence.

netspeedtray.spec calls write_release_docs() after PyInstaller has collected the app, so every build
(installer, portable zip, onefile exe) ships LICENSE.txt and a freshly generated notices file next to
NetSpeedTray.exe. The copy at the repo root is the same output for readers of the source tree;
regenerate it with this script after a dependency change (a unit test checks it covers requirements.txt).
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata as md
import os
import re
import shutil
import ssl
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
NOTICES_NAME = "THIRD-PARTY-NOTICES.txt"
LICENSE_NAME = "LICENSE.txt"   # the repo's LICENSE (GPLv3), shipped with an extension Notepad opens
SOURCE_URL = "https://github.com/erez-c137/NetSpeedTray"

# Every package in requirements.txt: (licence summary, home page). Summaries follow each package's own
# metadata (License-Expression where it has one). A package missing here stops the build on purpose.
RUNTIME_PACKAGES = {
    "contourpy": ("BSD-3-Clause", "https://github.com/contourpy/contourpy"),
    "cycler": ("BSD-3-Clause", "https://github.com/matplotlib/cycler"),
    "fonttools": ("MIT", "https://github.com/fonttools/fonttools"),
    "kiwisolver": ("BSD-3-Clause", "https://github.com/nucleic/kiwi"),
    "matplotlib": ("Matplotlib License (PSF-based); bundled fonts: see their section",
                   "https://matplotlib.org"),
    "numpy": ("BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 (bundles OpenBLAS, BSD-3-Clause)",
              "https://numpy.org"),
    "packaging": ("Apache-2.0 OR BSD-2-Clause", "https://github.com/pypa/packaging"),
    "pillow": ("MIT-CMU", "https://python-pillow.github.io"),
    "psutil": ("BSD-3-Clause", "https://github.com/giampaolo/psutil"),
    "pyparsing": ("MIT", "https://github.com/pyparsing/pyparsing"),
    "pyqt6": ("GPL-3.0-only", "https://www.riverbankcomputing.com/software/pyqt/"),
    "pyqt6-qt6": ("LGPL-3.0-only (the Qt 6 libraries)", "https://www.qt.io"),
    "pyqt6-sip": ("BSD-2-Clause", "https://github.com/Python-SIP/sip"),
    "python-bidi": ("LGPL-3.0", "https://github.com/MeirKriheli/python-bidi"),
    "python-dateutil": ("Apache-2.0 AND BSD-3-Clause", "https://github.com/dateutil/dateutil"),
    "pywin32": ("PSF-2.0", "https://github.com/mhammond/pywin32"),
    "six": ("MIT", "https://github.com/benjaminp/six"),
    "truststore": ("MIT", "https://github.com/sethmlarson/truststore"),
    "typing-extensions": ("PSF-2.0", "https://github.com/python/typing_extensions"),
    "winrt-runtime": ("MIT", "https://github.com/pywinrt/pywinrt"),
    "winrt-windows-foundation": ("MIT", "https://github.com/pywinrt/pywinrt"),
    "winrt-windows-foundation-collections": ("MIT", "https://github.com/pywinrt/pywinrt"),
    "winrt-windows-networking-connectivity": ("MIT", "https://github.com/pywinrt/pywinrt"),
    "wmi": ("MIT", "https://github.com/tjguk/wmi"),
}

_LICENSE_FILE_RE = re.compile(r"^(licen[cs]e|copying|notice|authors)", re.I)


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def runtime_requirements(path: str | None = None) -> list[str]:
    """Normalized names of the pinned packages in requirements.txt (pip-compile output)."""
    path = path or os.path.join(ROOT, "requirements.txt")
    names = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.split("#", 1)[0].strip()
            m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*==", line)
            if m:
                names.append(normalize(m.group(1)))
    return sorted(set(names))


def _read_text(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read().replace("\r\n", "\n").strip("\n")


def _license_texts(dist: md.Distribution) -> list[tuple[str, str]]:
    """(label, text) of every licence file the distribution declares; failing that, of any
    LICENSE*/COPYING* file at the top of its .dist-info."""
    files = [f for f in (dist.files or []) if ".dist-info" in str(f.parts[0])]
    declared = dist.metadata.get_all("License-File") or []
    picked = []
    for decl in declared:
        decl = decl.replace("\\", "/")
        for f in files:
            rel = "/".join(f.parts[1:])
            if rel in (decl, "licenses/" + decl):
                picked.append((decl, f))
                break
    if not picked:
        picked = [(f.name, f) for f in files if len(f.parts) == 2 and _LICENSE_FILE_RE.match(f.name)]
    out = []
    for label, f in picked:
        path = str(f.locate())
        if os.path.isfile(path):
            out.append((label, _read_text(path)))
    return out


def _python_license() -> str | None:
    path = os.path.join(sys.base_prefix, "LICENSE.txt")
    return _read_text(path) if os.path.isfile(path) else None


def generate(requirements_path: str | None = None) -> str:
    """The full notices text. Raises SystemExit naming any package that is unclassified or missing
    from this environment."""
    names = runtime_requirements(requirements_path)
    unknown = [n for n in names if n not in RUNTIME_PACKAGES]
    if unknown:
        raise SystemExit(f"[third_party_notices] add a licence entry to RUNTIME_PACKAGES for: {unknown}")

    components = []  # (name, version, licence, url, [(label, text)], note)
    missing = []
    for name in names:
        try:
            dist = md.distribution(name)
        except md.PackageNotFoundError:
            missing.append(name)
            continue
        lic, url = RUNTIME_PACKAGES[name]
        texts = _license_texts(dist)
        note = "" if texts else (f"This package ships no licence file in its wheel. Its licence is {lic}; "
                                 f"the full text and copyright notice are at {url}.")
        components.append((dist.metadata["Name"], dist.version, lic, url, texts, note))
    if missing:
        raise SystemExit(f"[third_party_notices] not installed in this environment "
                         f"(pip install -r requirements.txt): {missing}")

    py_ver = sys.version.split()[0]
    py_text = _python_license()
    components.append((
        "Python (CPython runtime)", py_ver, "PSF-2.0, plus the licences of the libraries it bundles",
        "https://www.python.org", [("LICENSE.txt", py_text)] if py_text else [],
        "" if py_text else "Full text: https://docs.python.org/3/license.html"))
    components.append((
        "OpenSSL (bundled with Python: libcrypto-3.dll, libssl-3.dll)",
        ssl.OPENSSL_VERSION.split()[1], "Apache-2.0", "https://www.openssl.org", [],
        "Licensed under the Apache License 2.0 (https://www.openssl.org/source/license.html). The "
        "full Apache-2.0 text is reproduced in the 'packaging' section of this file (LICENSE.APACHE)."))
    components.append((
        "SQLite (bundled with Python: sqlite3.dll)", "", "Public domain", "https://www.sqlite.org/copyright.html",
        [], "SQLite is in the public domain."))
    try:
        pyi = md.distribution("pyinstaller")
        components.append((
            "PyInstaller bootloader (inside NetSpeedTray.exe)", pyi.version,
            "GPL-2.0-or-later WITH Bootloader-exception", "https://pyinstaller.org",
            _license_texts(pyi), ""))
    except md.PackageNotFoundError:
        components.append((
            "PyInstaller bootloader (inside NetSpeedTray.exe)", "", "GPL-2.0-or-later WITH Bootloader-exception",
            "https://pyinstaller.org", [], "Full text: https://github.com/pyinstaller/pyinstaller/blob/develop/COPYING.txt"))
    components.append((
        "Third-party code inside the Qt libraries", "", "various (see link)",
        "https://doc.qt.io/qt-6/licenses-used-in-qt.html", [],
        "The Qt DLLs carry code from other projects (e.g. FreeType, HarfBuzz, libpng, PCRE2, zlib, "
        "md4c, double-conversion). Their licences and notices are listed at "
        "https://doc.qt.io/qt-6/licenses-used-in-qt.html."))
    components.append((
        "Fonts bundled with matplotlib (DejaVu, STIX, Computer Modern/BaKoMa, Last Resort)", "",
        "Bitstream Vera / DejaVu licence, SIL OFL 1.1, BaKoMa licence",
        "https://github.com/matplotlib/matplotlib/tree/main/LICENSE", [],
        "The DejaVu and STIX licences ship next to the fonts in "
        "_internal\\matplotlib\\mpl-data\\fonts\\ttf\\ (LICENSE_DEJAVU, LICENSE_STIX); the others are in "
        "matplotlib's LICENSE directory at the link above."))
    components.append((
        "Microsoft Visual C++ runtime (vcruntime140*.dll, msvcp140*.dll)", "",
        "Microsoft Visual Studio redistributable", "https://learn.microsoft.com/cpp/windows/redistributing-visual-cpp-files",
        [], "Redistributable Microsoft code shipped with Python, Qt and numpy; not open source. See the "
            "'Additional Conditions for this Windows binary build' in the Python section."))

    out = []
    w = out.append
    w("NetSpeedTray - third-party notices")
    w("=" * 34)
    w("")
    w("NetSpeedTray is free software: you can redistribute it and/or modify it under the terms of the")
    w("GNU General Public License version 3 (see LICENSE.txt next to this file). Source code:")
    w(f"{SOURCE_URL} (each release is a tag of that repository).")
    w("")
    w("This program bundles the components listed below. Each is the copyright of its respective")
    w("authors and is distributed under its own licence, reproduced (or linked) in its section.")
    w("")
    w("Qt (LGPL-3.0): NetSpeedTray uses the Qt 6 libraries unmodified and dynamically linked. They ship")
    w("as separate DLLs under _internal\\PyQt6\\Qt6\\, and you may replace them with your own build of a")
    w("compatible Qt version. Qt's source code is available from https://download.qt.io and")
    w("https://code.qt.io. The LGPL-3.0 adds permissions to the GPL-3.0, whose full text is LICENSE.txt.")
    w("")
    w("Generated by build/third_party_notices.py from the build environment's package metadata.")
    w("")
    w("Summary")
    w("-------")
    for name, ver, lic, url, _t, _n in components:
        w(f"* {name}{' ' + ver if ver else ''}")
        w(f"    Licence: {lic}")
        w(f"    {url}")
    seen = {}
    for name, ver, lic, url, texts, note in components:
        w("")
        w("")
        title = f"{name}{' ' + ver if ver else ''}"
        w("=" * 100)
        w(title)
        w(f"Licence: {lic}")
        w(f"Home: {url}")
        w("=" * 100)
        if note:
            w("")
            w(note)
        for label, text in texts:
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
            w("")
            w(f"--- {label} ---")
            if digest in seen:
                w(f"(Identical to {seen[digest]} above.)")
                continue
            seen[digest] = f"'{label}' in the {name} section"
            w(text)
    return "\n".join(out) + "\n"


def write_release_docs(target_dir: str, requirements_path: str | None = None) -> list[str]:
    """Put LICENSE.txt and THIRD-PARTY-NOTICES.txt into ``target_dir`` (next to NetSpeedTray.exe).
    Returns the written paths."""
    os.makedirs(target_dir, exist_ok=True)
    lic = os.path.join(target_dir, LICENSE_NAME)
    shutil.copyfile(os.path.join(ROOT, "LICENSE"), lic)
    notices = os.path.join(target_dir, NOTICES_NAME)
    with open(notices, "w", encoding="utf-8", newline="\r\n") as fh:
        fh.write(generate(requirements_path))
    return [lic, notices]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--output", default=os.path.join(ROOT, NOTICES_NAME))
    args = ap.parse_args()
    text = generate()
    with open(args.output, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    print(f"Wrote {args.output} ({len(text) // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
