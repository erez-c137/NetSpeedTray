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
# Version handling (v2.1.5, action plan item 6; ordering fixed before the 2.2.0 beta): the
# FixedFileInfo quad is numbers-only by Windows decree, so it is derived from the release core,
# and the 4th (build) field encodes the prerelease ordering the in-app updater uses
# (core/update_checker.py _parse_version: is_final, stage_rank, stage_number):
#
#     build = is_final * 10000 + stage_rank * 1000 + N
#
#     2.2.0-alpha.3 -> 2.2.0.3      2.2.0-beta.1 -> 2.2.0.1001     2.2.0-rc.2 -> 2.2.0.2002
#     2.2.0-pre/other.N -> 2.2.0.3000+N                          2.2.0 (final) -> 2.2.0.10000
#
# Until this was fixed a final got build 0 and a beta got build N, so 2.2.0 (2.2.0.0) sorted BELOW
# its own beta.1 (2.2.0.1). Nothing we ship compares the quad today (Inno copies with
# ignoreversion, the updater compares display strings, WinGet/Store never see a prerelease), but
# anything that does - a file-version installer check, an Intune/SCCM "file version >=" detection
# rule - would have refused the final over a beta. N gets three digits (0-999); more would spill
# into the next stage, so it aborts the build instead.
#
# The naive split on "." produced filevers=(2, 2, 0-beta, 1), a Python *expression* PyInstaller
# eval()s, dying with NameError. The FileVersion/ProductVersion *string* fields are free text and
# keep the full display version, so a beta exe is identifiable as a beta in Properties > Details,
# and a prerelease also sets VS_FF_PRERELEASE so FileVersionInfo.IsPreRelease is true.

# Mirrors update_checker._PRERELEASE_STAGES (that module imports PyQt, so it is not imported
# here); test_create_version_info.py pins that both order a release sequence identically.
_PRERELEASE_STAGES = {"alpha": 0, "a": 0, "beta": 1, "b": 1, "rc": 2, "c": 2, "pre": 2}
_UNKNOWN_STAGE_RANK = 3
_FINAL_BUILD = 10000
_STAGE_SPAN = 1000
VS_FF_PRERELEASE = 0x2


def _prerelease_rank(prerelease):
    """(stage_rank, stage_number) for a prerelease suffix, parsed as the in-app updater does."""
    stage_rank, stage_number = _UNKNOWN_STAGE_RANK, 0
    for token in prerelease.replace("-", ".").split("."):
        if token.isdigit():
            stage_number = int(token)
            continue
        name = token.lower()
        if name in _PRERELEASE_STAGES:
            stage_rank = _PRERELEASE_STAGES[name]
            continue
        head = name.rstrip("0123456789")  # digits glued to the stage name, e.g. 'beta2'
        tail = name[len(head):]
        if head in _PRERELEASE_STAGES:
            stage_rank = _PRERELEASE_STAGES[head]
            if tail:
                stage_number = int(tail)
    return stage_rank, stage_number


def version_quad(version_str):
    """Derive the numeric (major, minor, patch, build) quad Windows requires.

    '2.1.5'         -> (2, 1, 5, 10000)  (a final outranks every prerelease of it)
    '2.2.0-beta.1'  -> (2, 2, 0, 1001)
    '2.2.0-rc.2'    -> (2, 2, 0, 2002)
    '2.2.0-alpha.3' -> (2, 2, 0, 3)
    '2.2.0-beta'    -> (2, 2, 0, 1000)   (unnumbered prerelease)
    '2.1.4.7'       -> (2, 1, 4, 7)      (an explicit 4-part core is kept as-is)

    Raises ValueError on anything whose release core is not dot-separated integers, or
    whose prerelease number does not fit its stage, so a bad tag aborts the build with a
    message instead of producing a version_info.txt that PyInstaller dies inside.
    """
    cleaned = version_str.strip().lstrip("vV").split("+", 1)[0]  # build metadata is not precedence
    core, _, prerelease = cleaned.partition("-")
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
                f"the 4th field is where the prerelease ordering goes"
            )
        stage_rank, number = _prerelease_rank(prerelease)
        if number >= _STAGE_SPAN:
            raise ValueError(
                f"Version {version_str!r}: prerelease number {number} does not fit the build "
                f"field (0-{_STAGE_SPAN - 1}); it would collide with the next stage"
            )
        parts = (parts + [0, 0])[:3] + [stage_rank * _STAGE_SPAN + number]
    elif len(parts) == 4:
        pass
    else:
        parts = (parts + [0, 0])[:3] + [_FINAL_BUILD]
    for field in parts:
        if not 0 <= field <= 0xFFFF:
            # FixedFileInfo packs each field into a 16-bit WORD; an oversized value would
            # silently wrap in the built resource (review L1). Abort loudly instead.
            raise ValueError(
                f"Version {version_str!r}: field {field} does not fit a 16-bit version WORD (0-65535)"
            )
    return tuple(parts)


def is_prerelease(version_str):
    return "-" in version_str.split("+", 1)[0]


def numeric_version_string(version_str):
    """The dotted numeric quad, e.g. '2.2.0.1001' - the only form ISCC's VersionInfoVersion accepts."""
    return ".".join(str(p) for p in version_quad(version_str))


def create_version_file(version_str, output_path=None):
    quad = version_quad(version_str)
    # Numbers-only tuple for the eval()'d FixedFileInfo: (2, 2, 0, 1001)
    version_tuple = f"({', '.join(str(p) for p in quad)})"
    flags = hex(VS_FF_PRERELEASE) if is_prerelease(version_str) else "0x0"

    content = f"""
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={version_tuple},
    prodvers={version_tuple},
    mask=0x3f,
    flags={flags},
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
        help="print the dotted numeric quad (e.g. 2.2.0.1001) for ISCC's VersionInfoVersion "
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
