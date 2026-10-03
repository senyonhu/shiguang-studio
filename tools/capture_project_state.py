"""Read-only preservation inventory around a personal runtime upgrade."""
import argparse
import json
from pathlib import Path
from renpy_bridge.storage import file_hash


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--compare", type=Path)
    args = parser.parse_args()
    project = args.project.resolve()
    files = {path.relative_to(project).as_posix(): file_hash(path)
             for name in ("model", "assets", "source_inputs", "history", "checkpoints", "imports")
             for path in sorted((project / name).rglob("*")) if path.is_file() and path.suffix != ".lock"}
    report = {"project_path": str(project), "files": files, "project_modified": False}
    if args.compare:
        previous = json.loads(args.compare.read_text(encoding="utf-8"))
        report.update(unchanged=previous["files"] == files,
                      changed_paths=sorted(k for k in set(previous["files"]) | set(files)
                                           if previous["files"].get(k) != files.get(k)))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"files": len(files), "unchanged": report.get("unchanged"), "report": str(args.output)}, ensure_ascii=True))
    return int(report.get("unchanged") is False)


if __name__ == "__main__":
    raise SystemExit(main())
