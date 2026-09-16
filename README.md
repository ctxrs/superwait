# Superwait

Conditional waiting for coding agents. Wait for any, all, or a count of workers
and other conditions, wake early on a blocker, and keep one deadline across
interruptions.

Superwait provides an MCP tool and CLI, with setup for Codex, Claude Code, and
Cursor. The agent chooses the workflow; local code checks the conditions.
There are no model calls inside the wait.

## Install

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```sh
uv tool install 'git+https://github.com/ctxrs/superwait'
superwait setup codex --project /path/to/project
# Other hosts:
superwait setup claude --project /path/to/project
superwait setup cursor --project /path/to/project
```

Setup installs project-local MCP settings, lifecycle hooks, and agent
instructions. It preserves unrelated settings and backs up changed files as
`.superwait-backup`. Restart the host and accept its normal trust prompts.
Install hooks before spawning workers, and keep the Python tool environment
installed. See [host integration](docs/hosts.md) for details.

Linux is tested. Codex has live integration coverage; Claude Code and Cursor
have adapter tests and still need full live workflow qualification. Other
operating systems and multi-hour host waits remain unqualified.

## Express the wait

Call the MCP tool `wait_for` with a `request`. For example, wait for two of three
reviewers, or return early when an explicit blocker signal arrives:

```json
{
  "request": {
    "agents": ["reviewer-a-id", "reviewer-b-id", "reviewer-c-id"],
    "mode": "quorum",
    "quorum": 2,
    "wake_on": [{"kind": "signal", "key": "review-42/blocker", "state": "blocked"}],
    "timeout": "2h"
  }
}
```

Use the native worker IDs from your host. The provider defaults to the host
selected during setup. Codex task paths such as `/root/reviewer_a` also work
when accompanied by the parent `session` supplied in its startup context.
`list_agents` is available for discovery and troubleshooting.

`mode` is `all` by default, or `any`, or `quorum` with a count. Add `targets` for
other conditions; they count toward the same threshold as `agents`.

| Condition | Matches when |
| --- | --- |
| `agent` | A lifecycle hook observes a requested worker state |
| `signal` | A task-specific event is published |
| `file` | A path exists, is missing, changes, or contains literal text |
| `http` | A GET returns the requested status code |
| `command` | An observational command returns the requested exit code |

Checks run concurrently. Command probes take an argument array and run without
a shell; use an observational check because it repeats. `interval` controls
polling, with a default of one second. Use absolute paths when the MCP server's
working directory may differ from yours.

Publish an explicit checkpoint or blocker through the `signal` MCP tool or CLI:

```sh
superwait signal review-42/blocker --state blocked --data '{"reason":"missing fixture"}'
```

## Act on the result

The result includes `status`, `reason`, completed reports in `ready`, remaining
work in `pending`, and early wake conditions in `triggered`.

Pass the returned `continue_wait` object back as the next request to continue.
It preserves the deadline, remaining count, and file-change baselines, and
advances past delivered event signals. Persistent conditions such as an existing
blocker file must clear or be removed before continuing. After a quorum has
already been reached, continuation waits for all remaining work.

A stopped response does not prove an assignment succeeded. Read its report;
another host hook may continue that worker. For a resumed worker, use an `agent`
target with `after` set to its last returned `cursor`. Unknown workers stay
pending. `details: true` adds raw observations for troubleshooting.

## Long waits and the CLI

Setup configures a per-server MCP timeout of 24 hours plus 30 seconds for Codex
and Claude Code. `setup --max-wait 3d` changes that ceiling. Each request still
has its own timeout or timezone-qualified `deadline`. An absolute deadline
survives continuation; a timed-out continuation remains expired.

For long Cursor waits, or waits beyond the host's configured MCP timeout, run
the same engine through the host's background terminal:

```sh
superwait wait --request wait.json --output result.json
```

`wait.json` contains the request itself, without the MCP `request` wrapper.
The CLI prints one final JSON result and atomically saves the optional output.
It exits 0 for matched/early wake, 124 for timeout, 130 for Ctrl-C, and 2 for an
invalid request or target ambiguity. Native completion notifications or terminal
collection retrieve the result; the CLI does not background itself.

Cancellation stops the wait and any probes it launched. It never cancels the
workers being observed. The machine and process must remain alive.

## Local state

Hooks and waiters share a local SQLite database, defaulting to
`$XDG_STATE_HOME/superwait/events.sqlite3` or
`~/.local/state/superwait/events.sqlite3`. Set `SUPERWAIT_DB` or `--db` to use
another store. Stored observations include worker IDs, report excerpts, and
available transcript paths. `superwait prune --days 30` removes old observations.

The Codex task-name adapter reads only the metadata header of the exact worker
transcript supplied by its hook. It does not scan conversation history.
Network requests occur only for requested HTTP or command checks.

See [contributing](CONTRIBUTING.md) for local development. Licensed under [MIT](LICENSE).
