"""Session-wide test isolation for every test under src/netspeedtray/tests (unit AND integration).

No test may write the maintainer's real %APPDATA%/NetSpeedTray (config, history, the update records)
or %LOCALAPPDATA%/NetSpeedTray. Redirected here - the top of the test tree - so it runs before ANY
test module is imported: get_app_data_path() caches its first answer, and CI runs `pytest` from the
repo root, which collects tests/integration before tests/unit (a unit-level conftest was too late,
#402). utils/styles.py once wrote its chevron PNG cache there from every Settings test, and an
unisolated apply_update test wrote a stray update marker (#260 review). Per-test fixtures still
narrow this further where a test needs to.
"""
import os
import tempfile as _tempfile

_SESSION_PROFILE = _tempfile.mkdtemp(prefix="nst-test-profile-")
os.environ["APPDATA"] = os.path.join(_SESSION_PROFILE, "Roaming")
os.environ["LOCALAPPDATA"] = os.path.join(_SESSION_PROFILE, "Local")
