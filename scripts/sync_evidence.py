"""Index immutable reports, freeze mutable test output, and check index hashes."""
from pathlib import Path
import json
import shutil
import time
import xml.etree.ElementTree as ET
from renpy_bridge.storage import atomic_json, read_json, file_hash, digest, now

ROOT = Path(__file__).resolve().parents[1]


def main():
    index_path = ROOT / "docs/EVIDENCE.json"
    book = read_json(index_path)
    runs = {record["run_id"]: record for record in book["implementation_runs"]}
    declarations = [
        ("sdk-native", "evidence/m0-sdk/runs/1790890776311064600/report.json", ["M0.2"], "fixed SDK native Chinese fixture"),
        ("generator-native", "evidence/m3-generator/runs/1790892711802021600/report.json", ["M3.1", "M3.2"], "native generator literal text/layers/choices"),
        ("player-native", "evidence/m5-interactions/runs/1790893369055769800/report.json", ["M5.3"], "fixed fixture native save/load/rollback/history/preferences/AFM/skip"),
        ("audio-native", "evidence/m5-audio/runs/1790894031601175800/report.json", ["M3.3", "M5.4"], "native channel behavior and real default-speaker loopback"),
        ("old-save-native", "evidence/m6-save/runs/1790898359988917400/report.json", ["M6.3"], "0.1.3 short filenames; same SDK stable saved complete frame; later content updated"),
        ("install-recovery", "evidence/m7-install-recovery/runs/1790894210481929300/report.json", ["M7.1"], "14 filesystem transaction recovery cases; no host registration"),
        ("scale-public-mcp", "evidence/m9-scale/runs/1790898360326968000/report.json", ["M9.1"], "frozen0.1.3; 1000/5000 public STDIO MCP/native generation/compile/lint/package; package first/last-frame smoke, not full routes"),
        ("codex-author-vertical", "evidence/m3-vertical/codex-author/runs/20261001T215831Z-72f72660/report.json", ["M3.5", "M4.4"], "installed plugin real Codex author; 31 MCP calls; fixed five-frame input"),
        ("demo-workbook", "evidence/m8-demo/workbook/report.json", ["M8.1"], "artifact_tool authored human-readable Excel; three rendered sheets reviewed"),
    ]
    xml = ROOT / "evidence/m2-core/all-tests.xml"
    parsed = ET.parse(xml).getroot()
    cases = parsed.findall(".//testcase")
    if not cases or any(case.find("failure") is not None or case.find("error") is not None for case in cases):
        raise RuntimeError("Only an actually passing complete JUnit run can be indexed as passed")
    skipped = sum(case.find("skipped") is not None for case in cases)
    frozen = ROOT / "evidence/m2-core/runs" / str(time.time_ns()) / "tests.xml"
    frozen.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(xml, frozen)
    declarations.append(("core-business-current", frozen.relative_to(ROOT).as_posix(), ["M1.1", "M2.1", "M2.3", "M6.1", "M6.2"], f"{len(cases)-skipped} business/media/recovery/path tests passed, {skipped} skipped; mock SDK process fixtures not native evidence"))
    snapshot = digest({p.relative_to(ROOT).as_posix(): file_hash(p) for p in sorted((ROOT / "src").rglob("*.py"))})
    for run_id, relative, tasks, scope in declarations:
        path = ROOT / relative
        if not path.is_file():
            raise RuntimeError(f"Missing real report: {relative}")
        runs[run_id] = {"run_id": run_id, "task_ids": tasks, "result_status": "passed", "scope": scope,
                        "report_path": relative, "report_sha256": file_hash(path), "indexed_at": now(),
                        "source_digest_at_indexing": snapshot,
                        "source_digest_scope": "indexing snapshot only; actual run source/version records are authoritative"}
    # Superseded mutable aliases are retired, not overwritten as fresh native
    # evidence. Every retained record's referenced bytes must still match.
    invalid = []
    for run_id, record in list(runs.items()):
        path = ROOT / record["report_path"]
        if not path.is_file() or file_hash(path) != record["report_sha256"]:
            invalid.append({"run_id": run_id, "report_path": record["report_path"], "reason": "mutable alias changed or disappeared"})
            del runs[run_id]
    book.setdefault("retired_index_records", []).extend(invalid)
    book.update(implementation_runs=list(runs.values()), stage="implementation", updated_at=now())
    atomic_json(index_path, book)
    print(json.dumps({"indexed_reports": len(runs), "retired_mutable_aliases": len(invalid), "frozen_tests": str(frozen)}, ensure_ascii=True))


if __name__ == "__main__":
    main()
