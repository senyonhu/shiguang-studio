"""Fault evidence gates using file/report fixtures, never gameplay evidence.

No SDK, WebView, or process is launched by these tests. The release still has
to execute its native fault testcase against the real rendered child.
"""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from renpy_bridge import minigame_qa as qa
from renpy_bridge.errors import BridgeError
from renpy_bridge.sdk import ProcessResult
from renpy_bridge.storage import atomic_json


ATTEMPT, SESSION, DIGEST = 'a' * 32, 'b' * 32, 'c' * 64


def fixture_data(tmp_path):
    saves, evidence, target = (tmp_path / name for name in ('private', 'evidence', 'target'))
    for path in (saves, evidence, target / 'tests/screenshots'):
        path.mkdir(parents=True)
    identity = {'attempt_id': ATTEMPT, 'session_id': SESSION, 'load_epoch': 2}
    expected = {'outcome': 'error', 'target_frame_id': 'failure', 'frame_id': 'entry',
                'instance_id': 'door', 'game_id': 'puzzle', 'package_digest': DIGEST, 'package_version': '1.0.1'}
    state = {**identity, **{key: expected[key] for key in ('instance_id', 'game_id', 'package_digest', 'package_version')},
             'entry_definition': {'frame_id': 'entry'}, 'phase': 'entry'}
    parent = {'hwnd': 12345, 'process_id': 700, 'root_hwnd': 12345}
    events = [
        {'type': 'entry', 'time': 10.0, 'load_epoch': 2, 'state': deepcopy(state)},
        {'type': 'launch', 'time': 11.0, **identity, 'instance_id': 'door', 'package_digest': DIGEST,
         'development': True, 'window_mode': 'embedded_same_top_level_window', 'parent': parent},
        {'type': 'message', 'time': 12.0, 'load_epoch': 2, 'action': 'accepted', 'message': {
            'protocol': '1.0', 'session_id': SESSION, 'type': 'ready', 'payload': {'host': {
                'development': True, 'game_id': 'puzzle', 'package_hash': DIGEST, 'package_version': '1.0.1'}}}},
        {'type': 'development_owned_host_terminated', 'time': 15.0, **identity,
         'pid': 701, 'fault': 'terminate_own_host'},
        {'type': 'close', 'time': 16.0, 'load_epoch': 2, 'reason': 'error', 'attempt_id': ATTEMPT},
        {'type': 'terminal', 'time': 17.0, 'load_epoch': 2, 'state': {
            **state, 'phase': 'error', 'error': {'code': 'MINIGAME_HOST_EXITED'},
            'last_commit': {'kind': 'entry'}, 'completion': {'outcome': 'error', 'target_frame_id': 'failure',
                'branch_id': None, 'receipt_id': None, 'results': {}}}},
    ]
    record = {'kind': 'page_ready', 'at_utc': datetime.fromtimestamp(12.5, timezone.utc).isoformat(),
        'host_pid': 701, 'parent_pid': 700, 'parent_hwnd': 12345, 'root_hwnd': 12345,
        'webview_root_hwnd': 12345, 'child_visible': True, 'same_root': True, 'ws_child': True,
        'dpi_context_equal': True,
        'visible_owned_top_levels': [{'hwnd': 12345, 'pid': 700, 'class_name': 'SDL_app'}],
        'native_descendants': [{'class_name': 'Chrome_RenderWidgetHostHWND', 'visible': True, 'root': 12345}]}
    prepared = {'fault_plan': {'type': 'terminate_own_host', 'after': 'running'},
                'expected_return': expected, 'config': saves / 'config.json', 'actions': saves / 'actions.json',
                'case_name': 'fault_case', 'require_same_window': True, 'minimum_assertions': 3, 'port': 44444}
    atomic_json(prepared['config'], {'development': True})
    atomic_json(prepared['actions'], [])
    native = ProcessResult(['unexecuted-unit-fixture'], str(target), 0,
        '[rpytest] Test outcomes (Detailed)\n[rpytest]   PASSED fault_case\n'
        '[rpytest] Test outcomes (Summary)\n[rpytest] Assertions : 3 | 3 passed\n[rpytest] Status: PASSED\n',
        '', 1.0, False, 700, 1.0)
    return saves, evidence, target, prepared, native, events, record


def write_evidence(saves, events, record):
    (saves / 'minigame-events.jsonl').write_text('\n'.join(json.dumps(item) for item in events), encoding='utf-8')
    log = saves / 'html-minigames' / ATTEMPT / 'host-stderr.log'
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text('[html-embedded-window] ' + json.dumps(record) + '\n', encoding='utf-8')


def verify_fixture(tmp_path, mutate=None):
    saves, evidence, _, prepared, native, events, record = fixture_data(tmp_path)
    if mutate:
        mutate(events, record, prepared)
    write_evidence(saves, events, record)
    window = qa.preserve_window_evidence(qa.collect_window_verification(saves), saves, evidence)
    return qa.collect_owned_host_fault_verification(window, prepared, native)


def test_fault_requires_verified_rendering_ready_owned_kill_and_exact_error_return(tmp_path):
    report = verify_fixture(tmp_path)
    assert report['passed'] is True
    assert report['host_process_id'] == 701 and report['native_process_id'] == 700
    assert report['owned_termination_verified'] is True and report['injected_results'] is False


@pytest.mark.parametrize('mutation', [
    lambda e, r, p: e.pop(3),
    lambda e, r, p: e.append(deepcopy(e[3])),
    lambda e, r, p: e[3].update(session_id='d' * 32),
    lambda e, r, p: e[3].update(attempt_id='d' * 32),
    lambda e, r, p: e[3].update(load_epoch=1),
    lambda e, r, p: e[3].update(pid=700),
    lambda e, r, p: e[3].update(pid=999),
    lambda e, r, p: e[3].update(time=11.5),
    lambda e, r, p: e[2].update(action='ignored'),
    lambda e, r, p: e[2]['message'].update(session_id='d' * 32),
    lambda e, r, p: e[2]['message']['payload']['host'].update(package_hash='d' * 64),
    lambda e, r, p: e[1].update(development=False),
    lambda e, r, p: e[1]['parent'].update(process_id=999),
    lambda e, r, p: r.update(host_pid=999),
    lambda e, r, p: r.update(child_visible=False),
    lambda e, r, p: r.update(at_utc=datetime.fromtimestamp(16, timezone.utc).isoformat()),
    lambda e, r, p: r['visible_owned_top_levels'][0].update(class_name='other-window'),
    lambda e, r, p: e[4].update(reason='finish'),
    lambda e, r, p: e[5]['state']['error'].update(code='UNRELATED_ERROR'),
    lambda e, r, p: e[5]['state']['completion'].update(target_frame_id='win'),
    lambda e, r, p: e[5]['state']['completion'].update(branch_id='reward'),
    lambda e, r, p: e[5]['state']['completion'].update(receipt_id='reward-receipt'),
    lambda e, r, p: e[5]['state']['completion'].update(results={'score': 100}),
    lambda e, r, p: e[5]['state']['last_commit'].update(kind='finish'),
    lambda e, r, p: e[5]['state'].update(load_epoch=1),
    lambda e, r, p: e[5]['state']['entry_definition'].update(frame_id='another_call'),
    lambda e, r, p: e.insert(3, {'type': 'message', 'message': {'type': 'finish'}}),
    lambda e, r, p: e.insert(3, {'type': 'committed', 'receipt': {'kind': 'finish'}}),
])
def test_missing_stale_foreign_or_rewarded_fault_evidence_fails_closed(tmp_path, mutation):
    assert verify_fixture(tmp_path, mutation)['passed'] is False


def test_fault_does_not_start_browser_or_require_node_but_still_checks_real_record_gates(tmp_path, monkeypatch):
    saves, evidence, target, prepared, native, events, record = fixture_data(tmp_path)
    write_evidence(saves, events, record)
    def sdk_fixture(*args, **kwargs):
        for name in ('minigame_native_waiting.png', 'minigame_native_return.png'):
            (target / 'tests/screenshots' / name).write_bytes(b'unit-image-fixture-not-gameplay')
        return native
    def forbidden(*args, **kwargs):
        raise AssertionError('fault without page inputs must not launch or discover CDP')
    monkeypatch.setattr(qa, 'run_sdk', sdk_fixture)
    monkeypatch.setattr(qa, '_execute', forbidden)
    monkeypatch.setattr(qa.shutil, 'which', forbidden)
    report = qa.run_actual_qa(None, target, saves, {}, prepared, evidence)
    assert report['passed'] is True
    assert report['scope'] == 'real_owned_host_failure_and_native_return'
    assert report['browser']['executed'] is False and report['browser_report'] is None
    assert report['owned_host_fault_verification']['passed'] is True


def test_page_actions_cannot_be_smuggled_into_prepared_fault(tmp_path, monkeypatch):
    saves, evidence, target, prepared, _, _, _ = fixture_data(tmp_path)
    atomic_json(prepared['actions'], [{'type': 'click', 'selector': '#win'}])
    monkeypatch.setattr(qa, 'run_sdk', lambda *a, **k: pytest.fail('must reject before running SDK'))
    with pytest.raises(BridgeError):
        qa.run_actual_qa(None, target, saves, {}, prepared, evidence)


@pytest.mark.parametrize('changes', [{'exit_code': 1}, {'timed_out': True}, {'cleanup_complete': False}])
def test_native_failure_is_not_hidden_by_fault_records(tmp_path, monkeypatch, changes):
    saves, evidence, target, prepared, native, events, record = fixture_data(tmp_path)
    write_evidence(saves, events, record)
    from dataclasses import replace
    def sdk_fixture(*args, **kwargs):
        for name in ('minigame_native_waiting.png', 'minigame_native_return.png'):
            (target / 'tests/screenshots' / name).write_bytes(b'unit-image')
        return replace(native, **changes)
    monkeypatch.setattr(qa, 'run_sdk', sdk_fixture)
    report = qa.run_actual_qa(None, target, saves, {}, prepared, evidence)
    assert report['owned_host_fault_verification']['passed'] is (changes.get('cleanup_complete') is not False)
    assert report['passed'] is False


def test_changed_preserved_event_record_is_rejected(tmp_path):
    saves, evidence, _, prepared, native, events, record = fixture_data(tmp_path)
    write_evidence(saves, events, record)
    window = qa.preserve_window_evidence(qa.collect_window_verification(saves), saves, evidence)
    Path(window['event_log']).write_bytes(b'{"type":"untrusted"}')
    report = qa.collect_owned_host_fault_verification(window, prepared, native)
    assert report['passed'] is False and report['reason'] == 'event evidence changed'


def test_normal_scenario_cannot_use_fault_records_to_ignore_browser_failure(tmp_path, monkeypatch):
    saves, evidence, target, prepared, native, events, record = fixture_data(tmp_path)
    write_evidence(saves, events, record)
    prepared.pop('fault_plan')
    def sdk_fixture(*args, **kwargs):
        for name in ('minigame_native_waiting.png', 'minigame_native_return.png'):
            (target / 'tests/screenshots' / name).write_bytes(b'unit-image')
        return native
    def failed_browser(argv, *args, **kwargs):
        from dataclasses import replace
        atomic_json(Path(argv[argv.index('--output') + 1]) / 'report.json', {'status': 'failed'})
        return replace(native, exit_code=1, stdout='browser connection failed')
    monkeypatch.setattr(qa, 'run_sdk', sdk_fixture)
    monkeypatch.setattr(qa, '_execute', failed_browser)
    monkeypatch.setattr(qa.shutil, 'which', lambda name: 'fixture-node')
    report = qa.run_actual_qa(None, target, saves, {}, prepared, evidence)
    assert report['passed'] is False and report['owned_host_fault_verification'] is None
    assert report['browser']['exit_code'] == 1
