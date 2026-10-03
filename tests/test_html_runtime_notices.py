"""Verify that self-contained distributions keep exact upstream notices."""
import hashlib
import importlib.util
from pathlib import Path
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("native_notice_builder", ROOT / "tools/build_html_embedded_host.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def fixture(tmp_path, monkeypatch, missing=False):
    packages = tmp_path / "packages"
    records = {}
    for name, original in builder.NOTICE_ARCHIVES.items():
        record = dict(original)
        archive = packages / name / record["version"] / (name + "." + record["version"] + ".nupkg")
        archive.parent.mkdir(parents=True)
        with zipfile.ZipFile(archive, "w") as bundle:
            for member in record["members"]:
                if not missing:
                    bundle.writestr(member, (name + " original " + member + "\r\n").encode())
        record["sha256"] = hashlib.sha256(archive.read_bytes()).hexdigest()
        records[name] = record
    monkeypatch.setattr(builder, "NOTICE_ARCHIVES", records)
    return packages


def test_self_contained_keeps_every_original_notice_byte(tmp_path, monkeypatch):
    packages = fixture(tmp_path, monkeypatch)
    output = tmp_path / "host"
    records = builder.copy_runtime_notices(output, packages, include_dotnet=True)
    assert len(records) == 5
    for record in records:
        expected = (record["package"] + " original " + record["archive_member"] + "\r\n").encode()
        assert (output / record["path"]).read_bytes() == expected
        assert record["sha256"] == hashlib.sha256(expected).hexdigest()


def test_framework_dependent_does_not_claim_dotnet_distribution(tmp_path, monkeypatch):
    packages = fixture(tmp_path, monkeypatch)
    records = builder.copy_runtime_notices(tmp_path / "host", packages, include_dotnet=False)
    assert len(records) == 2
    assert {record["package"] for record in records} == {"microsoft.web.webview2"}


def test_changed_archive_stops_notice_publication(tmp_path, monkeypatch):
    packages = fixture(tmp_path, monkeypatch)
    original = next(iter(builder.NOTICE_ARCHIVES.items()))
    name, record = original
    archive = packages / name / record["version"] / (name + "." + record["version"] + ".nupkg")
    with archive.open("ab") as stream:
        stream.write(b"tampered")
    with pytest.raises(RuntimeError, match="archive differs"):
        builder.copy_runtime_notices(tmp_path / "host", packages, include_dotnet=True)
    assert not (tmp_path / "host").exists()


def test_missing_required_notice_stops_distribution(tmp_path, monkeypatch):
    packages = fixture(tmp_path, monkeypatch, missing=True)
    with pytest.raises(RuntimeError, match="member is missing"):
        builder.copy_runtime_notices(tmp_path / "host", packages, include_dotnet=True)
