"""Real filesystem lifecycle tests; no SDK or fake binary is executed here."""
from dataclasses import asdict
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import textwrap
from types import SimpleNamespace
import zipfile

import pytest

from renpy_bridge.errors import BridgeError
from renpy_bridge import pipeline
from renpy_bridge.sdk import ProcessResult
from renpy_bridge.storage import digest, file_hash
from renpy_bridge.derived_integrity import record_derived_sources, verify_derived_sources


def put(path, content=b"exact fixture bytes"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def result(argv, cwd, ok=True):
    return ProcessResult(list(map(str, argv)), str(cwd), 0 if ok else 1,
                         "[rpytest] Status: PASSED" if ok else "fixture failure",
                         "", 0.01, False, 0, None)


@pytest.fixture
def build(tmp_path, monkeypatch):
    # Reproduce a managed source path over MAX_PATH, including retained assets.
    source = tmp_path / ("deep-managed-" + "x" * 55) / ("revision-" + "y" * 50) / "job_fixture"
    retained = "1359bd610acf4b25c2bfa0f52820c3e462f5021e389d0fa7b9893a08962b0f19.wav"
    put(source / "game/bridge_assets" / retained)
    put(source / "game/bridge_assets/1359bd610acf4b25c2bfa0f52.wav")
    put(source / "game/bridge_story.rpy", b"# fixed controlled fixture\n")
    put(source / "game/bridge_testcases.rpy", b"# fixed test suite\n")
    put(source / "old-game/bridge_story.rpyc", b"immutable previous compiler identity")
    (source / "empty-directory").mkdir()
    record_derived_sources(source)
    release = tmp_path / "managed-publication" / "job_fixture"
    evidence = tmp_path / "jobs/job_fixture"
    evidence.mkdir(parents=True)
    report = {"ok": False, "status": "running", "commands": {},
              "interaction_verification": {"status": "passed"}}
    calls, stages = [], []

    def sdk(sdk, project, command, args, **kwargs):
        assert project == "launcher" and command == "distribute"
        stage = Path(args[0])
        output = Path(args[args.index("--destination") + 1])
        assert stage != source and stage.is_dir()
        assert pipeline._tree_manifest(stage) == pipeline._tree_manifest(source)
        assert (stage / "empty-directory").is_dir()
        assert max(pipeline._path_units(path.resolve()) for path in stage.rglob("*")) < 260
        package = output / "fixture-1.0-pc.zip"
        with zipfile.ZipFile(package, "x") as archive:
            archive.writestr("fixture-1.0-pc/fixture.exe", b"not executable; fixture bytes only")
            for filename in ["game/bridge_story.rpy", "game/bridge_assets/" + retained,
                             "game/bridge_assets/1359bd610acf4b25c2bfa0f52.wav", "old-game/bridge_story.rpyc"]:
                archive.writestr("fixture-1.0-pc/" + filename, (stage / filename).read_bytes())
        stages.append(stage.parent)
        calls.append((command, list(args), kwargs))
        return result(["SDK", "launcher", command, *args], stage.parent)

    def package(argv, cwd, timeout, env):
        assert Path(argv[0]).is_file() and Path(argv[0]).parent == cwd
        assert pipeline._path_units(Path(argv[0]).resolve()) < 260
        assert Path(argv[argv.index("--savedir") + 1]).parent == stages[0]
        assert Path(env["RENPY_MULTIPERSISTENT"]).parent == stages[0]
        put(cwd / "log.txt", b"actual test fixture log")
        put(cwd / "tests/screenshots/route_fixture.png", b"screenshot fixture only")
        calls.append(("package-tests", list(argv), {"cwd": str(cwd)}))
        return result(argv, cwd)

    monkeypatch.setattr(pipeline, "run_sdk", sdk)
    monkeypatch.setattr(pipeline, "_execute", package)

    def record(name, process, engine_root):
        report["commands"][name] = asdict(process)
        return process.exit_code == 0

    return SimpleNamespace(source=source, release=release, evidence=evidence, report=report,
                           calls=calls, stages=stages, sdk=sdk, package=package,
                           record=record, retained=retained, tmp_path=tmp_path)


def run(f):
    return pipeline._build_release(f.source, f.release, {"build_name": "fixture"},
                                   object(), 120, f.record, f.report, f.evidence)


def test_deep_source_short_execution_and_exact_published_tree(build):
    f = build
    assert pipeline._path_units(f.source / "game/bridge_assets" / f.retained) >= 260
    before = pipeline._tree_manifest(f.source)
    assert run(f) is True
    after = pipeline._tree_manifest(f.source)
    assert all(after[key] == value for key, value in before.items()
               if not key.startswith(".bridge-derived-integrity/"))
    assert verify_derived_sources(f.source, f.evidence / "post-build-integrity")["status"] == "passed"
    assert len(f.calls) == 2
    assert not f.stages[0].exists()
    execution, release = f.report["execution"], f.report["release"]
    assert execution["actually_tested_executable"] != release["executable"]
    assert execution["published_executable"] == release["executable"]
    assert execution["published_directory"] == release["independent_directory"]
    assert execution["actually_tested_executable_sha256"] == execution["published_executable_sha256"] == file_hash(Path(release["executable"]))
    assert f.report["commands"]["package-tests"]["argv"][0] == execution["actually_tested_executable"]
    assert f.report["commands"]["package-tests"]["cwd"] == execution["actually_tested_directory"]
    assert execution["tested_tree_sha256"] == execution["published_tree_sha256"]
    assert execution["tested_and_published_identical"] and execution["package_members_unchanged"]
    manifests = {}
    for field in ["package_members_manifest", "tested_tree_manifest", "published_tree_manifest"]:
        reference = execution[field]
        path = Path(reference["path"])
        assert file_hash(path) == reference["sha256"]
        manifest = json.loads(path.read_text(encoding="utf-8"))
        assert digest(manifest["files"]) == reference["tree_sha256"] == manifest["tree_sha256"]
        manifests[field] = manifest["files"]
    assert manifests["tested_tree_manifest"] == manifests["published_tree_manifest"]
    published = f.release / "independent"
    assert manifests["published_tree_manifest"] == pipeline._tree_manifest(published)
    assert all(manifests["tested_tree_manifest"][key] == item for key, item in manifests["package_members_manifest"].items())
    assert any(key.endswith("old-game/bridge_story.rpyc") for key in manifests["package_members_manifest"])
    assert any(key.endswith(f.retained) for key in manifests["package_members_manifest"])
    assert f.report["package_screenshots"][0]["path"].startswith(str(published))
    assert f.report["release_staging"]["cleanup"] == {"status": "removed", "directory_exists": False, "ownership_verified": True}
    assert f.report["interaction_verification"]["independent_package_status"] == "passed"
    assert file_hash(Path(release["path"])) == release["sha256"]


@pytest.mark.parametrize("stage", ["distribute", "package-tests"])
def test_failed_sdk_or_package_test_cleans_only_owned_stage(build, monkeypatch, stage):
    f = build
    sentinel = put(f.tmp_path / "unrelated/keep.bin", b"unrelated sentinel")
    if stage == "distribute":
        def fail(*args, **kwargs):
            staged = Path(args[3][0])
            f.stages.append(staged.parent)
            return result(["SDK"], staged.parent, False)
        monkeypatch.setattr(pipeline, "run_sdk", fail)
    else:
        monkeypatch.setattr(pipeline, "_execute", lambda argv, cwd, *args: result(argv, cwd, False))
    assert run(f) is False
    assert sentinel.read_bytes() == b"unrelated sentinel"
    assert not f.stages[0].exists()
    assert not f.report.get("independent_release_verified")
    assert not f.report.get("release")
    assert f.report["release_staging"]["cleanup"]["status"] == "removed"
    if stage == "package-tests":
        assert f.report["execution"]["status"] == "failed"


def test_exception_cleans_stage(build, monkeypatch):
    f = build
    def crash(argv, cwd, *args):
        raise RuntimeError("bounded fixture exception")
    monkeypatch.setattr(pipeline, "_execute", crash)
    with pytest.raises(RuntimeError, match="bounded fixture"):
        run(f)
    assert not f.stages[0].exists()
    assert f.report["release_staging"]["cleanup"]["status"] == "removed"


@pytest.mark.parametrize("member", ["game/bridge_story.rpy", "old-game/bridge_story.rpyc", "game/bridge_assets/1359bd610acf4b25c2bfa0f52.wav", "fixture.exe"])
def test_original_zip_member_mutation_refused(build, monkeypatch, member):
    f = build
    def mutate(argv, cwd, *args):
        put(cwd / member, b"altered member")
        return result(argv, cwd)
    monkeypatch.setattr(pipeline, "_execute", mutate)
    with pytest.raises(BridgeError) as error:
        run(f)
    assert error.value.code == "PACKAGE_RUNTIME_MUTATION"
    assert not f.stages[0].exists()
    assert not f.report.get("independent_release_verified")
    assert f.report["status"] == "failed" and f.report["execution"]["status"] == "failed"
    assert f.report["execution"]["package_members_unchanged"] is False
    changes = error.value.details["changed_members"]
    assert [item["path"] for item in changes] == ["fixture-1.0-pc/" + member]
    assert changes[0]["before"]["sha256"] != changes[0]["after"]["sha256"]
    assert Path(f.report["execution"]["tested_tree_manifest"]["path"]).is_file()
    progress = json.loads((f.evidence / "pipeline-progress.json").read_text(encoding="utf-8"))
    assert progress["status"] == "failed" and progress["execution"]["status"] == "failed"
    assert progress["error"]["details"]["changed_members"] == changes


def test_copy_changed_bytes_refused(tmp_path, monkeypatch):
    source, destination = tmp_path / "source", tmp_path / "destination"
    put(source / "identity.rpyc")
    original = shutil.copytree
    def corrupt(src, dest, *args, **kwargs):
        result = original(src, dest, *args, **kwargs)
        (Path(dest) / "identity.rpyc").write_bytes(b"changed")
        return result
    monkeypatch.setattr(pipeline.shutil, "copytree", corrupt)
    with pytest.raises(BridgeError) as error:
        pipeline._copy_verified_tree(source, destination)
    assert error.value.code == "RELEASE_COPY_MISMATCH"


def test_copy_source_mutation_refused(tmp_path, monkeypatch):
    source, destination = tmp_path / "source", tmp_path / "destination"
    put(source / "identity.rpyc")
    original = shutil.copytree
    def mutate(src, dest, *args, **kwargs):
        result = original(src, dest, *args, **kwargs)
        (Path(src) / "identity.rpyc").write_bytes(b"changed after copy")
        return result
    monkeypatch.setattr(pipeline.shutil, "copytree", mutate)
    with pytest.raises(BridgeError) as error:
        pipeline._copy_verified_tree(source, destination)
    assert error.value.code == "RELEASE_COPY_MISMATCH"


def test_empty_directory_path_budget_refused_before_copy(tmp_path):
    source = tmp_path / "source"
    (source / ("a" * 80) / ("b" * 80)).mkdir(parents=True)
    destination = tmp_path / ("destination" + "c" * 50)
    with pytest.raises(BridgeError) as error:
        pipeline._copy_verified_tree(source, destination, short=True)
    assert error.value.code == "RELEASE_PATH_BUDGET"
    assert not destination.exists()


def test_source_changed_during_package_qa_refused(build, monkeypatch):
    f = build
    def mutate(argv, cwd, timeout, env):
        process = f.package(argv, cwd, timeout, env)
        (f.source / "old-game/bridge_story.rpyc").write_bytes(b"unexpected external mutation")
        return process
    monkeypatch.setattr(pipeline, "_execute", mutate)
    with pytest.raises(BridgeError) as error:
        run(f)
    assert error.value.code == "RELEASE_SOURCE_MUTATION"
    assert not f.stages[0].exists()
    assert not f.report.get("independent_release_verified")


def test_missing_unique_package_cleans_stage(build, monkeypatch):
    f = build
    def empty(*args, **kwargs):
        staged = Path(args[3][0])
        f.stages.append(staged.parent)
        return result(["SDK"], staged.parent)
    monkeypatch.setattr(pipeline, "run_sdk", empty)
    with pytest.raises(BridgeError) as error:
        run(f)
    assert error.value.code == "PACKAGE_MISSING"
    assert not f.stages[0].exists()


def test_publish_corruption_refused_and_stage_cleaned(build, monkeypatch):
    f = build
    original = shutil.copy2
    def corrupt(src, dest, *args, **kwargs):
        copied = original(src, dest, *args, **kwargs)
        if Path(dest).suffix == ".zip":
            Path(dest).write_bytes(b"truncated publication")
        return copied
    monkeypatch.setattr(pipeline.shutil, "copy2", corrupt)
    with pytest.raises(BridgeError) as error:
        run(f)
    assert error.value.code == "RELEASE_COPY_MISMATCH"
    assert not f.stages[0].exists()
    assert not f.report.get("independent_release_verified")


@pytest.mark.parametrize("member", ["../outside.txt", "C:/outside.txt", "dir\\outside.txt", "safe/../../outside.txt"])
def test_zip_paths_refused_before_extraction(tmp_path, member):
    package = tmp_path / "bad.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr(member, b"must not escape")
    # Windows ZipInfo normalizes separators at creation and read time; preserve
    # the actual malformed on-disk name in both local and central headers.
    if "\\" in member:
        package.write_bytes(package.read_bytes().replace(member.replace("\\", "/").encode(), member.encode()))
    with pytest.raises(BridgeError) as error:
        pipeline._extract_verified_package(package, tmp_path / "independent")
    assert error.value.code == "PACKAGE_PATH_INVALID"
    assert not (tmp_path / "outside.txt").exists()
    assert not list((tmp_path / "independent").iterdir())


def test_zip_case_collisions_and_symlinks_refused(tmp_path):
    for number, names in enumerate([["game/data.bin", "game/DATA.bin"], ["symlink"]]):
        package = tmp_path / f"bad{number}.zip"
        with zipfile.ZipFile(package, "w") as archive:
            for name in names:
                item = zipfile.ZipInfo(name)
                if name == "symlink":
                    item.create_system = 3
                    item.external_attr = (stat.S_IFLNK | 0o777) << 16
                archive.writestr(item, b"unsafe fixture")
        with pytest.raises(BridgeError) as error:
            pipeline._extract_verified_package(package, tmp_path / f"independent{number}")
        assert error.value.code == "PACKAGE_PATH_INVALID"


def test_zip_path_budget_checked_before_extracting(tmp_path):
    package = tmp_path / "long.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("package/" + "a" * 100 + "/" + "b" * 100 + "/asset.wav", b"exact bytes")
    with pytest.raises(BridgeError) as error:
        pipeline._extract_verified_package(package, tmp_path / "independent")
    assert error.value.code == "RELEASE_PATH_BUDGET"
    assert not list((tmp_path / "independent").iterdir())


def test_path_budget_uses_utf16_units(tmp_path):
    path = Path("C:/") / ("\U0001f600" * 125) / "asset.wav"
    assert len(str(path)) < 260 and pipeline._path_units(path) >= 260
    with pytest.raises(BridgeError) as error:
        pipeline._short_paths([path])
    assert error.value.code == "RELEASE_PATH_BUDGET"


def test_cleanup_owner_tampering_refused(tmp_path):
    report = {}
    with pytest.raises(BridgeError) as error:
        with pipeline._short_release_workspace(report, tmp_path) as (root, stage):
            (root / ".bridge-release-owner").write_text("foreign owner", encoding="ascii")
    assert error.value.code == "RELEASE_CLEANUP_FAILED"
    assert root.is_dir() and stage["cleanup"]["status"] == "failed"
    # The test itself still owns this exact minted directory, restore and clean it.
    shutil.rmtree(root)


def test_link_rejection_preserves_external_sentinel(tmp_path):
    source, outside = tmp_path / "source", tmp_path / "outside"
    sentinel = put(outside / "keep.bin", b"do not copy or delete")
    source.mkdir()
    linked = source / "linked"
    if os.name == "nt":
        import subprocess
        completed = subprocess.run(["cmd", "/c", "mklink", "/J", str(linked), str(outside)], capture_output=True)
        assert completed.returncode == 0, completed.stderr
    else:
        linked.symlink_to(outside, target_is_directory=True)
    try:
        with pytest.raises(BridgeError) as error:
            pipeline._copy_verified_tree(source, tmp_path / "destination", short=True)
        assert error.value.code == "RELEASE_PATH_INVALID"
        report = {}
        with pytest.raises(BridgeError) as error:
            with pipeline._short_release_workspace(report, tmp_path) as (root, stage):
                nested = root / "linked"
                if os.name == "nt":
                    completed = subprocess.run(["cmd", "/c", "mklink", "/J", str(nested), str(outside)], capture_output=True)
                    assert completed.returncode == 0
                else:
                    nested.symlink_to(outside, target_is_directory=True)
        assert error.value.code == "RELEASE_CLEANUP_FAILED"
        assert sentinel.read_bytes() == b"do not copy or delete"
        nested.rmdir() if os.name == "nt" else nested.unlink()
        shutil.rmtree(root)
    finally:
        linked.rmdir() if os.name == "nt" else linked.unlink()


def test_canonicalization_after_creation_is_recorded(tmp_path, monkeypatch):
    actual = tmp_path / "actual-short"
    actual.mkdir()
    monkeypatch.setattr(pipeline.tempfile, "mkdtemp", lambda **kwargs: str(actual))
    report = {}
    with pipeline._short_release_workspace(report, tmp_path) as (root, stage):
        assert root == actual.resolve()
        assert stage["canonical_directory"] == str(actual.resolve())
        assert stage["requested_directory"] == str(actual)
    assert not actual.exists()


def test_controlled_sdk_cache_classification_has_priority_and_preserves_source_less_code(tmp_path):
    sdk = tmp_path / "fixed-sdk"
    put(sdk / "renpy/ast.py", b"# fixed engine source")
    put(sdk / "renpy/text/text.py", b"# fixed nested engine source")
    orphan = "renpy/__pycache__/source_less." + sys.implementation.cache_tag + ".pyc"
    put(sdk / orphan, b"source-less engine code must remain")
    put(sdk / "lib/python3.12/os.pyc", b"stdlib source-less code must remain")
    early = [("game/cache/bytecode-312.rpyb", ["all"])]
    engine = [("renpy/**__pycache__/**.pyc", ["all"])]
    classified = []
    build = SimpleNamespace(early_base_patterns=early, renpy_patterns=engine,
                            classify=lambda pattern, group: classified.append((pattern, group)))
    scope = {"build": build, "config": SimpleNamespace(renpy_base=str(sdk)),
             "renpy": SimpleNamespace(script=SimpleNamespace(BYTECODE_FILE="cache/bytecode-312.rpyb"))}
    # Execute only our fixed configuration, not SDK/project/customer code.
    exec(compile(textwrap.dedent(pipeline.RELEASE_BUILD_SOURCE.split("\n", 1)[1]), "controlled_build_fixture", "exec"), scope)
    assert early[:3] == [("game/cache/bytecode-312.rpyb", None), ("game/cache/py3analysis.rpyb", None), ("game/cache/screens.rpyb", None)]
    expected = {"renpy/__pycache__/ast." + sys.implementation.cache_tag + ".pyc",
                "renpy/text/__pycache__/text." + sys.implementation.cache_tag + ".pyc"}
    assert {name for name, group in engine[:-1] if group is None} == expected
    assert engine[-1] == ("renpy/**__pycache__/**.pyc", ["all"])
    assert orphan not in {name for name, _ in engine[:-1]}
    assert all(not name.startswith("lib/") for name, _ in engine[:-1])
    assert ("**.log", None) in classified


@pytest.mark.parametrize("cache", ["game/cache/screens.rpyb", "game/cache/py3analysis.rpyb", "game/cache/bytecode-312.rpyb", "renpy/__pycache__/ast.cpython-312.pyc"])
def test_retained_regenerable_cache_in_zip_refused(cache):
    members = {"bundle/" + cache: {"sha256": "x", "bytes": 1},
               "bundle/renpy/ast.py": {"sha256": "y", "bytes": 1}}
    with pytest.raises(BridgeError) as error:
        pipeline._verify_package_cache_policy(members)
    assert error.value.code == "PACKAGE_CACHE_PRESENT"


def test_cache_policy_does_not_exempt_or_drop_compiled_game_or_runtime_bytes():
    names = ["bundle/game/bridge_story.rpyc", "bundle/old-game/bridge_story.rpyc",
             "bundle/renpy/__pycache__/source_less.cpython-312.pyc", "bundle/lib/python3.12/os.pyc",
             "bundle/game/cache/build_info.json", "bundle/game/bridge_assets/retired.wav"]
    members = {name: {"sha256": "exact", "bytes": 1} for name in names}
    assert pipeline._verify_package_cache_policy(members)["verified"] is True
    assert set(members) == set(names)
