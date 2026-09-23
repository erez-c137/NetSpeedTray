"""
ConfigManager against a config file whose ACL the running user cannot use (#307).

A 2.1.6 user's NetSpeedTray_Config.json ended up writable only elevated. The folder stayed writable
(the save's temp file was created fine), but save() used shutil.move, which on Windows degrades to
COPYING over an existing destination - and that needs write access to the file itself. Every save
failed with [Errno 13] and left a tmpXXXX file behind. os.replace needs only the delete right the
folder grants, and the replacement inherits the folder's ACL, so the bad state heals on first save.

These tests use a real ACL rather than mocks: an explicit DENY entry for the current user (deny
beats allow even for an administrator, so this also reproduces on an elevated CI runner).
"""
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from netspeedtray import constants
from netspeedtray.utils.config import ConfigManager, ConfigError

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows ACL semantics")


def _whoami() -> str:
    return subprocess.run(["whoami"], capture_output=True, text=True, check=True).stdout.strip()


def _icacls(path: Path, *args: str) -> None:
    subprocess.run(["icacls", str(path), *args], capture_output=True, text=True, check=True)


@pytest.fixture
def cfg_file(tmp_path):
    path = tmp_path / "NetSpeedTray_Config.json"
    defaults = constants.config.defaults.DEFAULT_CONFIG.copy()
    path.write_text(json.dumps({**{k: v for k, v in defaults.items() if v is not None},
                                "update_rate": 0.5}), encoding="utf-8")
    yield path
    # Lift any deny entry so pytest can clean tmp_path up.
    for p in tmp_path.iterdir():
        subprocess.run(["icacls", str(p), "/reset"], capture_output=True)


def _tmp_leftovers(folder: Path):
    return [p.name for p in folder.iterdir() if p.name.startswith("tmp")]


def test_save_replaces_a_file_the_user_cannot_write(cfg_file):
    manager = ConfigManager(cfg_file)
    config = manager.load()
    _icacls(cfg_file, "/deny", f"{_whoami()}:(W)")

    config["update_rate"] = 2.0
    manager.save(config)                                   # 2.1.6: ConfigError [Errno 13]

    assert json.loads(cfg_file.read_text(encoding="utf-8"))["update_rate"] == 2.0
    assert _tmp_leftovers(cfg_file.parent) == []


def test_save_heals_the_acl(cfg_file):
    """The replacement is a new file, so it inherits the folder's ACL - the deny entry is gone and
    later saves no longer depend on the replace trick."""
    manager = ConfigManager(cfg_file)
    config = manager.load()
    _icacls(cfg_file, "/deny", f"{_whoami()}:(W)")
    config["update_rate"] = 2.0
    manager.save(config)

    with cfg_file.open("a", encoding="utf-8"):             # direct write access is back
        pass


def test_failed_save_leaves_no_temp_file(cfg_file):
    manager = ConfigManager(cfg_file)
    config = manager.load()
    config["update_rate"] = 2.0
    with patch("netspeedtray.utils.config.os.replace", side_effect=PermissionError(13, "denied")):
        with pytest.raises(ConfigError):
            manager.save(config)
    assert _tmp_leftovers(cfg_file.parent) == []


def test_unreadable_config_starts_on_defaults_and_keeps_the_original(cfg_file):
    """An unreadable config used to raise ConfigError out of load(), which monitor.py turns into a
    fatal 'NetSpeedTray must close' dialog. Start on defaults instead, move the unreadable file aside
    (the folder allows that) rather than overwrite it, and write a fresh config."""
    original = cfg_file.read_bytes()
    _icacls(cfg_file, "/deny", f"{_whoami()}:(RD)")

    config = ConfigManager(cfg_file).load()

    assert config["update_rate"] == constants.config.defaults.DEFAULT_CONFIG["update_rate"]
    aside = cfg_file.with_name(cfg_file.name + ".unreadable")
    assert aside.exists()
    subprocess.run(["icacls", str(aside), "/reset"], capture_output=True)
    assert aside.read_bytes() == original
    assert json.loads(cfg_file.read_text(encoding="utf-8"))   # a fresh, readable config
