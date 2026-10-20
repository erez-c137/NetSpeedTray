"""No source file may contain an invalid string escape (e.g. ``"HKCU\\...\\Explorer"`` in a docstring).

Python 3.12+ emits a SyntaxWarning for these - visible in the PyInstaller build log - and a future
version makes them a SyntaxError. Compiles every .py under src/ with that warning promoted to an error.
"""
import warnings
from pathlib import Path

SRC = Path(__file__).resolve().parents[3]


def test_no_invalid_escape_sequences_in_src():
    bad = []
    for path in sorted(SRC.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        with warnings.catch_warnings():
            warnings.simplefilter("error", SyntaxWarning)
            try:
                compile(text, str(path), "exec", dont_inherit=True)
            except SyntaxError as e:
                bad.append(f"{path.relative_to(SRC)}:{e.lineno}: {e.msg}")
    assert not bad, "\n".join(bad)
