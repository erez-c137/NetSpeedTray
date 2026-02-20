import json
import os
from pathlib import Path

def load_keys(path):
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    # ignore comment keys starting with //
    return {k for k in data.keys() if not k.startswith('//')}


def test_locale_key_parity():
    base = Path(__file__).parents[3] / 'netspeedtray' / 'constants' / 'locales' / 'en_US.json'
    base_keys = load_keys(base)
    locales_dir = base.parent
    missing = []
    extra = []
    for p in locales_dir.glob('*.json'):
        if p.name == 'en_US.json':
            continue
        keys = load_keys(p)
        diff_missing = base_keys - keys
        diff_extra = keys - base_keys
        if diff_missing:
            missing.append((p.name, sorted(list(diff_missing))))
        if diff_extra:
            extra.append((p.name, sorted(list(diff_extra))))
    assert not missing, f"Locales missing keys: {missing}"
    assert not extra, f"Locales have extra keys: {extra}"
