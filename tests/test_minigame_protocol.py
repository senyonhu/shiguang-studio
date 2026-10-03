from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import threading

import pytest

from renpy_bridge.minigame_protocol import (
    Capabilities, Phase, ProtocolError, ProtocolLimits, ProtocolSession,
    TimeoutKind, TimeoutPolicy, message_from_dict, parse_message, serialize_message,
)


class Clock:
    def __init__(self):
        self.value = 100.0

    def __call__(self):
        return self.value


def wire(kind, event="game_event", payload=None, session="session_one"):
    return {"protocol": "1.0", "session_id": session, "event_id": event,
            "type": kind, "payload": {} if payload is None else payload}


def session(**options):
    return ProtocolSession(session_id="session_one", channel_id="native_one", load_epoch=7,
                           game_id="puzzle", package_version="1.2.3", package_hash="a" * 64,
                           capabilities=Capabilities(pause=True, checkpoint=True), **options)


def receive(active, message, **context):
    return active.receive(message, channel_id=context.get("channel_id", "native_one"),
                          load_epoch=context.get("load_epoch", 7))


def running(**options):
    active = session(**options)
    receive(active, wire("ready", "ready", {"game_id": "puzzle", "package_version": "1.2.3",
                                            "capabilities": {"pause": True, "checkpoint": True}}))
    active.init(inputs={"difficulty": 2})
    active.start()
    return active


def finish(event="victory", score=75):
    return wire("finish", event, {"outcome": "win", "results": {"score": score}})


def checkpoint(event="save_one"):
    return wire("checkpoint", event, {"checkpoint_id": "assembly_1", "snapshot_version": "1.0", "state": {"stage": 1}})


def fails(code, operation):
    with pytest.raises(ProtocolError) as caught:
        operation()
    assert caught.value.code == code


def test_utf8_roundtrip_is_detached_immutable_and_not_line_delimited_twice():
    source = wire("init", payload={"title": "温室\n修复", "items": [{"value": 4}], "muted": False, "none": None})
    message = message_from_dict(source)
    source["payload"]["items"][0]["value"] = 999
    raw = serialize_message(message)
    assert "温室" in raw and "\\n" in raw and not raw.endswith("\n")
    assert parse_message(raw.encode("utf-8")).to_dict()["payload"]["items"][0]["value"] == 4
    with pytest.raises(TypeError):
        message.payload["title"] = "changed"


@pytest.mark.parametrize("raw,code", [
    ('{"protocol":"1.0","protocol":"1.0"}', "DUPLICATE_JSON_KEY"),
    (json.dumps(wire("finish", payload={"nested": {"x": 1}})).replace('"x": 1', '"x": 1, "x": 2'), "DUPLICATE_JSON_KEY"),
    (json.dumps(wire("finish", payload={"score": float("nan")})), "UNSAFE_JSON_NUMBER"),
    (json.dumps(wire("finish", payload={"score": float("inf")})), "UNSAFE_JSON_NUMBER"),
    (json.dumps(wire("finish", payload={"score": 2**53})), "UNSAFE_JSON_NUMBER"),
    (json.dumps(wire("finish", payload={"score": 0.0})).replace('0.0', '1e9999'), "UNSAFE_JSON_NUMBER"),
    (b"\xff", "INVALID_JSON_ENCODING"),
    ('{}\n{}', "INVALID_JSON"),
    (json.dumps({**wire("finish"), "load_epoch": 7}), "INVALID_ENVELOPE"),
    (json.dumps({**wire("finish"), "protocol": "2.0"}), "PROTOCOL_VERSION_UNSUPPORTED"),
])
def test_reject_ambiguous_or_nonportable_wire_without_session_mutation(raw, code):
    fails(code, lambda: parse_message(raw))


def test_message_size_depth_and_container_limits_are_independent():
    message = wire("init", payload={"value": "x" * 60})
    fails("MESSAGE_TOO_LARGE", lambda: parse_message(json.dumps(message), limits=ProtocolLimits(max_message_bytes=140)))
    nested = {"value": {"value": {"value": {"value": 2}}}}
    fails("MESSAGE_TOO_DEEP", lambda: message_from_dict(wire("init", payload=nested), limits=ProtocolLimits(max_depth=3)))
    fails("MESSAGE_TOO_COMPLEX", lambda: message_from_dict(wire("init", payload={"list": [1, 2, 3]}),
                                                           limits=ProtocolLimits(max_container_items=2)))
    fails("INVALID_JSON_STRING", lambda: message_from_dict(wire("init", payload={"broken": "\ud800"})))


def test_exact_session_field_cannot_override_native_channel_or_load_context():
    active = running()
    fails("CHANNEL_MISMATCH", lambda: receive(active, finish(), channel_id="other_native"))
    fails("LOAD_EPOCH_MISMATCH", lambda: receive(active, finish(), load_epoch=6))
    fails("LOAD_EPOCH_MISMATCH", lambda: receive(active, finish(), load_epoch=True))
    fails("SESSION_MISMATCH", lambda: receive(active, {**finish(), "session_id": "other_session"}))
    assert active.phase == Phase.RUNNING and active.pending is None


def test_ready_requires_actual_package_identity_and_game_cannot_send_host_commands():
    active = session()
    fails("INVALID_TRANSITION", active.start)
    fails("INVALID_TRANSITION", active.init)
    fails("PACKAGE_IDENTITY_MISMATCH", lambda: receive(active, wire("ready", payload={"game_id": "other", "package_version": "1.2.3"})))
    assert active.phase == Phase.LOADING
    valid = wire("ready", payload={"game_id": "puzzle", "package_version": "1.2.3", "capabilities": {"pause": True}}, session="")
    receive(active, valid)
    init = active.init()
    assert init.payload["load_epoch"] == 7
    active.start()
    fails("MESSAGE_DIRECTION_INVALID", lambda: receive(active, wire("ack", "untrusted_ack", {"event_id": "made_up", "accepted": True})))


def test_finish_validation_does_not_commit_reward_and_pending_retransmission_is_same_ticket():
    checked = []
    active = running(result_validator=lambda payload: checked.append(payload))
    first = receive(active, finish())
    second = receive(active, finish())
    assert checked == [{"outcome": "win", "results": {"score": 75}}]
    assert first.pending is second.pending is active.pending
    assert first.ack is None and active.export_committed() == [] and active.phase == Phase.FINISH_PENDING
    fails("EVENT_ID_REUSED", lambda: receive(active, finish(score=90)))
    fails("INVALID_TRANSITION", lambda: receive(active, finish("other_victory")))
    fails("COMMIT_RECEIPT_REQUIRED", lambda: active.commit("victory", persisted_receipt={}, load_epoch=7))
    assert active.phase == Phase.FINISH_PENDING


def test_finish_after_durable_commit_replays_exact_ack_without_new_pending():
    active = running()
    receive(active, finish())
    ack = active.commit("victory", persisted_receipt={"transaction_id": "txn_31", "revision": 8}, load_epoch=7)
    assert ack.payload == {"event_id": "victory", "accepted": True, "persisted": True}
    assert active.phase == Phase.COMPLETED and active.pending is None
    duplicate = receive(active, finish())
    assert duplicate.action == "replay" and duplicate.ack is ack and duplicate.pending is None
    assert active.commit("victory", persisted_receipt={"revision": 8, "transaction_id": "txn_31"}, load_epoch=7) is ack
    fails("COMMIT_RECEIPT_MISMATCH", lambda: active.commit("victory", persisted_receipt={"transaction_id": "new_txn"}, load_epoch=7))
    fails("SESSION_TERMINAL", lambda: receive(active, finish("new_id")))
    ledger = active.export_committed()
    assert ledger[0]["persisted"] is True and ledger[0]["persisted_receipt"]["revision"] == 8
    ledger[0]["persisted_receipt"]["revision"] = 999
    assert active.export_committed()[0]["persisted_receipt"]["revision"] == 8


def test_changed_object_key_order_is_retransmission_but_changed_type_and_number_are_not():
    active = running()
    receive(active, finish())
    reversed_keys = json.dumps({"payload": {"results": {"score": 75}, "outcome": "win"},
                                "type": "finish", "event_id": "victory", "session_id": "session_one", "protocol": "1.0"})
    assert receive(active, reversed_keys).pending is active.pending
    fails("EVENT_ID_REUSED", lambda: receive(active, finish(score=75.0)))
    fails("EVENT_ID_REUSED", lambda: receive(active, checkpoint("victory")))


def test_checkpoint_waits_for_main_thread_commit_and_pause_survives_commit():
    active = running()
    receive(active, checkpoint())
    assert active.phase == Phase.CHECKPOINT_PENDING
    fails("INVALID_TRANSITION", lambda: receive(active, checkpoint("save_two")))
    active.pause(reason="save_checkpoint")
    ack = active.commit("save_one", persisted_receipt={"save_slot": "entry_2"}, load_epoch=7)
    assert active.phase == Phase.PAUSED
    assert receive(active, checkpoint()).ack is ack
    active.resume()
    assert active.phase == Phase.RUNNING
    assert receive(active, checkpoint("save_two")).action == "pending"


def test_rejected_snapshot_can_retry_under_new_event_but_rejected_id_never_gains_persisted_ack():
    active = running()
    receive(active, checkpoint())
    rejected = active.reject("save_one", reason="disk full", load_epoch=7)
    assert rejected.payload["accepted"] is False and rejected.payload["persisted"] is False
    assert receive(active, checkpoint()).ack is rejected
    fails("EVENT_NOT_PENDING", lambda: active.commit("save_one", persisted_receipt={"slot": "new"}, load_epoch=7))
    assert receive(active, checkpoint("save_new")).action == "pending"


def test_invalidated_session_retains_ack_but_fences_uncommitted_results_and_old_callbacks():
    active = running()
    receive(active, checkpoint())
    ack = active.commit("save_one", persisted_receipt={"slot": "1"}, load_epoch=7)
    receive(active, finish())
    active.invalidate()
    assert receive(active, checkpoint()).ack is ack
    fails("SESSION_TERMINAL", lambda: receive(active, finish()))
    fails("SESSION_TERMINAL", lambda: active.commit("victory", persisted_receipt={"revision": 9}, load_epoch=7))
    fails("LOAD_EPOCH_MISMATCH", lambda: receive(active, checkpoint(), load_epoch=8))
    assert active.pending is None and active.phase == Phase.INVALIDATED


def test_native_close_cannot_create_success_without_prior_commit():
    active = running()
    closed = wire("closed", "native_close", {"reason": "finish_confirmed", "event_id": "victory"})
    fails("UNEXPECTED_CLOSE", lambda: receive(active, closed))
    receive(active, finish())
    fails("UNEXPECTED_CLOSE", lambda: receive(active, closed))
    active.commit("victory", persisted_receipt={"revision": 9}, load_epoch=7)
    assert receive(active, closed).action == "closed"


def test_schema_callback_rejection_is_atomic_and_cannot_mutate_authenticated_message():
    def validate(payload):
        payload["results"]["score"] = 999
        raise ValueError("not in allowed result range")
    active = running(result_validator=validate)
    source = finish()
    fails("PAYLOAD_SCHEMA_REJECTED", lambda: receive(active, source))
    assert source["payload"]["results"]["score"] == 75
    assert active.phase == Phase.RUNNING and active.pending is None


def test_capabilities_are_intersection_of_page_and_authoritative_configuration():
    active = ProtocolSession(session_id="session_one", channel_id="native_one", load_epoch=7,
                             game_id="puzzle", package_version="1.2.3", package_hash="a" * 64,
                             capabilities=Capabilities(False, False))
    receive(active, wire("ready", "ready", {"game_id": "puzzle", "package_version": "1.2.3",
                                            "capabilities": {"pause": True, "checkpoint": True}}))
    active.init(); active.start()
    fails("CAPABILITY_UNSUPPORTED", active.pause)
    fails("CAPABILITY_UNSUPPORTED", lambda: receive(active, checkpoint()))


def test_load_gameplay_and_commit_timeout_are_separate_and_pause_does_not_spend_player_time():
    clock = Clock()
    active = session(clock=clock, timeouts=TimeoutPolicy(load_seconds=5, gameplay_seconds=10, commit_seconds=3))
    clock.value += 6
    notice = active.timeout_status()
    assert notice.kind == TimeoutKind.LOAD and active.phase == Phase.LOADING
    active = running(clock=clock, timeouts=TimeoutPolicy(load_seconds=5, gameplay_seconds=10, commit_seconds=3))
    clock.value += 4; active.pause()
    clock.value += 100
    assert active.timeout_status() is None
    active.resume(); clock.value += 5
    assert active.timeout_status() is None
    receive(active, checkpoint()); clock.value += 4
    assert active.timeout_status().kind == TimeoutKind.COMMIT
    active.commit("save_one", persisted_receipt={"slot": "1"}, load_epoch=7)
    clock.value += 1
    assert active.timeout_status().kind == TimeoutKind.GAMEPLAY
    active.cancel(reason="gameplay_timeout")
    assert active.timeout_status() is None and active.export_committed()[0]["message"]["type"] == "checkpoint"


def test_racing_duplicate_receivers_admit_one_pending_and_two_commits_get_same_ack():
    active = running()
    barrier = threading.Barrier(2)
    def read():
        barrier.wait()
        return receive(active, finish())
    with ThreadPoolExecutor(max_workers=2) as pool:
        decisions = list(pool.map(lambda _: read(), range(2)))
    assert decisions[0].pending is decisions[1].pending
    barrier = threading.Barrier(2)
    def commit():
        barrier.wait()
        return active.commit("victory", persisted_receipt={"revision": 14}, load_epoch=7)
    with ThreadPoolExecutor(max_workers=2) as pool:
        acks = list(pool.map(lambda _: commit(), range(2)))
    assert acks[0] is acks[1] and len(active.export_committed()) == 1


def test_cancel_or_load_race_never_reopens_result_or_creates_second_commit():
    active = running()
    receive(active, finish())
    barrier = threading.Barrier(2)
    def invalidate():
        barrier.wait(); active.invalidate()
    def commit():
        barrier.wait()
        try:
            return active.commit("victory", persisted_receipt={"revision": 4}, load_epoch=7)
        except ProtocolError as error:
            assert error.code == "SESSION_TERMINAL"
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(invalidate), pool.submit(commit)]
        [future.result() for future in futures]
    assert active.phase == Phase.INVALIDATED and active.pending is None
    assert len(active.export_committed()) <= 1
    fails("SESSION_TERMINAL", lambda: receive(active, finish("late_result")))


def test_event_ledger_limit_does_not_discard_ack_to_admit_unbounded_replay_ids():
    active = running(limits=ProtocolLimits(max_events=5))
    receive(active, checkpoint())  # ready + init + start + checkpoint = four events
    ack = active.commit("save_one", persisted_receipt={"slot": "1"}, load_epoch=7)
    assert receive(active, checkpoint()).ack is ack
    fails("SESSION_LIMIT_EXCEEDED", lambda: receive(active, finish()))
    assert active.phase == Phase.RUNNING and active.pending is None
