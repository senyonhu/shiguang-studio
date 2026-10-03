"""Isolated storage integrity reproduction and read-only Excel cold-start audit.

Writes only beneath evidence/m9-review. Production projects are read solely
for existing source artifact timestamps and already-recorded MCP events.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import wave

from renpy_bridge.errors import BridgeError
from renpy_bridge import __version__
from renpy_bridge.storage import Store, atomic_json, digest, file_hash, read_json

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "evidence/m9-review"
AUTHOR_EVENTS = ROOT / "evidence/m8-demo/codex-author/runs/20261001T224019Z-d7c04763/raw-events.jsonl"
AUTHOR_LOG = AUTHOR_EVENTS.parent / "stderr.txt"


def write_wav(path: Path, value: int):
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(value.to_bytes(2, "little", signed=True) * 80)


def fixture(name):
    store = Store(OUT / "runs" / str(time.time_ns()) / name)
    pid = store.create(name, "create")["project_id"]
    return store, pid


def attempt(store, pid, action):
    before = store.head(pid)["revision"]
    try:
        result = action()
        error = None
    except BridgeError as exc:
        result = None
        error = exc.as_dict()
    return {"rejected": error is not None, "error": error, "result": result,
            "head_revision_before": before, "head_revision_after": store.head(pid)["revision"]}


def integrity_cases():
    result = []
    store, pid = fixture("checkpoint-model")
    frames = [{"frame_id": "first", "act_id": "act", "dialogue": {"text": "original"}, "flow": {"kind": "end"}}]
    store.apply_batch(pid, 0, "initialize", frames, acts=[{"act_id": "act"}])
    snap = store.checkpoint(pid, "confirmed")
    store.patch_frame(pid, "first", {"dialogue": {"text": "current"}}, 1, "current")
    path = store.directory(pid) / "history/revisions/00000001.json"
    model = read_json(path)
    model["frames"]["first"]["dialogue"]["text"] = "modified-history"
    atomic_json(path, model)
    item = attempt(store, pid, lambda: store.restore(pid, snap["snapshot_id"], 2, "restore"))
    item.update(case="checkpoint-model-hash", stored_checkpoint_hash=snap["model_hash"],
                tampered_normalized_model_hash=digest(store.load(pid, 1).model_dump(mode="json")),
                effective_text=store.load(pid).frames["first"].dialogue.text,
                fixture_directory=str(store.directory(pid)))
    result.append(item)
    for damage in ("missing", "changed"):
        store, pid = fixture("checkpoint-media-" + damage)
        source = store.home / "original.wav"
        write_wav(source, 100)
        payload = [{"asset_id": "voice", "kind": "audio", "source_path": str(source)}]
        store.register_assets(pid, 0, "old", payload)
        old = store.load(pid).assets["voice"]
        snap = store.checkpoint(pid, "old-media")
        write_wav(source, 200)
        store.register_assets(pid, 1, "new", payload)
        path = store.directory(pid) / old.path
        if damage == "missing":
            path.unlink()
        else:
            write_wav(path, -300)
        item = attempt(store, pid, lambda: store.restore(pid, snap["snapshot_id"], 2, "restore"))
        item.update(case="checkpoint-media-" + damage, expected_media_hash=old.sha256,
                    managed_file_exists=path.is_file(), actual_media_hash=file_hash(path) if path.is_file() else None,
                    restored_model_media_hash=store.load(pid).assets["voice"].sha256,
                    fixture_directory=str(store.directory(pid)))
        result.append(item)
    store, pid = fixture("dedup-container")
    source = store.home / "original.wav"
    write_wav(source, 100)
    store.register_assets(pid, 0, "first", [{"asset_id": "first", "kind": "audio", "source_path": str(source)}])
    old = store.load(pid).assets["first"]
    path = store.directory(pid) / old.path
    write_wav(path, -100)
    item = attempt(store, pid, lambda: store.register_assets(pid, 1, "second", [{"asset_id": "second", "kind": "audio", "source_path": str(source)}]))
    item.update(case="damaged-dedup-container", expected_media_hash=old.sha256,
                actual_media_hash=file_hash(path), second_asset_registered="second" in store.load(pid).assets,
                fixture_directory=str(store.directory(pid)))
    result.append(item)
    return result


def author_evidence():
    calls = []
    home = None
    pid = None
    for line in AUTHOR_EVENTS.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        item = event.get("item", {})
        if event.get("type") != "item.completed" or item.get("type") != "mcp_tool_call":
            continue
        if item.get("tool") not in ("get_environment", "create_project", "extract_source"):
            continue
        result = item.get("result")
        value = {}
        if isinstance(result, dict) and result.get("content"):
            try:
                value = json.loads(result["content"][0]["text"])
            except (ValueError, KeyError, TypeError):
                pass
        if item["tool"] == "get_environment":
            home = Path(value["home"])
        elif item["tool"] == "create_project":
            pid = value["project_id"]
        elif len(calls) < 2:
            calls.append({"item_id": item["id"], "arguments": item["arguments"], "status": item["status"],
                          "error": item.get("error"), "result_summary": {k: value[k] for k in ("hash", "total_records", "record_path") if k in value}})
    artifacts = []
    if home and pid:
        for path in (home / "projects" / pid / "source_inputs").iterdir():
            stat = path.stat()
            artifacts.append({"path": str(path), "bytes": stat.st_size, "sha256": file_hash(path),
                              "creation_time_utc": datetime.fromtimestamp(stat.st_ctime, timezone.utc).isoformat(),
                              "last_write_time_utc": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()})
    relevant_log = [line for line in AUTHOR_LOG.read_text(encoding="utf-8").splitlines()
                    if "extract_source" in line or "Error sending response id=4" in line]
    return {"production_home": str(home), "production_project_id": pid, "first_two_calls": calls,
            "existing_production_artifacts_read_only": artifacts, "log_lines": relevant_log,
            "source_events_sha256": file_hash(AUTHOR_EVENTS), "source_log_sha256": file_hash(AUTHOR_LOG)}


COLD_CODE = """
import json, sys, time
from pathlib import Path
start = time.monotonic()
import renpy_bridge.sources as sources
source_import_seconds = time.monotonic() - start
start = time.monotonic()
import openpyxl
openpyxl_import_seconds = time.monotonic() - start
samples = []
for attempt in range(2):
    start = time.monotonic()
    result = sources.extract_file(Path(sys.argv[1]))
    samples.append({'seconds': time.monotonic() - start, 'hash': result['hash'], 'records': len(result['records'])})
print(json.dumps({'python': sys.executable, 'source_module': sources.__file__, 'openpyxl_module': openpyxl.__file__,
                  'openpyxl_version': openpyxl.__version__, 'source_import_seconds': source_import_seconds,
                  'openpyxl_import_seconds': openpyxl_import_seconds, 'extractions': samples,
                  'optional_modules_loaded': {name: getattr(sys.modules.get(name), '__file__', None) for name in ('numpy', 'lxml', 'PIL')}}, ensure_ascii=True))
"""


def cold_audit(home: Path):
    source = ROOT / "outputs/formal-demo/demo-materials.xlsx"
    candidates = [("development", Path(sys.executable), ROOT / "src"),
                  ("production", home / ".runtime/Scripts/python.exe", home / "src")]
    out = []
    for name, executable, src in candidates:
        env = dict(os.environ, PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(src))
        argv = [str(executable), "-B", "-X", "importtime", "-c", COLD_CODE, str(source)]
        started = time.monotonic()
        try:
            proc = subprocess.run(argv, env=env, cwd=OUT, capture_output=True, encoding="utf-8", errors="replace",
                                  timeout=60, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            stdout, stderr, code, timed_out = proc.stdout, proc.stderr, proc.returncode, False
        except subprocess.TimeoutExpired as exc:
            stdout = (exc.stdout or b"").decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else exc.stdout or ""
            stderr = (exc.stderr or b"").decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else exc.stderr or ""
            code, timed_out = None, True
        (OUT / (name + "-cold-import.log")).write_text(stderr, encoding="utf-8")
        (OUT / (name + "-cold-result.txt")).write_text(stdout, encoding="utf-8")
        import_rows = []
        for line in stderr.splitlines():
            if line.startswith("import time:"):
                parts = line.split("|")
                try:
                    import_rows.append({"self_microseconds": int(parts[0].split(":")[1]),
                                        "cumulative_microseconds": int(parts[1]), "module": parts[2].strip()})
                except (ValueError, IndexError):
                    pass
        out.append({"name": name, "argv": argv, "exit_code": code, "timed_out": timed_out,
                    "duration_seconds": time.monotonic() - started,
                    "result": json.loads(stdout) if code == 0 else None,
                    "import_log": str(OUT / (name + "-cold-import.log")),
                    "largest_self_import_times": sorted(import_rows, key=lambda row: row["self_microseconds"], reverse=True)[:10]})
    return out


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    report = {"scope": "independent isolated Store integrity reproductions; production source/import audit is read-only",
              "executed_bridge_version": __version__,
              "created_at": datetime.now(timezone.utc).isoformat(), "production_project_written": False,
              "source_hashes_at_start": {p.name: file_hash(p) for p in sorted((ROOT / "src/renpy_bridge").glob("*.py"))}}
    report["integrity_cases"] = integrity_cases()
    report["author_cold_start_evidence"] = author_evidence()
    home = Path(report["author_cold_start_evidence"]["production_home"])
    report["installed_vs_development_sources"] = [{"file": p.name, "development_sha256": file_hash(p),
                                                   "production_sha256": file_hash(home / "src/renpy_bridge" / p.name)
                                                   if (home / "src/renpy_bridge" / p.name).is_file() else None}
                                                  for p in sorted((ROOT / "src/renpy_bridge").glob("*.py"))]
    report["cold_start_samples"] = cold_audit(home)
    report["source_hashes_at_end"] = {p.name: file_hash(p) for p in sorted((ROOT / "src/renpy_bridge").glob("*.py"))}
    report["findings"] = [
        {"id": "checkpoint-model-integrity", "location": "src/renpy_bridge/storage.py:413", "impact": "Valid modified historical JSON is accepted despite the recorded checkpoint hash and becomes the effective restored head.", "required_fix": "Verify the normalized historical Project digest against snapshot model_hash before publishing a restore; preserve the head on mismatch.", "tests": ["test_restore_rejects_valid_but_changed_checkpoint_model_before_commit"]},
        {"id": "checkpoint-media-integrity", "location": "src/renpy_bridge/storage.py:413", "impact": "A restored revision may reference missing or changed managed old media bytes.", "required_fix": "Before publishing, contain and verify restored managed assets exist and match their recorded sha256; reject missing/mismatch.", "tests": ["test_restore_rejects_unavailable_old_asset_before_commit[missing]", "test_restore_rejects_unavailable_old_asset_before_commit[changed]"]},
        {"id": "existing-dedup-container-integrity", "location": "src/renpy_bridge/storage.py:319", "impact": "An existing damaged but decodable hash-named file is reused and committed with an incorrect declared sha256.", "required_fix": "Verify an existing managed dedup target against the computed source hash before metadata extraction and commit; reject damaged content.", "tests": ["test_register_assets_rejects_damaged_existing_hash_container"]},
    ]
    report["non_findings"] = [{"id": "hash-to-copy-original-change", "result": "Meaningful monkeypatch race test passed: ASSET_CHANGED, effective head unchanged, temporary copy cleaned."},
                              {"id": "environment-version", "result": "Known hard-coded metadata mismatch; root already plans 0.1.2 correction, not a new independent issue."}]
    case_groups = {
        "checkpoint-model-integrity": {"checkpoint-model-hash"},
        "checkpoint-media-integrity": {"checkpoint-media-missing", "checkpoint-media-changed"},
        "existing-dedup-container-integrity": {"damaged-dedup-container"},
    }
    for finding in report["findings"]:
        matching = [item for item in report["integrity_cases"] if item["case"] in case_groups[finding["id"]]]
        finding["reproduced"] = any(not item["rejected"] or item["head_revision_before"] != item["head_revision_after"] for item in matching)
        finding["status"] = "reproduced" if finding["reproduced"] else "protected_in_executed_version"
    report["remaining_reproduced_integrity_issues"] = [finding["id"] for finding in report["findings"] if finding["reproduced"]]
    report["review_complete"] = True
    report["cold_start_conclusion"] = "The first real MCP extract call timed out at 300s and its retry succeeded. Separate read-only cold subprocess samples below measure current installed code; they cannot establish the original stall's cause. Existing artifact timestamps alone cannot assign file creation to the first call versus retry."
    atomic_json(OUT / "independent-review.json", report)
    print(json.dumps({"report": str(OUT / "independent-review.json"), "integrity_cases": [{"case": v['case'], 'rejected': v['rejected'], 'before': v['head_revision_before'], 'after': v['head_revision_after']} for v in report['integrity_cases']], 'cold_start_samples': [{'name': v['name'], 'seconds': v['duration_seconds'], 'timeout': v['timed_out'], 'result': v['result']} for v in report['cold_start_samples']]}, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
