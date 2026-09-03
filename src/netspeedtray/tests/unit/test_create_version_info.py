"""
The build chain must accept a prerelease version string, because a beta tag is built by the
same `build.bat` as a final release.

Until 2.1.5, `build/create_version_info.py` split the version on `.` and pasted the parts into
a Python tuple literal, so `2.2.0-beta.1` produced `filevers=(2, 2, 0-beta, 1)` - a Python
*expression* that PyInstaller `eval()`s, dying with `NameError: name 'beta' is not defined`.
`ast.parse` alone would NOT have caught that (`0-beta` parses fine as a BinOp), which is why the
assertions below insist every element of the quad survives `ast.literal_eval` as an `int`.

The contract pinned here (v2.1.5 action plan, item 6):
- The numeric quad comes from the release core only; a prerelease's trailing number becomes the
  4th (build) field: `2.2.0-beta.1` -> (2, 2, 0, 1); plain `2.1.5` -> (2, 1, 5, 0).
- `FileVersion`/`ProductVersion` *string* fields keep the FULL display string (Windows allows
  free text there), so the beta exe is identifiable as a beta in Properties > Details.
- `--numeric` prints the dotted quad for ISCC's `VersionInfoVersion` (which rejects any
  non-numeric version) and writes nothing - `AppVersion` keeps the display string.

#257 is the precedent for why the StringTable key and Translation entry are pinned too: version
metadata that looked present in the source was unreadable by Windows for three releases because
the StringTable/Translation plumbing was silently wrong.
"""

import ast
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parents[4]
_SCRIPT = _REPO_ROOT / "build" / "create_version_info.py"
_ARTIFACT = _REPO_ROOT / "build" / "version_info.txt"


def _load_module():
    spec = importlib.util.spec_from_file_location("create_version_info", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cvi = _load_module()

# (display version, expected numeric quad, expected dotted numeric string)
_CASES = [
    ("2.1.5", (2, 1, 5, 0), "2.1.5.0"),
    ("2.2.0-beta.1", (2, 2, 0, 1), "2.2.0.1"),
    ("2.2.0-rc.2", (2, 2, 0, 2), "2.2.0.2"),
]


def _parse_version_info(text):
    """Parse a generated version_info.txt the way that catches the 0-beta trap.

    Returns (quads, strings, string_table_key, translation) where quads maps
    filevers/prodvers to literal tuples, and strings maps StringStruct names to values.
    `ast.literal_eval` on each node is the load-bearing part: it rejects any element
    that is an expression rather than a literal.
    """
    tree = ast.parse(text, mode="eval")  # must be a single valid Python expression
    quads = {}
    strings = {}
    string_table_key = None
    translation = None
    for node in ast.walk(tree.body):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        if node.func.id == "FixedFileInfo":
            for kw in node.keywords:
                if kw.arg in ("filevers", "prodvers"):
                    quads[kw.arg] = ast.literal_eval(kw.value)
        elif node.func.id == "StringStruct":
            name, value = (ast.literal_eval(a) for a in node.args)
            strings[name] = value
        elif node.func.id == "StringTable":
            string_table_key = ast.literal_eval(node.args[0])
        elif node.func.id == "VarStruct":
            if ast.literal_eval(node.args[0]) == "Translation":
                translation = ast.literal_eval(node.args[1])
    return quads, strings, string_table_key, translation


@pytest.mark.parametrize("version, quad, _numeric", _CASES, ids=lambda c: str(c))
def test_generated_file_is_a_literal_expression_with_the_right_quad(version, quad, _numeric, tmp_path):
    """A prerelease suffix must never leak into the eval()'d tuple."""
    out = tmp_path / "version_info.txt"
    cvi.create_version_file(version, output_path=str(out))
    quads, strings, string_table_key, translation = _parse_version_info(out.read_text(encoding="utf-8"))

    assert quads["filevers"] == quad
    assert quads["prodvers"] == quad
    for value in quads.values():
        assert all(isinstance(part, int) for part in value)


@pytest.mark.parametrize("version, _quad, _numeric", _CASES, ids=lambda c: str(c))
def test_string_fields_keep_the_full_display_version(version, _quad, _numeric, tmp_path):
    """FileVersion/ProductVersion are free text; the beta exe must say it is a beta."""
    out = tmp_path / "version_info.txt"
    cvi.create_version_file(version, output_path=str(out))
    _quads, strings, _key, _translation = _parse_version_info(out.read_text(encoding="utf-8"))

    assert strings["FileVersion"] == version
    assert strings["ProductVersion"] == version
    assert strings["OriginalFilename"] == "NetSpeedTray.exe"


def test_string_table_key_and_translation_still_agree(tmp_path):
    """#257: the '040904B0' key and the Translation entry must agree, or Windows reads nothing."""
    out = tmp_path / "version_info.txt"
    cvi.create_version_file("2.2.0-beta.1", output_path=str(out))
    _quads, _strings, string_table_key, translation = _parse_version_info(out.read_text(encoding="utf-8"))

    assert string_table_key == "040904B0"
    assert translation == [0x0409, 1200]


@pytest.mark.parametrize("version, quad, numeric", _CASES, ids=lambda c: str(c))
def test_numeric_version_string_is_what_iscc_accepts(version, quad, numeric):
    """ISCC's VersionInfoVersion rejects any suffix; the quad must be digits and dots only."""
    assert cvi.version_quad(version) == quad
    assert cvi.numeric_version_string(version) == numeric


@pytest.mark.parametrize(
    "version, quad",
    [
        ("2.2.0-beta", (2, 2, 0, 0)),  # unnumbered prerelease: build field falls back to 0
        ("2.2.0-beta.10", (2, 2, 0, 10)),
        ("2.1.4.7", (2, 1, 4, 7)),  # an explicit 4-part core still round-trips
    ],
)
def test_quad_edge_cases(version, quad):
    assert cvi.version_quad(version) == quad


@pytest.mark.parametrize("version", ["not-a-version", "2.2.x", "", "2.1.5.0-beta.1"])
def test_a_core_that_is_not_numeric_fails_loudly(version):
    """Better a build abort with a message than a version_info.txt PyInstaller dies inside."""
    with pytest.raises(ValueError):
        cvi.version_quad(version)


@pytest.mark.parametrize("version, _quad, numeric", _CASES, ids=lambda c: str(c))
def test_cli_numeric_mode_prints_the_quad_and_writes_nothing(version, _quad, numeric, tmp_path):
    """build.bat captures this stdout for ISCC's /DAppVersionNumeric; it must never
    rewrite version_info.txt as a side effect."""
    before = _ARTIFACT.read_bytes() if _ARTIFACT.exists() else None
    result = subprocess.run(
        [sys.executable, str(_SCRIPT), version, "--numeric"],
        capture_output=True, text=True, cwd=str(tmp_path), timeout=60,
    )
    after = _ARTIFACT.read_bytes() if _ARTIFACT.exists() else None

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == numeric
    assert before == after, "--numeric must not touch build/version_info.txt"


def test_cli_default_mode_writes_a_parseable_file_to_output(tmp_path):
    """Pins the positional CLI build.bat / build-exe-only.bat rely on, plus --output."""
    out = tmp_path / "version_info.txt"
    result = subprocess.run(
        [sys.executable, str(_SCRIPT), "2.2.0-beta.1", "--output", str(out)],
        capture_output=True, text=True, cwd=str(tmp_path), timeout=60,
    )
    assert result.returncode == 0, result.stderr
    quads, strings, _key, _translation = _parse_version_info(out.read_text(encoding="utf-8"))
    assert quads["filevers"] == (2, 2, 0, 1)
    assert strings["ProductVersion"] == "2.2.0-beta.1"


def test_cli_rejects_garbage_with_a_nonzero_exit(tmp_path):
    result = subprocess.run(
        [sys.executable, str(_SCRIPT), "2.2.x", "--numeric"],
        capture_output=True, text=True, cwd=str(tmp_path), timeout=60,
    )
    assert result.returncode != 0


def test_quad_field_over_65535_fails_loudly():
    """Review L1: FixedFileInfo packs each field into a 16-bit WORD. An oversized prerelease
    number must abort the build with a message, not silently wrap in the version resource."""
    import pytest as _pytest
    with _pytest.raises(ValueError):
        cvi.version_quad("2.2.0-beta.99999")
