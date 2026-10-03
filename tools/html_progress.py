"""Update the independent HTML implementation ledger with evidence."""
import argparse
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "docs/HTML_MINIGAME_TASKS.json"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("task_id")
    p.add_argument("status", choices=["todo", "doing", "blocked", "done"])
    p.add_argument("--evidence", action="append", default=[])
    p.add_argument("--note", default="")
    args = p.parse_args()
    data = json.loads(LEDGER.read_text(encoding="utf-8-sig"))
    task = next(t for t in data["tasks"] if t["id"] == args.task_id)
    by_id = {t["id"]: t for t in data["tasks"]}
    if args.status == "done":
        if any(by_id[d]["status"] != "done" for d in task["depends_on"]):
            raise SystemExit("Dependencies must be done before accepting this task.")
        if not args.evidence:
            raise SystemExit("Completion requires an evidence path.")
    for value in args.evidence:
        resolved = (ROOT / value).resolve()
        if not resolved.is_relative_to(ROOT.resolve()) or not resolved.is_file():
            raise SystemExit("Evidence must be an existing workspace file.")
    task.setdefault("history", []).append({
        "at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
        "previous_status": task["status"], "status": args.status,
        "note": args.note, "evidence": args.evidence,
    })
    task["status"] = args.status
    task["evidence"] = list(dict.fromkeys(task["evidence"] + args.evidence))
    data["status"] = "active"
    data["plan_version"] = "1.0"
    data["authorized_by"] = "用户在当前会话明确指令：执行"
    data["updated_at"] = datetime.now(timezone(timedelta(hours=8))).isoformat()
    temp = LEDGER.with_suffix(".json.tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(LEDGER)
    print(json.dumps({"task_id": args.task_id, "status": args.status,
                      "counts": {s: sum(t["status"] == s for t in data["tasks"])
                                 for s in ["todo", "doing", "blocked", "done"]}}, ensure_ascii=False))


if __name__ == "__main__":
    main()

