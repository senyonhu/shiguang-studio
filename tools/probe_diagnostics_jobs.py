"""Real silent MCP/SDK concurrency, diagnostics, timeout and cancellation QA.

The probe creates a unique fixed Store fixture, not the formal Demo. Public
MCP calls use an independent stdio client; this is not desktop Codex evidence.
Only disposable generated copies receive syntax/init-sleep faults. Process
identity checks use exact argv, parent lineage and kernel creation timestamps.
"""
from __future__ import annotations

import asyncio
import argparse
from dataclasses import asdict
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import traceback
import tomllib

import psutil
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from renpy_bridge.jobs import JobManager, TERMINAL
from renpy_bridge.pipeline import _map_errors
from renpy_bridge.sdk import discover_sdk, run_sdk
from renpy_bridge.storage import Store, atomic_json, digest, file_hash, read_json

ROOT = Path(__file__).resolve().parents[1]
CODE_FILES = [ROOT / "src/renpy_bridge" / name for name in
              ("server.py", "jobs.py", "worker.py", "pipeline.py", "sdk.py", "generator.py", "native_qa.py", "storage.py")]


def identity(process: psutil.Process) -> dict:
    return {"pid": process.pid, "create_time": process.create_time(), "parent_pid": process.ppid(), "argv": process.cmdline()}


def same_live(record: dict) -> bool:
    try:
        process = psutil.Process(record["pid"])
        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE and process.create_time() == record["create_time"]
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        return False


def tool_dict(result) -> dict:
    if result.isError:
        raise RuntimeError(str(result.content))
    for block in result.content:
        if block.type == "text":
            value = json.loads(block.text)
            if value.get("ok") is False:
                raise RuntimeError(json.dumps(value, ensure_ascii=False))
            return value
    if isinstance(result.structuredContent, dict):
        return result.structuredContent
    raise RuntimeError("MCP tool did not return a JSON object")


def clone_engine(source: Path, target: Path) -> None:
    target.mkdir()
    shutil.copytree(source / "game", target / "game", ignore=shutil.ignore_patterns("saves", "cache", "__pycache__", "tests", "*.rpyc", "*.pyc"))
    for name in ("bridge-manifest.json", "bridge-source-map.json"):
        shutil.copyfile(source / name, target / name)


def record_execution(directory: Path, name: str, result) -> dict:
    for stream in ("stdout", "stderr"):
        (directory / (name + "." + stream + ".log")).write_text(getattr(result, stream), encoding="utf-8")
    return asdict(result)


def observed_sdk(sdk, target: Path, timeout: float, savedir: Path) -> tuple[object, list[dict]]:
    result = {}
    observed = {}
    expected = [str(sdk.python_executable), str(sdk.entrypoint), str(target.resolve()), "compile", "--savedir", str(savedir)]
    def launch():
        result["value"] = run_sdk(sdk, target, "compile", ["--savedir", str(savedir)], timeout_seconds=timeout,
                                  env={"RENPY_MULTIPERSISTENT": str(savedir.parent / "multipersistent")})
    thread = threading.Thread(target=launch, name="owned-sdk-timeout", daemon=False)
    thread.start()
    while thread.is_alive():
        for process in psutil.Process().children(recursive=True):
            try:
                args = process.cmdline()
                if args[1:] == expected[1:] and Path(args[0]).resolve() == sdk.python_executable.resolve():
                    for selected in [process, *process.children(recursive=True)]:
                        item = identity(selected)
                        observed[(item["pid"], item["create_time"])] = item
            except (psutil.NoSuchProcess, psutil.ZombieProcess):
                pass
        time.sleep(0.02)
    thread.join()
    if "value" not in result:
        raise RuntimeError("SDK owner did not return process evidence")
    return result["value"], list(observed.values())


async def run_probe(run_id: str, jobs_dir: Path, diagnostic_dir: Path) -> tuple[dict, dict]:
    code_hashes = {str(path.relative_to(ROOT)): file_hash(path) for path in CODE_FILES}
    script_hash = file_hash(Path(__file__).resolve())
    jobs_report = {"schema_version": 1, "probe": "real-native-concurrency-timeout-cancel", "complete": False,
                   "run_id": run_id, "run_directory": str(jobs_dir), "code_sha256": code_hashes,
                   "probe_sha256": script_hash, "scope": "Independent public stdio MCP and actual managed ROOT workers on a unique silent fixture; not Codex desktop integration.",
                   "formal_demo_modified": False, "audio_playback_requested": False, "mcp_calls": []}
    diagnostics_report = {"schema_version": 1, "probe": "actual-native-syntax-diagnostics", "complete": False,
                          "run_id": run_id, "run_directory": str(diagnostic_dir), "code_sha256": code_hashes,
                          "probe_sha256": script_hash, "scope": "Intentional syntax faults in disposable generated copies; actual SDK compile/lint and standard pipeline._map_errors. Not an authoring/Codex correction loop.",
                          "formal_demo_modified": False, "commands": {}}
    sentinel = None
    known_jobs = []
    def save():
        atomic_json(jobs_dir / "report.json", jobs_report)
        atomic_json(diagnostic_dir / "report.json", diagnostics_report)
        atomic_json(jobs_dir.parent.parent / "report.json", jobs_report)
        atomic_json(diagnostic_dir.parent.parent / "report.json", diagnostics_report)
    try:
        params = StdioServerParameters(command=sys.executable, args=["-m", "renpy_bridge.server"], cwd=str(ROOT),
                  env={**os.environ, "RENPY_BRIDGE_HOME": str(ROOT), "PYTHONPATH": str(ROOT / "src"), "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1"})
        jobs_report["mcp_server"] = {"command": params.command, "args": params.args, "cwd": str(ROOT),
                                     "environment_overrides": {name: params.env[name] for name in ("RENPY_BRIDGE_HOME", "PYTHONPATH", "PYTHONUTF8", "PYTHONUNBUFFERED")}}
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                jobs_report["mcp_initialize"] = (await session.initialize()).model_dump(mode="json")
                async def call(name, arguments):
                    began = time.monotonic()
                    value = tool_dict(await session.call_tool(name, arguments))
                    jobs_report["mcp_calls"].append({"name": name, "arguments": arguments, "duration_seconds": time.monotonic() - began,
                                                      "result_sha256": digest(value), "job_id": value.get("job_id"), "state": value.get("state")})
                    return value
                created = await call("create_project", {"name": "静音并发与诊断固定夹具 " + run_id, "operation_id": "diagnostic-fixture-" + run_id})
                project_id = created["project_id"]
                await call("register_assets", {"project_id": project_id, "expected_revision": 0, "operation_id": "asset-" + run_id,
                   "assets": [{"asset_id": "sdk_icon", "kind": "image", "source_path": str(ROOT / "runtimes/renpy-8.5.3/launcher/game/gui7/icon.png")}]})
                texts = ["第一句：保存、载入和回滚恢复完整画面。", "第二句：菜单、历史和设置保留剧情。", "选择一个固定分支。", "甲线终点。", "乙线终点。"]
                ids = ["first", "second", "choice", "end_a", "end_b"]
                frames = []
                for index, fid in enumerate(ids):
                    flow = {"kind": "next", "target_frame_id": ids[index + 1]} if index < 2 else {"kind": "end"}
                    if index == 2:
                        flow = {"kind": "choices", "choices": [{"choice_id": "a", "text": "路线甲", "target_frame_id": "end_a"}, {"choice_id": "b", "text": "路线乙", "target_frame_id": "end_b"}]}
                    frame = {"frame_id": fid, "act_id": "act_fixed", "order": index, "dialogue": {"text": texts[index]}, "flow": flow,
                             "provenance": {"source_id": "source_fixed", "source_file": "native-fixture.csv", "sheet": "剧情材料", "cell": "D" + str(index + 2), "row": index + 2}}
                    if index == 0:
                        frame["bg"] = {"component_id": "first_background", "kind": "background", "asset_id": "sdk_icon"}
                        frame["decorations"] = [{"component_id": "first_title", "kind": "text", "text": "第一画面"}]
                    if index == 1:
                        frame["decorations"] = [{"component_id": "second_title", "kind": "text", "text": "第二画面", "layout": {"x": 0.65, "y": 0.12}}]
                    frames.append(frame)
                await call("apply_frame_batch", {"project_id": project_id, "expected_revision": 1, "operation_id": "frames-" + run_id,
                    "frames": frames, "acts": [{"act_id": "act_fixed"}], "entry_frame_id": "first"})
                fixture_csv = jobs_dir / "native-fixture.csv"
                fixture_csv.write_text("frame_id,act_id,text\n" + "\n".join(fid + ",act_fixed," + texts[index] for index, fid in enumerate(ids)) + "\n", encoding="utf-8")
                store = Store(ROOT)
                model = store.load(project_id, 2)
                original_model_hash = digest(model.model_dump(mode="json"))
                jobs_report["fixture"] = {"project_id": project_id, "revision": 2, "model_sha256": original_model_hash,
                                          "source_fixture": str(fixture_csv), "source_fixture_sha256": file_hash(fixture_csv), "entry_frame_id": "first"}
                launches = await asyncio.gather(
                    call("check_project", {"project_id": project_id, "revision": 2}),
                    call("test_routes", {"project_id": project_id, "revision": 2, "include_interactions": True}),
                    call("render_preview", {"project_id": project_id, "revision": 2, "frame_ids": ["first", "choice", "end_b"]}))
                known_jobs.extend(value["job_id"] for value in launches)
                jobs_report["launched_jobs"] = launches
                save()
                deadline = time.monotonic() + 210
                states = {value["job_id"]: value for value in launches}
                overlap_seen = False
                while any(value["state"] not in TERMINAL for value in states.values()):
                    values = await asyncio.gather(*(call("get_job", {"job_id": job_id}) for job_id in states))
                    states = {value["job_id"]: value for value in values}
                    overlap_seen = overlap_seen or sum(value["state"] == "running" for value in values) >= 3
                    if time.monotonic() > deadline:
                        raise RuntimeError("Concurrent managed jobs exceeded probe deadline")
                    await asyncio.sleep(0.3)
                concurrency = {"jobs": [], "three_workers_overlapped": overlap_seen, "launcher_intervals": []}
                for value in states.values():
                    path = Path(value["report_path"])
                    pipeline = read_json(path)
                    manifest_path = Path(pipeline["generation"]["manifest_path"])
                    manifest = read_json(manifest_path)
                    launcher = manifest["native_gui_process"]
                    concurrency["launcher_intervals"].append({"job_id": value["job_id"], "argv": launcher["argv"],
                        "process_id": launcher["process_id"], "start_unix": launcher["process_create_time"],
                        "end_unix": launcher["process_create_time"] + launcher["duration_seconds"], "exit_code": launcher["exit_code"]})
                    concurrency["jobs"].append({"status": value, "report_path": str(path), "report_sha256": file_hash(path), "report_bytes": path.stat().st_size,
                        "manifest_sha256": file_hash(manifest_path), "pipeline": pipeline})
                intervals = sorted(concurrency["launcher_intervals"], key=lambda item: item["start_unix"])
                concurrency["launcher_serialized"] = all(left["end_unix"] <= right["start_unix"] + 0.05 for left, right in zip(intervals, intervals[1:]))
                concurrency["passed"] = overlap_seen and concurrency["launcher_serialized"] and all(item["status"]["state"] == "succeeded" and item["pipeline"]["ok"] and item["pipeline"]["status"] == "passed" for item in concurrency["jobs"])
                jobs_report["concurrency"] = concurrency
                if not concurrency["passed"]:
                    raise RuntimeError("Concurrent native workers did not all pass or launcher lock serialization was unproven")
                route = next(item["pipeline"] for item in concurrency["jobs"] if item["status"]["kind"] == "routes")
                assert route["interaction_verification"]["status"] == "passed"
                jobs_report["pipeline_interactions_verified"] = route["interaction_verification"]
                assert digest(store.load(project_id, 2).model_dump(mode="json")) == original_model_hash
                print(json.dumps({"stage": "concurrent-native-workers", "passed": True, "project_id": project_id}, ensure_ascii=False), flush=True)
                save()

                sdk = discover_sdk(ROOT / "runtimes/renpy-8.5.3")
                baseline = Path(next(item["pipeline"]["generation"]["project_path"] for item in concurrency["jobs"] if item["status"]["kind"] == "check"))
                broken = diagnostic_dir / "中文 固定语法故障副本"
                clone_engine(baseline, broken)
                source_map = read_json(broken / "bridge-source-map.json")
                entry = source_map["frames"]["second"]
                line = entry["dialogue_line"]
                script = broken / "game/script.rpy"
                content = script.read_text(encoding="utf-8").splitlines()
                previous = content[line - 1]
                content[line - 1] = '    menu ??? invalid fixed syntax'
                script.write_text("\n".join(content) + "\n", encoding="utf-8")
                diagnostics_report["fixture"] = {"project_id": project_id, "revision": 2, "model_sha256": original_model_hash,
                    "generated_copy": str(broken), "source_map_sha256": file_hash(broken / "bridge-source-map.json"),
                    "fault": {"file": str(script), "line": line, "previous_text": previous, "injected_text": content[line - 1], "faulted_script_sha256": file_hash(script)},
                    "expected_mapping": {"frame_id": "second", "act_id": "act_fixed", "provenance": model.frames["second"].provenance}}
                for command, args in [("compile", []), ("lint", ["--error-code"])]:
                    result = await asyncio.to_thread(run_sdk, sdk, broken, command, [*args, "--savedir", str(diagnostic_dir / "private-saves")], timeout_seconds=30,
                                     env={"RENPY_MULTIPERSISTENT": str(diagnostic_dir / "private-multipersistent")})
                    mapped = _map_errors(result.stdout + result.stderr, source_map, model)
                    mapping_passed = any(item["frame_id"] == "second" and item["act_id"] == "act_fixed" and item["line"] == line and item["provenance"] == model.frames["second"].provenance for item in mapped)
                    diagnostics_report["commands"][command] = {"process": record_execution(diagnostic_dir, command, result), "mapped_diagnostics": mapped,
                        "passed": result.exit_code not in (None, 0) and not result.timed_out and result.cleanup_complete and mapping_passed}
                    if (broken / "errors.txt").is_file():
                        shutil.copyfile(broken / "errors.txt", diagnostic_dir / (command + ".errors.txt"))
                diagnostics_report["model_unchanged"] = digest(store.load(project_id, 2).model_dump(mode="json")) == original_model_hash
                diagnostics_report["complete"] = all(value["passed"] for value in diagnostics_report["commands"].values()) and diagnostics_report["model_unchanged"]
                if not diagnostics_report["complete"]:
                    raise RuntimeError("Actual syntax error did not map back to frame/source provenance")
                print(json.dumps({"stage": "compile-lint-syntax-mapping", "passed": True, "line": line}, ensure_ascii=False), flush=True)
                save()

                sentinel = subprocess.Popen([str(Path(sys._base_executable)), "-c", "import time; time.sleep(180)"], cwd=jobs_dir,
                          stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                          creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                sentinel_identity = identity(psutil.Process(sentinel.pid))
                jobs_report["unrelated_sentinel"] = {"identity": sentinel_identity, "owned_by_probe": True, "role": "Unrelated silent process must survive timeout and cancellation"}
                sleeping = jobs_dir / "中文 超时工程副本"
                clone_engine(baseline, sleeping)
                (sleeping / "game/bridge_owned_timeout.rpy").write_text("init -2000 python:\n    import time as owned_timeout_time\n    owned_timeout_time.sleep(60)\n", encoding="utf-8")
                timeout_result, observed = await asyncio.to_thread(observed_sdk, sdk, sleeping, 2.0, jobs_dir / "timeout-private-saves")
                await asyncio.sleep(0.1)
                timeout_record = {"process": record_execution(jobs_dir, "owned-sdk-timeout", timeout_result), "observed_owned_processes": observed,
                                  "remaining_owned_processes": [item for item in observed if same_live(item)],
                                  "unrelated_sentinel_survived": same_live(sentinel_identity)}
                timeout_record["passed"] = timeout_result.timed_out and timeout_result.cleanup_complete and timeout_result.process_id in timeout_result.terminated_process_ids and bool(observed) and not timeout_record["remaining_owned_processes"] and timeout_record["unrelated_sentinel_survived"]
                jobs_report["timeout"] = timeout_record
                if not timeout_record["passed"]:
                    raise RuntimeError("Real SDK timeout cleanup or unrelated-process survival was unproven")
                save()

                head_before = store.head(project_id)
                head_path = store.directory(project_id) / "model/head.json"
                head_hash_before = file_hash(head_path)
                cancel_job = await call("check_project", {"project_id": project_id, "revision": 2})
                known_jobs.append(cancel_job["job_id"])
                observed_cancel = []
                deadline = time.monotonic() + 30
                while not observed_cancel:
                    cancel_job = await call("get_job", {"job_id": cancel_job["job_id"]})
                    if cancel_job["state"] in TERMINAL:
                        raise RuntimeError("Cancellation job completed before its owned SDK child was observed")
                    worker = JobManager._owned_process(cancel_job)
                    if worker is not None:
                        descendants = worker.children(recursive=True)
                        candidates = []
                        for child in descendants:
                            try:
                                argv = child.cmdline()
                                if str(sdk.entrypoint) in argv and any(cancel_job["job_id"] in part for part in argv):
                                    candidates.append(child)
                            except (psutil.NoSuchProcess, psutil.ZombieProcess):
                                pass
                        if candidates:
                            selected = [worker, *descendants]
                            parent = worker.parent()
                            if parent is not None and parent.pid == cancel_job.get("launcher_pid"):
                                selected.append(parent)
                            for child in selected:
                                try:
                                    observed_cancel.append(identity(child))
                                except (psutil.NoSuchProcess, psutil.ZombieProcess):
                                    pass
                    if time.monotonic() > deadline:
                        raise RuntimeError("Cancelled job's owned native SDK child was not observed within deadline")
                    await asyncio.sleep(0.02)
                cancelled = await call("cancel_job", {"job_id": cancel_job["job_id"]})
                cleanup_deadline = time.monotonic() + 5
                while any(same_live(item) for item in observed_cancel) and time.monotonic() < cleanup_deadline:
                    await asyncio.sleep(0.05)
                after = await call("get_job", {"job_id": cancel_job["job_id"]})
                cancellation = {"observed_before_cancel": cancel_job, "owned_processes": observed_cancel, "public_cancel_result": cancelled,
                                "persisted_status": after, "remaining_owned_processes": [item for item in observed_cancel if same_live(item)],
                                "head_before": head_before, "head_after": store.head(project_id), "head_sha256_before": head_hash_before,
                                "head_sha256_after": file_hash(head_path), "model_hash_after": digest(store.load(project_id, 2).model_dump(mode="json")),
                                "unrelated_sentinel_survived": same_live(sentinel_identity)}
                cancellation["passed"] = after["state"] == "cancelled" and after["cancel_cleanup"]["state"] in {"terminated", "inactive"} and not cancellation["remaining_owned_processes"] and cancellation["head_sha256_before"] == cancellation["head_sha256_after"] and cancellation["model_hash_after"] == original_model_hash and cancellation["unrelated_sentinel_survived"]
                jobs_report["cancellation"] = cancellation
                if not cancellation["passed"]:
                    raise RuntimeError("Public native cancellation did not preserve head/runnable or owned-process cleanup")
                jobs_report["complete"] = True
                jobs_report["not_verified"] = ["General process detachment beyond observed lineage", "Windows power loss or disk corruption", "Formal Demo and independent package regression", "Audio output", "Codex desktop tool discovery"]
                print(json.dumps({"stage": "native-timeout-and-public-cancel", "passed": True}, ensure_ascii=False), flush=True)
    except Exception as exc:
        jobs_report["error"] = repr(exc)
        jobs_report["traceback"] = traceback.format_exc()
        jobs_report["complete"] = False
    finally:
        manager = JobManager(Store(ROOT))
        for job_id in known_jobs:
            status = manager.get(job_id)
            if status["state"] not in TERMINAL:
                jobs_report.setdefault("probe_exit_cleanup", []).append(manager.cancel(job_id))
        if sentinel is not None:
            if sentinel.poll() is None:
                sentinel.terminate()
            sentinel.wait(timeout=5)
            jobs_report.setdefault("unrelated_sentinel", {})["closed_by_own_popen_handle"] = True
            jobs_report["unrelated_sentinel"]["exit_code"] = sentinel.returncode
        jobs_report["source_code_unchanged"] = code_hashes == {str(path.relative_to(ROOT)): file_hash(path) for path in CODE_FILES}
        diagnostics_report["source_code_unchanged"] = jobs_report["source_code_unchanged"]
        if not jobs_report["source_code_unchanged"]:
            jobs_report["complete"] = diagnostics_report["complete"] = False
        save()
    return jobs_report, diagnostics_report


async def run_voice_followup(previous_path: Path, run_dir: Path) -> dict:
    previous = read_json(previous_path)
    fixture = previous["fixture"]
    project_id, revision = fixture["project_id"], fixture["revision"]
    store = Store(ROOT)
    model = store.load(project_id)
    if not previous.get("complete") or model.revision != revision or model.name != "静音并发与诊断固定夹具 " + previous["run_id"]:
        raise ValueError("Voice followup requires this probe's completed, unchanged fixed fixture")
    speech_report = ROOT / "evidence/m0-audio/report.json"
    speech = read_json(speech_report)
    voice_path = Path(speech["tts"]["output_path"])
    report = {"schema_version": 1, "probe": "public-mcp-image-chinese-voice-branch-interactions", "complete": False,
              "run_directory": str(run_dir), "scope": "Public independent stdio MCP update and real native route/interaction workers on the earlier fixed fixture, not formal Demo or desktop Codex authoring.",
              "previous_silent_probe_report": str(previous_path), "previous_report_sha256": file_hash(previous_path),
              "prior_cancellation_scope": {"revision": revision, "note": "The earlier cancellation preserved its exact revision/head. This separate authorized public update deliberately advances that fixture afterward."},
              "project_id": project_id, "mcp_calls": [], "code_sha256": {str(path.relative_to(ROOT)): file_hash(path) for path in CODE_FILES},
              "probe_sha256": file_hash(Path(__file__).resolve()), "formal_demo_modified": False,
              "speech_source": {"report": str(speech_report), "report_sha256": file_hash(speech_report), "path": str(voice_path), "sha256": file_hash(voice_path), "text": speech["text"], "selected_voice": speech["tts"]["selected_voice"]},
              "audio_scope": {"loopback_recorded_here": False, "full_output_reference": str(ROOT / "evidence/m5-audio/runs/1790894031601175800/report.json"), "note": "This phase verifies public asset/dialogue mapping and execution in native routes/interactions; full-duration channel/output proof comes from the independent native-audio report for the identical speech hash."}}
    params = StdioServerParameters(command=sys.executable, args=["-m", "renpy_bridge.server"], cwd=str(ROOT),
             env={**os.environ, "RENPY_BRIDGE_HOME": str(ROOT), "PYTHONPATH": str(ROOT / "src"), "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1"})
    job_id = None
    def save():
        atomic_json(run_dir / "report.json", report)
        atomic_json(run_dir.parent.parent / "report.json", report)
    try:
        with (run_dir / "mcp-server.stderr.log").open("w", encoding="utf-8") as errlog:
            async with stdio_client(params, errlog=errlog) as (read, write):
                async with ClientSession(read, write) as session:
                    report["mcp_initialize"] = (await session.initialize()).model_dump(mode="json")
                    async def call(name, arguments):
                        value = tool_dict(await session.call_tool(name, arguments))
                        report["mcp_calls"].append({"name": name, "arguments": arguments, "result": value})
                        return value
                    environment = await call("get_environment", {"include_runtime": False})
                    package_version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
                    report["reported_versions"] = {"package_version": package_version, "environment_api_version": environment["version"], "consistent": package_version == environment["version"],
                                                   "note": "The frozen package is 0.1.1; get_environment still reports the prior hard-coded 0.1.0. This mismatch is recorded, not treated as matching version provenance."}
                    registered = await call("register_assets", {"project_id": project_id, "expected_revision": revision, "operation_id": "voice-assets-" + run_dir.name,
                              "assets": [{"asset_id": "fixed_chinese_voice", "kind": "audio", "source_path": str(voice_path),
                                          "provenance": {"generator": "Windows System.Speech", "voice": speech["tts"]["selected_voice"]["name"], "text": speech["text"], "source_report_sha256": file_hash(speech_report)}}]})
                    updated = await call("patch_frame", {"project_id": project_id, "frame_id": "first", "expected_revision": registered["revision"], "operation_id": "voice-map-" + run_dir.name,
                                            "patch": {"dialogue": {"text": speech["text"]}, "voice": "fixed_chinese_voice"}})
                    report["revision"] = updated["revision"]
                    job = await call("test_routes", {"project_id": project_id, "revision": updated["revision"], "include_interactions": True})
                    job_id = job["job_id"]
                    deadline = time.monotonic() + 150
                    while job["state"] not in TERMINAL:
                        await asyncio.sleep(0.3)
                        job = tool_dict(await session.call_tool("get_job", {"job_id": job_id}))
                        if time.monotonic() > deadline:
                            raise RuntimeError("Public voice/branch/interaction route job exceeded deadline")
                    pipeline_path = Path(job["report_path"])
                    pipeline = read_json(pipeline_path)
                    generation = pipeline["generation"]
                    manifest_path = Path(generation["manifest_path"])
                    manifest = read_json(manifest_path)
                    source_map = read_json(Path(generation["source_map_path"]))
                    first = source_map["frames"]["first"]
                    lines = (Path(generation["project_path"]) / "game/script.rpy").read_text(encoding="utf-8").splitlines()
                    voice_entry = manifest["assets"]["fixed_chinese_voice"]
                    voice_source_statement = '    voice "' + voice_entry["game_path"] + '"'
                    statement_present = voice_source_statement in lines[first["start_line"] - 1:first["end_line"]]
                    final_model = store.load(project_id, updated["revision"])
                    voice_mapping = final_model.frames["first"].voice == "fixed_chinese_voice" and final_model.frames["first"].dialogue.text == speech["text"] and voice_entry["sha256"] == file_hash(voice_path)
                    report.update(job=job, pipeline=pipeline, pipeline_report_path=str(pipeline_path), pipeline_report_sha256=file_hash(pipeline_path),
                                  pipeline_report_bytes=pipeline_path.stat().st_size, manifest_sha256=file_hash(manifest_path),
                                  generated_voice_statement_present=statement_present, voice_asset_dialogue_mapping_exact=voice_mapping,
                                  model_sha256=digest(final_model.model_dump(mode="json")), head_after=store.head(project_id))
                    report["complete"] = job["state"] == "succeeded" and pipeline["ok"] and pipeline["status"] == "passed" and pipeline["interaction_verification"]["status"] == "passed" and pipeline["route_coverage"]["route_count"] == 2 and statement_present and voice_mapping
    except Exception as exc:
        report["error"] = repr(exc)
        report["traceback"] = traceback.format_exc()
    finally:
        if job_id is not None:
            manager = JobManager(store)
            status = manager.get(job_id)
            if status["state"] not in TERMINAL:
                report["probe_exit_cleanup"] = manager.cancel(job_id)
        report["source_code_unchanged"] = report["code_sha256"] == {str(path.relative_to(ROOT)): file_hash(path) for path in CODE_FILES}
        if not report["source_code_unchanged"]:
            report["complete"] = False
        save()
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--voice-from-run", type=Path, help="Completed immutable silent jobs report; append voice via public MCP to that same fixed project.")
    args = parser.parse_args()
    run_id = str(time.time_ns())
    if args.voice_from_run:
        run_dir = ROOT / "evidence/m3-mcp-voice/runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=False)
        report = asyncio.run(run_voice_followup(args.voice_from_run.resolve(), run_dir))
        print(json.dumps({"voice_report": str(run_dir / "report.json"), "complete": report["complete"]}, ensure_ascii=False))
        return 0 if report["complete"] else 1
    jobs_dir = ROOT / "evidence/m2-native-jobs/runs" / run_id
    diagnostic_dir = ROOT / "evidence/m3-diagnostics/runs" / run_id
    jobs_dir.mkdir(parents=True, exist_ok=False)
    diagnostic_dir.mkdir(parents=True, exist_ok=False)
    jobs_report, diagnostics_report = asyncio.run(run_probe(run_id, jobs_dir, diagnostic_dir))
    print(json.dumps({"jobs_report": str(jobs_dir / "report.json"), "diagnostics_report": str(diagnostic_dir / "report.json"),
                      "jobs_complete": jobs_report["complete"], "diagnostics_complete": diagnostics_report["complete"]}, ensure_ascii=False))
    return 0 if jobs_report["complete"] and diagnostics_report["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
