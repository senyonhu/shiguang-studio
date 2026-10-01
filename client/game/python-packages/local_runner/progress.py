"""Atomic drafts, append-only learning events, and fact-only JSON exports."""
import json
import os
from pathlib import Path
import time
import uuid


class Progress:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def atomic(self, name, data):
        dest = self.directory / name
        temporary = dest.with_suffix(dest.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, dest)

    def draft(self, run_id, task_id, fragment, revision, before_reset=False):
        name = "draft-" + run_id + "-" + task_id + ("-before-reset" if before_reset else "") + ".json"
        self.atomic(name, dict(fragment=fragment, revision=revision, saved_at=time.time(), task_revision="tower-c11-1.1"))

    def latest(self, run_id, task_id):
        path = self.directory / ("draft-" + run_id + "-" + task_id + ".json")
        if not path.exists():
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        if value.get("task_revision") != "tower-c11-1.1" or not isinstance(value.get("fragment"), str) or len(value["fragment"].encode()) > 4096:
            return None
        return value

    def event(self, run_id, kind, **data):
        event_id = data.pop("event_id", None) or uuid.uuid4().hex
        value = dict(event_id=event_id, run_id=run_id, kind=kind, time=time.time(), **data)
        with (self.directory / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(value, ensure_ascii=False) + "\n")
        return value

    def receipt(self, run_id, task_id, receipt):
        self.atomic("receipt-" + run_id + "-" + task_id + ".json", receipt)

    def export(self, run_id, summary, include_code=True):
        events = []
        path = self.directory / "events.jsonl"
        seen = set()
        if path.exists():
            with path.open(encoding="utf-8") as stream:
                for line in stream:
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if event.get("run_id") != run_id or event.get("event_id") in seen:
                        continue
                    seen.add(event["event_id"])
                    if not include_code:
                        event.pop("fragment", None)
                    events.append(event)
        errors = {}
        for event in events:
            if event.get("kind") == "run_finished" and event.get("status") != "passed":
                kind = event.get("status", "unknown")
                errors[kind] = errors.get(kind, 0) + 1
        summary = dict(summary, error_categories=errors)
        data = dict(schema_version=1, content_version="1.1.0", task_revision="tower-c11-1.1", run_id=run_id, summary=summary, events=events)
        name = "experience-" + run_id + ".json"
        self.atomic(name, data)
        return str(self.directory / name)
