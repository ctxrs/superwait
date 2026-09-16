# Contributing

Use Python 3.11+ and uv. From a checkout:

```sh
uv sync --locked
uv run --locked pytest
uv build
```

The tests use temporary databases, synthetic lifecycle events, local HTTP
endpoints, subprocesses, and a real stdio MCP client. Keep them independent of
personal agent accounts, configuration, and conversation history.

For integration changes, include a focused regression and state which host
versions were actually exercised. Keep timeout, cancellation, and partial-result
behavior explicit. Adapter tests alone do not establish live host compatibility.

Submit changes through pull requests. Describe the observable behavior and how
you checked it. Avoid adding a service or framework when the existing wait
engine, host feature, or standard library is sufficient.
