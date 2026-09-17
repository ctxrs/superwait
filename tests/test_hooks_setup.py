import json
from pathlib import Path
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


@pytest.mark.parametrize("provider,mcp,hooks,skill", [
    ("codex", ".codex/config.toml", ".codex/hooks.json", ".agents/skills/superwait/SKILL.md"),
    ("claude", ".claude.json", ".claude/settings.json", ".claude/skills/superwait/SKILL.md"),
    ("cursor", ".cursor/mcp.json", ".cursor/hooks.json", ".cursor/skills/superwait/SKILL.md"),
])
def test_host_setup_defaults_to_user_scope_and_preserves_settings(tmp_path, monkeypatch, provider, mcp, hooks, skill):
    user_root = tmp_path / "user with spaces"
    working = tmp_path / "unrelated project"
    working.mkdir()
    monkeypatch.setattr(Path, "home", lambda: user_root)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.chdir(working)
    mcp_path, hooks_path = user_root / mcp, user_root / hooks
    mcp_path.parent.mkdir(parents=True, exist_ok=True)
    hooks_path.parent.mkdir(parents=True, exist_ok=True)
    existing = ('model = "chosen-model"\n' if provider == "codex" else
                json.dumps({"mcpServers": {"existing": {"command": "keep"}},
                            "custom": {"preserve": True}, "projects": {"example": {"keep": True}}}))
    mcp_path.write_text(existing)
    event = "subagentStop" if provider == "cursor" else "SubagentStop"
    hooks_path.write_text(json.dumps({"hooks": {event: [{"command": "existing"}]}, "custom": "keep"}))
    result = configure(provider, db=tmp_path / "db")
    assert result["scope"] == "user"
    assert set(result["written"]) == {str(user_root / p) for p in (mcp, hooks, skill)}
    assert not list(working.iterdir())
    assert not (user_root / ".mcp.json").exists()
    before = {p: Path(p).read_bytes() for p in result["written"]}
    assert configure(provider, db=tmp_path / "db") == result
    assert before == {p: Path(p).read_bytes() for p in result["written"]}
    saved = json.loads(hooks_path.read_text())
    assert saved["custom"] == "keep"
    assert saved["hooks"][event][0] == {"command": "existing"}
    assert len(saved["hooks"][event]) == 2
    backup = mcp_path.with_name(mcp_path.name + ".superwait-backup")
    assert backup.read_text() == existing
    assert backup.stat().st_mode & 0o777 == 0o600
    if provider == "codex":
        assert tomllib.loads(mcp_path.read_text())["model"] == "chosen-model"
    else:
        saved = json.loads(mcp_path.read_text())
        assert saved["mcpServers"]["existing"] == {"command": "keep"}
        assert saved["custom"] == {"preserve": True}
        assert saved["projects"] == {"example": {"keep": True}}


@pytest.mark.parametrize("provider,variable,mcp_name", [
    ("codex", "CODEX_HOME", "config.toml"),
    ("claude", "CLAUDE_CONFIG_DIR", ".claude.json"),
])
def test_host_setup_respects_config_directory_but_project_setup_stays_local(tmp_path, monkeypatch, provider, variable, mcp_name):
    user_root = tmp_path / "user"
    custom = tmp_path / "host config"
    monkeypatch.setattr(Path, "home", lambda: user_root)
    monkeypatch.setenv(variable, str(custom))
    result = configure(provider, db=tmp_path / "db")
    assert str(custom / mcp_name) in result["written"]
    assert (custom / ("hooks.json" if provider == "codex" else "settings.json")).exists()
    skill = user_root / ".agents" if provider == "codex" else custom
    assert (skill / "skills/superwait/SKILL.md").exists()
    before = {p: Path(p).read_bytes() for p in result["written"]}
    project = tmp_path / "repo"
    local = configure(provider, project, tmp_path / "db")
    assert local["scope"] == "project"
    assert all(Path(p).is_relative_to(project) for p in local["written"])
    assert before == {p: Path(p).read_bytes() for p in result["written"]}


def test_default_setup_cli_uses_user_scope(tmp_path, monkeypatch, capsys):
    import sys
    from superwait.cli import main

    monkeypatch.setattr(Path, "home", lambda: tmp_path / "user")
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.setattr(sys, "argv", ["superwait", "--db", str(tmp_path / "db"), "setup", "codex"])
    with pytest.raises(SystemExit) as result:
        main()
    assert result.value.code == 0
    assert json.loads(capsys.readouterr().out)["scope"] == "user"


def test_invalid_user_mcp_preserves_existing_files(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    path = tmp_path / ".claude.json"
    path.write_text('{"mcpServers": []}')
    with pytest.raises(ValueError, match="mcpServers"):
        configure("claude", db=tmp_path / "db")
    assert path.read_text() == '{"mcpServers": []}'
    assert not (tmp_path / ".claude").exists()
