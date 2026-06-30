"""
DEFAULT_* reconciliation guard (master-plan P0).

The settings live-preview shows what a value *would* look like, including when a key falls
back to its default. That only stays honest if there is a SINGLE source of truth for every
default. We keep two structures - ``DEFAULT_CONFIG`` (the seed config) and
``VALIDATION_SCHEMA`` (per-key rules incl. ``default``) - and ``_validate_config`` fills any
missing key from the schema default. If those two ever disagree, a freshly-seeded config and
a validated-from-partial config would differ for the same key: a silent default-disagreement.

These tests make any such disagreement fail CI instead of surfacing as a wrong preview/default.
"""
from netspeedtray import constants


DEFAULT_CONFIG = constants.config.defaults.DEFAULT_CONFIG
VALIDATION_SCHEMA = constants.config.defaults.VALIDATION_SCHEMA


def test_default_config_and_schema_cover_the_same_keys():
    dc = set(DEFAULT_CONFIG)
    vs = set(VALIDATION_SCHEMA)
    assert dc == vs, (
        f"DEFAULT_CONFIG and VALIDATION_SCHEMA must cover identical keys. "
        f"Only in DEFAULT_CONFIG: {sorted(dc - vs)}; only in SCHEMA: {sorted(vs - dc)}"
    )


def test_default_config_values_equal_schema_defaults():
    mismatches = {
        k: (DEFAULT_CONFIG[k], VALIDATION_SCHEMA[k].get("default"))
        for k in DEFAULT_CONFIG
        if k in VALIDATION_SCHEMA and DEFAULT_CONFIG[k] != VALIDATION_SCHEMA[k].get("default")
    }
    assert not mismatches, (
        "DEFAULT_CONFIG values must equal their VALIDATION_SCHEMA 'default' "
        f"(seed vs. validated-fallback would diverge): {mismatches}"
    )


def test_every_schema_entry_declares_a_default():
    missing = [k for k, rules in VALIDATION_SCHEMA.items() if "default" not in rules]
    assert not missing, f"Every schema entry must declare a 'default' (live-preview fallback): {missing}"


def test_validating_an_empty_config_reproduces_default_config():
    """A partial (empty) config validated must equal DEFAULT_CONFIG - the reconciliation in action."""
    from netspeedtray.utils.config import ConfigManager

    validated = ConfigManager._validate_config(ConfigManager.__new__(ConfigManager), {})
    # _validate_config may apply cross-field business rules; compare the keys it populates.
    for key, default in DEFAULT_CONFIG.items():
        assert validated.get(key) == default, (
            f"Validating an empty config should reproduce the default for '{key}': "
            f"got {validated.get(key)!r}, expected {default!r}"
        )
