"""Maintain implementation evidence index with source snapshots."""
from pathlib import Path
import argparse
from renpy_bridge.storage import atomic_json, read_json, file_hash, digest, now

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run_id")
    parser.add_argument("report")
    parser.add_argument("--task", action="append", default=[])
    parser.add_argument("--status", choices=["passed", "failed", "blocked", "not_run"], required=True)
    parser.add_argument("--scope", required=True)
    args = parser.parse_args()
    path = ROOT / args.report
    if not path.is_file():
        parser.error("report missing")
    index_path = ROOT / "docs/EVIDENCE.json"
    book = read_json(index_path)
    record = {"run_id": args.run_id, "task_ids": args.task, "result_status": args.status, "scope": args.scope,
              "report_path": args.report, "report_sha256": file_hash(path), "indexed_at": now(),
              "source_digest_at_indexing": digest({p.relative_to(ROOT).as_posix(): file_hash(p) for p in sorted((ROOT / "src").rglob("*.py"))}),
              "source_digest_scope": "current snapshot; actual run versions/process details remain authoritative in referenced report"}
    book["implementation_runs"] = [run for run in book["implementation_runs"] if run["run_id"] != args.run_id] + [record]
    book.update(stage="implementation", updated_at=now())
    atomic_json(index_path, book)


if __name__ == "__main__":
    main()
