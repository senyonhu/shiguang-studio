"""Upgrade an existing HTML demo's three packages through one real MCP candidate.

The revision and every call are recorded in a new evidence run. No gameplay or
release results are fabricated, and historical immutable packages are retained.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import uuid

from jsonschema import Draft202012Validator
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from create_html_minigame_demo import ROOT, write_json, hash_file
from renpy_bridge.minigame_packages import validate_directory

PACKAGES = ROOT / "examples/html_minigames/production/v1.0.1"


async def upgrade(args) -> dict:
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    output = args.evidence_root.resolve() / run_id
    output.mkdir(parents=True, exist_ok=False)
    home = args.home.resolve()
    if not home.is_relative_to(ROOT):
        raise ValueError("Only the workspace service is authorized by this tool")
    inventory = json.loads((PACKAGES / "package-digests.json").read_text(encoding="utf-8"))
    for name, item in inventory["packages"].items():
        info = validate_directory(PACKAGES / name)
        if info.digest != item["package_hash"] or info.manifest["version"] != "1.0.1":
            raise ValueError("New package does not match the frozen v1.0.1 inventory")
    params = StdioServerParameters(command=sys.executable, args=["-m", "renpy_bridge.server"], cwd=str(ROOT),
        env=dict(os.environ, RENPY_BRIDGE_HOME=str(home), PYTHONUTF8="1", PYTHONPATH=str(ROOT / "src")))
    summary = {"run_id": run_id, "scope": "real_stdio_mcp_atomic_package_upgrade", "evidence_root": str(output),
        "project_id": args.project_id, "expected_revision": args.expected_revision,
        "gameplay_verified": False, "window_matrix_verified": False, "release_verified": False}
    requests = 0
    try:
        with (output / "server.stderr.log").open("x", encoding="utf-8") as log:
            async with stdio_client(params, errlog=log) as (read, write):
                async with ClientSession(read, write) as session:
                    write_json(output / "initialize.json", (await session.initialize()).model_dump(mode="json"))
                    listing = await session.list_tools()
                    write_json(output / "tools.json", listing.model_dump(mode="json"))
                    tools = {tool.name: tool for tool in listing.tools}

                    async def call(name, arguments):
                        nonlocal requests
                        Draft202012Validator(tools[name].inputSchema).validate(arguments)
                        response = await session.call_tool(name, arguments)
                        requests += 1
                        write_json(output / f"{requests:04d}-{name}.json", {"tool": name, "arguments": arguments,
                            "result": response.model_dump(mode="json"), "captured_at": datetime.now(timezone.utc).isoformat()})
                        if response.isError:
                            raise RuntimeError("MCP rejected " + name)
                        result = json.loads(next(block.text for block in response.content if block.type == "text"))
                        if result.get("ok") is False:
                            raise RuntimeError(f"{name}: {result.get('error')}")
                        return result

                    before = (await call("inspect_project", {"project_id": args.project_id, "section": "full"}))["project"]
                    if before["revision"] != args.expected_revision:
                        raise ValueError("Revision changed before upgrade")
                    old_packages = {}
                    for game_id, game in before["minigames"].items():
                        info = validate_directory(home / "projects" / args.project_id / game["path"])
                        if info.digest != game["package_digest"]:
                            raise ValueError("Old immutable package changed before upgrade")
                        old_packages[game_id] = {"path": str(info.root), "digest": info.digest}
                    candidate = await call("begin_import", {"project_id": args.project_id, "frame_ids": [],
                        "game_ids": [value["game_id"] for value in inventory["packages"].values()], "variable_ids": [],
                        "expected_revision": args.expected_revision, "operation_id": "html-audio-begin-" + run_id})
                    session_id = candidate["import_session_id"]
                    for name, item in inventory["packages"].items():
                        await call("stage_minigame_package", {"project_id": args.project_id,
                            "import_session_id": session_id, "batch_id": "audio-upgrade-" + name,
                            "source_path": str(PACKAGES / name), "expected_digest": item["package_hash"],
                            "provenance": before["minigames"][item["game_id"]]["provenance"]})
                    committed = await call("commit_import", {"project_id": args.project_id, "import_session_id": session_id,
                        "expected_revision": args.expected_revision, "operation_id": "html-audio-commit-" + run_id})
                    after = (await call("inspect_project", {"project_id": args.project_id, "section": "full"}))["project"]
                    for field in ("frames", "variables", "acts", "characters", "assets", "entry_frame_id", "save_namespace", "name"):
                        if before[field] != after[field]:
                            raise ValueError("Package upgrade unexpectedly changed " + field)
                    for game_id, item in old_packages.items():
                        if validate_directory(Path(item["path"])).digest != item["digest"]:
                            raise ValueError("Historical package bytes changed during upgrade")
                    summary.update(ok=True, revision=committed["revision"], project_root=str(home / "projects" / args.project_id),
                        candidate_commits=1, packages=inventory["packages"], historical_packages=old_packages,
                        frames_and_variables_unchanged=True, historical_packages_unchanged=True)
                    await call("create_checkpoint", {"project_id": args.project_id,
                        "note": "小游戏1.0.1动态音量设置升级；只更新三个包，保留旧版本。真实同窗验收待运行。"})
    except Exception as error:
        summary.update(ok=False, error={"type": type(error).__name__, "message": str(error)})
        raise
    finally:
        summary.update(call_count=requests, completed_at=datetime.now(timezone.utc).isoformat())
        write_json(output / "summary.json", summary)
        write_json(output / "evidence-integrity.json", {"algorithm": "sha256", "files": [
            {"path": file.relative_to(output).as_posix(), "sha256": hash_file(file), "bytes": file.stat().st_size}
            for file in sorted(output.iterdir()) if file.is_file()]})
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, default=ROOT)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--expected-revision", type=int, required=True)
    parser.add_argument("--evidence-root", type=Path, default=ROOT / "evidence/html-minigames/formal-upgrade/runs")
    asyncio.run(upgrade(parser.parse_args()))
