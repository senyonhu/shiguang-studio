"""Protocol test; does not pretend to be a real Codex client test."""
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]


async def main():
    report = {"run_id": "m0-mcp-protocol", "started_at": datetime.now().astimezone().isoformat(),
              "scope": "independent_mcp_client", "is_codex_client_evidence": False}
    env = dict(os.environ, RENPY_BRIDGE_HOME=str(ROOT), PYTHONUTF8="1")
    params = StdioServerParameters(command=sys.executable, args=["-m", "renpy_bridge.server"], env=env, cwd=str(ROOT))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            report["initialize"] = (await session.initialize()).model_dump(mode="json")
            listing = await session.list_tools()
            report["tools"] = listing.model_dump(mode="json")
            result = await session.call_tool("get_environment", {})
            report["call"] = result.model_dump(mode="json")
            invalid = await session.call_tool("get_environment", {"include_runtime": {"invalid": True}})
            report["invalid_call"] = invalid.model_dump(mode="json")
            report["status"] = "passed" if not result.isError and invalid.isError else "failed"
    out = ROOT / "evidence/m0-mcp"
    out.mkdir(parents=True, exist_ok=True)
    (out / "protocol.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": report["status"], "report": str(out / "protocol.json")}, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
