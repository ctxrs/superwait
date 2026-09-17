# Superwait reference

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

| Condition | Matches when |
| --- | --- |
| `agent` | A lifecycle hook observes a requested worker state |
| `signal` | A task-specific event is published |
| `file` | A path exists, is missing, changes, or contains literal text |
| `http` | A GET returns the requested status code |
| `command` | An observational command returns the requested exit code |

Checks run concurrently. Command probes take an argument array and run without
a shell. They repeat, so use them only for observational checks. `interval`
controls polling and defaults to one second. Prefer absolute paths when the MCP
server's working directory may differ from the caller's.

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
- `continue_wait`: a ready-to-use request for remaining work, or `null`

Pass `continue_wait` back as the next request to resume. It preserves the
deadline, remaining count, and file-change baselines, and advances past
delivered event signals. Persistent conditions such as an existing blocker
file must clear or be removed before continuing. After a quorum is reached,
continuation waits for all remaining work.

A stopped worker response does not prove its assignment succeeded. Read its
report; another host hook may continue that worker. For a resumed worker, use
an `agent` target with `after` set to its last returned cursor. Unknown workers
stay pending. Set `details` to `true` for raw observations.

## Long waits and the CLI

Setup configures a per-server MCP timeout of 24 hours plus 30 seconds for Codex
and Claude Code. Change the ceiling with `setup --max-wait 3d`. Each request
still has its own `timeout` or timezone-qualified `deadline`. An absolute
deadline survives continuation; a timed-out continuation remains expired.

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

## Install from source

To install the current source instead of the PyPI release:

```sh
uv tool install 'git+https://github.com/ctxrs/superwait'
```

## Python and package installation

[uv](https://docs.astral.sh/uv/getting-started/installation/) is the recommended
installer. It creates a persistent, isolated tool environment and can download
Python automatically. You do not need to set up Python or a virtual environment
yourself. To explicitly select a runtime for the published package:

```sh
uv tool install --python 3.12 superwait
```

The [README](../README.md#install) uses the draft archive while host-wide setup
is unreleased. The command above installs PyPI 0.2.0, whose setup still requires
`--project`.

If you already manage Python 3.11+ with pipx, `pipx install superwait` is another
option. Keep whichever tool environment you install: the generated MCP and hook
commands point at its Python executable. A temporary `uvx` run is not the
recommended setup path because its cached environment can be removed.

If `superwait` is not found after installation, follow uv's printed PATH
instructions or run `uv tool update-shell` and open a new terminal. Setup itself
does not edit shell profiles.

Sources: [uv tool environments](https://docs.astral.sh/uv/guides/tools/),
[automatic Python downloads](https://docs.astral.sh/uv/guides/install-python/#automatic-python-downloads).
