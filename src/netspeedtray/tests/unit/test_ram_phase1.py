"""RAM phase 1 (2.2.1): pins the four startup-memory cuts so a later import can't quietly undo them.

1.1 OpenBLAS / OpenMP single-threaded - set by monitor.py before anything can import numpy.
1.2 the unread per-interface raw deque is gone from WidgetState (covered in test_widget_state).
1.3 the Settings package is not imported on the startup path.
1.4 urllib.request / utils.https load on the first update check, not at startup.

The import checks run in a fresh interpreter: the test session's own sys.modules already holds
everything some other test imported.
"""
import ast
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

SRC = Path(__file__).resolve().parents[3]
MONITOR = SRC / "monitor.py"
_BLAS_VARS = ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS")


def _run(code: str, extra_env=None) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in _BLAS_VARS}
    env["PYTHONPATH"] = str(SRC)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env.update(extra_env or {})
    out = subprocess.run([sys.executable, "-c", code], cwd=str(SRC), env=env, capture_output=True,
                         text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-3000:]
    return json.loads(out.stdout.strip().splitlines()[-1])


# --- 1.1 ------------------------------------------------------------------------------------------

def test_monitor_sets_single_threaded_blas_on_import():
    r = _run("import os, json, monitor; print(json.dumps({k: os.environ.get(k) for k in "
             "('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS')}))")
    assert r == {"OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1"}


def test_monitor_keeps_a_users_own_blas_setting():
    r = _run("import os, json, monitor; print(json.dumps([os.environ.get('OPENBLAS_NUM_THREADS'), "
             "os.environ.get('OMP_NUM_THREADS')]))", {"OPENBLAS_NUM_THREADS": "4", "OMP_NUM_THREADS": "2"})
    assert r == ["4", "2"]


def test_blas_env_is_set_before_any_other_import():
    """OpenBLAS reads the variable once, when numpy first loads - so the setdefault calls must run
    ahead of every import in monitor.py (anything imported could pull numpy in, now or later)."""
    body = ast.parse(MONITOR.read_text(encoding="utf-8")).body
    if isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant):
        body = body[1:]   # docstring
    first_import = next(i for i, n in enumerate(body) if isinstance(n, (ast.Import, ast.ImportFrom)))
    assert isinstance(body[first_import], ast.Import)
    assert [a.name for a in body[first_import].names] == ["os"]
    set_vars = []
    for node in body[first_import + 1:]:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            break
        src = ast.unparse(node)
        set_vars += [v for v in _BLAS_VARS if "environ.setdefault" in src and repr(v) in src]
    assert sorted(set_vars) == sorted(_BLAS_VARS), "both must be set before the second import"


# --- 1.3 / 1.4 ------------------------------------------------------------------------------------

_STARTUP_IMPORTS = """
import json, sys
import monitor
import netspeedtray.views.widget.main
import netspeedtray.views
import netspeedtray.core.update_installer, netspeedtray.core.update_applier
import netspeedtray.utils.export_cli, netspeedtray.core.shutdown_channel
print(json.dumps({m: m in sys.modules for m in (
    'netspeedtray.views.settings', 'netspeedtray.utils.support_bundle',
    'urllib.request', 'netspeedtray.utils.https')}))
"""


def test_startup_path_does_not_import_settings_or_the_https_stack():
    r = _run(_STARTUP_IMPORTS)
    assert r == {"netspeedtray.views.settings": False, "netspeedtray.utils.support_bundle": False,
                 "urllib.request": False, "netspeedtray.utils.https": False}, r


def test_views_package_still_exports_settings_dialog_lazily():
    r = _run("import json, sys\nimport netspeedtray.views as v\nbefore = 'netspeedtray.views.settings' in sys.modules\n"
             "from netspeedtray.views import SettingsDialog\n"
             "from netspeedtray.views.settings import SettingsDialog as S2\n"
             "try:\n    v.NoSuchThing\n    missing = 'no error'\nexcept AttributeError:\n    missing = 'AttributeError'\n"
             "print(json.dumps([before, SettingsDialog is S2, missing, 'SettingsDialog' in v.__all__]))")
    assert r == [False, True, "AttributeError", True]


def test_update_check_still_goes_through_the_verifying_https_helper(q_app):
    """Lazy import or not, the check must keep using utils.https (truststore context), never a bare
    urllib.request.urlopen."""
    from netspeedtray.core import update_checker as uc
    resp = MagicMock()
    resp.__enter__.return_value.read.return_value = json.dumps({"tag_name": "v9.9.9", "assets": []}).encode()
    worker = uc._CheckWorker()
    got = []
    worker.finished.connect(lambda *a: got.append(a[0]))
    with patch("netspeedtray.utils.https.urlopen", return_value=resp) as helper:
        worker.run()
    assert helper.call_count == 1
    assert helper.call_args.args[0].full_url == uc.RELEASES_URL
    assert got == ["v9.9.9"]
