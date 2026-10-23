"""
Every en_US key must be used by the app: each one costs 15 translators' time (#364). 2.2 shipped with
19 keys nothing read (old tooltips, the removed tray hardware row, the never-shown 'unfold' flyout,
and the dead interim data-cap dialog). This catches the next one.

A key counts as used when production code names it literally, or matches one of the few prefixes /
suffixes that code builds at runtime (listed below with where they're built).
"""
import json
import re
from pathlib import Path

PKG = Path(__file__).resolve().parents[2]   # src/netspeedtray

# Keys assembled at runtime, e.g. getattr(i18n, f"ORDER_POSITION_{i+1}").
_DYNAMIC = (
    re.compile(r"^ORDER_POSITION_\d+$"),                # views/settings/pages/widget.py
    re.compile(r"^ARROW_PRESET_[A-Z_]+$"),               # utils/components.py (ArrowStylePicker)
    re.compile(r"^(HIGH|LOW)_SPEED_(THRESHOLD|COLOR)_TOOLTIP$"),   # views/settings/pages/colors.py
)

# Unused today but kept on purpose - say why.
_KEEP: set = set()   # (the Settings > Hardware tooltips once listed here are wired up - rc.2 G6)


def _production_source() -> str:
    parts = []
    for p in PKG.parent.rglob("*.py"):   # the package + the src/monitor.py entry point
        if "tests" in p.parts:
            continue
        parts.append(p.read_text(encoding="utf-8", errors="ignore"))
    return "\n".join(parts)


def test_every_en_us_key_is_used():
    keys = json.loads((PKG / "constants" / "locales" / "en_US.json").read_text(encoding="utf-8"))
    names = set(re.findall(r"\b[A-Z][A-Z0-9_]*\b", _production_source()))
    unused = [k for k in keys
              if not k.startswith("//") and k not in _KEEP and k not in names
              and not any(rx.match(k) for rx in _DYNAMIC)]
    assert unused == [], f"en_US keys no production code reads: {unused}"
