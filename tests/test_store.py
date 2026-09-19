import json

from fundr import store


def test_save_and_load_json(tmp_path, monkeypatch):
    monkeypatch.setenv("FUNDR_DATA", str(tmp_path))
    path = store.save_json("p00", "x.json", {"a": 1})
    assert path == tmp_path / "p00" / "x.json"
    assert store.load_json("p00", "x.json") == {"a": 1}


def test_append_jsonl(tmp_path, monkeypatch):
    monkeypatch.setenv("FUNDR_DATA", str(tmp_path))
    store.append_jsonl("p09", "live.jsonl", {"n": 1})
    path = store.append_jsonl("p09", "live.jsonl", {"n": 2})
    lines = path.read_text().splitlines()
    assert [json.loads(line)["n"] for line in lines] == [1, 2]
