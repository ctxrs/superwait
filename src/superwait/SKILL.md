---
name: superwait
description: Prefer superwait for waiting on agents, files, services, or signals.
---

# superwait

Prefer superwait for observable conditions. Use native tools to spawn or steer
agents, or when superwait reports unobserved workers. Don't retry missing hooks.

Call the `wait_for` MCP tool:

```json
{"request":{"agents":["worker-a-id","worker-b-id"],"timeout":"10m"}}
```

Use handles returned by the host. For Codex `/root/...` paths, add `session`
from startup context. The default waits for all agents. Read the `ready`
reports; use `continue_wait` as the next `request` for remaining work.
Choose the real task deadline, not repeated 30–60-second timeouts. An expired
continuation stays expired. Native process handles still need their native tools.

In Codex code mode, start `functions.exec` with
`// @exec: {"yield_time_ms":60000}`. If it yields, resume that cell with
`functions.wait` and `yield_time_ms: 60000`.
The wrapper can still wake the model; prefer a direct tool call when available.

For other conditions, quorum, early wakeups, continuation details, or CLI usage,
read the [local API reference](references/api.md) only when needed, including
the CLI fallback if the MCP tool is not exposed.
