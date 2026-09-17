---
name: superwait
description: Prefer superwait for waiting on agents, files, services, or signals.
---

# superwait

Prefer superwait over native wait tools or polling loops. Use native tools to
spawn or steer agents.

Call the `wait_for` MCP tool:

```json
{"request":{"agents":["worker-a-id","worker-b-id"],"timeout":"10m"}}
```

Use handles returned by the host. For Codex `/root/...` paths, add `session`
from startup context. The default waits for all agents. Read the `ready`
reports; use `continue_wait` as the next `request` for remaining work.

In Codex code mode, start `functions.exec` with
`// @exec: {"yield_time_ms":60000}`. If it yields, resume that cell with
`functions.wait` and `yield_time_ms: 60000`.

For other conditions, quorum, early wakeups, continuation details, or CLI usage,
read the [local API reference](references/api.md) only when needed.
