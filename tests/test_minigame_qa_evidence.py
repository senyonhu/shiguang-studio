"""Real file preservation gates, without claiming any host or gameplay execution."""
import hashlib
from pathlib import Path
import shutil

import pytest

from renpy_bridge.errors import BridgeError
from renpy_bridge.minigame_qa import preserve_window_evidence


def fixture(tmp_path):
    saves, evidence = tmp_path / 'private', tmp_path / 'evidence'
    host = saves / 'html-minigames' / ('a' * 32) / 'host-stderr.log'
    host.parent.mkdir(parents=True)
    evidence.mkdir()
    host.write_bytes(b'[html-embedded-window] {"same_root":true}\n')
    events = saves / 'minigame-events.jsonl'
    events.write_bytes(b'{"type":"launch","attempt_id":"' + b'a' * 32 + b'"}\n')
    sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    return saves, evidence, host, events, {'passed': True, 'files': [{'path': str(host), 'sha256': sha(host)}],
                                         'event_log': str(events), 'event_log_sha256': sha(events)}


def test_original_observations_survive_private_release_cleanup(tmp_path):
    saves, evidence, host, events, window = fixture(tmp_path)
    original_host, original_events = host.read_bytes(), events.read_bytes()
    result = preserve_window_evidence(window, saves, evidence)
    assert saves.resolve().is_relative_to(tmp_path.resolve())
    shutil.rmtree(saves)
    assert Path(result['files'][0]['path']).read_bytes() == original_host
    assert Path(result['event_log']).read_bytes() == original_events
    assert result['event_log_sha256'] == hashlib.sha256(original_events).hexdigest()
    assert result['files'][0]['original_path'] == str(host)
    assert result['raw_evidence_preserved'] is True
    assert window['event_log'] == str(events)


@pytest.mark.parametrize('changed', ['host', 'events'])
def test_changed_observation_is_not_archived_as_verified(tmp_path, changed):
    saves, evidence, host, events, window = fixture(tmp_path)
    (host if changed == 'host' else events).write_bytes(b'changed')
    with pytest.raises(BridgeError) as caught:
        preserve_window_evidence(window, saves, evidence)
    assert caught.value.code == 'MINIGAME_QA_EVIDENCE_INVALID'
    assert not (evidence / 'window-records').exists()


def test_unowned_file_reference_cannot_be_copied(tmp_path):
    saves, evidence, _, _, window = fixture(tmp_path)
    unrelated = tmp_path / 'unrelated.log'
    unrelated.write_bytes(b'unrelated')
    window['files'] = [{'path': str(unrelated), 'sha256': hashlib.sha256(b'unrelated').hexdigest()}]
    with pytest.raises(BridgeError):
        preserve_window_evidence(window, saves, evidence)
    assert not (evidence / 'window-records').exists()


def test_existing_evidence_is_never_overwritten(tmp_path):
    saves, evidence, _, _, window = fixture(tmp_path)
    preserve_window_evidence(window, saves, evidence)
    before = (evidence / 'window-records/minigame-events.jsonl').read_bytes()
    with pytest.raises(FileExistsError):
        preserve_window_evidence(window, saves, evidence)
    assert (evidence / 'window-records/minigame-events.jsonl').read_bytes() == before
