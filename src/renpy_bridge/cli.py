"""Narrow structured-JSON CLI over the same bridge core used by MCP."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from .environment import get_environment_info
from .errors import BridgeError
from .jobs import JobManager
from .storage import Store


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise BridgeError("CLI_USAGE", message)


def nonnegative(value: str) -> int:
    try:
        integer = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("Expected an integer.") from error
    if integer < 0:
        raise argparse.ArgumentTypeError("Expected a nonnegative integer.")
    return integer


def positive(value: str) -> int:
    integer = nonnegative(value)
    if integer == 0:
        raise argparse.ArgumentTypeError("Expected a positive integer.")
    return integer


def parser() -> JsonArgumentParser:
    result = JsonArgumentParser(description=__doc__)
    result.add_argument("--home", type=Path, help="Managed bridge home; does not change user configuration.")
    commands = result.add_subparsers(dest="command", required=True)

    environment = commands.add_parser("environment", help="Read bridge environment and SDK capability information.")
    environment.add_argument("--no-runtime", action="store_true", help="Skip active SDK capability probing.")

    create = commands.add_parser("create", help="Create a project with explicit idempotency identity.")
    create.add_argument("name")
    create.add_argument("--operation-id", required=True)
    create.add_argument("--width", type=positive, default=1280)
    create.add_argument("--height", type=positive, default=720)

    inspect = commands.add_parser("inspect", help="List projects or inspect a project section/object.")
    inspect.add_argument("project_id", nargs="?")
    inspect.add_argument(
        "--section",
        choices=("summary", "frames", "acts", "characters", "assets", "full"),
        default="summary",
    )
    inspect.add_argument("--object-id")
    inspect.add_argument("--offset", type=nonnegative, default=0)
    inspect.add_argument("--limit", type=positive, default=50)

    extract = commands.add_parser("extract", help="Extract and retain source records with provenance.")
    extract.add_argument("project_id")
    extract.add_argument("source_path", type=Path)
    extract.add_argument("--source-id")
    extract.add_argument("--offset", type=nonnegative, default=0)
    extract.add_argument("--limit", type=positive, default=200)

    run = commands.add_parser("run", help="Start a fixed-kind asynchronous engine job.")
    run.add_argument("kind", choices=("check", "preview", "routes", "build"))
    run.add_argument("project_id")
    run.add_argument("--revision", type=nonnegative, required=True)
    run.add_argument("--frame-id", action="append", dest="frame_ids", help="Preview target; repeat for multiple frames.")
    run.add_argument("--max-routes", type=positive, help="Routes job bound; defaults to 256.")
    status = commands.add_parser("check-job", help="Read job state and report/log paths.")
    status.add_argument("job_id")
    cancel = commands.add_parser("cancel", help="Cancel an ownership-verified managed job.")
    cancel.add_argument("job_id")
    return result


def dispatch(args) -> dict:
    home = args.home.expanduser().resolve() if args.home is not None else None
    if home is not None:
        # This invocation only. Worker children need the same managed home.
        os.environ["RENPY_BRIDGE_HOME"] = str(home)
    if args.command == "environment":
        return get_environment_info(include_runtime=not args.no_runtime)
    store = Store(home)
    if args.command == "create":
        return store.create(args.name, args.operation_id, args.width, args.height)
    if args.command == "inspect":
        if args.project_id is None:
            if args.section != "summary" or args.object_id or args.offset or args.limit != 50:
                raise BridgeError("CLI_USAGE", "Section, object and pagination options require a project_id.")
            return store.list_projects()
        return store.inspect(args.project_id, args.section, args.offset, args.limit, args.object_id)
    if args.command == "extract":
        return store.extract_source(
            args.project_id, str(args.source_path), args.source_id, args.offset, args.limit
        )
    jobs = JobManager(store)
    if args.command == "run":
        if args.frame_ids and args.kind != "preview":
            raise BridgeError("CLI_USAGE", "--frame-id is supported only for preview jobs.")
        if args.max_routes is not None and args.kind != "routes":
            raise BridgeError("CLI_USAGE", "--max-routes is supported only for routes jobs.")
        if args.kind == "preview":
            if not args.frame_ids:
                raise BridgeError("CLI_USAGE", "Preview jobs require at least one --frame-id.")
            options = {"frame_ids": list(dict.fromkeys(args.frame_ids))}
        elif args.kind == "routes":
            options = {"max_routes": args.max_routes if args.max_routes is not None else 256}
        else:
            options = {}
        return jobs.start(args.kind, args.project_id, args.revision, options)
    if args.command == "check-job":
        return jobs.get(args.job_id)
    if args.command == "cancel":
        return jobs.cancel(args.job_id)
    raise BridgeError("CLI_USAGE", "Unsupported command.")


def main(argv: list[str] | None = None) -> int:
    previous_home = os.environ.get("RENPY_BRIDGE_HOME")
    try:
        args = parser().parse_args(argv)
        result = dispatch(args)
        payload = result if "ok" in result else {"ok": True, **result}
        print(json.dumps(payload, ensure_ascii=False, allow_nan=False, indent=2))
        return 0 if payload.get("ok") is not False else 2
    except BridgeError as error:
        print(json.dumps(error.as_dict(), ensure_ascii=False, indent=2))
        return 2
    except KeyboardInterrupt:
        print(json.dumps({
            "ok": False, "error": {
                "code": "CLI_INTERRUPTED", "message": "CLI interrupted.", "details": {},
            }
        }))
        return 130
    except Exception as error:
        print(json.dumps({
            "ok": False, "error": {
                "code": "CLI_ERROR", "message": f"{type(error).__name__}: {error}", "details": {},
            }
        }, ensure_ascii=False, indent=2))
        return 2
    finally:
        if previous_home is None:
            os.environ.pop("RENPY_BRIDGE_HOME", None)
        else:
            os.environ["RENPY_BRIDGE_HOME"] = previous_home


if __name__ == "__main__":
    raise SystemExit(main())

