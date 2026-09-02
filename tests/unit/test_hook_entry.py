import io
import json

from tracedeck.hook_entry import HOOK_EVENTS, capture


def test_hook_output_contract_is_neutral(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("TRACEDECK_DATA_DIR", str(tmp_path / "data"))
    payload = io.BytesIO(json.dumps({"session_id": "s"}).encode())

    for event in HOOK_EVENTS:
        assert capture(event, payload) == 0
        output = capsys.readouterr().out
        if event == "Stop":
            assert output == '{"continue":true}\n'
        else:
            assert output == ""


def test_disabled_capture_does_not_read_or_write(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("TRACEDECK_ENABLED", "0")
    monkeypatch.setenv("TRACEDECK_DATA_DIR", str(tmp_path / "data"))

    assert capture("UserPromptSubmit", io.BytesIO(b"not-json")) == 0
    assert capsys.readouterr().out == ""
    assert not (tmp_path / "data").exists()

