"""
Generate THIRD-PARTY-NOTICES.txt: every component the frozen app bundles, with its version, licence,
home page and the licence text its own package ships.

    python build/third_party_notices.py [--output PATH]

Run it with the BUILD venv's Python: versions and licence texts are read from the installed
distributions' metadata (importlib.metadata), so the result describes exactly what PyInstaller packs.
The package list is requirements.txt (the pinned runtime closure); each package also needs an entry in
RUNTIME_PACKAGES below, so a new dependency fails here until someone has looked at its licence.
Texts a component requires but its wheel does not ship (Qt's third-party code, CPython's
incorporated software, OpenSSL, pywinrt, WMI) are kept in build/licences/ (provenance in its README.txt).

netspeedtray.spec calls write_release_docs() after PyInstaller has collected the app, so every build
(installer, portable zip, onefile exe) ships LICENSE.txt and a freshly generated notices file next to
NetSpeedTray.exe. The copy at the repo root is the same output for readers of the source tree;
regenerate it with this script after a dependency change (a unit test checks it against the requirements.txt pins).
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

VENDORED_DIR = os.path.join(ROOT, "build", "licences")
# Packages whose wheels ship no licence file: the text is kept in build/licences/ instead.
VENDORED_FOR_PACKAGE = {
    "winrt-runtime": "winrt/pywinrt-LICENSE.txt",
    "winrt-windows-foundation": "winrt/pywinrt-LICENSE.txt",
    "winrt-windows-foundation-collections": "winrt/pywinrt-LICENSE.txt",
    "winrt-windows-networking-connectivity": "winrt/pywinrt-LICENSE.txt",
    "wmi": "wmi/LICENSE.txt",
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


def _vendored(rel: str) -> str:
    """A licence text kept in build/licences/ (see its README.txt for where each came from)."""
    path = os.path.join(VENDORED_DIR, *rel.split("/"))
    if not os.path.isfile(path):
        raise SystemExit(f"[third_party_notices] vendored licence text missing: build/licences/{rel}")
    return _read_text(path)


def app_version() -> str:
    text = _read_text(os.path.join(ROOT, "src", "netspeedtray", "__init__.py"))
    m = re.search(r"^__version__\s*=\s*['\"]([^'\"]+)['\"]", text, re.M)
    if not m:
        raise SystemExit("[third_party_notices] __version__ not found in src/netspeedtray/__init__.py")
    return m.group(1)


def _dist_version(name: str) -> str:
    return md.distribution(name).version


def generate(requirements_path: str | None = None) -> str:
    """The full notices text. Raises SystemExit naming any package that is unclassified or missing
    from this environment, or any vendored text that is missing."""
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
        note = ""
        if not texts and name in VENDORED_FOR_PACKAGE:
            rel = VENDORED_FOR_PACKAGE[name]
            texts = [(rel.split("/")[-1], _vendored(rel))]
            note = "This package's wheel ships no licence file; the text below is kept in build/licences/."
        if not texts:
            raise SystemExit(f"[third_party_notices] {name} ships no licence file: add its text to "
                             f"build/licences/ and VENDORED_FOR_PACKAGE")
        components.append((dist.metadata["Name"], dist.version, lic, url, texts, note))
    if missing:
        raise SystemExit(f"[third_party_notices] not installed in this environment "
                         f"(pip install -r requirements.txt): {missing}")

    py_ver = sys.version.split()[0]
    py_mm = ".".join(py_ver.split(".")[:2])
    py_texts = []
    py_license = os.path.join(sys.base_prefix, "LICENSE.txt")
    if os.path.isfile(py_license):
        py_texts.append(("LICENSE.txt (this Python build's own)", _read_text(py_license)))
    py_texts += [
        ("Licenses and Acknowledgements for Incorporated Software (CPython Doc/license.rst)",
         _vendored("python/incorporated-software.txt")),
        ("HACL* (hashlib's MD5/SHA-1/SHA-2/SHA-3)", _vendored("python/hacl-LICENSE.txt")),
        ("XZ Utils / liblzma (_lzma.pyd)", _vendored("python/xz-COPYING.txt")),
    ]
    components.append((
        "Python (CPython runtime)", py_ver, "PSF-2.0, plus the licences of the software it incorporates",
        "https://www.python.org", py_texts,
        f"Licenses and Acknowledgements for Incorporated Software (expat, libmpdec, mimalloc, zlib, "
        f"libffi and more): https://docs.python.org/{py_mm}/license.html - reproduced below."))
    components.append((
        "OpenSSL (bundled with Python: libcrypto-3.dll, libssl-3.dll)",
        ssl.OPENSSL_VERSION.split()[1], "Apache-2.0", "https://www.openssl.org",
        [("LICENSE.txt", _vendored("openssl/LICENSE.txt"))], ""))
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
        raise SystemExit("[third_party_notices] pyinstaller is not installed: run this with the build venv")
    components.append((
        "Third-party code inside the Qt libraries", "", "FTL, Old MIT, libpng, BSD-3-Clause, MIT",
        "https://doc.qt.io/qt-6/licenses-used-in-qt.html",
        [("FreeType (used under the FreeType License, FTL)", _vendored("qt/freetype-FTL.txt")),
         ("HarfBuzz", _vendored("qt/harfbuzz-COPYING.txt")),
         ("libpng", _vendored("qt/libpng-LICENSE.txt")),
         ("PCRE2", _vendored("qt/pcre2-LICENCE.txt")),
         ("double-conversion", _vendored("qt/double-conversion-LICENSE.txt")),
         ("md4c", _vendored("qt/md4c-LICENSE.txt"))],
        "Portions of this software are copyright (c) 2025 The FreeType Project (www.freetype.org). All rights "
        "reserved. The Qt DLLs carry code from the projects below; the full list for Qt 6 is at "
        "https://doc.qt.io/qt-6/licenses-used-in-qt.html."))
    mpl = md.distribution("matplotlib")
    font_texts = []
    for lic_name in ("LICENSE_DEJAVU", "LICENSE_STIX"):
        path = str(mpl.locate_file(f"matplotlib/mpl-data/fonts/ttf/{lic_name}"))
        if not os.path.isfile(path):
            raise SystemExit(f"[third_party_notices] matplotlib's {lic_name} not found at {path}")
        font_texts.append((lic_name, _read_text(path)))
    components.append((
        "Fonts bundled with matplotlib (DejaVu, STIX, Computer Modern/BaKoMa, Last Resort)", "",
        "Bitstream Vera / DejaVu licence, STIX Font License, BaKoMa Fonts Licence, SIL OFL 1.1",
        "https://matplotlib.org", font_texts,
        "The DejaVu and STIX licences are reproduced below. The BaKoMa, Last Resort and STIX font "
        "licences are also reproduced in this file's matplotlib section."))
    components.append((
        "Microsoft Visual C++ runtime (vcruntime140*.dll, msvcp140*.dll)", "",
        "Microsoft Visual Studio redistributable", "https://learn.microsoft.com/cpp/windows/redistributing-visual-cpp-files",
        [], "Redistributable Microsoft code shipped with Python, Qt and numpy; not open source. See the "
            "'Additional Conditions for this Windows binary build' in the Python section."))

    app_ver = app_version()
    qt_ver = _dist_version("pyqt6-qt6")
    qt_mm = ".".join(qt_ver.split(".")[:2])
    pyqt_ver = _dist_version("pyqt6")
    bidi_ver = _dist_version("python-bidi")
    pyi_ver = pyi.version

    out = []
    w = out.append
    w("NetSpeedTray - third-party notices")
    w("=" * 34)
    w("")
    w("NetSpeedTray is free software: you can redistribute it and/or modify it under the terms of the")
    w("GNU General Public License version 3 (see LICENSE.txt next to this file).")
    w("")
    w("This program bundles the components listed below. Each is the copyright of its respective")
    w("authors and is distributed under its own licence, reproduced in its section.")
    w("")
    w("Qt (LGPL-3.0): NetSpeedTray uses the Qt 6 libraries unmodified (some unused modules omitted) and")
    w("dynamically linked. They ship as separate DLLs under _internal\\PyQt6\\Qt6\\, and you may replace")
    w("them with your own build of a compatible Qt version. The LGPL-3.0 adds permissions to the")
    w("GPL-3.0, whose full text is LICENSE.txt.")
    w("")
    w("Source code (GPLv3 section 6(d)) - the Corresponding Source of this build:")
    w(f"* NetSpeedTray {app_ver}: {SOURCE_URL}/tree/v{app_ver} (the tagged release)")
    w(f"* Qt {qt_ver}: https://download.qt.io/archive/qt/{qt_mm}/{qt_ver}/single/ "
      f"(per module: .../{qt_ver}/submodules/)")
    w(f"* PyQt6 {pyqt_ver}: https://files.pythonhosted.org/packages/source/P/PyQt6/pyqt6-{pyqt_ver}.tar.gz")
    w(f"* python-bidi {bidi_ver}: "
      f"https://files.pythonhosted.org/packages/source/p/python-bidi/python_bidi-{bidi_ver}.tar.gz")
    w(f"* PyInstaller {pyi_ver} (bootloader): "
      f"https://files.pythonhosted.org/packages/source/p/pyinstaller/pyinstaller-{pyi_ver}.tar.gz "
      f"(also https://github.com/pyinstaller/pyinstaller/tree/v{pyi_ver})")
    w("")
    w("Generated by build/third_party_notices.py from the build environment's package metadata and the")
    w("texts in build/licences/.")
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
