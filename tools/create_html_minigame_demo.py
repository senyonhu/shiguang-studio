"""Create a new Chinese HTML minigame demo only through real STDIO MCP calls.

Never updates the existing Windows demo. Every invocation creates a distinct
project and evidence run. This does not fabricate gameplay or mark validation
jobs passed. The returned project remains available for native verification.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import uuid

from jsonschema import Draft202012Validator
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ROOT / "examples/html_minigames/production/v1.0.1"


def write_json(path: Path, value) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def hash_file(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def binding(variable_id: str) -> dict:
    return {"kind": "variable", "variable_id": variable_id}


def effects(prefix: str, score: str, repaired=False) -> list[dict]:
    result = [
        {"effect_id": prefix + "_store_score", "op": "set", "variable_id": score,
         "value": {"kind": "result", "field": "score"}},
        {"effect_id": prefix + "_add_total", "op": "add", "variable_id": "total_score",
         "value": {"kind": "result", "field": "score"}},
    ]
    if repaired:
        result.append({"effect_id": prefix + "_repaired", "op": "set", "variable_id": "greenhouse_repaired",
                       "value": {"kind": "literal", "value": True}})
    return result


def fixture() -> tuple[list[dict], list[dict]]:
    variables = [
        {"variable_id": "puzzle_scramble", "type": "int", "default": 12, "minimum": 2, "maximum": 80},
        {"variable_id": "signal_rounds", "type": "int", "default": 6, "minimum": 3, "maximum": 24},
        {"variable_id": "signal_seconds", "type": "float", "default": 12.0, "minimum": 3, "maximum": 60},
        {"variable_id": "target_temperature", "type": "int", "default": 37, "minimum": 20, "maximum": 50},
        *[{"variable_id": name, "type": "int", "default": 0, "minimum": 0}
          for name in ("puzzle_score", "signal_score", "repair_score", "total_score")],
        {"variable_id": "greenhouse_repaired", "type": "bool", "default": False},
    ]
    for value in variables:
        value["provenance"] = {"source_id": "html_demo_story_v1"}
    frames = []

    def frame(frame_id: str, text: str, flow: dict, speaker=None):
        frames.append({
            "frame_id": frame_id, "act_id": "act_greenhouse", "order": len(frames),
            "source_label": frame_id, "bg": None, "characters": [], "decorations": [],
            "dialogue": {"speaker_id": speaker, "text": text}, "voice": None, "bgm": None,
            "sfx": [], "transition": {"kind": "none", "duration": 0.3}, "flow": flow,
            "provenance": {"source_id": "html_demo_story_v1", "source_location": "scene:" + frame_id},
        })

    def next_frame(frame_id: str, text: str, target: str, speaker=None):
        frame(frame_id, text, {"kind": "next", "target_frame_id": target}, speaker)

    next_frame("frame_intro", "雨停之后，林夏找到了旧温室。门后的灯仍亮着，像有人在等待。", "frame_puzzle_intro")
    next_frame("frame_puzzle_intro", "门锁上的星图乱了。把数字排好，我们就能进去。", "frame_puzzle", "linxia")
    frame("frame_puzzle", "试着打开星图锁。", {
        "kind": "minigame", "game_id": "prod_puzzle", "instance_id": "instance_star_lock",
        "inputs": {"seed": {"kind": "literal", "value": 1742}, "scramble_moves": binding("puzzle_scramble")},
        "result_branches": [
            {"branch_id": "branch_puzzle_great", "outcome": "win", "predicate": {
                "op": "gte", "left": {"kind": "result", "field": "score"}, "right": {"kind": "literal", "value": 800}},
             "effects": effects("puzzle_great", "puzzle_score"), "target_frame_id": "frame_puzzle_great"},
            {"branch_id": "branch_puzzle_win", "outcome": "win", "effects": effects("puzzle_win", "puzzle_score"),
             "target_frame_id": "frame_puzzle_win"},
        ],
        "cancel_target_frame_id": "frame_puzzle_cancel", "error_target_frame_id": "frame_puzzle_error",
        "fallback_target_frame_id": "frame_puzzle_fallback", "save_mode": "entry_restart",
    })
    next_frame("frame_puzzle_great", "咔哒。你几乎没有犹豫就找到了星图的秩序，门缓缓打开。", "frame_signal_intro")
    next_frame("frame_puzzle_win", "星图终于归位。林夏推开门，闻到了湿润的泥土气息。", "frame_signal_intro")
    next_frame("frame_puzzle_cancel", "林夏收回手，绕到温室侧门。还有别的路可以走。", "frame_signal_intro")
    next_frame("frame_puzzle_error", "门锁暂时没有反应。林夏找到一把备用钥匙，决定先检查电路。", "frame_signal_intro")
    next_frame("frame_puzzle_fallback", "门锁轻轻震了一下。林夏暂时记下这件事，带你走向电路箱。", "frame_signal_intro")
    next_frame("frame_signal_intro", "供电箱的信号正在衰减。跟着亮起的端子接线，别让它归零。", "frame_signal", "linxia")
    frame("frame_signal", "接通信号回路。", {
        "kind": "minigame", "game_id": "prod_timed", "instance_id": "instance_signal_circuit",
        "inputs": {"duration_seconds": binding("signal_seconds"), "rounds": binding("signal_rounds")},
        "result_branches": [
            {"branch_id": "branch_signal_win", "outcome": "win", "effects": effects("signal_win", "signal_score"),
             "target_frame_id": "frame_signal_win"},
            {"branch_id": "branch_signal_lose", "outcome": "lose", "effects": effects("signal_lose", "signal_score"),
             "target_frame_id": "frame_signal_lose"},
        ],
        "cancel_target_frame_id": "frame_signal_cancel", "error_target_frame_id": "frame_signal_error",
        "fallback_target_frame_id": "frame_signal_fallback", "save_mode": "entry_restart",
    })
    next_frame("frame_signal_win", "电路箱里的灯一盏盏亮起。温室深处，风扇重新转动。", "frame_repair_intro")
    next_frame("frame_signal_lose", "信号熄灭了。林夏接上备用电池：没关系，我们还有机会。", "frame_repair_intro", "linxia")
    next_frame("frame_signal_cancel", "你停下接线。林夏把电路切到备用线路，留出时间继续修复。", "frame_repair_intro")
    next_frame("frame_signal_error", "接线台暂时失灵。林夏合上盖子，启动备用供电。", "frame_repair_intro")
    next_frame("frame_signal_fallback", "电路箱传来一声短响。林夏检查备用线路，示意你继续。", "frame_repair_intro")
    next_frame("frame_repair_intro", "最后是温控台。选对探头，接好端子，再把读数校准。每完成一道工序，我们都会记下进度。", "frame_repair", "linxia")
    frame("frame_repair", "修复温控台。", {
        "kind": "minigame", "game_id": "prod_checkpoint", "instance_id": "instance_greenhouse_repair",
        "inputs": {"target_temperature": binding("target_temperature")},
        "result_branches": [{"branch_id": "branch_repair_win", "outcome": "win",
            "effects": effects("repair_win", "repair_score", repaired=True), "target_frame_id": "frame_repair_win"}],
        "cancel_target_frame_id": "frame_repair_cancel", "error_target_frame_id": "frame_repair_error",
        "fallback_target_frame_id": "frame_repair_fallback", "save_mode": "checkpoint",
    })
    next_frame("frame_repair_win", "读数稳定下来，温暖的风穿过叶片。林夏笑了：明天，这里会重新开花。", "frame_ending", "linxia")
    next_frame("frame_repair_cancel", "你放下工具。林夏收好检修单，约你明天回来继续。", "frame_ending")
    next_frame("frame_repair_error", "温控台需要更多检修。林夏先把植物搬到暖房，决定明天再试。", "frame_ending")
    next_frame("frame_repair_fallback", "温控台暂时安静下来。林夏记下读数，先让植物休息一晚。", "frame_ending")
    frame("frame_ending", "夜色落在玻璃上。温室尽头的灯，为明天留着。", {"kind": "end"})
    return frames, variables


async def create(args) -> dict:
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    output = args.evidence_root.resolve() / run_id
    output.mkdir(parents=True, exist_ok=False)
    home = args.home.resolve()
    if not home.is_relative_to(ROOT):
        raise ValueError("此制作工具只连接工作区服务，不能指向安装插件或工作区外的工程库。")
    env = dict(os.environ, RENPY_BRIDGE_HOME=str(home), PYTHONUTF8="1", PYTHONPATH=str(ROOT / "src"))
    params = StdioServerParameters(command=sys.executable, args=["-m", "renpy_bridge.server"], cwd=str(ROOT), env=env)
    frames, variables = fixture()
    digests = json.loads((PACKAGES / "package-digests.json").read_text(encoding="utf-8"))
    write_json(output / "request-fixture.json", {"frames": frames, "variables": variables, "packages": digests})
    request_number = 0
    summary = {"run_id": run_id, "evidence_root": str(output), "home": str(home),
               "scope": "real_stdio_mcp_authoring", "gameplay_verified": False, "release_verified": False}
    try:
        with (output / "server.stderr.log").open("x", encoding="utf-8") as log:
            async with stdio_client(params, errlog=log) as (read, write):
                async with ClientSession(read, write) as session:
                    initialized = await session.initialize()
                    write_json(output / "initialize.json", initialized.model_dump(mode="json"))
                    listing = await session.list_tools()
                    write_json(output / "tools.json", listing.model_dump(mode="json"))
                    tools = {tool.name: tool for tool in listing.tools}

                    async def call(name, arguments):
                        nonlocal request_number
                        Draft202012Validator(tools[name].inputSchema).validate(arguments)
                        response = await session.call_tool(name, arguments)
                        request_number += 1
                        write_json(output / f"{request_number:04d}-{name}.json", {
                            "tool": name, "arguments": arguments, "result": response.model_dump(mode="json"),
                            "captured_at": datetime.now(timezone.utc).isoformat(),
                        })
                        if response.isError:
                            raise RuntimeError(f"MCP 拒绝 {name}，见原始调用记录")
                        result = json.loads(next(block.text for block in response.content if block.type == "text"))
                        if result.get("ok") is False:
                            raise RuntimeError(f"{name} 失败: {result.get('error')}")
                        return result

                    await call("get_environment", {"include_runtime": True})
                    await call("get_minigame_capabilities", {})
                    project = await call("create_project", {"name": args.name, "operation_id": "html-demo-create-" + run_id})
                    summary.update(project_id=project["project_id"], project_root=project["path"])
                    project_id = project["project_id"]
                    await call("plan_migration", {"project_id": project_id, "target_schema": "1.1"})
                    migrated = await call("apply_migration", {"project_id": project_id, "expected_revision": project["revision"],
                        "operation_id": "html-demo-migrate-" + run_id, "target_schema": "1.1"})
                    revision = migrated["revision"]
                    candidate = await call("begin_import", {"project_id": project_id, "expected_revision": revision,
                        "operation_id": "html-demo-begin-" + run_id, "frame_ids": [frame["frame_id"] for frame in frames],
                        "game_ids": [digests["packages"][name]["game_id"] for name in ("puzzle", "timed", "checkpoint")],
                        "variable_ids": [variable["variable_id"] for variable in variables]})
                    session_id = candidate["import_session_id"]
                    # Stage forward frame/package references first, then the immutable packages; one head commit at the end.
                    await call("stage_import_batch", {"project_id": project_id, "import_session_id": session_id,
                        "batch_id": "story-with-typed-variables", "frames": frames, "variables": variables,
                        "characters": [{"character_id": "linxia", "name": "林夏", "color": "#b8e5d0"}],
                        "acts": [{"act_id": "act_greenhouse", "name": "温室尽头的灯", "order": 0,
                                  "frame_ids": [frame["frame_id"] for frame in frames]}], "entry_frame_id": "frame_intro"})
                    for name in ("puzzle", "timed", "checkpoint"):
                        await call("stage_minigame_package", {"project_id": project_id, "import_session_id": session_id,
                            "batch_id": "package-" + name, "source_path": str(PACKAGES / name),
                            "expected_digest": digests["packages"][name]["package_hash"],
                            "provenance": {"source_id": "html_demo_story_v1", "source_location": "package:" + name}})
                    committed = await call("commit_import", {"project_id": project_id, "import_session_id": session_id,
                        "expected_revision": revision, "operation_id": "html-demo-commit-" + run_id})
                    summary.update(revision=committed["revision"], frame_count=len(frames), variable_count=len(variables),
                                   packages=digests["packages"], candidate_commits=1)
                    await call("inspect_project", {"project_id": project_id, "section": "full"})
                    await call("create_checkpoint", {"project_id": project_id, "note": "新的HTML小游戏演示；制作完成，真实玩法及独立发行待验证。"})
        summary["ok"] = True
    except Exception as error:
        summary.update(ok=False, error={"type": type(error).__name__, "message": str(error)})
        raise
    finally:
        summary["call_count"] = request_number
        summary["completed_at"] = datetime.now(timezone.utc).isoformat()
        write_json(output / "summary.json", summary)
        write_json(output / "evidence-integrity.json", {"algorithm": "sha256", "files": [
            {"path": file.relative_to(output).as_posix(), "sha256": hash_file(file), "bytes": file.stat().st_size}
            for file in sorted(output.iterdir()) if file.is_file()
        ]})
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, default=ROOT)
    parser.add_argument("--evidence-root", type=Path, default=ROOT / "evidence/html-minigames/formal-demo/runs")
    parser.add_argument("--name", default="温室尽头的灯 HTML小游戏演示")
    asyncio.run(create(parser.parse_args()))


if __name__ == "__main__":
    main()
