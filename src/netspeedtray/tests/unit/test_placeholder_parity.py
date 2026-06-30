"""
Guardrail: every locale must carry the SAME set of {placeholder} names as en_US for each key.

The app renders many strings via ``str.format(**kwargs)`` with a FIXED kwarg set per key. So:
  * a locale that introduces a placeholder en_US doesn't supply -> KeyError the moment it's shown
    (a real crash; always flagged).
  * a locale that OMITS a placeholder is safe for .format() (the extra kwarg is just unused) - but
    omitting a *meaningful* value (e.g. {procs}, {updated_at}) silently loses information, so we flag
    that too - EXCEPT grammar helpers like {plural} that many languages legitimately don't use.

Key parity (test_locales_parity) can't catch any of this because the key is still present; only
comparing placeholders can. Fails the build with a precise locale::key list, caught in the PR.
"""
import json
import re
from pathlib import Path

_LOCALES = Path(__file__).parents[3] / "netspeedtray" / "constants" / "locales"
_PLACEHOLDER = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)")   # the NAME inside {name} or {name:.0f}

# Grammar helpers a translation may legitimately drop (English-style pluralization the language
# handles differently or not at all). Dropping these is fine; adding any unknown name is not.
_DROPPABLE = {"plural"}


def _placeholder_names(value: str) -> set:
    return set(_PLACEHOLDER.findall(value.replace("{{", "").replace("}}", "")))   # ignore literal {{ }}


def test_placeholders_are_format_safe():
    en = json.loads((_LOCALES / "en_US.json").read_text(encoding="utf-8"))
    expected = {k: _placeholder_names(v) for k, v in en.items()
                if isinstance(v, str) and not k.startswith("//")}

    problems = []
    for path in sorted(_LOCALES.glob("*.json")):
        if path.name == "en_US.json":
            continue
        d = json.loads(path.read_text(encoding="utf-8"))
        for key, want in expected.items():
            if key not in d or not isinstance(d[key], str):
                continue  # missing keys are test_locales_parity's job
            got = _placeholder_names(d[key])
            extra = got - want                       # added a field the app won't supply -> KeyError
            lost = (want - got) - _DROPPABLE         # dropped a meaningful value
            if extra:
                problems.append(f"{path.name} :: {key}: adds unknown placeholder(s) {sorted(extra)} "
                                f"(would KeyError); en_US supplies {sorted(want)}")
            if lost:
                problems.append(f"{path.name} :: {key}: drops value placeholder(s) {sorted(lost)} "
                                f"(silently lost); en_US has {sorted(want)}")

    assert not problems, (
        "Placeholder problems vs en_US:\n  " + "\n  ".join(problems))
