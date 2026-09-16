"""Thin MCP transport; the engine also runs directly as a CLI."""

from typing import Any, Literal

from mcp.server.mcpserver import Context, MCPServer

from .engine import wait_for as run_wait
from .models import WaitRequest
from .store import Store


def serve(store: Store, default_provider="codex"):
    server = MCPServer("superwait", instructions=(
        "Express a wait in one request: agents, any/all/quorum, early wake conditions, and deadline. "
        f"The agents shorthand defaults to {default_provider}. Use native IDs or Codex /root/task paths directly. "
        "Codex task paths require session from the SessionStart context to avoid matching another conversation. "
        "Results give ready reports, pending work, triggered conditions, and a copyable continue_wait request. "
        "list_agents is for discovery or troubleshooting; a stopped response is not proof its task passed. "
        "Unknown agents remain pending. signal reports checkpoints or blockers explicitly. "
        "For a wait longer than the host's MCP timeout, use the superwait CLI in its background terminal. "
        "Cancelling a wait never stops the agents it observes."
    ))

    @server.tool()
    async def wait_for(request: WaitRequest, ctx: Context) -> dict[str, Any]:
        """Wait for any/all/quorum targets or wake_on conditions, until a deadline.

        Use agents=[native_handle, ...] for workers; add targets for other conditions.
        A target is agent, signal, file, http, or command. Command probes execute
        repeatedly without a shell: use observational checks. File changed uses
        the state at call entry. timeout accepts 30s, 10m, 2h, 1d. An absolute
        deadline overrides timeout and preserves the deadline on retries.
        Returns matched/interrupted/timed_out/error, ready reports, pending work,
        and triggered conditions. Pass continue_wait back as request to continue
        remaining work with the same deadline. details=true adds raw observations.
        Protocol cancellation stops this wait and its own probes only.
        """
        async def progress(elapsed, duration):
            await ctx.report_progress(elapsed, duration)
        if request.provider is None:
            request = request.model_copy(update={"provider": default_provider})
        return await run_wait(request, store, progress)

    @server.tool()
    def list_agents(provider: Literal["codex", "claude", "cursor"], session: str | None = None) -> dict[str, Any]:
        """List up to 50 recent hook-observed agents and their event cursors.

        Specify session when known. Empty means no hooks have observed these
        agents, not that they finished. after=seq waits for a later observation.
        """
        return {"agents": store.agents(provider, session), "limit": 50}

    @server.tool()
    def signal(key: str, state: str = "ready", data: dict | None = None) -> dict[str, Any]:
        """Publish an explicit checkpoint or outcome to local waiting processes.

        Use a task-specific unique key shared with the waiting agent. This does
        not send a message, wake a closed conversation, or change an agent's work.
        """
        if not key or not state:
            raise ValueError("key and state cannot be empty")
        return {"seq": store.emit("signal", key, state, data=data)}

    server.run(transport="stdio")
