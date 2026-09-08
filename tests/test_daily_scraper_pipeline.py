import json
from pathlib import Path

import data
import send_to_site


class _Response:
    def __init__(self, status_code, text="ok", request_id="req-test"):
        self.status_code = status_code
        self.text = text
        self.headers = {"X-Request-ID": request_id}


def _configure(monkeypatch, tmp_path, new_items, retry_items=None, batch_size=25):
    input_file = tmp_path / "new_items.json"
    retry_file = tmp_path / "retry_items.json"
    input_file.write_text(json.dumps(new_items), encoding="utf-8")
    if retry_items is not None:
        retry_file.write_text(json.dumps(retry_items), encoding="utf-8")
    monkeypatch.setattr(send_to_site, "INPUT_FILE", str(input_file))
    monkeypatch.setattr(send_to_site, "RETRY_FILE", str(retry_file))
    monkeypatch.setattr(send_to_site, "API_URL", "https://example.test/api/oak/import")
    monkeypatch.setattr(send_to_site, "API_KEY", "test-key")
    monkeypatch.setattr(send_to_site, "BATCH_SIZE", batch_size)
    return retry_file


def test_poison_record_is_isolated_and_queued(monkeypatch, tmp_path):
    items = [{"ID": "1"}, {"ID": "bad"}, {"ID": "3"}]
    retry_file = _configure(monkeypatch, tmp_path, items, batch_size=3)
    calls = []

    def fake_post(url, json, headers, timeout):
        batch = json["items"]
        calls.append([item["ID"] for item in batch])
        return _Response(500 if any(item["ID"] == "bad" for item in batch) else 200)

    monkeypatch.setattr(send_to_site.requests, "post", fake_post)

    assert send_to_site.main() == 0
    assert json.loads(retry_file.read_text(encoding="utf-8")) == [{"ID": "bad"}]
    assert calls == [["1", "bad", "3"], ["1"], ["bad", "3"], ["bad"], ["3"]]


def test_retry_queue_is_merged_deduplicated_and_cleared(monkeypatch, tmp_path):
    retry_file = _configure(
        monkeypatch,
        tmp_path,
        [{"ID": "2"}, {"ID": "3"}],
        retry_items=[{"ID": "1"}, {"ID": "2"}],
    )
    calls = []

    def fake_post(url, json, headers, timeout):
        calls.extend(item["ID"] for item in json["items"])
        return _Response(200)

    monkeypatch.setattr(send_to_site.requests, "post", fake_post)

    assert send_to_site.main() == 0
    assert calls == ["1", "2", "3"]
    assert json.loads(retry_file.read_text(encoding="utf-8")) == []


def test_auth_failure_fails_without_overwriting_retry_queue(monkeypatch, tmp_path):
    original_retry = [{"ID": "old"}]
    retry_file = _configure(
        monkeypatch, tmp_path, [{"ID": "new"}], retry_items=original_retry
    )
    monkeypatch.setattr(
        send_to_site.requests,
        "post",
        lambda *args, **kwargs: _Response(401, "unauthorized"),
    )

    assert send_to_site.main() == 1
    assert json.loads(retry_file.read_text(encoding="utf-8")) == original_retry


def test_oak_import_realigns_serial_sequence_before_upserts():
    source = Path(data.__file__).read_text(encoding="utf-8")
    lock_pos = source.index("LOCK TABLE dissertations IN SHARE ROW EXCLUSIVE MODE")
    sequence_pos = source.index("pg_get_serial_sequence('dissertations', 'id')")
    loop_pos = source.index("            for item in items:", sequence_pos)

    assert lock_pos < sequence_pos < loop_pos


def test_workflow_persists_runtime_state_without_git_push():
    workflow = (
        Path(__file__).resolve().parents[1] / ".github" / "workflows" / "daily.yml"
    ).read_text(encoding="utf-8")

    assert "actions/cache/restore@v5" in workflow
    assert "actions/cache/save@v5" in workflow
    assert "last_state.json" in workflow
    assert "retry_items.json" in workflow
    assert "permissions:\n  contents: read" in workflow
    assert "git push" not in workflow
