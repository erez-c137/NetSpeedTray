"""Old location-explainer state is retired without resetting user settings."""
import json

from netspeedtray import constants
from netspeedtray.utils.config import ConfigManager


def test_old_location_hint_is_removed_and_other_settings_survive(tmp_path):
    path = tmp_path / 'NetSpeedTray_Config.json'
    original = {
        'config_version': constants.config.defaults.CONFIG_SCHEMA_VERSION,
        'location_onboarding_dismissed': True,
        'font_size': 10,
        'identity_mode': 'both',
        'show_network_identity': True,
    }
    path.write_text(json.dumps(original), encoding='utf-8')
    loaded = ConfigManager(path).load()
    assert 'location_onboarding_dismissed' not in loaded
    persisted = json.loads(path.read_text(encoding='utf-8'))
    assert 'location_onboarding_dismissed' not in persisted
    assert persisted['font_size'] == 10
    assert persisted['identity_mode'] == 'both'
    assert persisted['show_network_identity'] is True
    backup = path.with_name(path.name + '.bak.v' + original['config_version'] + '.fields')
    assert json.loads(backup.read_text(encoding='utf-8')) == original


def test_new_config_has_no_location_explainer_state(tmp_path):
    loaded = ConfigManager(tmp_path / 'new.json').load()
    assert 'location_onboarding_dismissed' not in loaded
