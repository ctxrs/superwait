---
name: superwait
description: Wait for agents and external conditions together, with any/all/quorum, early wake conditions, long deadlines, and useful partial results.
---

# Superwait

Express when you want to wake. You choose the workflow; native tools remain
available for spawning, steering, or collecting additional results.

## One request

Call wait_for with a request. For example, wait for two reviewers, wake early
on a blocker, and stop after two hours:

~~~json
{
  "agents": ["/root/reviewer_a", "/root/reviewer_b", "/root/reviewer_c"],
  "session": "parent-session-from-startup-context",
  "mode": "quorum",
  "quorum": 2,
  "wake_on": [{"kind": "signal", "key": "review-42/blocker", "state": "blocked", "label": "Review blocked"}],
  "timeout": "2h"
}
~~~

agents accepts native IDs and Codex task paths directly. The installed host
is the default provider; set provider for a different host. Hooks must be
installed before spawning. Codex paths become resolvable when its stop hook
supplies the worker metadata. Codex task paths require the parent session from
the SessionStart context supplied by setup; this prevents matching an old
conversation with the same names. The Codex CLI fallback fills it from the
calling thread's environment. Native UUIDs need no extra scope.
list_agents is available for discovery or troubleshooting, not a prerequisite.

mode is all by default, or any, or quorum with a count. Add targets
for other conditions; they and agents count toward the same threshold.
wake_on returns early independently of that threshold. Add label to any
condition to make its meaning explicit in the result.

~~~json
{"kind":"file","path":"/workspace/ci-failed.json","label":"CI failed"}
{"kind":"http","url":"http://localhost:8080/health","status":200}
{"kind":"signal","key":"build-42","state":"ready"}
{"kind":"file","path":"/workspace/server.log","event":"contains","text":"Ready"}
{"kind":"command","argv":["python3","/workspace/check_ci.py"],"exit_code":0}
~~~

Files support exists (default), missing, changed, and literal contains.
HTTP matches the status code. Command probes run repeatedly without a shell;
use an observational check, not the build/deploy itself. Prefer absolute paths.
interval controls polling, default 1 second; increase it for remote services.

## Use the result

- status: matched, interrupted, timed_out, or error.
- reason and triggered: why the wait ended and which early condition fired.
- ready: observed results and event cursors; pending: remaining work/errors.
- continue_wait: a ready-to-use request for the remaining work, or null.

After handling an interruption, pass continue_wait back as the next request
if you want to continue. It retains the absolute deadline and file-change
baselines, carries the remaining quorum, and consumes delivered event signals.
Persistent conditions such as an existing blocker file must clear or be removed
from wake_on before proceeding. After reaching a quorum, continuation waits
for all remaining work. It does not repeat reports you already received.

timeout accepts 30s, 10m, 2h, or 1d. A timezone-qualified deadline
overrides it. A timed-out continuation remains expired; choose a new deadline
explicitly if you want more time. For a resumed worker, use an agent target
with after set to its last returned cursor to await a new response:

~~~json
{"session":"parent-session","targets":[{"kind":"agent","provider":"codex","id":"/root/reviewer_a","after":42}],"timeout":"2h"}
~~~

A stopped response is not proof the assignment passed; read its result. Another
stop hook can continue a worker. Unknown workers stay pending. Cursor stop
hooks sometimes lack enough identity: use an explicit unique outcome signal
for indistinguishable concurrent assignments. Signal keys should identify the
particular task; existing matching signals count unless after excludes them.
Use signal(key, state, data) for explicit checkpoints and outcomes.

## Long waits and cancellation

Setup configures Codex/Claude's MCP timeout for 24 hours by default. For waits
beyond the configured host limit, or long Cursor waits, use the same request
with superwait wait --request wait.json --output result.json in the host's
background terminal. The CLI emits one final result; normal host notifications
or native collection retrieve it. It does not wake a closed conversation.

Cancel the MCP call or send Ctrl-C to the CLI to stop waiting. The observed
workers continue; only the wait's own probes are cleaned up. The process and
machine must stay alive. CLI exits: 0 matched/interrupted, 124 timeout,
130 cancelled, 2 error. details: true adds raw observations for diagnosis.
