# Host integration

All three hosts use the same `wait_for`, `list_agents`, and `signal` MCP API.
The CLI runs the same wait engine.

## Installation scope

**Host-wide setup is recommended:** run `superwait setup codex`, `superwait setup
claude`, or `superwait setup cursor` once. This installs into the selected host's
user configuration and applies across projects for that OS user. It does not
configure other users, remote machines, or cloud agent VMs.

Host-wide setup is new on this draft branch. Use the preview install in the
[README](../README.md#install); PyPI 0.2.0 still requires `--project`.

| Host | User MCP configuration | User lifecycle hooks | User skill |
| --- | --- | --- | --- |
| Codex | `~/.codex/config.toml` | `~/.codex/hooks.json` | `~/.agents/skills/superwait/SKILL.md` |
| Claude Code | `~/.claude.json` | `~/.claude/settings.json` | `~/.claude/skills/superwait/SKILL.md` |
| Cursor | `~/.cursor/mcp.json` | `~/.cursor/hooks.json` | `~/.cursor/skills/superwait/SKILL.md` |

Codex honors `CODEX_HOME` for its config and hooks; its user skill stays in
`~/.agents/skills`. Claude Code honors `CLAUDE_CONFIG_DIR`: when set, the MCP
entry goes into `.claude.json` inside that directory, with settings and skills
alongside it. Cursor uses its standard `~/.cursor` directory.

### Project-only setup

Add `--project /path/to/repo` to limit setup to one repository. This explicitly
uses the repository paths below, even if a user configuration directory is set
in your environment.

| Host | MCP configuration | Lifecycle hook configuration | Skill |
| --- | --- | --- | --- |
| Codex | `.codex/config.toml` | `.codex/hooks.json` | `.agents/skills/superwait/SKILL.md` |
| Claude Code | `.mcp.json` | `.claude/settings.json` | `.claude/skills/superwait/SKILL.md` |
| Cursor | `.cursor/mcp.json` | `.cursor/hooks.json` | `.cursor/skills/superwait/SKILL.md` |

Setup preserves unrelated settings, creates owner-only `.superwait-backup`
copies of existing files before changing them, and can be run again without
duplicating its hook entries in the same scope. It does not change host trust
or approval settings.

### Moving an existing project installation to host-wide setup

Setup does not remove existing project installations. Hosts can load hooks from
both scopes, which would record events twice. Remove only the old superwait MCP
entry, its hook entries, and its installed skill from the project before using
the host-wide installation there. Keep unrelated settings and hooks. For Codex,
the managed MCP entry is between `# BEGIN superwait` and `# END superwait`.
Then restart the host and review its normal trust prompts.

Sources: [Codex config](https://learn.chatgpt.com/docs/config-file/config-reference),
[Codex hooks](https://developers.openai.com/codex/hooks),
[Codex skills](https://learn.chatgpt.com/docs/build-skills),
[Claude Code MCP scopes](https://code.claude.com/docs/en/mcp#user-scope),
[Claude Code settings](https://code.claude.com/docs/en/settings),
[Cursor MCP](https://cursor.com/docs/context/mcp),
[Cursor hooks](https://cursor.com/docs/hooks),
[Cursor skills](https://cursor.com/docs/context/skills).

## Codex

`SubagentStart` and `SubagentStop` supply the agent ID and parent session ID.
Superwait records the last assistant message when present. A stop hook observes
a response ending before other stop hooks necessarily finish deciding whether
to continue it. It is not a durable promise that the agent will never run again.

Some Codex runtimes return a task path such as `/root/reviewer` from spawning,
but lifecycle hooks report a UUID. Both can now be passed directly to `agents`.
At the stop hook, Superwait reads only the first metadata record of the
exact worker transcript Codex supplies. It validates both worker and parent IDs
before recording the task path. This metadata adapter supports the Codex 0.153.1
header format. It reads no conversation body and scans
no history. It is version-dependent; absent or unsupported metadata leaves
UUID-based waiting and explicit signals available.

A previously unknown task path remains pending until that mapping arrives.
Task paths require `session` even if only one old mapping exists: an unscoped
name could otherwise match a previous conversation before the new worker stops.
Setup installs a SessionStart hook that supplies this context to Codex; it
makes no permission decisions. The CLI fills missing scope from CODEX_THREAD_ID
only for task paths. Explicit session values and ordinary UUIDs are preserved.
Duplicate or ambiguous references return an actionable error. `list_agents`
remains available for discovery and diagnosis, with UUIDs in `key` and observed
task paths in `data.aliases`.

Setup sets `tool_timeout_sec` to the desired maximum wait plus 30 seconds.
The server sends MCP progress keepalives during normal polling; these do not
override the client's hard timeout. The tool remains cancellable.

For noninteractive `codex exec`, first review and trust the hooks with `/hooks`
in the same project and Codex home. A `never` approval policy cannot approve
MCP calls that still require a prompt. After reviewing this server's access,
configure `tools.wait_for.approval_mode = "approve"` and the other needed tools
under `[mcp_servers.superwait]`, or use that server's
`default_tools_approval_mode = "approve"`. Setup leaves that decision to the
host's normal approval flow. This approval includes repeated command and HTTP
probes with the server process's access.

Sources: [hooks](https://developers.openai.com/codex/hooks),
[MCP settings](https://learn.chatgpt.com/docs/extend/mcp),
[skill discovery](https://learn.chatgpt.com/docs/build-skills).

## Claude Code

The same lifecycle event names provide `agent_id` and `session_id`. Setup also
observes `PostToolUse` for `SubagentHandback` because newer versions can deliver
the actual report there; the final assistant text may only be a closing note.
The report does not itself mark the agent as stopped.

The per-server `timeout` is in milliseconds. Current Claude Code can background
long MCP calls and notify on completion; its wall-clock limit still applies.
The CLI can also run under Bash or Monitor. Monitor receives its final JSON line
when the wait returns. A killed subagent may never emit a stop event, so retain
the wait's deadline and check native status if needed.

Sources: [hook payloads](https://code.claude.com/docs/en/hooks#subagentstop),
[MCP timeouts/backgrounding](https://code.claude.com/docs/en/mcp),
[Monitor](https://code.claude.com/docs/en/tools-reference#monitor-tool).

## Cursor

`subagentStart` includes `subagent_id` and `parent_conversation_id`.
The documented `subagentStop` payload can omit both. Superwait uses the common
conversation ID and matches the exact task/type fingerprint only when there is
one running candidate in that parent. Concurrent identical tasks remain
unmatched. No timing heuristic guesses which agent finished.

For these cases, assign unique outcome signal keys in worker instructions.
Signals also work for checkpoints and blockers in every host. Cursor's explicit
`error` and `aborted` stop states are preserved when correlated.

The public MCP documentation does not specify a supported long-call timeout
setting. Setup does not invent one. Prefer the CLI through the background
terminal for hours-long waits. A live Cursor build is needed to qualify its
background completion delivery and exact hook payloads.

Sources: [hooks](https://cursor.com/docs/hooks),
[MCP](https://cursor.com/docs/mcp),
[subagents](https://cursor.com/docs/subagents).

## Permissions and lifecycle

The tool does not bypass host permissions. MCP/terminal/hook trust is still
owned by the host. A predicate is an operation: HTTP requests and command probes
use the access of the local server process, so authorize it as you would other
local MCP tools. Use observational probes because they run repeatedly.

Signals are local observations, not messages to other tasks. No hosted service,
cloud account, or model endpoint is involved. Hooks and CLI use one shared
SQLite store. No provider database is accessed. The Codex task-name adapter
reads the metadata header described above; Claude and Cursor use their
documented hook fields.
