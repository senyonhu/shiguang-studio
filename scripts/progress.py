"""Maintain the implementation ledger without inventing verification results."""
import argparse
import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("task")
    parser.add_argument("status", choices=["todo", "doing", "blocked", "done"])
    parser.add_argument("--evidence", action="append", default=[])
    parser.add_argument("--note", default="")
    args = parser.parse_args()
    path = ROOT / "docs/TASKS.json"
    book = json.loads(path.read_text(encoding="utf-8"))
    task = next(t for t in book["tasks"] if t["id"] == args.task)
    if args.status == "done" and not args.evidence:
        parser.error("done requires evidence")
    for reference in args.evidence:
        if not (ROOT / reference).exists():
            parser.error(f"missing evidence: {reference}")
    task.update(status=args.status, evidence=args.evidence, note=args.note)
    book.update(phase="implementation", implementation_started=True, updated_at=datetime.now().astimezone().isoformat())
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(book, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


if __name__ == "__main__":
    main()
