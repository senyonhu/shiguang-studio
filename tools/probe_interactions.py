"""Run real Ren'Py player interactions on a fresh, disposable native game.

Requires the already acquired and verified 8.5.3 SDK and the editable bridge
package. This captures evidence; it does not create or overwrite regression
baselines, and it does not validate audio output or release compatibility.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import shutil
import sys
import time
import traceback

from renpy_bridge.generator import generate_project
from renpy_bridge.models import Act, Asset, Character, Component, Dialogue, Flow, Frame, Layout, Project
from renpy_bridge.native_qa import NATIVE_QA_SOURCE, interaction_testcases
from renpy_bridge.sdk import discover_sdk, run_sdk


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence" / "m5-interactions"


def generated_input_hashes(project: Path) -> dict[str, str]:
    return {path.relative_to(project).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(project.rglob("*")) if path.is_file() and
            path.suffix not in {".rpyc", ".pyc"} and
            not set(path.relative_to(project).parts) & {"saves", "cache", "tests", "__pycache__"} and
            path.name not in {"log.txt", "traceback.txt", "errors.txt"}}


def run_generated_interactions(project_path: Path, first_frame_id: str, second_frame_id: str,
                               first_text: str | None = None, second_text: str | None = None) -> int:
    """Run QA in a clean generated copy; the entry frame must be first_frame_id.

    This helper never opens or writes the authoring model. Both selected frames
    must have dialogue and a single native dismiss must advance first to second.
    Existing generated QA scripts are excluded only from this disposable copy.
    """
    run_dir = EVIDENCE / "runs" / str(time.time_ns())
    run_dir.mkdir(parents=True, exist_ok=False)
    report = {"schema_version": 1, "probe": "native-player-interactions-generated-copy",
              "run_directory": str(run_dir), "complete": False, "commands": {},
              "authoring_model_modified": False, "source_engine_executed": False,
              "code_sha256": {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                              for path in [Path(__file__).resolve(), ROOT / "src/renpy_bridge/native_qa.py"]}}
    def write_report():
        value = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        (run_dir / "report.json").write_text(value, encoding="utf-8")
        (EVIDENCE / "report.json").write_text(value, encoding="utf-8")
    try:
        original = project_path.resolve(strict=True)
        if not (original / "game").is_dir() or not (original / "bridge-manifest.json").is_file():
            raise ValueError("--project must be a generated bridge project with game and manifest")
        if original.is_symlink() or any(path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()) for path in original.rglob("*")):
            raise ValueError("Generated project copy cannot traverse symbolic links or junctions")
        before = generated_input_hashes(original)
        manifest = json.loads((original / "bridge-manifest.json").read_text(encoding="utf-8"))
        source_map = json.loads((original / "bridge-source-map.json").read_text(encoding="utf-8"))
        if {first_frame_id, second_frame_id} - set(source_map["frames"]):
            raise ValueError("Selected first/second frame is absent from the generated source map")
        report["source"] = {"path": str(original), "project_id": manifest["project_id"], "revision": manifest["revision"], "input_hashes": before}
        target = run_dir / "中文 原生交互副本"
        target.mkdir()
        shutil.copytree(original / "game", target / "game", ignore=shutil.ignore_patterns("saves", "cache", "tests", "__pycache__", "*.rpyc", "*.pyc"))
        for name in ("bridge-manifest.json", "bridge-source-map.json"):
            shutil.copyfile(original / name, target / name)
        removed = []
        for name in ("bridge_testcases.rpy", "bridge_interactions.rpy", "bridge_audio_qa.rpy", "testcases.rpy"):
            path = target / "game" / name
            if path.is_file():
                removed.append({"name": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
                path.unlink()
        qa_source = interaction_testcases(first_frame_id, second_frame_id, first_text=first_text, second_text=second_text)
        (target / "game/bridge_interactions.rpy").write_text(NATIVE_QA_SOURCE + '\n\ntestsuite global:\n    teardown:\n        exit\n\n' + qa_source, encoding="utf-8")
        report["fixture"] = {"copied_project": str(target), "first_frame_id": first_frame_id, "second_frame_id": second_frame_id,
                             "first_text": first_text, "second_text": second_text, "excluded_existing_qa": removed}
        sdk = discover_sdk(ROOT / "runtimes/renpy-8.5.3")
        report["sdk_version"] = sdk.version
        saves, multi = run_dir / "private-saves", run_dir / "private-multipersistent"
        saves.mkdir()
        multi.mkdir()
        report["save_isolation"] = {"savedir": str(saves), "multipersistent": str(multi), "game_local_saves": str(target / "game/saves")}
        for name, command, arguments, timeout in [("compile", "compile", [], 60), ("lint", "lint", ["--error-code"], 60),
                                                   ("native-interactions", "test", ["rb_interactions", "--report-detailed"], 150)]:
            execution = run_sdk(sdk, target, command, [*arguments, "--savedir", str(saves)], timeout_seconds=timeout, env={"RENPY_MULTIPERSISTENT": str(multi)})
            report["commands"][name] = asdict(execution)
            (run_dir / (name + ".stdout.log")).write_text(execution.stdout, encoding="utf-8")
            (run_dir / (name + ".stderr.log")).write_text(execution.stderr, encoding="utf-8")
            for engine_name in ("log.txt", "traceback.txt", "errors.txt"):
                if (target / engine_name).is_file():
                    shutil.copyfile(target / engine_name, run_dir / (name + "." + engine_name))
            write_report()
            if execution.exit_code != 0 or execution.timed_out or not execution.cleanup_complete:
                raise RuntimeError(f"{name} failed or exceeded its bound")
        report["source_input_unchanged"] = before == generated_input_hashes(original)
        if not report["source_input_unchanged"]:
            raise RuntimeError("Original generated input changed while QA was running")
        report["complete"] = True
        report["verified"] = ["native-save-load-rollback", "native-history", "native-preferences", "native-auto-forward", "native-skip"]
    except Exception as exc:
        report["error"] = repr(exc)
        report["traceback"] = traceback.format_exc()
    write_report()
    print(json.dumps({"report_path": str(run_dir / "report.json"), "complete": report["complete"]}, ensure_ascii=False))
    return 0 if report["complete"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, help="Existing generated project; only a clean evidence copy is executed.")
    parser.add_argument("--first-frame")
    parser.add_argument("--second-frame")
    parser.add_argument("--first-text")
    parser.add_argument("--second-text")
    args = parser.parse_args()
    if args.project:
        if not args.first_frame or not args.second_frame:
            parser.error("--project requires --first-frame and --second-frame")
        return run_generated_interactions(args.project, args.first_frame, args.second_frame, args.first_text, args.second_text)
    run_dir = EVIDENCE / "runs" / str(time.time_ns())
    run_dir.mkdir(parents=True, exist_ok=False)
    report: dict = {
        "schema_version": 1,
        "probe": "native-player-interactions",
        "code_sha256": {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                        for path in [Path(__file__).resolve(), ROOT / "src/renpy_bridge/generator.py", ROOT / "src/renpy_bridge/native_qa.py"]},
        "run_directory": str(run_dir),
        "commands": {},
        "complete": False,
        "screenshot_policy": "Fresh isolated capture evidence; no approved regression baseline is overwritten.",
        "actual_audio_output_verified": False,
        "old_save_compatibility_verified": False,
        "release_package_verified": False,
    }

    def write_report() -> None:
        content = json.dumps(report, ensure_ascii=False, indent=2)
        (run_dir / "report.json").write_text(content, encoding="utf-8")
        EVIDENCE.mkdir(parents=True, exist_ok=True)
        (EVIDENCE / "report.json").write_text(content, encoding="utf-8")

    try:
        sdk = discover_sdk(ROOT / "runtimes" / "renpy-8.5.3")
        report["sdk_version"] = sdk.version
        report["sdk_root"] = str(sdk.root)
        asset_root = run_dir / "authoring"
        (asset_root / "assets").mkdir(parents=True)
        icon = sdk.root / "launcher" / "game" / "gui7" / "icon.png"
        icon_data = icon.read_bytes()
        (asset_root / "assets" / "icon.png").write_bytes(icon_data)
        asset = Asset(
            asset_id="asset_icon", kind="image", path="assets/icon.png",
            sha256=hashlib.sha256(icon_data).hexdigest(),
        )
        first_id, second_id, third_id = "frame_first", "frame_second", "frame_last"
        frames = {
            first_id: Frame(
                frame_id=first_id, act_id="act_qa", order=0,
                bg=Component(component_id="bg_first", kind="background", asset_id="asset_icon"),
                decorations=[Component(
                    component_id="label_first", kind="text", text="存档前：第一画面",
                    layout=Layout(x=0.5, y=0.15, anchor_x=0.5, anchor_y=0.5, width=0.9),
                )],
                dialogue=Dialogue(speaker_id="actor_qa", text="第一句：保存、载入与回滚应恢复这句话和完整画面。"),
                flow=Flow(kind="next", target_frame_id=second_id),
            ),
            second_id: Frame(
                frame_id=second_id, act_id="act_qa", order=1,
                characters=[Component(
                    component_id="actor_second", kind="character", asset_id="asset_icon", character_id="actor_qa",
                    layout=Layout(x=0.75, y=0.9, anchor_x=0.5, anchor_y=1.0, height=0.5),
                )],
                decorations=[Component(
                    component_id="label_second", kind="text", text="推进后：第二画面",
                    layout=Layout(x=0.5, y=0.15, anchor_x=0.5, anchor_y=0.5, width=0.9),
                )],
                dialogue=Dialogue(speaker_id="actor_qa", text="第二句：玩家菜单、历史与偏好不应改变当前剧情。"),
                flow=Flow(kind="next", target_frame_id=third_id),
            ),
            third_id: Frame(
                frame_id=third_id, act_id="act_qa", order=2,
                dialogue=Dialogue(text="第三句：自动播放和跳过仍走原生剧情路径。"),
                flow=Flow(kind="end"),
            ),
        }
        project = Project(
            project_id="project_native_interactions", name="原生玩家交互验收",
            save_namespace="rb_native_interactions_fixture",
            acts={"act_qa": Act(act_id="act_qa", frame_ids=[first_id, second_id, third_id])},
            frames=frames,
            characters={"actor_qa": Character(character_id="actor_qa", name="测试角色")},
            assets={"asset_icon": asset}, entry_frame_id=first_id,
        )
        target = run_dir / "中文 原生交互工程"
        report["generation"] = generate_project(project, asset_root, target, sdk)
        # Expected first/second text comes from the immutable fixture, rather
        # than reading back whatever happened to be visible at the time.
        first_text = frames[first_id].dialogue.text
        second_text = frames[second_id].dialogue.text
        qa_source = interaction_testcases(first_id, second_id, namespace="rb_interactions",
                                         first_text=first_text, second_text=second_text)
        source = NATIVE_QA_SOURCE + '\n\ntestsuite global:\n    teardown:\n        exit\n\n' + qa_source
        (target / "game" / "bridge_interactions.rpy").write_text(source, encoding="utf-8")
        report["fixture"] = {"project_path": str(target), "first_frame_id": first_id, "second_frame_id": second_id}
        savedir = run_dir / "private-saves"
        savedir.mkdir()
        multipersistent = run_dir / "private-multipersistent"
        multipersistent.mkdir()
        env = {"RENPY_MULTIPERSISTENT": str(multipersistent)}
        report["save_isolation"] = {"savedir": str(savedir), "multipersistent": str(multipersistent), "extra_savedirs": [],
                                    "game_local_saves": str(target / "game/saves"),
                                    "note": "Fixed SDK also adds game/saves; both destinations belong to this disposable generated fixture."}
        for name, command, arguments, timeout in [
            ("compile", "compile", [], 60),
            ("lint", "lint", ["--error-code"], 60),
            ("native-interactions", "test", ["rb_interactions", "--report-detailed"], 150),
        ]:
            result = run_sdk(sdk, target, command, [*arguments, "--savedir", str(savedir)], timeout_seconds=timeout, env=env)
            (run_dir / f"{name}.stdout.log").write_text(result.stdout, encoding="utf-8")
            (run_dir / f"{name}.stderr.log").write_text(result.stderr, encoding="utf-8")
            report["commands"][name] = asdict(result)
            for engine_file in ("log.txt", "traceback.txt", "errors.txt"):
                path = target / engine_file
                if path.is_file():
                    (run_dir / f"{name}.{engine_file}").write_bytes(path.read_bytes())
            write_report()
            if result.exit_code != 0 or result.timed_out or not result.cleanup_complete:
                raise RuntimeError(f"{name} failed: exit={result.exit_code}, timeout={result.timed_out}, cleanup={result.cleanup_complete}")
        report["complete"] = True
        report["verified"] = ["native-save-load-rollback", "native-history", "native-preferences", "native-auto-forward", "native-skip"]
        write_report()
        print(json.dumps({"report_path": str(run_dir / "report.json"), "complete": True}, ensure_ascii=False))
        return 0
    except Exception as exc:
        report["error"] = repr(exc)
        report["traceback"] = traceback.format_exc()
        write_report()
        print(json.dumps({"report_path": str(run_dir / "report.json"), "complete": False, "error": repr(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
