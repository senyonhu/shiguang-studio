"""Protect generated HTML, native dependencies and Python alongside .rpy."""
import pytest

from renpy_bridge.derived_integrity import REGISTRY, record_derived_sources, verify_derived_sources
from renpy_bridge.errors import BridgeError
from renpy_bridge.storage import read_json


def generated(tmp_path):
    root = tmp_path / 'generated'
    for directory in ('game/bridge_minigames/digest', 'html-runtime/win-x64', 'game/python-packages'):
        (root / directory).mkdir(parents=True)
    for path, data in {
        'game/script.rpy': b'label start:\n    return\n',
        'game/bridge_minigames/digest/index.html': b'<html>offline</html>',
        'game/bridge_minigames/digest/LICENSE.txt': b'local license',
        'html-runtime/win-x64/HtmlMinigameHost.exe': b'host',
        'game/python-packages/runtime.py': b'VALUE = 1\n',
    }.items():
        (root / path).write_bytes(data)
    return root


ROOTS = ['game/bridge_minigames', 'html-runtime', 'game/python-packages']


@pytest.mark.parametrize('path', [
    'game/bridge_minigames/digest/index.html',
    'game/bridge_minigames/digest/LICENSE.txt',
    'html-runtime/win-x64/HtmlMinigameHost.exe',
    'game/python-packages/runtime.py',
])
def test_changed_package_host_or_python_preserves_evidence_and_refuses_use(tmp_path, path):
    root = generated(tmp_path)
    record_derived_sources(root, managed_roots=ROOTS)
    record = read_json(root / REGISTRY)
    assert record['schema_version'] == '2'
    assert path in record['sources']
    (root / path).write_bytes(b'tampered')
    with pytest.raises(BridgeError) as error:
        verify_derived_sources(root, tmp_path / 'evidence')
    assert error.value.code == 'DERIVED_SOURCES_CHANGED'
    change = error.value.details['diagnostics'][0]
    assert change['path'] == path and change['kind'] == 'modified'
    assert change['actual_backup_path']


@pytest.mark.parametrize('kind', ['added', 'missing'])
def test_complete_package_membership_is_protected(tmp_path, kind):
    root = generated(tmp_path)
    record_derived_sources(root, managed_roots=ROOTS)
    path = 'game/bridge_minigames/digest/' + ('injected.js' if kind == 'added' else 'index.html')
    if kind == 'added':
        (root / path).write_bytes(b'injected')
    else:
        (root / path).unlink()
    with pytest.raises(BridgeError) as error:
        verify_derived_sources(root, tmp_path / 'evidence')
    assert error.value.details['diagnostics'][0]['kind'] == kind


def test_v1_baseline_does_not_silently_accept_new_protected_files(tmp_path):
    root = generated(tmp_path)
    record_derived_sources(root)
    before = (root / REGISTRY).read_bytes()
    with pytest.raises(BridgeError) as error:
        record_derived_sources(root, managed_roots=ROOTS)
    assert error.value.code == 'DERIVED_BASELINE_UPGRADE_REQUIRED'
    assert (root / REGISTRY).read_bytes() == before
    assert read_json(root / REGISTRY)['schema_version'] == '1'


def test_explicit_owned_qa_script_can_be_added_without_accepting_other_changes(tmp_path):
    root = generated(tmp_path)
    record_derived_sources(root, managed_roots=ROOTS)
    (root / 'game/qa.rpy').write_bytes(b'testcase qa:\n    exit\n')
    record_derived_sources(root, controlled_paths=['game/qa.rpy'])
    assert verify_derived_sources(root, tmp_path / 'evidence')['status'] == 'passed'
    (root / 'html-runtime/injected.dll').write_bytes(b'injection')
    with pytest.raises(BridgeError):
        record_derived_sources(root, controlled_paths=['html-runtime/injected.dll'])
