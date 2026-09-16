import json
import tomllib

import pytest

from superwait.hooks import record_hook
from superwait.setup import configure
from superwait.store import Store


def cursor_start(id, task="Review auth", session="parent"):
    return {"hook_event_name": "subagentStart", "subagent_id": id, "task": task,
            "parent_conversation_id": session, "subagent_type": "generalPurpose"}


def cursor_stop(task="Review auth", session="parent", **kwargs):
    return {"hook_event_name": "subagentStop", "conversation_id": session, "task": task,
            "subagent_type": "generalPurpose", "status": "completed", "summary": "Review complete", **kwargs}


def test_cursor_missing_id_correlates_unique_task_not_another_parent(tmp_path):
    store = Store(tmp_path / "db")
    assert record_hook("cursor", cursor_start("a"), store) == {"permission": "allow"}
    record_hook("cursor", cursor_start("b", session="other"), store)
    record_hook("cursor", cursor_stop(status="error"), store)
    assert store.latest("agent", "a", provider="cursor")["state"] == "error"
    assert store.latest("agent", "b", provider="cursor")["state"] == "running"


def test_cursor_ambiguous_stop_cannot_complete_wrong_agent(tmp_path):
    store = Store(tmp_path / "db")
    for id in "ab":
        record_hook("cursor", cursor_start(id), store)
    record_hook("cursor", cursor_stop(), store)
    assert {a["state"] for a in store.agents("cursor")} == {"running"}
    record_hook("cursor", cursor_stop(subagent_id="b"), store)
    assert store.latest("agent", "b", provider="cursor")["state"] == "stopped"


def test_claude_handback_survives_closing_text(tmp_path):
    store = Store(tmp_path / "db")
    base = {"agent_id": "a", "session_id": "s"}
    record_hook("claude", {**base, "hook_event_name": "SubagentStart"}, store)
    record_hook("claude", {**base, "hook_event_name": "PostToolUse", "tool_name": "SubagentHandback", "tool_input": {"message": "actual report"}}, store)
    record_hook("claude", {**base, "hook_event_name": "SubagentStop", "last_assistant_message": "done"}, store)
    event = store.latest("agent", "a", provider="claude")
    assert event["data"]["report"] == "actual report"
    assert event["data"]["summary"] == "done"


@pytest.mark.parametrize("provider", ["codex", "claude", "cursor"])
def test_setup_preserves_unrelated_config_and_is_idempotent(tmp_path, provider):
    project = tmp_path / "project with spaces"
    directory = project / (".claude" if provider == "claude" else f".{provider}")
    directory.mkdir(parents=True)
    path = directory / ("settings.json" if provider == "claude" else "hooks.json")
    event = "subagentStop" if provider == "cursor" else "SubagentStop"
    existing = {"hooks": {event: [{"command": "existing"}]}, "custom": "keep"}
    path.write_text(json.dumps(existing))
    if provider == "codex":
        (directory / "config.toml").write_text('model = "chosen-model"\n')
    result = configure(provider, project, tmp_path / "events", "2h")
    before = {p: open(p).read() for p in result["written"]}
    configure(provider, project, tmp_path / "events", "2h")
    assert before == {p: open(p).read() for p in result["written"]}
    config = json.loads(path.read_text())
    assert config["custom"] == "keep"
    assert config["hooks"][event][0] == {"command": "existing"}
    assert len(config["hooks"][event]) == 2
    if provider == "codex":
        parsed = tomllib.loads((directory / "config.toml").read_text())
        assert parsed["model"] == "chosen-model"
        assert parsed["mcp_servers"]["superwait"]["tool_timeout_sec"] == 7230


def test_event_cursors_are_not_reused_after_pruning(tmp_path):
    store = Store(tmp_path / "db")
    old = store.emit("signal", "a", "ready")
    with store.connect() as db:
        db.execute("UPDATE events SET at=0")
    store.prune(1)
    assert store.emit("signal", "b", "ready") > old


def test_setup_does_not_write_mcp_config_when_existing_hooks_are_invalid(tmp_path):
    path = tmp_path / ".cursor/hooks.json"
    path.parent.mkdir()
    path.write_text('[]')
    with pytest.raises(ValueError):
        configure("cursor", tmp_path, tmp_path / "db")
    assert not (tmp_path / ".cursor/mcp.json").exists()
