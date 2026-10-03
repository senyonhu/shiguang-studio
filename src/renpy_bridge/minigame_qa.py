"""Explicit real-input minigame QA, separate from simulated result rules."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime
import json
import hashlib
import math
import re
from pathlib import Path
import shutil
import socket
import stat

from .errors import BridgeError
from .generator import stable_label
from .models import flow_targets
from .native_qa import NATIVE_QA_SOURCE
from .sdk import _execute, run_sdk
from .storage import atomic_json, file_hash, read_json


ACTION_FIELDS = {
    'click': {'type', 'selector', 'timeout'}, 'key': {'type', 'key', 'code', 'virtual_key'},
    'wait_selector': {'type', 'selector', 'timeout'}, 'wait_text': {'type', 'selector', 'text', 'timeout'},
    'assert_text': {'type', 'selector', 'text'}, 'capture': {'type', 'name'},
    'delay': {'type', 'ms'}, 'solve_puzzle': {'type'}, 'follow_signal': {'type'},
}
MAX_SCENARIOS = 1024
SCENARIOS_PER_BATCH = 64


def validate_minigame_options(options: dict) -> dict:
    allowed = {'frame_id', 'actions', 'expected_outcome', 'expected_target_frame_id', 'expected_variables', 'from_entry', 'fault_plan'}
    if not isinstance(options, dict) or set(options) - allowed:
        raise BridgeError('INVALID_JOB_OPTIONS', '小游戏实测参数含不支持的字段')
    result = dict(options)
    for field in ('frame_id', 'expected_outcome', 'expected_target_frame_id'):
        if not isinstance(result.get(field), str) or not result[field]:
            raise BridgeError('INVALID_JOB_OPTIONS', '小游戏实测需要明确帧、结果和返回目标', field=field)
    actions = result.get('actions')
    if not isinstance(actions, list) or len(actions) > 512:
        raise BridgeError('INVALID_JOB_OPTIONS', '真实输入动作必须为至多512条的列表')
    for action in actions:
        if not isinstance(action, dict) or action.get('type') not in ACTION_FIELDS or set(action) - ACTION_FIELDS[action['type']]:
            raise BridgeError('INVALID_JOB_OPTIONS', '不接受任意JS或结果注入，须使用声明的真实输入动作')
        kind = action['type']
        for field in ({'selector'} if kind in {'click', 'wait_selector', 'wait_text', 'assert_text'} else
                      {'key'} if kind == 'key' else {'name'} if kind == 'capture' else set()):
            if not isinstance(action.get(field), str) or not 0 < len(action[field]) <= 2048:
                raise BridgeError('INVALID_JOB_OPTIONS', '动作缺少有效文字字段', field=field)
        if kind in {'wait_text', 'assert_text'} and not isinstance(action.get('text'), str):
            raise BridgeError('INVALID_JOB_OPTIONS', '文字断言需要text')
        if kind == 'capture' and (not action['name'].isascii() or not action['name'].replace('_', '').replace('-', '').isalnum()):
            raise BridgeError('INVALID_JOB_OPTIONS', '截图名称只允许英文数字下划线与短横线')
        if 'timeout' in action and (type(action['timeout']) not in {int, float} or not 0 < action['timeout'] <= 30):
            raise BridgeError('INVALID_JOB_OPTIONS', '单动作等待须在0至30秒之间')
        if kind == 'delay' and (type(action.get('ms')) not in {int, float} or not 0 <= action['ms'] <= 15000):
            raise BridgeError('INVALID_JOB_OPTIONS', '单动作延迟须在0至15秒之间')
        if 'virtual_key' in action and (type(action['virtual_key']) is not int or not 0 <= action['virtual_key'] <= 255):
            raise BridgeError('INVALID_JOB_OPTIONS', '键盘码须为0至255整数')
    if type(result.get('from_entry', False)) is not bool or not isinstance(result.get('expected_variables', {}), dict):
        raise BridgeError('INVALID_JOB_OPTIONS', '入口和变量断言参数不合法')
    result.setdefault('from_entry', False)
    result.setdefault('expected_variables', {})
    fault = result.get('fault_plan')
    if fault is not None and (fault != {'type': 'terminate_own_host', 'after': 'running'}
                              or result['expected_outcome'] != 'error' or actions):
        raise BridgeError('INVALID_JOB_OPTIONS', '故障验收仅允许运行后终止本次受管宿主，须声明error且不与页面动作混用')
    if result['expected_outcome'] == 'error' and fault is None:
        raise BridgeError('INVALID_JOB_OPTIONS', 'error实测需要真实宿主故障计划，不能注入页面结果')
    try:
        json.dumps(result, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise BridgeError('INVALID_JOB_OPTIONS', '实测参数必须为有限JSON数据') from error
    return result


def validate_scenario(project, options):
    frame = project.frames.get(options['frame_id'])
    if frame is None or frame.flow.kind != 'minigame':
        raise BridgeError('MINIGAME_FRAME_REQUIRED', '实测目标必须为已登记的小游戏调用帧')
    game = project.minigames[frame.flow.game_id]
    outcomes = game.manifest['results']['outcomes']
    if options['expected_outcome'] not in [*outcomes, 'cancel', 'error']:
        raise BridgeError('INVALID_MINIGAME_SCENARIO', '期望结果不在包声明范围内')
    if options['expected_target_frame_id'] not in flow_targets(frame):
        raise BridgeError('INVALID_MINIGAME_SCENARIO', '期望目标不是该调用的受管结果目标')
    system_target = {'cancel': frame.flow.cancel_target_frame_id, 'error': frame.flow.error_target_frame_id}.get(options['expected_outcome'])
    if system_target is not None and options['expected_target_frame_id'] != system_target:
        raise BridgeError('INVALID_MINIGAME_SCENARIO', '取消和故障必须核验其声明的系统返回目标')
    if options['from_entry'] and project.entry_frame_id != frame.frame_id:
        raise BridgeError('MINIGAME_ENTRY_REQUIRED', '单场景从入口实测要求入口就是该调用；多段剧情需显式有界路线')
    from .minigame_packages import validate_typed_value
    for key, value in options['expected_variables'].items():
        definition = project.variables.get(key)
        if definition is None:
            raise BridgeError('INVALID_MINIGAME_SCENARIO', '断言变量未登记', variable_id=key)
        validate_typed_value(definition.value_schema(), value)
    return frame


def testcase_source(project, options, case_name='minigame_actual_input'):
    frame = validate_scenario(project, options)
    q = lambda value: json.dumps(value, ensure_ascii=False)
    target = project.frames[options['expected_target_frame_id']]
    lines = ['# Actual browser input QA; no finish injection.', NATIVE_QA_SOURCE,
        'init -100 python:', '    import os',
        '    if os.environ.get("RENPY_BRIDGE_MINIGAME_QA_JSON"):', '        config.developer = True',
        'testsuite global:', '    teardown:', '        exit', '',
        f'testcase {case_name}:', '    $ _test.timeout = 180.0',
        '    $ _preferences.text_cps = 0',
        '    run Start()' if options['from_entry'] else f'    run Start({q(stable_label(frame.frame_id))})']
    if frame.dialogue:
        lines += [f'    pause 0.1 until eval (_rb_qa_visible_text() == {q(frame.dialogue.text)})',
                  '    pause 0.5', '    keysym "dismiss" until screen "rb_minigame_waiting" timeout 30.0']
    lines += ['    pause 0.1 until screen "rb_minigame_waiting"',
              '    pause 0.5', '    screenshot "minigame_native_waiting"']
    if options['expected_outcome'] == 'cancel':
        lines += ['    pause 5.0', '    click "返回剧情"']
    elif options.get('fault_plan'):
        lines += ['    pause 0.1 until eval (_rb_mg.development_host_running(rb_minigame_state))',
                  '    pause 3.0',
                  '    $ _rb_mg.development_terminate_own_host(rb_minigame_state)']
    lines += [f'    pause 0.1 until eval (rb_current_frame_id == {q(target.frame_id)})',
              '    pause 0.5',
              f'    assert eval (rb_minigame_state["completion"]["outcome"] == {q(options["expected_outcome"])})',
              f'    assert eval (rb_minigame_state["completion"]["target_frame_id"] == {q(target.frame_id)})']
    if target.dialogue:
        lines += [f'    pause 0.1 until eval (_rb_qa_visible_text() == {q(target.dialogue.text)})',
                  f'    assert eval _rb_qa_assert_text({q(target.dialogue.text)})']
    for key, value in options['expected_variables'].items():
        lines.append(f'    assert eval (rb_story_variables[{q(key)}] == {value!r})')
    lines += ['    screenshot "minigame_native_return"', '']
    return '\n'.join(lines)


def prepare_qa(project, options, target, saves, *, case_name='minigame_actual_input'):
    """Create a bounded development channel in private job state only."""
    options = validate_minigame_options(options)
    frame = validate_scenario(project, options)
    game = project.minigames[frame.flow.game_id]
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    actions = saves / 'browser-actions.json'
    config = saves / 'minigame-qa.json'
    atomic_json(actions, options['actions'])
    atomic_json(config, {'development': True, 'devtools_port': port,
                        'event_log_path': str(saves / 'minigame-events.jsonl')})
    return {'config': config, 'port': port, 'actions': actions,
            'source': testcase_source(project, options, case_name), 'case_name': case_name,
            'require_same_window': True,
            'minimum_assertions': 2 + bool(project.frames[options['expected_target_frame_id']].dialogue)
                                  + len(options['expected_variables']),
            'fault_plan': options.get('fault_plan'),
            'expected_return': {'outcome': options['expected_outcome'],
                'target_frame_id': options['expected_target_frame_id'], 'frame_id': frame.frame_id,
                'instance_id': frame.flow.instance_id, 'game_id': game.game_id,
                'package_digest': game.package_digest, 'package_version': game.package_version}}


def collect_window_verification(saves):
    """Check actual host observations against this run's declared SDL parent."""
    event_path = saves / 'minigame-events.jsonl'
    launches, observations, evidence = {}, [], []
    try:
        if not event_path.is_file() or event_path.stat().st_size > 8 * 1024 * 1024:
            return {'passed': False, 'reason': 'current_run_window_events_missing'}
        events = [json.loads(line) for line in event_path.read_text(encoding='utf-8').splitlines() if line]
        launches = {item['attempt_id']: item for item in events if item.get('type') == 'launch'}
        for attempt_id, launch in launches.items():
            path = saves / 'html-minigames' / attempt_id / 'host-stderr.log'
            if (not re.fullmatch(r'[0-9a-f]{32}', attempt_id) or not path.is_file()
                    or path.stat().st_size > 8 * 1024 * 1024):
                continue
            evidence.append({'path': str(path), 'sha256': file_hash(path)})
            for line in path.read_text(encoding='utf-8', errors='replace').splitlines():
                if line.startswith('[html-embedded-window] '):
                    record = json.loads(line.split(' ', 1)[1])
                    record['attempt_id'] = attempt_id
                    record['expected_parent'] = launch['parent']
                    observations.append(record)
    except (OSError, ValueError, KeyError, TypeError):
        return {'passed': False, 'reason': 'window_evidence_invalid', 'files': evidence}
    def valid(record):
        parent = record['expected_parent']
        top_levels = record.get('visible_owned_top_levels', [])
        return (record.get('same_root') is True and record.get('ws_child') is True
                and record.get('dpi_context_equal') is True and record.get('parent_hwnd') == parent['hwnd']
                and record.get('parent_pid') == parent['process_id'] and record.get('root_hwnd') == parent['hwnd']
                and record.get('webview_root_hwnd') == parent['hwnd'] and len(top_levels) == 1
                and top_levels[0].get('hwnd') == parent['hwnd'])
    rendered = [item for item in observations if item.get('kind') == 'page_ready' and any(
        child.get('visible') and child.get('class_name') == 'Chrome_RenderWidgetHostHWND'
        and child.get('root') == item.get('parent_hwnd') for child in item.get('native_descendants', []))]
    passed = bool(launches and rendered) and all(valid(item) for item in rendered)
    return {'passed': passed, 'scope': 'actual_child_and_browser_tree_under_original_sdl_root',
            'visible_parent_count': 1 if passed else None, 'files': evidence,
            'event_log': str(event_path), 'event_log_sha256': file_hash(event_path),
            'rendered_observations': rendered, 'physical_dpi_matrix_verified': False}


def preserve_window_evidence(window, saves, evidence):
    """Retain original observations before a release's private staging is removed."""
    records = [(item['path'], item['sha256']) for item in window.get('files', [])]
    if window.get('event_log'):
        records.append((window['event_log'], window['event_log_sha256']))
    if not records:
        return window
    root = Path(saves).absolute()
    contents = []
    for value, expected in records:
        source = Path(value).absolute()
        try:
            relative = source.relative_to(root)
            allowed = relative == Path('minigame-events.jsonl') or (
                len(relative.parts) == 3 and relative.parts[0] == 'html-minigames'
                and re.fullmatch(r'[0-9a-f]{32}', relative.parts[1])
                and relative.parts[2] == 'host-stderr.log')
            if not allowed:
                raise ValueError('not an owned window observation')
            for entry in (source, *source.parents):
                info = entry.lstat()
                if entry.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
                    raise ValueError('linked evidence path')
                if entry == root:
                    break
            if not stat.S_ISREG(source.lstat().st_mode) or source.stat().st_size > 8 * 1024 * 1024:
                raise ValueError('non-regular or oversized evidence')
            data = source.read_bytes()
            if len(data) > 8 * 1024 * 1024 or hashlib.sha256(data).hexdigest() != expected:
                raise ValueError('observation changed after verification')
            name = 'minigame-events.jsonl' if len(relative.parts) == 1 else 'host-' + relative.parts[1] + '.log'
            contents.append((str(source), name, expected, data))
        except (OSError, ValueError, TypeError) as error:
            raise BridgeError('MINIGAME_QA_EVIDENCE_INVALID', '本次窗口原始证据不能安全保留', path=str(source), reason=str(error)) from error
    output = Path(evidence) / 'window-records'
    output.mkdir(exist_ok=False)
    copied = {}
    for original, name, expected, data in contents:
        destination = output / name
        with destination.open('xb') as stream:
            stream.write(data)
        if file_hash(destination) != expected:
            raise BridgeError('MINIGAME_QA_EVIDENCE_INVALID', '窗口原始证据复制哈希不同', path=str(destination))
        copied[original] = {'path': str(destination), 'sha256': expected, 'original_path': original}
    result = {**window, 'files': [copied[str(Path(item['path']).absolute())] for item in window.get('files', [])],
              'raw_evidence_preserved': True}
    if window.get('event_log'):
        original = str(Path(window['event_log']).absolute())
        result.update(event_log=copied[original]['path'], event_log_original_path=original)
    return result


def collect_owned_host_fault_verification(window, prepared, native_result):
    """Bind a real owned-host kill to its rendered SDL child and error return.

    This gate does not replace native assertions or accept a failed browser
    report. A fault with no page inputs has its own evidence requirements.
    """
    result = {'passed': False, 'scope': 'rendered_owned_host_termination_and_error_return',
              'injected_results': False}
    try:
        if not window.get('passed') or not window.get('raw_evidence_preserved'):
            raise ValueError('verified original window records required')
        if native_result.cleanup_complete is not True:
            raise ValueError('native process cleanup incomplete')
        path = Path(window['event_log'])
        data = path.read_bytes()
        if len(data) > 8 * 1024 * 1024 or hashlib.sha256(data).hexdigest() != window['event_log_sha256']:
            raise ValueError('event evidence changed')
        events = [json.loads(line) for line in data.decode('utf-8').splitlines() if line]
        if any(not isinstance(item, dict) for item in events):
            raise ValueError('invalid event record')
        def one(kind):
            values = [(index, item) for index, item in enumerate(events) if item.get('type') == kind]
            if len(values) != 1:
                raise ValueError('exactly one ' + kind + ' required')
            return values[0]
        entry_index, entry = one('entry')
        launch_index, launch = one('launch')
        kill_index, kill = one('development_owned_host_terminated')
        close_index, close = one('close')
        terminal_index, terminal = one('terminal')
        expected = prepared['expected_return']
        attempt, session, epoch = launch['attempt_id'], launch['session_id'], launch['load_epoch']
        if (not re.fullmatch(r'[0-9a-f]{32}', attempt) or not re.fullmatch(r'[0-9a-f]{32}', session)
                or type(epoch) is not int or epoch < 0 or launch.get('development') is not True
                or launch.get('window_mode') != 'embedded_same_top_level_window'):
            raise ValueError('invalid active launch identity')
        def identity(item):
            return (item.get('attempt_id') == attempt and item.get('session_id') == session
                    and type(item.get('load_epoch')) is int and item['load_epoch'] == epoch)
        for item in (entry['state'], kill, terminal['state']):
            if not identity(item):
                raise ValueError('stale attempt/session/epoch')
        for item in (launch, entry['state'], terminal['state']):
            for field in ('instance_id', 'package_digest'):
                if item.get(field) != expected[field]:
                    raise ValueError('unexpected ' + field)
        for state in (entry['state'], terminal['state']):
            if (state.get('game_id') != expected['game_id'] or state.get('package_version') != expected['package_version']
                    or state.get('entry_definition', {}).get('frame_id') != expected['frame_id']):
                raise ValueError('unexpected saved call definition')
        ready = [(index, item) for index, item in enumerate(events) if item.get('type') == 'message'
                 and item.get('message', {}).get('type') == 'ready' and item.get('action') == 'accepted']
        if len(ready) != 1:
            raise ValueError('accepted ready required')
        ready_index, ready_event = ready[0]
        message, host = ready_event['message'], ready_event['message']['payload']['host']
        if (message.get('protocol') != '1.0' or message.get('session_id') != session
                or ready_event.get('load_epoch') != epoch or host.get('development') is not True
                or host.get('game_id') != expected['game_id'] or host.get('package_hash') != expected['package_digest']
                or host.get('package_version') != expected['package_version']):
            raise ValueError('ready does not authenticate this package/session')
        if not entry_index < launch_index < ready_index < kill_index < close_index < terminal_index:
            raise ValueError('fault event order invalid')
        timestamps = [item['time'] for item in (entry, launch, ready_event, kill, close, terminal)]
        if (any(type(value) not in {int, float} or not math.isfinite(value) for value in timestamps)
                or timestamps != sorted(timestamps)):
            raise ValueError('fault event time invalid')
        if any(item.get('load_epoch') != epoch for item in (entry, close, terminal)):
            raise ValueError('event epoch mismatch')
        parent = launch['parent']
        host_pid = kill.get('pid')
        if (type(host_pid) is not int or host_pid <= 0 or host_pid == parent.get('process_id')
                or parent.get('process_id') != native_result.process_id
                or kill.get('fault') != 'terminate_own_host'):
            raise ValueError('owned process identity mismatch')
        rendered = [item for item in window['rendered_observations'] if item.get('attempt_id') == attempt]
        if not rendered:
            raise ValueError('rendered child missing')
        for record in rendered:
            tops = record.get('visible_owned_top_levels', [])
            observed = datetime.fromisoformat(record['at_utc']).timestamp()
            if (record.get('host_pid') != host_pid or record.get('parent_pid') != native_result.process_id
                    or record.get('parent_hwnd') != parent['hwnd'] or record.get('child_visible') is not True
                    or not timestamps[1] <= observed <= timestamps[3] or len(tops) != 1
                    or tops[0].get('class_name') != 'SDL_app' or tops[0].get('pid') != native_result.process_id):
                raise ValueError('rendered child does not belong to killed host/original SDL')
        state = terminal['state']
        completion = state.get('completion', {})
        if (close.get('attempt_id') != attempt or close.get('reason') != 'error'
                or state.get('phase') != 'error' or state.get('error', {}).get('code') != 'MINIGAME_HOST_EXITED'
                or expected['outcome'] != 'error' or completion.get('outcome') != 'error'
                or completion.get('target_frame_id') != expected['target_frame_id']
                or completion.get('branch_id') is not None or completion.get('receipt_id') is not None
                or completion.get('results') != {} or (state.get('last_commit') or {}).get('kind') == 'finish'):
            raise ValueError('error return or no-reward state invalid')
        if any((item.get('type') == 'message' and item.get('message', {}).get('type') == 'finish')
               or (item.get('type') == 'committed' and item.get('receipt', {}).get('kind') == 'finish') for item in events):
            raise ValueError('finish cannot substitute an owned-host fault')
        result.update(passed=True, attempt_id=attempt, session_id=session, load_epoch=epoch,
                      host_process_id=host_pid, native_process_id=native_result.process_id,
                      package_digest=expected['package_digest'], target_frame_id=expected['target_frame_id'],
                      rendered_child_verified=True, accepted_ready_verified=True, owned_termination_verified=True,
                      no_result_reward_verified=True, event_log=str(path), event_log_sha256=window['event_log_sha256'])
    except (OSError, ValueError, KeyError, TypeError, AttributeError, OverflowError) as error:
        result['reason'] = str(error)
    return result


def run_actual_qa(sdk, target, saves, environment, prepared, evidence, *, executable=None):
    fault = prepared.get('fault_plan')
    if fault is not None and (fault != {'type': 'terminate_own_host', 'after': 'running'}
                             or prepared.get('expected_return', {}).get('outcome') != 'error'
                             or read_json(prepared['actions']) != []):
        raise BridgeError('INVALID_JOB_OPTIONS', '受控故障验收不能混用页面动作或任意进程身份')
    node = shutil.which('node') if fault is None else None
    driver = Path(__file__).resolve().parents[2] / 'tools/probe_html_cdp.mjs'
    if fault is None and (not node or not driver.is_file()):
        raise BridgeError('BROWSER_QA_UNAVAILABLE', '真实HTML输入验收需要固定测试驱动和Node运行环境，不能改用伪造结果')
    env = {**environment, 'RENPY_BRIDGE_MINIGAME_QA_JSON': str(prepared['config'])}
    browser_output = evidence / 'actual-browser'
    expected_screenshots = ('minigame_native_waiting.png', 'minigame_native_return.png')
    # Each execution must produce its own images; a previous scenario is not evidence.
    for name in expected_screenshots:
        stale = target / 'tests/screenshots' / name
        if stale.is_file():
            stale.unlink()
    with ThreadPoolExecutor(max_workers=2) as workers:
        case_name = prepared.get('case_name', 'minigame_actual_input')
        native = workers.submit(_execute, [str(executable), str(target), 'test', case_name,
                    '--report-detailed', '--savedir', str(saves)], target, 240, env) if executable else workers.submit(
                    run_sdk, sdk, target, 'test', [case_name, '--report-detailed', '--savedir', str(saves)],
                    timeout_seconds=240, env=env)
        browser = None
        if fault is None:
            command = [node, str(driver), '--port', str(prepared['port']), '--url-prefix', 'https://minigame-',
                       '--actions', str(prepared['actions']), '--output', str(browser_output)]
            browser = workers.submit(_execute, command, driver.parent.parent, 200, {})
        native_result = native.result()
        browser_result = browser.result() if browser is not None else None
    browser_report = browser_output / 'report.json'
    screenshots = []
    for name in expected_screenshots:
        source = target / 'tests/screenshots' / name
        if source.is_file():
            output = evidence / name
            shutil.copyfile(source, output)
            screenshots.append({'path': str(output), 'sha256': file_hash(output)})
    native_output = native_result.stdout + native_result.stderr
    window = preserve_window_evidence(collect_window_verification(saves), saves, evidence)
    sections = re.findall(r'\[rpytest\] Test outcomes \(Detailed\)(.*?)\[rpytest\] Test outcomes \(Summary\)',
                          native_output, flags=re.DOTALL)
    statuses = re.findall(r'^\[rpytest\]\s+(PASSED|PENDING|SKIPPED|XFAILED|FAILED|XPASSED)\s+(?:global::)?'
                          + re.escape(case_name) + r'(?=\s|$)', '\n'.join(sections), flags=re.MULTILINE)
    case_passed = bool(statuses) and all(status == 'PASSED' for status in statuses)
    assertions = re.findall(r'\[rpytest\]\s+Assertions\s*:\s*(\d+)\s*\|\s*(\d+)\s+passed', native_output)
    assertion_count = int(assertions[-1][1]) if assertions else 0
    fault_verification = collect_owned_host_fault_verification(window, prepared, native_result) if fault is not None else None
    input_passed = (fault_verification['passed'] if fault is not None else
                    browser_result.exit_code == 0 and not browser_result.timed_out
                    and browser_report.is_file() and read_json(browser_report).get('status') == 'passed')
    passed = (native_result.exit_code == 0 and not native_result.timed_out and '[rpytest] Status: PASSED' in native_output
              and case_passed and len(screenshots) == len(expected_screenshots)
              and assertion_count >= prepared.get('minimum_assertions', 0)
              and (window['passed'] or not prepared.get('require_same_window', False))
              and input_passed)
    return {'passed': passed, 'scope': 'real_owned_host_failure_and_native_return' if fault is not None else 'real_browser_input_and_native_return',
            'native': asdict(native_result),
            'browser': asdict(browser_result) if browser_result is not None else {'executed': False, 'reason': 'declared_owned_host_fault_without_page_inputs'},
            'browser_report': str(browser_report) if browser_result is not None else None,
            'browser_report_sha256': file_hash(browser_report) if browser_result is not None and browser_report.is_file() else None,
            'screenshots': screenshots, 'requested_testcase': case_name, 'requested_testcase_passed': case_passed,
            'same_window_verification': window,
            'owned_host_fault_verification': fault_verification,
            'passed_native_assertions': assertion_count,
            'driver_sha256': file_hash(driver) if browser_result is not None else None,
            'injected_results': False, 'development_channel_shipped_enabled': False}


def validate_release_coverage(project, scenarios):
    """Require each declared outcome and real system exits before publishing."""
    if not isinstance(scenarios, list) or not 1 <= len(scenarios) <= MAX_SCENARIOS:
        raise BridgeError('MINIGAME_SCENARIOS_REQUIRED', '发行需要明确的真实输入验收场景')
    seen = set()
    for raw in scenarios:
        options = validate_minigame_options(raw)
        validate_scenario(project, options)
        seen.add((options['frame_id'], options['expected_outcome']))
    missing = []
    for frame in project.frames.values():
        if frame.flow.kind == 'minigame':
            outcomes = project.minigames[frame.flow.game_id].manifest['results']['outcomes']
            for outcome in [*outcomes, 'cancel', 'error']:
                if (frame.frame_id, outcome) not in seen:
                    missing.append({'frame_id': frame.frame_id, 'outcome': outcome})
    if missing:
        raise BridgeError('MINIGAME_RELEASE_COVERAGE_INCOMPLETE', '发行须真实覆盖每个调用的声明结果、取消和宿主故障', missing=missing)
    return {'minimum_outcomes_verified_by_declared_scenarios': True, 'result_branches_verified': False,
            'combination_routes_verified': False, 'complete_for_finite_graph': False,
            'scope': 'declared_outcomes_cancel_and_owned_host_failure'}


def prepare_scenario_suite(project, scenarios, target, private):
    if not isinstance(scenarios, list) or not 1 <= len(scenarios) <= MAX_SCENARIOS:
        raise BridgeError('MINIGAME_SCENARIOS_REQUIRED', '含HTML作品需要1至1024个明确的真实输入验收场景')
    prepared, sources, covered = [], [], set()
    for index, raw in enumerate(scenarios):
        options = validate_minigame_options(raw)
        frame = validate_scenario(project, options)
        covered.add(frame.frame_id)
        directory = private / f'scenario-{index:04d}'
        directory.mkdir(parents=True, exist_ok=True)
        item = prepare_qa(project, options, target, directory, case_name=f'minigame_actual_input_{index:04d}')
        item['options'] = options
        item['saves'] = directory
        source = item['source']
        sources.append(source if index == 0 else source[source.index('testcase '):])
        prepared.append(item)
    required = {frame.frame_id for frame in project.frames.values() if frame.flow.kind == 'minigame'}
    if required - covered:
        raise BridgeError('MINIGAME_SCENARIOS_INCOMPLETE', '每个小游戏调用至少需要一个真实玩法场景', frame_ids=sorted(required - covered))
    coverage = {'entry_mode': 'declared_scenarios', 'complete_for_finite_graph': False,
                'scope': 'bounded_actual_minigame_inputs_and_return_targets', 'combination_routes_verified': False,
                'scenario_count': len(prepared), 'batch_size': SCENARIOS_PER_BATCH,
                'batch_count': (len(prepared) + SCENARIOS_PER_BATCH - 1) // SCENARIOS_PER_BATCH,
                'frame_ids': sorted(covered),
                'declared_outcomes': [{'frame_id': item['options']['frame_id'], 'outcome': item['options']['expected_outcome'],
                                       'target_frame_id': item['options']['expected_target_frame_id']} for item in prepared]}
    return '\n'.join(sources), prepared, coverage


def run_scenario_suite(sdk, target, environment, prepared, evidence, *, executable=None):
    reports, batches = [], []
    requested_batches = (len(prepared) + SCENARIOS_PER_BATCH - 1) // SCENARIOS_PER_BATCH
    for index, item in enumerate(prepared):
        batch_index = index // SCENARIOS_PER_BATCH
        if index % SCENARIOS_PER_BATCH == 0:
            batches.append({'batch_index': batch_index, 'first_scenario_index': index,
                            'requested': min(SCENARIOS_PER_BATCH, len(prepared) - index), 'executed': 0,
                            'passed': False, 'scenario_reports': []})
        directory = evidence / f'minigame-scenario-{index:04d}'
        directory.mkdir(parents=True, exist_ok=True)
        report = run_actual_qa(sdk, target, item['saves'], environment, item, directory, executable=executable)
        report['scenario'] = item['options']
        report_path = directory / 'report.json'
        atomic_json(report_path, report)
        reports.append(report)
        batch = batches[-1]
        batch['executed'] += 1
        batch['scenario_reports'].append({'scenario_index': index, 'passed': report['passed'],
                                          'path': str(report_path), 'sha256': file_hash(report_path)})
        batch['passed'] = batch['executed'] == batch['requested'] and all(child['passed'] for child in batch['scenario_reports'])
        atomic_json(evidence / f'minigame-batch-{batch_index:04d}.json', batch)
        atomic_json(evidence / 'minigame-suite-progress.json', {'requested': len(prepared), 'executed': len(reports),
                   'requested_batches': requested_batches, 'batches': batches,
                   'passed': len(reports) == len(prepared) and all(value['passed'] for value in batches),
                   'combination_routes_verified': False})
        if not report['passed']:
            break
    return {'passed': len(reports) == len(prepared) and all(item['passed'] for item in reports),
            'requested': len(prepared), 'executed': len(reports), 'reports': reports, 'batches': batches,
            'batch_size': SCENARIOS_PER_BATCH, 'requested_batches': requested_batches,
            'scope': 'declared_real_input_scenarios', 'combination_routes_verified': False}
