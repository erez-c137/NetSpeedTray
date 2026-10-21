"""
DATABASE.md must not promise row flags nothing writes (2.2 RC checkup F7): it documented net_raw.flags
bits 4 (resumed from sleep) and 8 (first sample) as if they were set, while no code sets either. While
that stays true the document has to call them reserved; once code sets one, this test says to update
the document.
"""
import re
from pathlib import Path

from netspeedtray.core.db import schema_v8 as S

SRC = Path(__file__).resolve().parents[2]          # src/netspeedtray
REPO = SRC.parents[1]


def _setters(name: str):
    hits = []
    for p in SRC.rglob("*.py"):
        if "tests" in p.parts or p.name == "schema_v8.py":
            continue
        text = p.read_text(encoding="utf-8")
        # A use is anything but a mention in a docstring/comment: the bare constant in code.
        for line in text.splitlines():
            code = line.split("#", 1)[0]
            if re.search(rf"\b{name}\b", code) and "``" not in code:
                hits.append((p.name, line.strip()))
    return hits


def test_unset_flag_bits_are_documented_as_reserved():
    doc = (REPO / "DATABASE.md").read_text(encoding="utf-8")
    para = doc[doc.index("`flags`: `1`"):]
    para = para[:para.index("\n\n")]
    for name, bit in (("FLAG_RESUMED", S.FLAG_RESUMED), ("FLAG_FIRST_SAMPLE", S.FLAG_FIRST_SAMPLE)):
        if _setters(name):
            continue   # set somewhere now: document what it means instead
        assert f"`{bit}`" in para and "reserved" in para, (
            f"{name} ({bit}) is set nowhere; DATABASE.md must call it reserved")
