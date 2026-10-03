"""Real fresh STDIO first-XLSX calls, without inherited module state or warmups."""
import asyncio
import builtins
import hashlib
import json
import os
from pathlib import Path
import sys
import time

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import pytest


@pytest.mark.parametrize("first_limit", [20, 1000])
def test_fresh_public_stdio_first_xlsx_returns_before_deadline(tmp_path, first_limit):
    from openpyxl import Workbook

    path = tmp_path / "冷启动 中文.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "自然资料"
    for i in range(263):
        sheet.append([f"原始第{i + 1}条，保留 {{字面}} [文本]"])
    workbook.save(path)
    workbook.close()
    expected_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, RENPY_BRIDGE_HOME=str(tmp_path / "isolated-home"),
               PYTHONPATH=str(root / "src"), PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1")
    params = StdioServerParameters(command=sys.executable, args=["-B", "-m", "renpy_bridge.server"],
                                  cwd=str(tmp_path), env=env)

    def payload(result):
        assert not result.isError
        return result.structuredContent or json.loads(next(c.text for c in result.content if hasattr(c, "text")))

    async def scenario():
        with (tmp_path / "server.stderr.log").open("w", encoding="utf-8") as log:
            async with stdio_client(params, errlog=log) as (read, write):
                async with ClientSession(read, write) as session:
                    await asyncio.wait_for(session.initialize(), timeout=12)
                    created = payload(await session.call_tool("create_project", {"name": "冷首次提取", "operation_id": "create"}))
                    project_id = created["project_id"]
                    started = time.monotonic()
                    # This is the first extraction in a new interpreter. A
                    # small reply also exercises the formerly blocked import.
                    result = await asyncio.wait_for(session.call_tool("extract_source", {
                        "project_id": project_id, "source_path": str(path),
                        "source_id": "source_fixture", "offset": 0, "limit": first_limit,
                    }), timeout=12)
                    first = payload(result)
                    assert time.monotonic() - started < 12
                    assert first["ok"] and first["hash"] == expected_hash
                    assert first["total_records"] == 263
                    assert len(first["records"]) == min(first_limit, 263)
                    assert first["records"][0]["value"] == "原始第1条，保留 {字面} [文本]"
                    # Both reply sizes work in the same process after its
                    # first call; this is extraction, not a content commit.
                    second = payload(await asyncio.wait_for(session.call_tool("extract_source", {
                        "project_id": project_id, "source_path": str(path),
                        "source_id": "source_fixture", "offset": 0,
                        "limit": 1000 if first_limit == 20 else 20,
                    }), timeout=12))
                    assert second["hash"] == expected_hash and second["total_records"] == 263
                    summary = payload(await session.call_tool("inspect_project", {"project_id": project_id}))
                    assert summary["revision"] == 0 and summary["counts"]["frames"] == 0
                    originals = list((tmp_path / "isolated-home/projects" / project_id / "source_inputs").glob("*.xlsx"))
                    assert len(originals) == 1 and hashlib.sha256(originals[0].read_bytes()).hexdigest() == expected_hash
                    assert not originals[0].name.startswith(".snapshot-")

    asyncio.run(scenario())


def test_missing_xlsx_dependency_is_logged_and_keeps_extractor_error(tmp_path, monkeypatch, caplog):
    from renpy_bridge import server
    from renpy_bridge.sources import SourceError, extract_file

    real_import = builtins.__import__

    def unavailable(name, *args, **kwargs):
        if name == "openpyxl":
            raise ImportError("controlled missing XLSX dependency")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", unavailable)
    transports = []
    monkeypatch.setattr(server.mcp, "run", lambda **kwargs: transports.append(kwargs))
    server.main()
    assert transports == [{"transport": "stdio"}]
    assert "EXTRACTOR_UNAVAILABLE" in caplog.text and "controlled missing XLSX dependency" in caplog.text
    path = tmp_path / "unavailable.xlsx"
    path.write_bytes(b"placeholder; import fails before workbook parsing")
    with pytest.raises(SourceError) as error:
        extract_file(path)
    assert error.value.code == "EXTRACTOR_UNAVAILABLE"
    text = tmp_path / "still-supported.txt"
    text.write_text("其他工具和格式仍可读。", encoding="utf-8")
    assert extract_file(text)["records"][0]["text"] == "其他工具和格式仍可读。"
