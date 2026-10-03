"""Bounded, transport-independent HTML minigame messages and session state.

This module never imports Ren'Py, touches a file, or applies story rewards.
The caller authenticates a native channel, persists a critical event on the
story main thread, and only then calls ``commit`` to produce an ACK. Every load
must create a fresh session/channel identity; ``load_epoch`` also fences queued
callbacks from older transports without expanding the five-field wire format.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
import math
import re
import threading
import time
from types import MappingProxyType
from typing import Any
from uuid import uuid4

from .errors import BridgeError

PROTOCOL_VERSION = "1.0"
MESSAGE_TYPES = frozenset({
    "ready", "init", "start", "pause", "resume", "checkpoint", "finish",
    "error", "cancel", "ack", "closed", "layout", "settings",
})
_ENVELOPE_KEYS = frozenset({"protocol", "session_id", "event_id", "type", "payload"})
_IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_SAFE_INTEGER = 2**53 - 1
Validator = Callable[[dict[str, Any]], Any]


class ProtocolError(BridgeError):
    """A rejected message or transition; rejection never applies story effects."""


@dataclass(frozen=True)
class ProtocolLimits:
    max_message_bytes: int = 1024 * 1024
    max_depth: int = 32
    max_string_bytes: int = 64 * 1024
    max_key_bytes: int = 128
    max_container_items: int = 8192
    max_nodes: int = 32768
    max_events: int = 4096
    max_session_bytes: int = 16 * 1024 * 1024

    def __post_init__(self) -> None:
        for name, value in vars(self).items():
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.max_depth > 64:
            raise ValueError("JSON depth cannot exceed 64")


DEFAULT_LIMITS = ProtocolLimits()


def _failure(code: str, message: str, **details: Any) -> ProtocolError:
    return ProtocolError(code, message, **details)


def _utf8(value: str) -> bytes:
    try:
        return value.encode("utf-8", errors="strict")
    except UnicodeError as error:
        raise _failure("INVALID_JSON_STRING", "JSON strings must be valid Unicode") from error


def _validate_json(value: Any, limits: ProtocolLimits) -> None:
    """Iterate before serialization; also rejects cycles, non-JSON and NaN."""
    stack = [(value, 0)]
    seen_containers: set[int] = set()
    nodes = 0
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if nodes > limits.max_nodes:
            raise _failure("MESSAGE_TOO_COMPLEX", "JSON node limit exceeded")
        if depth > limits.max_depth:
            raise _failure("MESSAGE_TOO_DEEP", "JSON depth limit exceeded")
        kind = type(current)
        if kind is dict or kind is list:
            identity = id(current)
            if identity in seen_containers:
                # Reject shared mutable containers too; callers can detach them.
                raise _failure("INVALID_JSON_VALUE", "JSON cannot contain cycles or shared containers")
            seen_containers.add(identity)
            if len(current) > limits.max_container_items:
                raise _failure("MESSAGE_TOO_COMPLEX", "JSON container limit exceeded")
            if kind is dict:
                for key, item in current.items():
                    if type(key) is not str or not key or len(_utf8(key)) > limits.max_key_bytes:
                        raise _failure("INVALID_JSON_KEY", "JSON keys must be bounded nonempty strings")
                    stack.append((item, depth + 1))
            else:
                stack.extend((item, depth + 1) for item in current)
        elif kind is str:
            if len(_utf8(current)) > limits.max_string_bytes:
                raise _failure("MESSAGE_TOO_LARGE", "JSON string limit exceeded")
        elif kind is int:
            if abs(current) > _SAFE_INTEGER:
                raise _failure("UNSAFE_JSON_NUMBER", "Integer exceeds the JavaScript safe range")
        elif kind is float:
            if not math.isfinite(current) or abs(current) > _SAFE_INTEGER:
                raise _failure("UNSAFE_JSON_NUMBER", "JSON number must be finite and bounded")
        elif current is None or kind is bool:
            pass
        else:
            raise _failure("INVALID_JSON_VALUE", "Only JSON primitive, object and array values are allowed")


def _freeze(value: Any) -> Any:
    if type(value) is dict:
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if type(value) is list:
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_thaw(item) for item in value]
    return value


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def _validate_envelope(data: Any, limits: ProtocolLimits) -> None:
    _validate_json(data, limits)
    if type(data) is not dict or set(data) != _ENVELOPE_KEYS:
        raise _failure("INVALID_ENVELOPE", "Wire envelope must have exactly five protocol fields")
    if data["protocol"] != PROTOCOL_VERSION or type(data["protocol"]) is not str:
        raise _failure("PROTOCOL_VERSION_UNSUPPORTED", "Unsupported minigame protocol")
    if type(data["type"]) is not str or data["type"] not in MESSAGE_TYPES:
        raise _failure("MESSAGE_TYPE_UNSUPPORTED", "Unsupported protocol message type")
    if type(data["session_id"]) is not str or (
        not _IDENTIFIER.fullmatch(data["session_id"])
        and not (data["session_id"] == "" and data["type"] in {"ready", "error"})
    ):
        raise _failure("INVALID_SESSION_ID", "Invalid session identity")
    if type(data["event_id"]) is not str or not _IDENTIFIER.fullmatch(data["event_id"]):
        raise _failure("INVALID_EVENT_ID", "Invalid event identity")
    if type(data["payload"]) is not dict:
        raise _failure("INVALID_PAYLOAD", "Message payload must be a JSON object")
    if len(_utf8(_canonical(data))) > limits.max_message_bytes:
        raise _failure("MESSAGE_TOO_LARGE", "Serialized message limit exceeded")


@dataclass(frozen=True)
class Message:
    protocol: str
    session_id: str
    event_id: str
    type: str
    payload: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"protocol": self.protocol, "session_id": self.session_id,
                "event_id": self.event_id, "type": self.type, "payload": _thaw(self.payload)}


def message_from_dict(data: dict[str, Any], *, limits: ProtocolLimits = DEFAULT_LIMITS) -> Message:
    _validate_envelope(data, limits)
    return Message(data["protocol"], data["session_id"], data["event_id"], data["type"], _freeze(data["payload"]))


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _failure("DUPLICATE_JSON_KEY", "Duplicate JSON object keys are forbidden")
        result[key] = value
    return result


def _number(value: str) -> int | float:
    if len(value) > 64:
        raise _failure("UNSAFE_JSON_NUMBER", "JSON numeric token is too long")
    parsed = float(value) if any(character in value for character in ".eE") else int(value)
    if (type(parsed) is float and not math.isfinite(parsed)) or abs(parsed) > _SAFE_INTEGER:
        raise _failure("UNSAFE_JSON_NUMBER", "JSON number must be finite and bounded")
    return parsed


def parse_message(raw: str | bytes, *, limits: ProtocolLimits = DEFAULT_LIMITS) -> Message:
    if type(raw) is bytes:
        if len(raw) > limits.max_message_bytes:
            raise _failure("MESSAGE_TOO_LARGE", "Wire message limit exceeded")
        try:
            text = raw.decode("utf-8", errors="strict")
        except UnicodeError as error:
            raise _failure("INVALID_JSON_ENCODING", "Wire messages must be UTF-8") from error
    elif type(raw) is str:
        text = raw
        if len(_utf8(text)) > limits.max_message_bytes:
            raise _failure("MESSAGE_TOO_LARGE", "Wire message limit exceeded")
    else:
        raise _failure("INVALID_WIRE_MESSAGE", "Wire message must be UTF-8 bytes or text")
    try:
        data = json.loads(text, object_pairs_hook=_pairs, parse_int=_number,
                          parse_float=_number, parse_constant=lambda _: (_ for _ in ()).throw(
                              _failure("UNSAFE_JSON_NUMBER", "NaN and Infinity are forbidden")))
    except ProtocolError:
        raise
    except (ValueError, RecursionError) as error:
        raise _failure("INVALID_JSON", "Wire message is not valid bounded JSON") from error
    return message_from_dict(data, limits=limits)


def serialize_message(message: Message | dict[str, Any], *, limits: ProtocolLimits = DEFAULT_LIMITS) -> str:
    data = message.to_dict() if isinstance(message, Message) else message
    _validate_envelope(data, limits)
    return _canonical(data)


class Phase(StrEnum):
    LOADING = "loading"
    READY = "ready"
    INITIALIZED = "initialized"
    RUNNING = "running"
    PAUSED = "paused"
    CHECKPOINT_PENDING = "checkpoint_pending"
    FINISH_PENDING = "finish_pending"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"
    INVALIDATED = "invalidated"


TERMINAL_PHASES = frozenset({Phase.COMPLETED, Phase.CANCELLED, Phase.FAILED, Phase.INVALIDATED})


@dataclass(frozen=True)
class Capabilities:
    pause: bool = True
    checkpoint: bool = False

    def __post_init__(self) -> None:
        if type(self.pause) is not bool or type(self.checkpoint) is not bool:
            raise ValueError("Capabilities must be booleans")


class TimeoutKind(StrEnum):
    LOAD = "load"
    GAMEPLAY = "gameplay"
    COMMIT = "commit"


@dataclass(frozen=True)
class TimeoutPolicy:
    load_seconds: float = 30.0
    gameplay_seconds: float | None = None
    commit_seconds: float = 15.0

    def __post_init__(self) -> None:
        for name, value in vars(self).items():
            if value is None and name == "gameplay_seconds":
                continue
            if type(value) not in {int, float} or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be a finite positive duration")


@dataclass(frozen=True)
class TimeoutNotice:
    kind: TimeoutKind
    session_id: str
    load_epoch: int
    elapsed_seconds: float
    limit_seconds: float


@dataclass(frozen=True)
class PendingEvent:
    message: Message
    load_epoch: int
    fingerprint: str
    received_at: float


@dataclass(frozen=True)
class CommittedEvent:
    message: Message
    ack: Message
    load_epoch: int
    persisted_receipt: Mapping[str, Any]
    persisted: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {"message": self.message.to_dict(), "ack": self.ack.to_dict(),
                "load_epoch": self.load_epoch, "persisted": self.persisted,
                "persisted_receipt": _thaw(self.persisted_receipt)}


@dataclass(frozen=True)
class ReceiveDecision:
    action: str  # accepted, duplicate, pending, replay, or closed
    message: Message
    pending: PendingEvent | None = None
    ack: Message | None = None


@dataclass
class _EventRecord:
    message: Message
    fingerprint: str
    pending: PendingEvent | None = None
    ack: Message | None = None
    committed: CommittedEvent | None = None


class ProtocolSession:
    """Serializes state transitions; critical validation and commit are separate.

    ``channel_id`` is trusted local transport identity, never a value supplied by
    HTML. Every ``receive`` and ``commit`` must carry the caller's captured load
    epoch. A terminal/invalidated session may only replay an existing identical
    ACK; it cannot admit another pending result. Exported receipts are data for
    the caller's durable ledger, not a persistence operation by this module.
    """

    def __init__(
        self, *, session_id: str, channel_id: str, load_epoch: int,
        game_id: str, package_version: str, package_hash: str,
        capabilities: Capabilities = Capabilities(),
        limits: ProtocolLimits = DEFAULT_LIMITS,
        timeouts: TimeoutPolicy = TimeoutPolicy(),
        input_validator: Validator | None = None,
        result_validator: Validator | None = None,
        snapshot_validator: Validator | None = None,
        clock: Callable[[], float] = time.monotonic,
        event_id_factory: Callable[[], str] = lambda: uuid4().hex,
        allow_unbound_ready: bool = True,
    ) -> None:
        for name, value in (("session_id", session_id), ("channel_id", channel_id), ("game_id", game_id)):
            if type(value) is not str or not _IDENTIFIER.fullmatch(value):
                raise ValueError(f"Invalid {name}")
        if type(load_epoch) is not int or not 0 <= load_epoch <= _SAFE_INTEGER:
            raise ValueError("Invalid load_epoch")
        if type(package_hash) is not str or not re.fullmatch(r"[0-9a-f]{64}", package_hash):
            raise ValueError("Invalid package_hash")
        if type(package_version) is not str or not package_version or len(_utf8(package_version)) > 128:
            raise ValueError("Invalid package_version")
        self.session_id, self.channel_id, self.load_epoch = session_id, channel_id, load_epoch
        self.game_id, self.package_version, self.package_hash = game_id, package_version, package_hash
        self.limits, self.timeouts = limits, timeouts
        self._configured_capabilities = capabilities
        self._capabilities = Capabilities(False, False)
        self._input_validator, self._result_validator, self._snapshot_validator = input_validator, result_validator, snapshot_validator
        self._clock, self._event_id_factory = clock, event_id_factory
        self._allow_unbound_ready = allow_unbound_ready
        self._lock = threading.RLock()
        self._phase = Phase.LOADING
        self._started_at = self._now()
        self._running_since: float | None = None
        self._active_seconds = 0.0
        self._pending: PendingEvent | None = None
        self._after_checkpoint = Phase.RUNNING
        self._records: dict[str, _EventRecord] = {}
        self._host_ids: set[str] = set()
        self._ledger_bytes = 0

    @property
    def phase(self) -> Phase:
        with self._lock:
            return self._phase

    @property
    def capabilities(self) -> Capabilities:
        with self._lock:
            return self._capabilities

    @property
    def pending(self) -> PendingEvent | None:
        with self._lock:
            return self._pending

    def _now(self) -> float:
        value = self._clock()
        if type(value) not in {int, float} or not math.isfinite(value) or value < 0:
            raise _failure("INVALID_CLOCK", "Protocol clock must be finite and nonnegative")
        return float(value)

    def _authenticate(self, channel_id: str, load_epoch: int) -> None:
        if type(channel_id) is not str or channel_id != self.channel_id:
            raise _failure("CHANNEL_MISMATCH", "Message did not arrive on the active native channel")
        if type(load_epoch) is not int or load_epoch != self.load_epoch:
            raise _failure("LOAD_EPOCH_MISMATCH", "Message belongs to an earlier load")

    def _active(self) -> None:
        if self._phase in TERMINAL_PHASES:
            raise _failure("SESSION_TERMINAL", "Session is terminal and cannot accept a new transition", phase=self._phase.value)

    def _validate_callback(self, callback: Validator | None, payload: Mapping[str, Any]) -> None:
        if callback is None:
            return
        try:
            result = callback(_thaw(payload))
            if result is False:
                raise ValueError("Validator rejected payload")
        except Exception as error:
            raise _failure("PAYLOAD_SCHEMA_REJECTED", "Package schema rejected the payload") from error

    def _stop_clock(self, now: float) -> None:
        if self._running_since is not None:
            self._active_seconds += max(0.0, now - self._running_since)
            self._running_since = None

    def _record(self, message: Message, fingerprint: str) -> _EventRecord:
        size = len(_utf8(serialize_message(message, limits=self.limits)))
        if len(self._records) + len(self._host_ids) >= self.limits.max_events or self._ledger_bytes + size > self.limits.max_session_bytes:
            raise _failure("SESSION_LIMIT_EXCEEDED", "Session event ledger limit exceeded")
        record = _EventRecord(message, fingerprint)
        self._records[message.event_id] = record
        self._ledger_bytes += size
        return record

    def _host_message(self, kind: str, payload: dict[str, Any]) -> Message:
        event_id = self._event_id_factory()
        message = message_from_dict({"protocol": PROTOCOL_VERSION, "session_id": self.session_id,
                                     "event_id": event_id, "type": kind, "payload": payload}, limits=self.limits)
        if event_id in self._host_ids or event_id in self._records:
            raise _failure("EVENT_ID_REUSED", "Host event factory returned a reused event ID")
        size = len(_utf8(serialize_message(message, limits=self.limits)))
        if len(self._host_ids) + len(self._records) >= self.limits.max_events or self._ledger_bytes + size > self.limits.max_session_bytes:
            raise _failure("SESSION_LIMIT_EXCEEDED", "Session event ledger limit exceeded")
        self._host_ids.add(event_id)
        self._ledger_bytes += size
        return message

    def receive(self, value: Message | str | bytes | dict[str, Any], *, channel_id: str, load_epoch: int) -> ReceiveDecision:
        with self._lock:
            self._authenticate(channel_id, load_epoch)
            message = (parse_message(value, limits=self.limits) if isinstance(value, (str, bytes))
                       else message_from_dict(value.to_dict() if isinstance(value, Message) else value, limits=self.limits))
            unbound_ready = message.type == "ready" and message.session_id == "" and self._allow_unbound_ready
            unbound_startup_error = (self._phase == Phase.LOADING and message.type == "error" and message.session_id == ""
                                    and message.payload.get("code") == "HOST_STARTUP_FAILED")
            if message.session_id != self.session_id and not (unbound_ready or unbound_startup_error):
                raise _failure("SESSION_MISMATCH", "Message session identity does not match the active session")
            fingerprint = hashlib.sha256(_utf8(serialize_message(message, limits=self.limits))).hexdigest()
            existing = self._records.get(message.event_id)
            if existing:
                if existing.fingerprint != fingerprint:
                    raise _failure("EVENT_ID_REUSED", "The same event ID was reused with different content")
                if existing.ack:
                    return ReceiveDecision("replay", message, ack=existing.ack)
                self._active()
                return ReceiveDecision("pending" if existing.pending else "duplicate", message, pending=existing.pending)
            if message.event_id in self._host_ids:
                raise _failure("EVENT_ID_REUSED", "Inbound event reused a host event ID")
            if message.type == "closed":
                if self._phase != Phase.COMPLETED or message.payload.get("reason") != "finish_confirmed":
                    raise _failure("UNEXPECTED_CLOSE", "Close cannot establish a successful result")
                original = self._records.get(message.payload.get("event_id"))
                if not original or not original.committed or original.message.type != "finish":
                    raise _failure("UNEXPECTED_CLOSE", "Close is not bound to a committed finish")
                self._record(message, fingerprint)
                return ReceiveDecision("closed", message)
            self._active()
            now = self._now()
            if message.type == "ready":
                if self._phase != Phase.LOADING:
                    raise _failure("INVALID_TRANSITION", "Ready is only valid while loading")
                payload = message.payload
                if payload.get("game_id") != self.game_id or payload.get("package_version") != self.package_version:
                    raise _failure("PACKAGE_IDENTITY_MISMATCH", "Ready package identity does not match the launch")
                host = payload.get("host")
                if host is not None and (not isinstance(host, Mapping) or host.get("package_hash") != self.package_hash
                                          or host.get("game_id") != self.game_id or host.get("package_version") != self.package_version):
                    raise _failure("PACKAGE_IDENTITY_MISMATCH", "Host ready digest does not match the immutable package")
                declared = payload.get("capabilities", {})
                if not isinstance(declared, Mapping) or any(type(item) is not bool for item in declared.values()):
                    raise _failure("INVALID_PAYLOAD", "Ready capabilities must be booleans")
                capabilities = Capabilities(self._configured_capabilities.pause and declared.get("pause", False),
                                            self._configured_capabilities.checkpoint and declared.get("checkpoint", False))
                self._record(message, fingerprint)
                self._capabilities = capabilities
                self._phase = Phase.READY
            elif message.type in {"checkpoint", "finish"}:
                if self._phase not in {Phase.RUNNING, Phase.PAUSED} or self._pending is not None:
                    raise _failure("INVALID_TRANSITION", "A critical event requires an active game with no pending commit")
                payload = message.payload
                if message.type == "checkpoint":
                    if not self._capabilities.checkpoint:
                        raise _failure("CAPABILITY_UNSUPPORTED", "Package does not support checkpoints")
                    if set(payload) != {"checkpoint_id", "snapshot_version", "state"} or not isinstance(payload.get("state"), Mapping):
                        raise _failure("INVALID_PAYLOAD", "Checkpoint payload has invalid fields")
                    for key in ("checkpoint_id", "snapshot_version"):
                        if type(payload[key]) is not str or not payload[key] or len(_utf8(payload[key])) > 128:
                            raise _failure("INVALID_PAYLOAD", "Checkpoint identity must be a bounded string")
                    self._validate_callback(self._snapshot_validator, payload)
                else:
                    if set(payload) != {"outcome", "results"} or not isinstance(payload.get("results"), Mapping):
                        raise _failure("INVALID_PAYLOAD", "Finish payload has invalid fields")
                    if type(payload.get("outcome")) is not str or not payload["outcome"] or len(_utf8(payload["outcome"])) > 64:
                        raise _failure("INVALID_PAYLOAD", "Outcome must be a bounded string")
                    self._validate_callback(self._result_validator, payload)
                record = self._record(message, fingerprint)
                pending = PendingEvent(message, self.load_epoch, fingerprint, now)
                record.pending = self._pending = pending
                self._after_checkpoint = self._phase
                self._stop_clock(now)
                self._phase = Phase.CHECKPOINT_PENDING if message.type == "checkpoint" else Phase.FINISH_PENDING
                return ReceiveDecision("pending", message, pending=pending)
            elif message.type in {"error", "cancel"}:
                if message.type == "error":
                    if not isinstance(message.payload.get("code"), str) or not isinstance(message.payload.get("message"), str):
                        raise _failure("INVALID_PAYLOAD", "Error requires a string code and message")
                    fatal = message.payload.get("fatal", True)
                    if type(fatal) is not bool:
                        raise _failure("INVALID_PAYLOAD", "Error fatal flag must be boolean")
                else:
                    if not isinstance(message.payload.get("reason"), str) or not message.payload["reason"]:
                        raise _failure("INVALID_PAYLOAD", "Cancellation requires a reason")
                    fatal = True
                self._record(message, fingerprint)
                if fatal:
                    self._stop_clock(now)
                    self._pending = None
                    self._phase = Phase.FAILED if message.type == "error" else Phase.CANCELLED
            else:
                raise _failure("MESSAGE_DIRECTION_INVALID", "Host controls cannot originate in the game channel")
            return ReceiveDecision("accepted", message)

    def init(self, *, inputs: dict[str, Any] | None = None, snapshot: dict[str, Any] | None = None,
             settings: dict[str, Any] | None = None) -> Message:
        with self._lock:
            self._active()
            if self._phase != Phase.READY:
                raise _failure("INVALID_TRANSITION", "Init requires an actual page-ready event")
            inputs = {} if inputs is None else inputs
            settings = {} if settings is None else settings
            _validate_json(inputs, self.limits)
            _validate_json(settings, self.limits)
            if type(inputs) is not dict or type(settings) is not dict or (snapshot is not None and type(snapshot) is not dict):
                raise _failure("INVALID_PAYLOAD", "Inputs, settings and snapshot must be JSON objects")
            self._validate_callback(self._input_validator, inputs)
            if snapshot is not None:
                _validate_json(snapshot, self.limits)
                self._validate_callback(self._snapshot_validator, snapshot)
            message = self._host_message("init", {
                "game_id": self.game_id, "package_version": self.package_version, "package_hash": self.package_hash,
                "load_epoch": self.load_epoch, "inputs": inputs, "snapshot": snapshot, "settings": settings,
                "capabilities": {"pause": self._capabilities.pause, "checkpoint": self._capabilities.checkpoint},
            })
            self._phase = Phase.INITIALIZED
            return message

    def start(self) -> Message:
        with self._lock:
            self._active()
            if self._phase != Phase.INITIALIZED:
                raise _failure("INVALID_TRANSITION", "Start requires init")
            message = self._host_message("start", {})
            self._phase = Phase.RUNNING
            self._running_since = self._now()
            return message

    def layout(self, geometry: dict[str, Any]) -> Message:
        """Trusted native child layout; it is never a game result or reward."""
        with self._lock:
            self._active()
            _validate_json(geometry, self.limits)
            if type(geometry) is not dict or set(geometry) != {"parent_client", "viewport", "footer"}:
                raise _failure("INVALID_LAYOUT", "Native layout requires client, viewport and footer")
            client = geometry["parent_client"]
            if type(client) is not list or len(client) != 2 or any(type(value) is not int or not 0 < value <= 32768 for value in client):
                raise _failure("INVALID_LAYOUT", "Native client dimensions are invalid")
            for key in ("viewport", "footer"):
                rect = geometry[key]
                if (type(rect) is not list or len(rect) != 4 or any(type(value) is not int for value in rect)
                        or rect[0] < 0 or rect[1] < 0 or rect[2] <= 0 or rect[3] <= 0
                        or rect[0] + rect[2] > client[0] or rect[1] + rect[3] > client[1]):
                    raise _failure("INVALID_LAYOUT", "Native rectangle is outside the client area")
            viewport, footer = geometry["viewport"], geometry["footer"]
            if footer[0] != viewport[0] or footer[2] != viewport[2] or footer[1] != viewport[1] + viewport[3]:
                raise _failure("INVALID_LAYOUT", "Story toolbar must be adjacent to the HTML layer")
            return self._host_message("layout", geometry)

    def settings(self, settings: dict[str, Any]) -> Message:
        with self._lock:
            self._active()
            if self._phase not in {Phase.RUNNING, Phase.PAUSED}:
                raise _failure("INVALID_TRANSITION", "Settings require an active game")
            _validate_json(settings, self.limits)
            if type(settings) is not dict:
                raise _failure("INVALID_PAYLOAD", "Settings must be a JSON object")
            return self._host_message("settings", settings)

    def pause(self, *, reason: str = "host_pause") -> Message:
        with self._lock:
            self._active()
            if not self._capabilities.pause:
                raise _failure("CAPABILITY_UNSUPPORTED", "Package cannot be paused")
            if self._phase not in {Phase.RUNNING, Phase.CHECKPOINT_PENDING} or (
                self._phase == Phase.CHECKPOINT_PENDING and self._after_checkpoint != Phase.RUNNING
            ):
                raise _failure("INVALID_TRANSITION", "Pause requires a running game")
            message = self._host_message("pause", {"reason": reason})
            self._stop_clock(self._now())
            if self._phase == Phase.CHECKPOINT_PENDING:
                self._after_checkpoint = Phase.PAUSED
            else:
                self._phase = Phase.PAUSED
            return message

    def resume(self) -> Message:
        with self._lock:
            self._active()
            if not self._capabilities.pause:
                raise _failure("CAPABILITY_UNSUPPORTED", "Package cannot be resumed")
            if self._phase != Phase.PAUSED:
                raise _failure("INVALID_TRANSITION", "Resume requires a paused game with no pending commit")
            message = self._host_message("resume", {})
            self._phase = Phase.RUNNING
            self._running_since = self._now()
            return message

    def commit(self, event_id: str, *, persisted_receipt: dict[str, Any], load_epoch: int) -> Message:
        """Produce ACK only after caller persisted the event; never grant rewards."""
        with self._lock:
            self._authenticate(self.channel_id, load_epoch)
            _validate_json(persisted_receipt, self.limits)
            if type(persisted_receipt) is not dict or not persisted_receipt:
                raise _failure("COMMIT_RECEIPT_REQUIRED", "Critical ACK requires a nonempty durable commit receipt")
            record = self._records.get(event_id)
            if record and record.committed:
                if _canonical(_thaw(record.committed.persisted_receipt)) != _canonical(persisted_receipt):
                    raise _failure("COMMIT_RECEIPT_MISMATCH", "Commit replay cannot change the durable receipt")
                return record.committed.ack
            self._active()
            if not record or record.pending is None or self._pending is not record.pending:
                raise _failure("EVENT_NOT_PENDING", "Only the current validated critical event can be committed")
            size = len(_utf8(_canonical(persisted_receipt)))
            if self._ledger_bytes + size > self.limits.max_session_bytes:
                raise _failure("SESSION_LIMIT_EXCEEDED", "Durable receipt exceeds the session ledger budget")
            if self._ledger_bytes + size + 512 > self.limits.max_session_bytes:
                raise _failure("SESSION_LIMIT_EXCEEDED", "Durable receipt and ACK exceed the session ledger budget")
            ack = self._host_message("ack", {"event_id": event_id, "accepted": True, "persisted": True})
            committed = CommittedEvent(record.message, ack, self.load_epoch, _freeze(persisted_receipt))
            record.committed, record.ack, record.pending = committed, ack, None
            self._ledger_bytes += size
            self._pending = None
            if record.message.type == "finish":
                self._phase = Phase.COMPLETED
            else:
                self._phase = self._after_checkpoint
                if self._phase == Phase.RUNNING:
                    self._running_since = self._now()
            return ack

    def reject(self, event_id: str, *, reason: str, load_epoch: int) -> Message:
        """Explicit rejection is cached too, and cannot later become a commit."""
        with self._lock:
            self._authenticate(self.channel_id, load_epoch)
            self._active()
            record = self._records.get(event_id)
            if not record or record.pending is None or self._pending is not record.pending:
                raise _failure("EVENT_NOT_PENDING", "Only a pending event can be rejected")
            ack = self._host_message("ack", {"event_id": event_id, "accepted": False, "persisted": False, "reason": reason})
            record.ack, record.pending = ack, None
            self._pending = None
            self._phase = self._after_checkpoint
            if self._phase == Phase.RUNNING:
                self._running_since = self._now()
            return ack

    def cancel(self, *, reason: str) -> Message:
        with self._lock:
            self._active()
            if type(reason) is not str or not reason:
                raise _failure("INVALID_PAYLOAD", "Cancellation requires a reason")
            message = self._host_message("cancel", {"reason": reason})
            self._stop_clock(self._now())
            self._pending = None
            self._phase = Phase.CANCELLED
            return message

    def invalidate(self) -> None:
        """Fence old callbacks after load/quit, retaining already persisted ACKs."""
        with self._lock:
            self._stop_clock(self._now())
            self._pending = None
            self._phase = Phase.INVALIDATED

    def timeout_status(self) -> TimeoutNotice | None:
        """Classify timeouts; the caller chooses retry/cancel, never implicit win."""
        with self._lock:
            if self._phase in TERMINAL_PHASES:
                return None
            now = self._now()
            if self._phase in {Phase.LOADING, Phase.READY, Phase.INITIALIZED}:
                kind, elapsed, limit = TimeoutKind.LOAD, max(0.0, now - self._started_at), self.timeouts.load_seconds
            elif self._pending is not None:
                kind, elapsed, limit = TimeoutKind.COMMIT, max(0.0, now - self._pending.received_at), self.timeouts.commit_seconds
            else:
                kind = TimeoutKind.GAMEPLAY
                elapsed = self._active_seconds + (max(0.0, now - self._running_since) if self._running_since is not None else 0.0)
                limit = self.timeouts.gameplay_seconds
            if limit is not None and elapsed >= limit:
                return TimeoutNotice(kind, self.session_id, self.load_epoch, elapsed, float(limit))
            return None

    def export_committed(self) -> list[dict[str, Any]]:
        """Detached data for caller persistence; this module performs no I/O."""
        with self._lock:
            return [record.committed.to_dict() for record in self._records.values() if record.committed is not None]
