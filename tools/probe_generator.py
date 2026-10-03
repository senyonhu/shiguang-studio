"""Execute a small native generator fixture; this is not the final demo."""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import shutil
import time

from renpy_bridge.generator import generate_project
from renpy_bridge.models import Act, Asset, Character, Component, Dialogue, Flow, Choice, Frame, Project
from renpy_bridge.native_qa import NATIVE_QA_SOURCE
from renpy_bridge.sdk import discover_sdk, run_sdk

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence" / "m3-generator"


def main() -> None:
    run_dir = EVIDENCE / "runs" / str(time.time_ns())
    run_dir.mkdir(parents=True, exist_ok=False)
    sdk = discover_sdk(ROOT / "runtimes" / "renpy-8.5.3")
    authoring = run_dir / "input"
    asset_dir = authoring / "assets"
    asset_dir.mkdir(parents=True, exist_ok=True)
    icon = asset_dir / "sdk-icon.png"
    shutil.copyfile(sdk.root / "launcher" / "game" / "gui7" / "icon.png", icon)
    sha = hashlib.sha256(icon.read_bytes()).hexdigest()
    literal = '你好  连续空格 "引号" \\路径 [不存在变量] {b}字面{/b} 100% 〖字|音〗\n第二行\tTab'
    decorative_literal = '字面量 [rb_current_frame_id] {b}不加粗{/b} 100% "引号" \\路径\n第二行'
    ids = ["frame_entry", "frame_pure", "frame_choice", "frame_end_a", "frame_end_b"]
    frames = {
        "frame_entry": Frame(frame_id="frame_entry", act_id="act_probe",
            bg=Component(component_id="bg_entry", kind="background", asset_id="asset_icon"),
            decorations=[Component(component_id="text_literal", kind="text",
                text=decorative_literal)],
            dialogue=Dialogue(speaker_id="character_probe", text=literal),
            flow=Flow(kind="next", target_frame_id="frame_pure")),
        "frame_pure": Frame(frame_id="frame_pure", act_id="act_probe", dialogue=None,
            flow=Flow(kind="next", target_frame_id="frame_choice")),
        "frame_choice": Frame(frame_id="frame_choice", act_id="act_probe",
            characters=[Component(component_id="character_icon", kind="character", character_id="character_probe", asset_id="asset_icon")],
            dialogue=Dialogue(speaker_id="character_probe", text="对白和选项在同一次交互显示。"),
            flow=Flow(kind="choices", choices=[Choice(choice_id="choice_a", text="路线甲", target_frame_id="frame_end_a"),
                                               Choice(choice_id="choice_b", text="路线乙", target_frame_id="frame_end_b")])),
        "frame_end_a": Frame(frame_id="frame_end_a", act_id="act_probe", dialogue=Dialogue(text="甲线终点，已清除角色和装饰。"), flow=Flow(kind="end")),
        "frame_end_b": Frame(frame_id="frame_end_b", act_id="act_probe", dialogue=Dialogue(text="乙线终点，已清除角色和装饰。"), flow=Flow(kind="end")),
    }
    project = Project(project_id="project_native_probe", name="原生生成器中文探测", save_namespace="rb_native_probe",
                      acts={"act_probe": Act(act_id="act_probe", frame_ids=ids)}, frames=frames,
                      characters={"character_probe": Character(character_id="character_probe", name="测试角色")},
                      assets={"asset_icon": Asset(asset_id="asset_icon", kind="image", path="assets/sdk-icon.png", sha256=sha)},
                      entry_frame_id="frame_entry")
    target = run_dir / "中文 原生生成器工程"
    result = generate_project(project, authoring, target, sdk)
    test_source = NATIVE_QA_SOURCE + '''
testsuite global:
    teardown:
        exit

testcase route_a:
    $ _test.timeout = 15.0
    $ _preferences.text_cps = 0
    run Start()
    assert eval (rb_current_frame_id == "frame_entry")
    pause 0.8
    screenshot "entry"
    $ expected_literal = M3_EXPECTED_LITERAL
    $ _rb_qa_assert_text(expected_literal)
    $ _rb_qa_assert_layer_text([M3_EXPECTED_DECORATION])
    keysym "dismiss" until eval (rb_current_frame_id == "frame_pure")
    pause 0.25
    screenshot "pure"
    assert not screen "say"
    assert eval (not renpy.get_showing_tags(layer="rb_background"))
    assert eval (not renpy.get_showing_tags(layer="rb_characters"))
    assert eval (not renpy.get_showing_tags(layer="rb_decorations"))
    keysym "dismiss" until eval (rb_current_frame_id == "frame_choice")
    pause 0.25
    assert eval (rb_current_frame_id == "frame_choice")
    assert screen "say"
    $ print("NATIVE_MENU_CAPTION_TEXT=" + repr(renpy.get_displayable("say", "what").get_all_text()))
    screenshot "choice"
    assert eval (renpy.get_displayable("say", "what").get_all_text() == "对白和选项在同一次交互显示。")
    assert "路线甲"
    assert "路线乙"
    click "路线甲"
    pause 0.25
    assert eval (rb_current_frame_id == "frame_end_a")
    assert eval (not renpy.get_showing_tags(layer="rb_background"))
    assert eval (not renpy.get_showing_tags(layer="rb_characters"))
    assert eval (not renpy.get_showing_tags(layer="rb_decorations"))
    pause 0.8
    screenshot "end_a"

testcase route_b:
    $ _preferences.text_cps = 0
    run MainMenu(confirm=False)
    run Start()
    assert eval (rb_current_frame_id == "frame_entry")
    keysym "dismiss" until eval (rb_current_frame_id == "frame_choice")
    pause 0.25
    click "路线乙"
    pause 0.25
    assert eval (rb_current_frame_id == "frame_end_b")
    pause 0.8
    screenshot "end_b"
'''
    (target / "game" / "testcases.rpy").write_text(test_source.replace("M3_EXPECTED_LITERAL", repr(literal)).replace("M3_EXPECTED_DECORATION", repr(decorative_literal)), encoding="utf-8")
    report = {"generation": result, "commands": {}, "complete": False, "run_directory": str(run_dir),
              "code_sha256": {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                              for path in [Path(__file__).resolve(), ROOT / "src/renpy_bridge/generator.py", ROOT / "src/renpy_bridge/native_qa.py"]},
              "known_limitations": [{"code": "TAB_GLYPH_UNSUPPORTED", "status": "warning",
                                     "description": "Logical U+0009 is preserved exactly. The fixed Chinese font renders a visible missing-glyph box for Tab; formal demo text does not contain Tab."}],
              "actual_audio_output_verified": False, "release_package_verified": False}
    def write_report():
        content = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        (run_dir / "report.json").write_text(content, encoding="utf-8")
        (EVIDENCE / "report.json").write_text(content, encoding="utf-8")
    isolated = {"RENPY_MULTIPERSISTENT": str(run_dir / "private-multipersistent")}
    saves = run_dir / "private-saves"
    saves.mkdir(parents=True, exist_ok=True)
    for name, command, arguments, timeout in [
        ("compile", "compile", [], 90),
        ("lint", "lint", ["--error-code"], 90),
        ("native-routes", "test", ["--report-detailed"], 120),
    ]:
        execution = run_sdk(sdk, target, command, [*arguments, "--savedir", str(saves)],
                            timeout_seconds=timeout, env=isolated)
        report["commands"][name] = asdict(execution)
        (run_dir / f"{name}.stdout.txt").write_text(execution.stdout, encoding="utf-8")
        (run_dir / f"{name}.stderr.txt").write_text(execution.stderr, encoding="utf-8")
        if (target / "log.txt").is_file():
            shutil.copyfile(target / "log.txt", run_dir / f"{name}.engine-log.txt")
        print(f"{name}: exit={execution.exit_code} timeout={execution.timed_out}", flush=True)
        if execution.exit_code != 0:
            print(execution.stdout[-3000:] + execution.stderr[-3000:], flush=True)
        write_report()
    screenshots = [{"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                   for path in target.rglob("*.png") if "screenshots" in path.parts]
    report["screenshots"] = screenshots
    report["complete"] = all(item["exit_code"] == 0 and not item["timed_out"] for item in report["commands"].values()) and len(screenshots) >= 5
    write_report()
    print(f"generator complete={report['complete']} screenshot_count={len(screenshots)}", flush=True)


if __name__ == "__main__":
    main()
