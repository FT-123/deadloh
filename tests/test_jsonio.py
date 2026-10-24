import json

from core.jsonio import write_json_atomic


def test_write_json_atomic_writes_utf8_and_leaves_no_tmp(tmp_path):
    target = tmp_path / "meta.json"

    write_json_atomic(target, {"name": "Мод", "enabled": True})

    assert json.loads(target.read_text(encoding="utf-8")) == {"name": "Мод", "enabled": True}
    assert not (tmp_path / "meta.json.tmp").exists()


def test_write_json_atomic_overwrites_existing_file(tmp_path):
    target = tmp_path / "meta.json"
    target.write_text('{"old": true}', encoding="utf-8")

    write_json_atomic(target, {"new": 1})

    assert json.loads(target.read_text(encoding="utf-8")) == {"new": 1}
