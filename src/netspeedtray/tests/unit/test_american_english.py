"""
User-facing English is American English.

The app's own UI strings were already consistent - `en_US.json` never drifted - but the prose around
them had: `changelog.md` said "their own colours" in the same sentence as the real UI label "Custom
arrow colors", and the published 2.1.3 release notes shipped the British spelling. Documentation
that contradicts the product is worse than either choice made consistently.

This guards the **English text a user actually reads**: the UI strings, the README, the changelog and
the contributor-facing markdown. It deliberately does NOT police Python comments (not user-facing, and
the noise would drown the signal) or other locales - `Centre` is correct French and `grau` is not our
business.

Note `analysis` is NOT in the list: only the verb differs (analyse/analyze), and the noun is spelled
the same on both sides of the Atlantic. Same for `practice` as a noun.
"""

import json
import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).parents[4]
_LOCALES = Path(__file__).parents[3] / "netspeedtray" / "constants" / "locales"

# British -> American. Only unambiguous cases; anything a US style guide also accepts is left out.
_BRITISH = {
    "colour": "color", "colours": "colors", "coloured": "colored", "colourful": "colorful",
    "behaviour": "behavior", "behaviours": "behaviors",
    "centre": "center", "centres": "centers", "centred": "centered",
    "grey": "gray", "greyed": "grayed",
    "licence": "license", "defence": "defense", "offence": "offense",
    "catalogue": "catalog", "programme": "program", "artefact": "artifact",
    "judgement": "judgment", "ageing": "aging", "fulfil": "fulfill",
    "neighbour": "neighbor", "honour": "honor", "honours": "honors", "honoured": "honored",
    "favour": "favor", "favours": "favors", "favoured": "favored", "favourite": "favorite",
    "labelled": "labeled", "labelling": "labeling", "unlabelled": "unlabeled",
    "relabelled": "relabeled", "modelled": "modeled", "modelling": "modeling",
    "travelling": "traveling", "marvellous": "marvelous",
    "analyse": "analyze", "analysed": "analyzed", "analysing": "analyzing",
    "localise": "localize", "localised": "localized", "localisation": "localization",
    "normalise": "normalize", "normalised": "normalized", "normalisation": "normalization",
    "recognise": "recognize", "recognised": "recognized",
    "organise": "organize", "organised": "organized", "organisation": "organization",
    "initialise": "initialize", "initialised": "initialized",
    "customise": "customize", "customised": "customized", "customisable": "customizable",
    "optimise": "optimize", "optimised": "optimized", "optimisation": "optimization",
    "summarise": "summarize", "summarised": "summarized",
    "utilise": "utilize", "utilised": "utilized", "utilisation": "utilization",
    "prioritise": "prioritize", "visualise": "visualize",
    "synchronise": "synchronize", "synchronised": "synchronized",
    "minimise": "minimize", "minimised": "minimized",
    "maximise": "maximize", "maximised": "maximized",
    "apologise": "apologize", "serialise": "serialize",
    "whilst": "while", "amongst": "among",
}

_RX = re.compile(r"\b(" + "|".join(sorted(_BRITISH, key=len, reverse=True)) + r")\b", re.IGNORECASE)


def _offenders(text):
    """(british, american, line_no) for each hit."""
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        for m in _RX.finditer(line):
            w = m.group(0)
            out.append((w, _BRITISH[w.lower()], i))
    return out


def test_ui_strings_are_american():
    """en_US.json is what users read inside the app - the highest-stakes file here."""
    data = json.loads((_LOCALES / "en_US.json").read_text(encoding="utf-8"))
    problems = []
    for key, value in data.items():
        if not isinstance(value, str):
            continue
        for brit, amer, _ in _offenders(value):
            problems.append(f"{key}: {value!r} uses {brit!r}, should be {amer!r}")
    assert not problems, "British spelling in en_US.json:\n  " + "\n  ".join(problems)


@pytest.mark.parametrize("name", ["changelog.md", "README.md", "TRANSLATORS.md", "privacy.md"])
def test_public_markdown_is_american(name):
    path = _ROOT / name
    if not path.exists():
        pytest.skip(f"{name} not present")
    problems = [f"line {n}: {b!r} -> {a!r}" for b, a, n in _offenders(path.read_text(encoding="utf-8"))]
    assert not problems, f"British spelling in {name}:\n  " + "\n  ".join(problems)


def test_the_word_list_itself_is_sane():
    """Every mapping must actually change the word, and never map to another British spelling."""
    for brit, amer in _BRITISH.items():
        assert brit != amer, f"{brit!r} maps to itself"
        assert amer not in _BRITISH, f"{brit!r} -> {amer!r}, which is itself flagged as British"
