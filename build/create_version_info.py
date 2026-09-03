# build/create_version_info.py
import argparse
import os
import re
import sys

# A note on the structure below, because the wrong version of it looks right and builds fine:
#
# StringFileInfo must contain a StringTable keyed by "<lang><codepage>" - it is that key that ties
# the strings to a translation. Passing the StringStructs to StringFileInfo directly (as this did
# until 2.1.3) still produces an exe: PyInstaller accepts it, the strings really are written into
# the resource, and nothing warns. But Windows finds no StringTable to read them from, so every
# consumer - Explorer's Properties > Details tab, .NET's FileVersionInfo, installers, AV reputation
# heuristics - sees a binary with no version metadata at all.
#
# "040904B0" is US English (0x0409) + codepage 1200 (0x04B0, Unicode), and the Translation entry
# below must agree with it. They previously disagreed too: the key said Unicode while Translation
# declared 1252.
#
# Version handling (v2.1.5, action plan item 6): the FixedFileInfo quad is numbers-only by Windows
# decree, so it is derived from the release core, with a prerelease's trailing number as the 4th
# (build) field - "2.2.0-beta.1" -> (2, 2, 0, 1), plain "2.1.5" -> (2, 1, 5, 0). The naive split
# on "." produced filevers=(2, 2, 0-beta, 1), a Python *expression* PyInstaller eval()s, dying
# with NameError. The FileVersion/ProductVersion *string* fields are free text and keep the full
# display version, so a beta exe is identifiable as a beta in Properties > Details.

_PRERELEASE_NUMBER_RE = re.compile(r"(\d+)\s*$")


def version_quad(version_str):
    """Derive the numeric (major, minor, patch, build) quad Windows requires.

    '2.1.5'        -> (2, 1, 5, 0)
    '2.2.0-beta.1' -> (2, 2, 0, 1)   (the prerelease number becomes the build field)
    '2.2.0-rc.2'   -> (2, 2, 0, 2)
    '2.2.0-beta'   -> (2, 2, 0, 0)   (unnumbered prerelease)
    '2.1.4.7'      -> (2, 1, 4, 7)   (an explicit 4-part core is kept as-is)

    Raises ValueError on anything whose release core is not dot-separated integers,
    so a bad tag aborts the build with a message instead of producing a
    version_info.txt that PyInstaller dies inside.
    """
    core, _, prerelease = version_str.partition("-")
    try:
        parts = [int(p) for p in core.split(".")]
    except ValueError:
        raise ValueError(
            f"Version {version_str!r}: release core {core!r} is not dot-separated integers"
        ) from None
    if not 1 <= len(parts) <= 4:
        raise ValueError(f"Version {version_str!r}: expected 1-4 numeric parts, got {len(parts)}")

    if prerelease:
        if len(parts) > 3:
            raise ValueError(
                f"Version {version_str!r}: a 4-part core cannot also carry a prerelease suffix - "
                f"the 4th field is where the prerelease number goes"
            )
        match = _PRERELEASE_NUMBER_RE.search(prerelease)
        build = int(match.group(1)) if match else 0
        parts = (parts + [0, 0])[:3] + [build]
    else:
        parts = (parts + [0, 0, 0])[:4]
    for field in parts:
        if not 0 <= field <= 0xFFFF:
            # FixedFileInfo packs each field into a 16-bit WORD; an oversized value would
            # silently wrap in the built resource (review L1). Abort loudly instead.
            raise ValueError(
                f"Version {version_str!r}: field {field} does not fit a 16-bit version WORD (0-65535)"
            )
    return tuple(parts)


def numeric_version_string(version_str):
    """The dotted numeric quad, e.g. '2.2.0.1' - the only form ISCC's VersionInfoVersion accepts."""
    return ".".join(str(p) for p in version_quad(version_str))


def create_version_file(version_str, output_path=None):
    quad = version_quad(version_str)
    # Numbers-only tuple for the eval()'d FixedFileInfo: (2, 2, 0, 1)
    version_tuple = f"({', '.join(str(p) for p in quad)})"

    content = f"""
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={version_tuple},
    prodvers={version_tuple},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo(
      [
        StringTable(
          '040904B0',
          [
            StringStruct('CompanyName', 'Erez C137'),
            StringStruct('FileDescription', 'NetSpeedTray'),
            StringStruct('FileVersion', '{version_str}'),
            StringStruct('InternalName', 'NetSpeedTray'),
            StringStruct('LegalCopyright', 'Copyright (c) Erez C137'),
            StringStruct('OriginalFilename', 'NetSpeedTray.exe'),
            StringStruct('ProductName', 'NetSpeedTray'),
            StringStruct('ProductVersion', '{version_str}'),
          ]
        )
      ]
    ),
    VarFileInfo([VarStruct('Translation', [0x0409, 1200])])
  ]
)
"""

    if output_path is None:
        output_path = os.path.join(os.path.dirname(__file__), 'version_info.txt')
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(content.strip())

    print(f"Generated version_info.txt for version {version_str}")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Generate PyInstaller version_info.txt, or print the numeric quad for ISCC."
    )
    parser.add_argument("version", help="display version, e.g. 2.1.5 or 2.2.0-beta.1")
    parser.add_argument(
        "--numeric",
        action="store_true",
        help="print the dotted numeric quad (e.g. 2.2.0.1) for ISCC's VersionInfoVersion "
        "and exit without writing anything",
    )
    parser.add_argument(
        "--output",
        help="write version_info.txt to this path instead of beside this script",
    )
    args = parser.parse_args(argv)

    try:
        if args.numeric:
            print(numeric_version_string(args.version))
        else:
            create_version_file(args.version, args.output)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
