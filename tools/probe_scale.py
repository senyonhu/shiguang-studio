"""Measure real STDIO MCP import/reopen/edit at 1000 and 5000 frames.

Fixtures use copied SDK artwork and generated source rows, not demo artwork.
Reports distinguish core/API scale from native compile/lint and make no claim
that all frames have been visually reviewed or played through.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import csv
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys
import time
import traceback
import zipfile

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import psutil

from renpy_bridge.generator import GENERATOR_VERSION, generate_project, stable_label
from renpy_bridge.derived_integrity import record_derived_sources
from renpy_bridge.native_qa import NATIVE_QA_SOURCE
from renpy_bridge.sdk import discover_sdk, run_sdk, _execute
from renpy_bridge.storage import Store, atomic_json, digest, file_hash, read_json


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence/m9-scale"
BATCH_SIZE = 200


@asynccontextmanager
async def session_for(home: Path):
    env = dict(os.environ, RENPY_BRIDGE_HOME=str(home), PYTHONUTF8="1",
               PYTHONPATH=str(ROOT / "src") + (os.pathsep + os.environ["PYTHONPATH"] if os.environ.get("PYTHONPATH") else ""))
    params = StdioServerParameters(command=sys.executable, args=["-m", "renpy_bridge.server"], env=env, cwd=str(ROOT))
    home.parent.mkdir(parents=True, exist_ok=True)
    with (home.parent / "mcp-server.stderr.log").open("a", encoding="utf-8") as log:
        async with stdio_client(params, errlog=log) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session


def disk_bytes(path):
    return sum(item.stat().st_size for item in Path(path).rglob("*") if item.is_file())


def code_hashes():
    return {str(path.relative_to(ROOT)): file_hash(path) for path in
            [Path(__file__).resolve(), *sorted((ROOT / "src/renpy_bridge").glob("*.py"))]}


def finite_route_summary(project):
    """Enumerate the fixed three finite routes, retaining compact evidence."""
    pending = [(project.entry_frame_id, [], [])]
    summaries = []
    covered = set()
    choices = []
    while pending:
        fid, path, selections = pending.pop()
        while True:
            if fid in path:
                raise AssertionError("The scale fixture contains an unexpected cycle")
            frame = project.frames[fid]
            path.append(fid)
            covered.add(fid)
            if frame.flow.kind == "end":
                summaries.append({"step_count": len(path), "frame_sequence_sha256": digest(path), "selected_choices": selections})
                break
            if frame.flow.kind == "next":
                fid = frame.flow.target_frame_id
                continue
            for choice in frame.flow.choices:
                selection = {"frame_id": fid, "choice_id": choice.choice_id, "target_frame_id": choice.target_frame_id}
                choices.append(selection)
                pending.append((choice.target_frame_id, list(path), selections + [selection]))
            break
    assert len(summaries) == 3 and covered == set(project.frames)
    return {"route_count": len(summaries), "routes": summaries, "choice_edges": choices,
            "reachable_frame_count": len(covered), "method": "pure model enumeration; not native route playback"}


async def sample_memory(stop, record):
    root_process = psutil.Process(os.getpid())
    record.update(peak_probe_rss_bytes=0, peak_owned_process_sum_rss_bytes=0, peak_server_rss_bytes=0, server_pids=[])
    while not stop.is_set():
        processes = [root_process]
        try:
            processes += root_process.children(recursive=True)
        except psutil.NoSuchProcess:
            pass
        total = 0
        for process in processes:
            try:
                rss = process.memory_info().rss
                total += rss
                if process.pid == root_process.pid:
                    record["peak_probe_rss_bytes"] = max(record["peak_probe_rss_bytes"], rss)
                elif "renpy_bridge.server" in process.cmdline():
                    record["peak_server_rss_bytes"] = max(record["peak_server_rss_bytes"], rss)
                    if process.pid not in record["server_pids"]:
                        record["server_pids"].append(process.pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        record["peak_owned_process_sum_rss_bytes"] = max(record["peak_owned_process_sum_rss_bytes"], total)
        try:
            await asyncio.wait_for(stop.wait(), timeout=0.05)
        except asyncio.TimeoutError:
            pass
    record["scope"] = "50ms samples of probe RSS and its live STDIO MCP descendants; summed RSS is not unique physical memory"


async def measure_core(run_dir: Path, count: int, artwork: Path):
    directory = run_dir / f"frames-{count}"
    directory.mkdir()
    home = directory / "bridge-home"
    source = directory / "规模 资料.csv"
    ids = [f"frame_{index:05d}" for index in range(count)]
    with source.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["幕", "帧身份", "原文"])
        for index, frame_id in enumerate(ids):
            writer.writerow(["规模幕", frame_id, f"规模原文第{index + 1}帧"])
    frames = [{
        "frame_id": frame_id, "act_id": "act_scale", "order": index,
        "bg": {"component_id": "background", "kind": "background", "asset_id": "background_a" if index % 2 == 0 else "background_b"},
        "dialogue": {"text": f"规模原文第{index + 1}帧"},
        "flow": {"kind": "next", "target_frame_id": ids[index + 1]} if index + 1 < count else {"kind": "end"},
        "provenance": {"source_id": f"source_scale_{count}", "row": index + 2},
    } for index, frame_id in enumerate(ids)]
    branch_index = count // 2
    frames[branch_index]["flow"] = {"kind": "choices", "choices": [
        {"choice_id": f"choice_{offset}", "text": f"规模分支{offset}", "target_frame_id": ids[branch_index + offset]}
        for offset in [1, 2, 3]
    ]}
    for offset in [1, 2, 3]:
        frames[branch_index + offset]["flow"] = {"kind": "next", "target_frame_id": ids[branch_index + 4]}
    report = {"frame_count": count, "batch_size": BATCH_SIZE, "tool_calls": [], "phases": {}, "complete": False,
              "is_codex_client_evidence": False, "scope": "independent real STDIO MCP core/API scale; no visual/route-playthrough claim"}
    stop = asyncio.Event()
    sampler = asyncio.create_task(sample_memory(stop, report.setdefault("memory", {})))
    started = time.perf_counter()

    async def call(session, name, arguments, expected_error=None):
        before = time.perf_counter()
        response = await session.call_tool(name, arguments)
        data = json.loads(next(content.text for content in response.content if content.type == "text"))
        report["tool_calls"].append({"name": name, "seconds": time.perf_counter() - before,
                                      "request_bytes": len(json.dumps(arguments, ensure_ascii=False).encode()),
                                      "response_bytes": len(json.dumps(data, ensure_ascii=False).encode()),
                                      "ok": data.get("ok"), "error_code": data.get("error", {}).get("code")})
        if expected_error is not None:
            if data.get("ok") is not False or data.get("error", {}).get("code") != expected_error:
                raise AssertionError(f"{name} did not return required {expected_error}: {data}")
        elif response.isError or data.get("ok") is not True:
            raise AssertionError(f"{name} failed: {data}")
        return data

    try:
        first = time.perf_counter()
        async with session_for(home) as session:
            environment = await call(session, "get_environment", {"include_runtime": False})
            assert Path(environment["home"]).resolve() == home.resolve()
            created = await call(session, "create_project", {"name": f"{count}帧规模夹具", "operation_id": "create"})
            project_id = created["project_id"]
            assets = await call(session, "register_assets", {
                "project_id": project_id, "expected_revision": 0, "operation_id": "same-bytes-assets",
                "assets": [{"asset_id": identity, "kind": "image", "source_path": str(artwork)} for identity in ["background_a", "background_b"]],
            })
            extracted = await call(session, "extract_source", {"project_id": project_id, "source_path": str(source),
                                     "source_id": f"source_scale_{count}", "offset": count - 1, "limit": 2})
            assert extracted["total_records"] == count + 1 and len(extracted["records"]) == 2
            assert extracted["records"][-1]["values"][1] == ids[-1]
            begun = await call(session, "begin_import", {"project_id": project_id, "frame_ids": ids, "expected_revision": 1, "operation_id": "begin"})
            session_id = begun["import_session_id"]
            first_batch = {"project_id": project_id, "import_session_id": session_id, "batch_id": "batch_0000", "frames": frames[:BATCH_SIZE], "acts": [{"act_id": "act_scale", "name": "规模幕"}]}
            staged = await call(session, "stage_import_batch", first_batch)
            repeated = await call(session, "stage_import_batch", first_batch)
            assert staged == repeated and staged["staged_count"] == BATCH_SIZE
            summary = await call(session, "inspect_project", {"project_id": project_id})
            assert summary["revision"] == 1 and summary["counts"]["frames"] == 0
            incomplete = await call(session, "commit_import", {"project_id": project_id, "import_session_id": session_id,
                                     "expected_revision": 1, "operation_id": "incomplete"}, "IMPORT_INCOMPLETE")
            assert len(incomplete["error"]["details"]["missing_frame_ids"]) == count - BATCH_SIZE
        report["phases"]["initial_extract_and_partial_candidate"] = time.perf_counter() - first
        # Closing the transport terminates the first MCP server. The reopened
        # process receives only IDs, not any in-memory candidate object.
        restart = time.perf_counter()
        async with session_for(home) as session:
            reopened = await call(session, "begin_import", {"project_id": project_id, "frame_ids": ids, "expected_revision": 1, "operation_id": "begin"})
            assert reopened["import_session_id"] == session_id
            summary = await call(session, "inspect_project", {"project_id": project_id})
            assert summary["revision"] == 1 and summary["counts"]["frames"] == 0
            report["reopened_candidate_preserved_effective_head"] = True
            report["phases"]["server_restart_and_resume"] = time.perf_counter() - restart
            batches_started = time.perf_counter()
            for offset in range(BATCH_SIZE, count, BATCH_SIZE):
                staged = await call(session, "stage_import_batch", {"project_id": project_id, "import_session_id": session_id,
                                    "batch_id": f"batch_{offset // BATCH_SIZE:04d}", "frames": frames[offset:offset + BATCH_SIZE]})
                assert staged["staged_count"] == min(count, offset + BATCH_SIZE)
            report["phases"]["remaining_batches"] = time.perf_counter() - batches_started
            commit_started = time.perf_counter()
            args = {"project_id": project_id, "import_session_id": session_id, "expected_revision": 1, "operation_id": "commit"}
            committed = await call(session, "commit_import", args)
            assert committed["revision"] == 2 and await call(session, "commit_import", args) == committed
            report["phases"]["atomic_commit_with_idempotent_retry"] = time.perf_counter() - commit_started
            patch_started = time.perf_counter()
            middle = ids[count // 2]
            updated_text = f"已通过MCP局部修改第{count // 2 + 1}帧"
            await call(session, "patch_frame", {"project_id": project_id, "frame_id": middle, "patch": {"dialogue": {"text": updated_text}}, "expected_revision": 2, "operation_id": "patch-middle"})
            inspected = await call(session, "inspect_project", {"project_id": project_id, "section": "frames", "object_id": middle})
            assert inspected["object"]["dialogue"]["text"] == updated_text and inspected["revision"] == 3
            report["phases"]["single_frame_patch_and_inspection"] = time.perf_counter() - patch_started
            pagination_started = time.perf_counter()
            pages = []
            for label, offset in [("first", 0), ("middle", count // 2), ("last", count - 1)]:
                page = await call(session, "inspect_project", {"project_id": project_id, "section": "frames", "offset": offset, "limit": 1})
                assert page["total"] == count and page["offset"] == offset and len(page["items"]) == 1
                assert page["items"][0]["frame_id"] == ids[offset]
                wanted = updated_text if offset == count // 2 else f"规模原文第{offset + 1}帧"
                assert page["items"][0]["dialogue"]["text"] == wanted
                pages.append({"page": label, "offset": offset, "frame_id": ids[offset], "text": wanted})
            report["pagination_verified"] = pages
            report["phases"]["first_middle_last_pagination"] = time.perf_counter() - pagination_started
            summary_started = time.perf_counter()
            summary = await call(session, "inspect_project", {"project_id": project_id})
            assert summary["counts"]["frames"] == count and len(summary["flow"]["reachable"]) == count
            assert summary["flow"]["ending_frames"] == [ids[-1]]
            assert not summary["flow"]["cycles"] and not summary["flow"]["without_exit"] and not summary["flow"]["unreachable"]
            report["phases"]["nonrecursive_graph_check"] = time.perf_counter() - summary_started
        reopened_store = Store(home)
        project = reopened_store.load(project_id)
        assert project.frames[ids[0]].dialogue.text == "规模原文第1帧"
        assert project.frames[ids[-1]].dialogue.text == f"规模原文第{count}帧"
        assert project.frames[middle].dialogue.text == updated_text
        asset_paths = {asset.path for asset in project.assets.values()}
        managed_files = list((reopened_store.directory(project_id) / "assets").glob("*"))
        assert len(asset_paths) == len(managed_files) == 1
        assert all(file_hash(reopened_store.directory(project_id) / asset.path) == asset.sha256 for asset in project.assets.values())
        graph_started = time.perf_counter()
        report["branch_merge"] = {"branch_frame_id": ids[branch_index], "join_frame_id": ids[branch_index + 4],
                                  "branch_frame_ids": ids[branch_index + 1:branch_index + 4],
                                  **finite_route_summary(project)}
        report["phases"]["three_branch_pure_enumeration"] = time.perf_counter() - graph_started
        report.update(complete=True, project_id=project_id, revision=project.revision, home=str(home),
                      model_hash=digest(project.model_dump(mode="json")), frame_id_count=len(project.frames),
                      source_record_count=count + 1, unique_managed_asset_file_count=1, logical_asset_count=2,
                      candidate_interruption_and_retry_verified=True, total_core_seconds=time.perf_counter() - started)
        report["core_disk_bytes"] = disk_bytes(directory)
        return report, project, reopened_store
    finally:
        stop.set()
        await sampler
        atomic_json(directory / "core-report.json", report)


def smoke_source(project):
    first = project.frames[project.entry_frame_id]
    last = next(frame for frame in project.frames.values() if frame.flow.kind == "end")
    return NATIVE_QA_SOURCE + f'''
testsuite global:
    teardown:
        exit

testcase scale_package_smoke:
    $ _test.timeout = 30.0
    $ _preferences.text_cps = 0
    run Start()
    pause 0.1 until eval (rb_current_frame_id == {json.dumps(first.frame_id)} and _rb_qa_visible_text() == {json.dumps(first.dialogue.text, ensure_ascii=False)})
    assert eval _rb_qa_assert_text({json.dumps(first.dialogue.text, ensure_ascii=False)})
    screenshot "scale_package_entry"
    # This jump is an explicitly declared package smoke. It is not route coverage.
    run Jump({json.dumps(stable_label(last.frame_id))})
    pause 0.1 until eval (rb_current_frame_id == {json.dumps(last.frame_id)} and _rb_qa_visible_text() == {json.dumps(last.dialogue.text, ensure_ascii=False)})
    pause 0.3
    assert eval _rb_qa_assert_text({json.dumps(last.dialogue.text, ensure_ascii=False)})
    assert eval (rb_current_frame_id == {json.dumps(last.frame_id)})
    screenshot "scale_package_last_frame"
    keysym "dismiss"
    pause 0.1 until screen "main_menu"
'''


async def main():
    run_dir = EVIDENCE / "runs" / str(time.time_ns())
    run_dir.mkdir(parents=True)
    report = {"run_directory": str(run_dir), "scope": "1000/5000 frame branching core/API scale plus native compile/lint/PC package and independent executable Start+Jump-to-end smoke; no complete native routes or audio claim",
              "is_codex_client_evidence": False, "formal_demo": False, "complete": False, "core": [], "native": [],
              "generator_version": GENERATOR_VERSION, "code_sha256_at_start": code_hashes()}

    def write_report():
        atomic_json(run_dir / "report.json", report)
        atomic_json(EVIDENCE / "report.json", report)

    try:
        sdk = discover_sdk(ROOT / "runtimes/renpy-8.5.3")
        report["sdk_lock"] = read_json(ROOT / "runtimes/sdk-lock.json")
        assert sdk.version == report["sdk_lock"]["version"]
        artwork = sdk.root / "launcher/game/gui7/icon.png"
        selected = []
        for count in [1000, 5000]:
            core, project, store = await measure_core(run_dir, count, artwork)
            report["core"].append(core)
            selected.append((project, store))
            write_report()
            print(f"core {count}: passed, {core['total_core_seconds']:.3f}s, peak server RSS {core['memory']['peak_server_rss_bytes'] / 1024 / 1024:.1f}MiB", flush=True)
        for project, store in selected:
            count = len(project.frames)
            native_dir = run_dir / f"native-{count}"
            native = {"frame_count": count, "sdk_version": sdk.version, "generator_version": GENERATOR_VERSION, "commands": {}, "complete": False,
                      "visual_verified": False, "routes_played_verified": False}
            report["native"].append(native)
            before = time.perf_counter()
            native["generation"] = generate_project(project, store.directory(project.project_id), native_dir, sdk)
            native["generation_seconds"] = time.perf_counter() - before
            (native_dir / "game/bridge_scale_testcases.rpy").write_text(smoke_source(project), encoding="utf-8")
            (native_dir / "game/bridge_scale_build.rpy").write_text('init python:\n    build.classify("tests/**", None)\n    build.classify("**.log", None)\n    build.classify("**.txt", None)\n', encoding="utf-8")
            native["controlled_qa_sources"] = record_derived_sources(native_dir, controlled_paths=["game/bridge_scale_testcases.rpy", "game/bridge_scale_build.rpy"])
            saves = run_dir / f"native-{count}-private-saves"
            saves.mkdir()
            env = {"RENPY_MULTIPERSISTENT": str(run_dir / f"native-{count}-private-multipersistent")}
            for command, extra in [("compile", []), ("lint", ["--error-code"])]:
                execution = run_sdk(sdk, native_dir, command, [*extra, "--savedir", str(saves)], timeout_seconds=1200, env=env)
                native["commands"][command] = asdict(execution)
                (run_dir / f"native-{count}-{command}.stdout.txt").write_text(execution.stdout, encoding="utf-8")
                (run_dir / f"native-{count}-{command}.stderr.txt").write_text(execution.stderr, encoding="utf-8")
                write_report()
                print(f"native {count} {command}: exit={execution.exit_code}, timeout={execution.timed_out}", flush=True)
                if execution.exit_code != 0 or execution.timed_out:
                    raise RuntimeError(f"Native {command} failed: {execution.stdout[-1200:]} {execution.stderr[-1200:]}")
            release = run_dir / f"pc-package-{count}"
            release.mkdir()
            packed = run_sdk(sdk, "launcher", "distribute", [str(native_dir), "--destination", str(release), "--package", "pc", "--no-update", "--savedir", str(saves)], timeout_seconds=1200, env=env)
            native["commands"]["distribute"] = asdict(packed)
            (run_dir / f"native-{count}-distribute.stdout.txt").write_text(packed.stdout, encoding="utf-8")
            (run_dir / f"native-{count}-distribute.stderr.txt").write_text(packed.stderr, encoding="utf-8")
            write_report()
            print(f"native {count} distribute: exit={packed.exit_code}, timeout={packed.timed_out}", flush=True)
            if packed.exit_code != 0 or packed.timed_out:
                raise RuntimeError(f"Native distribute failed: {packed.stdout[-1200:]} {packed.stderr[-1200:]}")
            packages = list(release.glob("*.zip"))
            if len(packages) != 1:
                raise AssertionError("A unique PC ZIP is required")
            package = packages[0]
            independent = release / "independent"
            independent.mkdir()
            with zipfile.ZipFile(package) as archive:
                for item in archive.infolist():
                    if not (independent / item.filename).resolve().is_relative_to(independent.resolve()):
                        raise AssertionError("PC ZIP contains a path outside the isolated extraction root")
                entry_count = len(archive.infolist())
                archive.extractall(independent)
            build_name = native["generation"]["build_name"]
            executables = [path for path in independent.rglob("*.exe") if path.name == build_name + ".exe"]
            if len(executables) != 1:
                raise AssertionError("PC package must contain the unique independent Windows executable")
            executable = executables[0]
            package_saves = run_dir / f"package-{count}-private-saves"
            package_saves.mkdir()
            smoke = _execute([str(executable), str(executable.parent), "test", "--report-detailed", "--savedir", str(package_saves)], executable.parent, 120, env)
            native["commands"]["independent-package-smoke"] = asdict(smoke)
            (run_dir / f"native-{count}-package-smoke.stdout.txt").write_text(smoke.stdout, encoding="utf-8")
            (run_dir / f"native-{count}-package-smoke.stderr.txt").write_text(smoke.stderr, encoding="utf-8")
            native["package"] = {"path": str(package), "sha256": file_hash(package), "bytes": package.stat().st_size,
                                 "zip_entry_count": entry_count, "executable": str(executable), "executable_sha256": file_hash(executable),
                                 "independent_directory": str(executable.parent), "qa_harness_included": True,
                                 "smoke_scope": "native Start first dialogue, declared Jump to last dialogue, return to main menu; not route traversal"}
            native["screenshots"] = [{"path": str(path), "sha256": file_hash(path)} for path in sorted((executable.parent / "tests/screenshots").glob("*.png"))]
            write_report()
            print(f"independent PC {count} smoke: exit={smoke.exit_code}, timeout={smoke.timed_out}, zip={package.stat().st_size} bytes", flush=True)
            if smoke.exit_code != 0 or smoke.timed_out or "[rpytest] Status: PASSED" not in smoke.stdout + smoke.stderr:
                raise RuntimeError(f"Independent package smoke failed: {smoke.stdout[-1500:]} {smoke.stderr[-1200:]}")
            if len(native["screenshots"]) != 2:
                raise AssertionError("Independent package entry and last-frame screenshots are required")
            native["complete"] = native["independent_executable_smoke_verified"] = True
            native["generated_disk_bytes"] = disk_bytes(native_dir)
            native["release_and_extracted_disk_bytes"] = disk_bytes(release)
            write_report()
        report["complete"] = all(core["complete"] for core in report["core"]) and len(report["native"]) == 2 and all(native["complete"] for native in report["native"])
        report["code_sha256_at_end"] = code_hashes()
        if report["code_sha256_at_start"] != report["code_sha256_at_end"]:
            raise RuntimeError("Production code changed during the scale gate; do not treat this as a frozen-version pass")
        report["total_run_disk_bytes"] = disk_bytes(run_dir)
        write_report()
        print(f"Scale report: {run_dir / 'report.json'}", flush=True)
        return 0
    except Exception as error:
        report["error"] = {"type": type(error).__name__, "message": str(error), "traceback": traceback.format_exc()}
        write_report()
        print(report["error"]["traceback"], flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
