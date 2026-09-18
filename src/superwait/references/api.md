# Superwait API reference

Superwait exposes the same wait engine through an MCP tool and a CLI. The MCP
server provides `wait_for`, `list_agents`, and `signal`.

## Wait requests

Call `wait_for` with a `request`:

```json
{
  "request": {
    "agents": ["reviewer-a-id", "reviewer-b-id", "reviewer-c-id"],
    "mode": "quorum",
    "quorum": 2,
    "wake_on": [
      {"kind": "signal", "key": "review-42/blocker", "state": "blocked"}
    ],
    "timeout": "2h"
  }
}
```

Use native worker IDs from the selected host. Codex task paths such as
`/root/reviewer_a` also work when accompanied by the parent `session` supplied
in its startup context. `list_agents` is available for discovery and
troubleshooting.

`mode` is `all` by default, or `any`, or `quorum` with a count. Conditions in
`targets` count toward the same threshold as `agents`. Conditions in `wake_on`
return early independently of that threshold.

### Request fields

| Field | Meaning / default |
| --- | --- |
| `agents` | Worker handles; shorthand for agent conditions; default `[]` |
| `provider` | `codex`, `claude`, or `cursor`; defaults to the configured host (standalone: `codex`) |
| `session` | Parent session for unscoped agent conditions in the selected provider; required for Codex task paths |
| `targets` | Explicit conditions; default `[]`; at least one agent or target is required |
| `mode` | `all` (default), `any`, or `quorum` |
| `quorum` | Required positive count for `quorum` mode only; no greater than the number of agents and targets |
| `wake_on` | Conditions that interrupt the wait; default `[]` |
| `timeout` | String containing positive seconds or a duration such as `30s`, `4m30s`, `2h`, `1d`; default `10m`; `ms` also supported |
| `deadline` | Timezone-qualified timestamp overriding `timeout`; continuation preserves it |
| `interval` | Seconds between probes, as a number or duration such as `3s`; default `1`, minimum `0.05`; increase for remote checks |
| `details` | Include raw observations; default `false` |

Unknown fields and duplicate main targets are rejected. Every condition accepts
an optional `label` for readable results. The same condition types work in
`targets` and `wake_on`.

### Conditions

| Condition | Matches when |
| --- | --- |
| `agent` | A lifecycle hook observes a requested worker state |
| `signal` | A task-specific event is published |
| `file` | A path exists, is missing, changes, or contains literal text |
| `http` | A GET returns the requested status code |
| `command` | An observational command returns the requested exit code |

| Kind | Required fields | Optional fields |
| --- | --- | --- |
| `agent` | `provider`, `id` | `session`; `states` defaults to `["stopped", "error", "aborted"]` (`running` also supported); `after` defaults to `0` |
| `signal` | `key` | `state` (any if omitted); `after` defaults to `0` |
| `file` | `path` | `event`: `exists` (default), `missing`, `changed`, or `contains`; `text` required for `contains`; `since` is a continuation baseline, not needed for new waits |
| `http` | `url` (`http://` or `https://`) | `status` defaults to `200`; redirects are not followed |
| `command` | Nonempty `argv` array | `cwd`; `exit_code` defaults to `0`; positive `probe_timeout` defaults to `10` seconds |

`after` is a nonnegative event cursor: only later observations match. File
`contains` uses literal UTF-8 text; `changed` compares file metadata against the
initial observation or preserved `since` baseline.

For example, wait for a healthy service and a ready log, but wake on a blocker:

```json
{
  "request": {
    "targets": [
      {"kind": "http", "url": "http://localhost:8080/health", "status": 200},
      {"kind": "file", "path": "/workspace/server.log", "event": "contains", "text": "Ready"}
    ],
    "wake_on": [{"kind": "file", "path": "/workspace/blocker.json"}],
    "timeout": "10m"
  }
}
```

Checks run concurrently. Command probes take an argument array and run without
a shell. They repeat, so use them only for observational checks. `interval`
controls polling and defaults to one second. Prefer absolute paths when the MCP
server's working directory may differ from the caller's.

### Agent handles and hooks

Install hooks before spawning workers. Codex task paths become resolvable
when a stop hook supplies worker metadata; native UUIDs need no extra session
scope. The Codex CLI fills the session from `CODEX_THREAD_ID` when available.
Cursor stop hooks sometimes lack enough identity: use a unique outcome signal
for indistinguishable concurrent assignments.

An agent ID with no lifecycle observation gets five seconds for hook delivery,
then becomes `unobserved`. If the required target count cannot be reached without
it, or it is a wake condition, the wait returns `error` with no continuation.
`any` and quorum waits continue when other targets can still satisfy them.
Check the ID, hook trust, and shared database, or use native waiting. A shorter request can
still time out first. This is not evidence that the worker failed or finished.
Codex task paths can remain pending while a worker in the specified parent is
running, because their aliases are learned at stop. Already observed workers
can run for the full requested deadline.

### Other MCP tools

`list_agents(provider, session?)` returns `agents` and `limit` (50) for recent
hook-observed workers. Use it for discovery or troubleshooting, not before every
wait. `provider` is required: `codex`, `claude`, or `cursor`. The `health` field
reports the shared database, observation count, and latest observation time.

`signal(key, state="ready", data=null)` publishes an event and returns its `seq`
cursor. The key and state must be nonempty strings; optional data is a JSON object.
Use task-specific keys: existing matching signals count unless `after` excludes
them.

Publish an explicit checkpoint or blocker through the `signal` MCP tool or CLI:

```sh
superwait signal review-42/blocker \
  --state blocked \
  --data '{"reason":"missing fixture"}'
```

## Results and continuation

The result includes:

- `status`: `matched`, `interrupted`, `timed_out`, or `error`
- `reason`: why the wait ended
- `ready`: completed reports
- `pending`: remaining work or condition errors
- `triggered`: early wake conditions that fired
- `progress`: counts of `ready`, `required`, and `total` conditions
- `deadline`: the absolute deadline; `elapsed_seconds`: time spent waiting
- `continue_wait`: a ready-to-use request for remaining work, or `null`

Reports include the observed state, available worker result, and event cursor
where applicable. `details: true` adds `checks` and raw `targets` / `wake_on`
observations. An early wake condition wins if it and the main threshold match
in the same check.

Pass `continue_wait` back as the next request to resume. It preserves the
deadline, remaining count, and file-change baselines, and advances past
delivered event signals. Persistent conditions such as an existing blocker
file must clear or be removed before continuing. After a quorum is reached,
continuation waits for all remaining work.

A stopped worker response does not prove its assignment succeeded. Read its
report; another host hook may continue that worker. For a resumed worker, use
an `agent` target with `after` set to its last returned cursor. Unobserved IDs
return the setup diagnostic described above; they never count as completed.

For a new response from a resumed worker:

```json
{
  "request": {
    "session": "parent-session-from-startup-context",
    "targets": [{"kind": "agent", "provider": "codex", "id": "/root/reviewer_a", "after": 42}],
    "timeout": "2h"
  }
}
```

## Long waits and the CLI

Setup configures a per-server MCP timeout of 24 hours plus 30 seconds for Codex
and Claude Code. Change the ceiling with `setup --max-wait 3d`. Each request
still has its own `timeout` or timezone-qualified `deadline`. An absolute
deadline survives continuation; a timed-out continuation remains expired.
Choose the actual task deadline rather than repeatedly issuing short waits.
Use `wake_on` for blockers that should interrupt it.

In Codex code mode, start `functions.exec` with
`// @exec: {"yield_time_ms":60000}`. If it yields, resume that cell with
`functions.wait` and `yield_time_ms: 60000`. This avoids unnecessary model
turns collecting an unfinished tool call.
The wrapper still requires model interactions when it yields; superwait cannot
remove the host's execution-cell limit. Prefer a direct MCP call when exposed.
Native terminal/process handles still require the native collection tool.

For long Cursor waits, or waits beyond a host's configured MCP timeout, run the
same request through the host's background terminal:

```sh
superwait wait --request wait.json --output result.json
```

`wait.json` contains the request itself without the MCP `request` wrapper. The
CLI prints one final JSON result and atomically saves the optional output. It
exits 0 for matched or interrupted, 124 for timeout, 130 for Ctrl-C, and 2 for
an invalid request or target ambiguity. The CLI does not background itself.

Cancellation stops the wait and probes it launched. It does not cancel the
workers being observed. The process and machine must remain alive.

### CLI commands

Global options go before the command: `--db PATH` selects the shared store;
`--provider codex|claude|cursor` selects the default host for agent shorthand.

| Command | Options / behavior |
| --- | --- |
| `wait` | `--request FILE` (default `-` reads stdin); `--timeout DURATION` overrides duration and clears a supplied deadline; `--output FILE` saves the result |
| `signal KEY` | `--state STATE` (default `ready`); `--data JSON` (default `{}`) |
| `agents PROVIDER` | Optional `--session SESSION`; lists up to 50 recent workers |
| `doctor PROVIDER` | Optional `--session SESSION`; shows the database, package version, lifecycle-event count, and latest event time; exit 1 if none have been observed |
| `setup PROVIDER` | Host-wide by default; `--project PATH` installs for one repository; `--max-wait DURATION` defaults to `24h` |
| `serve` | Runs the MCP server over stdio |
| `hook PROVIDER` | Lifecycle recorder used by setup; reads the host's JSON event from stdin |
| `prune` | `--days NUMBER` deletes older observations; positive number, default `30` |

Setup installs `SKILL.md` and this reference together, preserving unrelated
host settings and saving originals of changed files as `.superwait-backup`.
Restart the host and review its MCP/hooks trust prompts after setup.

### Verify agent recording

In Codex, open `/hooks` and review/trust the superwait hooks. Spawning a new
worker must produce observations in the same database the MCP server reads:

```sh
superwait doctor codex
superwait agents codex
```

`doctor` reports `no_observations` for an empty or wrong-scoped store. `observed`
means events exist, not that the currently running host loaded the latest setup.
Check `last_event_at` (Unix seconds) and a new worker's final report. Existing
sessions may retain their old hooks until restarted or resumed with fresh config.
File, HTTP, command and signal waits do not require agent hooks. Setup never
approves hooks or bypasses host trust. If the MCP tool is absent, use the CLI
command installed at the end of this reference, then reload the host.

## Local state

Hooks and waiters share a local SQLite database, defaulting to
`$XDG_STATE_HOME/superwait/events.sqlite3` or
`~/.local/state/superwait/events.sqlite3`. Set `SUPERWAIT_DB` or pass `--db` to
use another store. Stored observations include worker IDs, report excerpts,
and available transcript paths.

```sh
superwait prune --days 30
```

The Codex task-name adapter reads only the metadata header of the exact worker
transcript supplied by its hook. It does not scan conversation history. Network
requests occur only for explicitly requested HTTP or command checks.
