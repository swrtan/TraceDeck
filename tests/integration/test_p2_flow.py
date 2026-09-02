import io
import json

from tracedeck.db import connect
from tracedeck.hook_entry import capture
from tracedeck.normalize import project_spool
from tracedeck.spool import AtomicSpool


def test_mocked_hook_session_flows_from_stdin_to_sqlite(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    monkeypatch.setenv("TRACEDECK_DATA_DIR", str(data_dir))
    events = [
        ("SessionStart", {"session_id": "s", "cwd": "C:/work", "model": "gpt"}),
        ("UserPromptSubmit", {"session_id": "s", "turn_id": "t", "prompt": "hello", "model": "gpt"}),
        ("Stop", {"session_id": "s", "turn_id": "t", "last_assistant_message": "done", "model": "gpt"}),
    ]
    for event_name, payload in events:
        assert capture(event_name, io.BytesIO(json.dumps(payload).encode())) == 0

    db = connect(data_dir / "trace.db")
    processed, quarantined = project_spool(AtomicSpool(data_dir / "spool"), db)
    assert (processed, quarantined) == (3, 0)
    assert db.execute("SELECT lifecycle_status FROM turns").fetchone()[0] == "completed"
    assert db.execute("SELECT assistant_response FROM turns").fetchone()[0] is None
    db.close()
