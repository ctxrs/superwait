"""Project-local setup, preserving unrelated settings and saving originals."""

import json
import os
import shlex
import sys
import tomllib
from importlib.resources import files
from pathlib import Path

from .models import seconds
from .store import default_db


def write(path: Path, contents: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_text() == contents:
            return
        backup = path.with_name(path.name + ".superwait-backup")
        if not backup.exists():
            fd = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(path.read_bytes())
    path.write_text(contents)


def configure(provider, project, db=None, max_wait="24h"):
    project = project.expanduser().absolute()
    db = (db or default_db()).expanduser().absolute()
    prefix = [sys.executable, "-m", "superwait", "--db", str(db), "--provider", provider]
    hook_command = shlex.join([*prefix, "hook", provider])
    server = {"command": prefix[0], "args": [*prefix[1:], "serve"]}
    host_timeout = int(seconds(max_wait)) + 30
    hostdir = {"codex": ".codex", "claude": ".claude", "cursor": ".cursor"}[provider]
    written = []

    def put(path, content):
        write(path, content)
        written.append(str(path))

    def read_json(path):
        value = json.loads(path.read_text()) if path.exists() else {}
        if not isinstance(value, dict):
            raise ValueError(f"expected a JSON object in {path}")
        return value

    hookpath = project / hostdir / ("settings.json" if provider == "claude" else "hooks.json")
    hook_config = read_json(hookpath)
    if not isinstance(hook_config.get("hooks", {}), dict):
        raise ValueError("existing hooks must be an object")

    if provider == "codex":
        path = project / hostdir / "config.toml"
        text = path.read_text() if path.exists() else ""
        parsed = tomllib.loads(text)
        start, stop = "# BEGIN superwait", "# END superwait"
        if start in text:
            before, rest = text.split(start, 1)
            _, after = rest.split(stop, 1)
            text = before.rstrip() + after
        elif "superwait" in parsed.get("mcp_servers", {}):
            raise ValueError("existing superwait MCP entry is not managed by setup; edit it explicitly")
        fragment = (f'{start}\n[mcp_servers.superwait]\ncommand = {json.dumps(server["command"])}\n'
                    f'args = {json.dumps(server["args"])}\ntool_timeout_sec = {host_timeout}\n{stop}\n')
        put(path, text.rstrip() + "\n\n" + fragment)
    else:
        path = project / (".mcp.json" if provider == "claude" else ".cursor/mcp.json")
        config = read_json(path)
        if provider == "claude":
            server["timeout"] = host_timeout * 1000
        config.setdefault("mcpServers", {})["superwait"] = server
        put(path, json.dumps(config, indent=2) + "\n")

    config = hook_config
    hooks = config.setdefault("hooks", {})
    if provider == "cursor":
        config.setdefault("version", 1)
    events = ["subagentStart", "subagentStop"] if provider == "cursor" else ["SubagentStart", "SubagentStop"]
    if provider == "claude":
        events.append("PostToolUse")
    if provider == "codex":
        events.append("SessionStart")
    for event in events:
        entries = hooks.setdefault(event, [])
        # Remove only our recorder entries, identified by their exact invocation.
        def ours(entry):
            commands = [entry.get("command", "")] + [h.get("command", "") for h in entry.get("hooks", [])]
            return any("-m superwait --db " in c and c.endswith(" hook " + provider) for c in commands)
        entries[:] = [entry for entry in entries if not ours(entry)]
        if provider == "cursor":
            entries.append({"command": hook_command, "timeout": 5})
        else:
            entry = {"hooks": [{"type": "command", "command": hook_command, "timeout": 5}]}
            if event == "PostToolUse":
                entry["matcher"] = "SubagentHandback"
            entries.append(entry)
    put(hookpath, json.dumps(config, indent=2) + "\n")
    skilldir = ".agents" if provider == "codex" else hostdir
    skillpath = project / skilldir / "skills/superwait/SKILL.md"
    put(skillpath, files("superwait").joinpath("SKILL.md").read_text()
        + "\nCLI for this installation (includes the shared event database):\n\n```sh\n"
        + shlex.join(prefix) + " wait --request wait.json\n```\n")
    return {"written": written, "database": str(db), "max_wait": max_wait,
            "next": "Restart the host and review its MCP/hooks trust prompts. Cursor: use the CLI for waits beyond its tool timeout."}
