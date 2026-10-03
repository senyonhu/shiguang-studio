"""Exercise real managed workers with fixed native fixtures, not final demo."""
import argparse
import json
from pathlib import Path
import time

from renpy_bridge.storage import Store, read_json, atomic_json
from renpy_bridge.jobs import JobManager, TERMINAL

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--kind", choices=["check", "preview", "routes", "build"], default="routes")
    args = parser.parse_args()
    run_id = str(time.time_ns())
    evidence = ROOT / "evidence/m3-pipeline" / run_id
    evidence.mkdir(parents=True)
    store = Store(ROOT)
    pid = store.create("原生接口纵向切片", "pipeline-" + run_id)["project_id"]
    speech = read_json(ROOT / "evidence/m0-audio/report.json")
    store.register_assets(pid, 0, "assets", [
        {"asset_id": "background", "kind": "image", "source_path": str(ROOT / "runtimes/renpy-8.5.3/launcher/game/gui7/icon.png")},
        {"asset_id": "voice", "kind": "audio", "source_path": speech["tts"]["output_path"]},
    ])
    frames = [
        {"frame_id": "opening", "act_id": "act_one", "bg": {"component_id": "back", "kind": "background", "asset_id": "background"}, "dialogue": {"speaker_id": "hero", "text": speech["text"]}, "voice": "voice", "flow": {"kind": "next", "target_frame_id": "choice"}},
        {"frame_id": "choice", "act_id": "act_one", "dialogue": {"speaker_id": "hero", "text": "你想走哪条路？"}, "flow": {"kind": "choices", "choices": [{"choice_id": "a", "text": "沿着河边", "target_frame_id": "river"}, {"choice_id": "b", "text": "回到教室", "target_frame_id": "classroom"}]}},
        {"frame_id": "river", "act_id": "act_one", "characters": [{"component_id": "actor", "kind": "character", "character_id": "hero", "asset_id": "background"}], "dialogue": {"text": "河边的风很温柔。"}, "flow": {"kind": "next", "target_frame_id": "ending"}},
        {"frame_id": "classroom", "act_id": "act_one", "decorations": [{"component_id": "title", "kind": "text", "text": "教室"}], "dialogue": {"text": "夕阳照进教室。"}, "flow": {"kind": "next", "target_frame_id": "ending"}},
        {"frame_id": "ending", "act_id": "act_one", "dialogue": {"text": "两条路在这里汇合。"}, "flow": {"kind": "end"}},
    ]
    store.apply_batch(pid, 1, "frames", frames, [{"act_id": "act_one"}], [{"character_id": "hero", "name": "小夏"}], "opening")
    options = {"frame_ids": ["opening", "choice", "ending"]} if args.kind == "preview" else {}
    manager = JobManager(store)
    job = manager.start(args.kind, pid, 2, options)
    atomic_json(evidence / "launched.json", job)
    print(json.dumps({"project_id": pid, "job_id": job["job_id"], "kind": args.kind}, ensure_ascii=False), flush=True)
    while job["state"] not in TERMINAL:
        time.sleep(1)
        job = manager.get(job["job_id"])
    report = read_json(Path(job["report_path"])) if Path(job["report_path"]).is_file() else None
    record = {"run_id": run_id, "scope": "managed_native_fixture", "formal_demo": False, "job": job, "report": report}
    atomic_json(evidence / "report.json", record)
    atomic_json(ROOT / "evidence/m3-pipeline" / (args.kind + "-latest.json"), record)
    print(json.dumps({"state": job["state"], "error": job.get("error"), "report_path": str(evidence / "report.json")}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
