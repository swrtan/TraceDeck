import json

from tracedeck.hooks import install, remove_hooks, status, uninstall


def test_install_merges_existing_hooks_and_is_idempotent(tmp_path):
    home = tmp_path / ".codex"
    home.mkdir()
    path = home / "hooks.json"
    original = {"description": "keep", "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "custom"}]}]}}
    path.write_text(json.dumps(original), encoding="utf-8")

    install(home)
    first = json.loads(path.read_text(encoding="utf-8"))
    install(home)
    second = json.loads(path.read_text(encoding="utf-8"))

    assert first == second
    assert second["description"] == "keep"
    assert any(group["hooks"][0]["command"].startswith("py -3 -m tracedeck") for group in second["hooks"]["Stop"])
    assert (home / "hooks.json.bak").exists()


def test_uninstall_preserves_unrelated_handlers(tmp_path):
    home = tmp_path / ".codex"
    install(home)
    path = home / "hooks.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["hooks"]["Stop"].append({"hooks": [{"type": "command", "command": "custom"}]})
    path.write_text(json.dumps(document), encoding="utf-8")
    uninstall(home)

    result = json.loads(path.read_text(encoding="utf-8"))
    assert result["hooks"]["Stop"] == [{"hooks": [{"type": "command", "command": "custom"}]}]
    assert status(home)["any_installed"] is False

