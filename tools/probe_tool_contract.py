"""Snapshot/validate documentation through public MCP Tool schemas and calls."""
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

ROOT = Path(__file__).resolve().parents[1]


def text_object(result):
    if result.isError:
        raise AssertionError(result.model_dump(mode="json"))
    return json.loads(next(item.text for item in result.content if item.type == "text"))


async def probe(job_ids):
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    output = ROOT / "evidence/m1-tools/runs" / run_id
    output.mkdir(parents=True)
    params = StdioServerParameters(command=sys.executable, args=["-m", "renpy_bridge.server"],
                                  env=dict(os.environ, RENPY_BRIDGE_HOME=str(ROOT), PYTHONUTF8="1"), cwd=str(ROOT))
    report = {"scope": "public_mcp_tool_contract", "is_codex_client_evidence": False, "run_id": run_id,
              "example_validation": [], "job_report_validation": []}
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            listing = await session.list_tools()
            tools = {tool.name: tool for tool in listing.tools}
            environment = text_object(await session.call_tool("get_environment", {"include_runtime": False}))
            snapshot = {"scope": "public_mcp_list_tools_snapshot", "captured_at": datetime.now(timezone.utc).isoformat(),
                        "plugin_version": environment["version"], "schema_version": environment["schema_version"],
                        "tools": [tools[name].model_dump(mode="json", exclude_none=True) for name in sorted(tools)]}
            (ROOT / "docs/tools.schema.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
            for example in json.loads((ROOT / "docs/tool-examples.json").read_text(encoding="utf-8")):
                schema = tools[example["tool"]].inputSchema
                Draft202012Validator(schema).validate(example["arguments"])
                report["example_validation"].append({"tool": example["tool"], "status": "passed"})
            invalid = await session.call_tool("get_job", {"job_id": "job_0123456789abcdef0123456789abcdef", "detail": "invalid"})
            assert invalid.isError, "Public MCP schema must reject an unsupported report detail"
            report["invalid_detail_rejected"] = True
            for job_id in job_ids:
                status = text_object(await session.call_tool("get_job", {"job_id": job_id}))
                compact = text_object(await session.call_tool("get_job", {"job_id": job_id, "include_report": True}))
                full = text_object(await session.call_tool("get_job", {"job_id": job_id, "include_report": True, "detail": "full"}))
                assert "report" not in status
                assert compact["state"] == full["state"] == "succeeded"
                assert compact["revision"] == full["revision"]
                assert compact["report"]["screenshots"] == full["report"]["screenshots"]
                for name, command in compact["report"]["commands"].items():
                    assert "stdout" not in command and "stderr" not in command
                    assert command["exit_code"] == full["report"]["commands"][name]["exit_code"]
                assert full["report"]["commands"]["native-tests"]["stdout"]
                if "route_coverage" in compact["report"]:
                    assert "routes" not in compact["report"]["route_coverage"]
                    assert compact["report"]["route_coverage"]["edges"] == full["report"]["route_coverage"]["edges"]
                compact_size = len(json.dumps(compact, ensure_ascii=False).encode("utf-8"))
                full_size = len(json.dumps(full, ensure_ascii=False).encode("utf-8"))
                assert compact_size < full_size
                report["job_report_validation"].append({"job_id": job_id, "revision": full["revision"],
                                                        "compact_bytes": compact_size, "full_bytes": full_size, "status": "passed"})
    report["status"] = "passed"
    (output / "contract.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": report["status"], "report": str(output / "contract.json"),
                      "tool_count": len(tools), "example_count": len(report["example_validation"]),
                      "job_report_validation": report["job_report_validation"]}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-id", action="append", default=[])
    args = parser.parse_args()
    asyncio.run(probe(args.job_id))


if __name__ == "__main__":
    main()
