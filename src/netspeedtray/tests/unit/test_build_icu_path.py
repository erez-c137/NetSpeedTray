"""Exercise the spec's PATH guard without running a PyInstaller build."""

import ast
import os
from pathlib import Path

import pytest


SPEC = Path(__file__).resolve().parents[4] / 'build' / 'netspeedtray.spec'


@pytest.fixture
def guard():
    # Load the actual function from the executable spec; importing the whole
    # spec would launch Analysis/EXE and require PyInstaller in the test suite.
    tree = ast.parse(SPEC.read_text(encoding='utf-8'))
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == '_remove_path_entries_shadowing_windows_icu')
    namespace = {'os': os}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(SPEC), 'exec'), namespace)
    return namespace[function.name]


@pytest.mark.parametrize('dll', ['icuuc.dll', 'icuin.dll'])
def test_foreign_icu_is_removed_but_system32_and_other_tools_stay(guard, tmp_path, monkeypatch, dll):
    system_root = tmp_path / 'Windows'
    system32 = system_root / 'System32'
    foreign = tmp_path / 'Poppler bin'
    unrelated = tmp_path / 'other tool'
    for folder in (system32, foreign, unrelated):
        folder.mkdir(parents=True)
    (system32 / dll).touch()
    (foreign / dll).touch()
    # Quoted paths and trailing separators are common in Windows build shells.
    entries = [str(unrelated), f'"{foreign}"', f'"{system32}{os.sep}"', str(unrelated)]
    monkeypatch.setenv('SystemRoot', str(system_root))
    monkeypatch.setenv('PATH', os.pathsep.join(entries))

    guard()

    assert os.environ['PATH'].split(os.pathsep) == [entries[0], entries[2], entries[3]]


def test_no_system_icu_keeps_the_original_path(guard, tmp_path, monkeypatch):
    foreign = tmp_path / 'Poppler'
    foreign.mkdir()
    (foreign / 'icuuc.dll').touch()
    monkeypatch.setenv('SystemRoot', str(tmp_path / 'no Windows ICU'))
    original = os.pathsep.join([str(foreign), 'relative-tool', ''])
    monkeypatch.setenv('PATH', original)

    guard()

    assert os.environ['PATH'] == original


def test_non_shadowing_icu_name_stays(guard, tmp_path, monkeypatch):
    system32 = tmp_path / 'Windows' / 'System32'
    system32.mkdir(parents=True)
    (system32 / 'icuuc.dll').touch()
    foreign = tmp_path / 'tool'
    foreign.mkdir()
    (foreign / 'icuin.dll').touch()
    monkeypatch.setenv('SystemRoot', str(system32.parent))
    monkeypatch.setenv('PATH', str(foreign))

    guard()

    assert os.environ['PATH'] == str(foreign)


def test_guard_runs_before_dependency_analysis():
    tree = ast.parse(SPEC.read_text(encoding='utf-8'))
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Name)]
    guard_call = next(node for node in calls
                      if node.func.id == '_remove_path_entries_shadowing_windows_icu')
    analysis = next(node for node in calls if node.func.id == 'Analysis')
    assert guard_call.lineno < analysis.lineno
