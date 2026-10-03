"""Public QA must require explicit real-input scenarios and report narrow scope."""
from pathlib import Path

import pytest

from renpy_bridge.errors import BridgeError
from renpy_bridge.minigame_packages import validate_directory
from renpy_bridge.minigame_qa import validate_minigame_options, validate_scenario, testcase_source as build_testcase, prepare_scenario_suite
from renpy_bridge.models import Project
from renpy_bridge.pipeline import enumerate_routes, native_tests


@pytest.fixture
def project():
    root = Path(__file__).resolve().parents[1]
    info = validate_directory(root / 'examples/html_minigames/production/puzzle')
    ids = ['game', 'win', 'cancel', 'error', 'fallback']
    frames = {key: {'frame_id': key, 'act_id': 'main', 'dialogue': {'text': key}, 'flow': {'kind': 'end'}} for key in ids}
    frames['game']['flow'] = {'kind': 'minigame', 'game_id': info.manifest['game_id'], 'instance_id': 'door',
        'result_branches': [{'branch_id': 'won', 'outcome': 'win', 'effects': [], 'target_frame_id': 'win'}],
        'cancel_target_frame_id': 'cancel', 'error_target_frame_id': 'error', 'fallback_target_frame_id': 'fallback'}
    return Project.model_validate({'schema_version': '1.1', 'project_id': 'qa', 'save_namespace': 'qa',
        'entry_frame_id': 'game', 'acts': {'main': {'act_id': 'main', 'frame_ids': ids}}, 'frames': frames,
        'variables': {'flag': {'variable_id': 'flag', 'type': 'bool', 'default': False}},
        'minigames': {info.manifest['game_id']: {'game_id': info.manifest['game_id'], 'package_digest': info.digest,
            'package_version': info.manifest['version'], 'path': 'minigames/' + info.digest, 'manifest': info.manifest}}})


def scenario(**overrides):
    return {'frame_id': 'game', 'actions': [{'type': 'solve_puzzle'}], 'expected_outcome': 'win',
            'expected_target_frame_id': 'win', **overrides}


@pytest.mark.parametrize('actions', [
    [{'type': 'evaluate', 'code': 'Bridge.finish("win")'}],
    [{'type': 'click', 'selector': '#win', 'code': 'arbitrary'}],
    [{'type': 'click'}], [{'type': 'delay', 'ms': 16000}],
    [{'type': 'capture', 'name': '../escape'}], [{'type': 'key', 'key': 'a', 'virtual_key': True}],
    [{'type': 'delay', 'ms': float('nan')}],
])
def test_unsafe_or_unbounded_actions_are_rejected(actions):
    with pytest.raises(BridgeError):
        validate_minigame_options(scenario(actions=actions))


def test_target_and_variable_assertions_require_authoritative_definitions(project):
    for changes in ({'expected_target_frame_id': 'outside'}, {'expected_outcome': 'fake_win'},
                    {'expected_variables': {'flag': 1}}, {'expected_variables': {'unknown': 0}}):
        with pytest.raises(BridgeError):
            validate_scenario(project, validate_minigame_options(scenario(**changes)))


def test_structural_enumeration_includes_system_edges_without_claiming_gameplay(project):
    paths = enumerate_routes(project)
    assert {path[0]['outcome'] for path in paths} == {'win', 'cancel', 'error', 'fallback'}
    with pytest.raises(BridgeError) as error:
        native_tests(project, 'routes', {})
    assert error.value.code == 'MINIGAME_PLAYTHROUGH_REQUIRED'


def test_native_harness_uses_real_inputs_and_expected_return_without_injecting_finish(project):
    text = build_testcase(project, validate_minigame_options(scenario(expected_variables={'flag': False})))
    assert 'run Start("' in text and 'run Jump(' not in text and 'keysym "dismiss"' in text
    assert 'rb_minigame_state["completion"]' in text
    assert 'rb_story_variables["flag"] == False' in text
    assert 'finish(' not in text and 'warp' not in text
    cancel = build_testcase(project, validate_minigame_options(scenario(actions=[], expected_outcome='cancel', expected_target_frame_id='cancel')))
    assert 'click "返回剧情"' in cancel


def test_entry_dialogue_input_repeats_until_waiting_with_a_bounded_timeout(project):
    text = build_testcase(project, validate_minigame_options(scenario()))
    assert 'keysym "dismiss" until screen "rb_minigame_waiting" timeout 30.0' in text
    assert text.index('_rb_qa_visible_text()') < text.index('keysym "dismiss" until')
    assert 'run Jump(' not in text and 'finish(' not in text


def test_bounded_declared_suite_keeps_combination_coverage_unverified(project, tmp_path):
    source, prepared, coverage = prepare_scenario_suite(project, [scenario()], tmp_path, tmp_path / 'private')
    assert coverage['scenario_count'] == 1 and coverage['combination_routes_verified'] is False
    assert coverage['complete_for_finite_graph'] is False
    assert prepared[0]['case_name'] in source
    assert prepared[0]['config'].is_file()
    with pytest.raises(BridgeError):
        prepare_scenario_suite(project, [], tmp_path, tmp_path / 'private')
