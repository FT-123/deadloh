"""Тесты релизной сборки: версии, zip-раскладка и обновление APP_VERSION."""

import zipfile

import build_release as br


def test_normalize_version():
    assert br.normalize_version("1.4") == "1.4"
    assert br.normalize_version("v1.4") == "1.4"
    assert br.normalize_version(" 1.2.3 ") == "1.2.3"


def test_bump_version_keeps_component_count():
    assert br.bump_version("1.2", "patch") == "1.3"
    assert br.bump_version("1.2", "minor") == "1.3"
    assert br.bump_version("1.2.3", "patch") == "1.2.4"
    assert br.bump_version("1.2.3", "minor") == "1.3.0"
    assert br.bump_version("1.2.3", "major") == "2.0.0"


def test_resolve_version_prefers_explicit_then_bump():
    assert br.resolve_version("1.2", "2.0", None, interactive=False) == "2.0"
    assert br.resolve_version("1.2", None, "minor", interactive=False) == "1.3"
    assert br.resolve_version("1.2", None, None, interactive=False) == "1.2"


def test_make_release_zip_puts_contents_at_root(tmp_path):
    bundle = tmp_path / "DeadlockModManager"
    (bundle / "_internal").mkdir(parents=True)
    (bundle / "DeadlockModManager.exe").write_bytes(b"exe")
    (bundle / "_internal" / "data.bin").write_bytes(b"data")
    (bundle / "__pycache__").mkdir()
    (bundle / "__pycache__" / "x.pyc").write_bytes(b"junk")

    zip_path = tmp_path / "release.zip"
    files, digest = br.make_release_zip(bundle, zip_path)

    with zipfile.ZipFile(zip_path) as archive:
        names = set(archive.namelist())
    assert names == {"DeadlockModManager.exe", "_internal/data.bin"}
    assert files == 2
    assert len(digest) == 64


def test_missing_binaries_reports_absent_files(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    for name in br.REQUIRED_BINARIES:
        (bundle / name).write_bytes(b"x")
    assert br.missing_binaries(bundle) == []

    (bundle / "vpkmerge.exe").unlink()
    assert br.missing_binaries(bundle) == ["vpkmerge.exe"]


def test_write_and_restore_app_version(tmp_path):
    updater = tmp_path / "updater.py"
    updater.write_text('APP_VERSION = "1.2"\n', encoding="utf-8")

    original = br.write_app_version("1.4", updater)
    assert 'APP_VERSION = "1.4"' in updater.read_text(encoding="utf-8")
    assert br.read_app_version(updater) == "1.4"

    br.restore_app_version(original, updater)
    assert br.read_app_version(updater) == "1.2"


def test_detect_repo_parses_github_remote(monkeypatch, tmp_path):
    class Result:
        returncode = 0
        stdout = "git@github.com:owner/repo.git\n"

    monkeypatch.setattr(br.subprocess, "run", lambda *args, **kwargs: Result())
    assert br.detect_repo(tmp_path) == "owner/repo"
