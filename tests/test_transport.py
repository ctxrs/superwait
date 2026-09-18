import asyncio
import json
import os
import signal
import sys

import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters


async def command(db, *args, stdin=None):
    process = await asyncio.create_subprocess_exec(sys.executable, "-m", "superwait", "--db", str(db), *args,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await process.communicate(stdin)
    return process.returncode, out, err


async def test_cli_wait_gets_hook_from_separate_process(tmp_path):
    db = tmp_path / "db"
    req = tmp_path / "wait.json"
    req.write_text(json.dumps({"targets": [{"kind": "agent", "provider": "codex", "id": "worker"}], "timeout": "3s", "interval": .05}))
    task = asyncio.create_task(command(db, "wait", "--request", str(req)))
    await asyncio.sleep(.2)
    rc, out, err = await command(db, "hook", "codex", stdin=json.dumps({"hook_event_name": "SubagentStop", "session_id": "s", "agent_id": "worker", "last_assistant_message": "checked"}).encode())
    assert rc == 0, err.decode()
    assert json.loads(out) == {}
    rc, out, err = await task
    assert rc == 0, err.decode()
    assert json.loads(out)["status"] == "matched"


async def test_cli_timeout_and_cancellation(tmp_path):
    db = tmp_path / "db"
    req = json.dumps({"targets": [{"kind": "signal", "key": "missing"}], "timeout": "50ms"}).encode()
    rc, out, err = await command(db, "wait", stdin=req)
    assert rc == 124, err.decode()
    assert json.loads(out)["status"] == "timed_out"
    if os.name != "posix":
        return
    process = await asyncio.create_subprocess_exec(sys.executable, "-m", "superwait", "--db", str(db), "wait", "--timeout", "2h",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    process.stdin.write(req)
    await process.stdin.drain()
    process.stdin.close()
    await asyncio.sleep(.3)
    process.send_signal(signal.SIGINT)
    out, err = await process.communicate()
    assert process.returncode == 130, err.decode()
    assert json.loads(out)["status"] == "cancelled"


async def test_real_stdio_mcp_schema_wait_signal_and_cancellation(tmp_path):
    params = StdioServerParameters(command=sys.executable, args=["-m", "superwait", "--db", str(tmp_path / "db"), "serve"], env=dict(os.environ))
    async with Client(params) as client:
        tools = (await client.list_tools()).tools
        assert {t.name for t in tools} == {"wait_for", "list_agents", "signal"}
        wait = asyncio.create_task(client.call_tool("wait_for", {"request": {"targets": [{"kind": "signal", "key": "ready"}], "timeout": "3s", "interval": .05}}))
        await asyncio.sleep(.15)
        assert not wait.done()
        response = await client.call_tool("signal", {"key": "ready", "data": {"value": 42}})
        assert not response.is_error
        result = await wait
        assert not result.is_error, result
        assert result.structured_content["status"] == "matched"
        assert result.structured_content["ready"][0]["result"]["value"] == 42
        waiting = asyncio.create_task(client.call_tool("wait_for", {"request": {"targets": [{"kind": "signal", "key": "never"}], "timeout": "2h"}}))
        await asyncio.sleep(.1)
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        assert not (await client.call_tool("list_agents", {"provider": "codex"})).is_error


@pytest.mark.skipif(os.name != "posix", reason="POSIX process liveness check")
async def test_mcp_cancellation_reaps_its_command_probe(tmp_path):
    pidfile = tmp_path / "probe.pid"
    script = "import pathlib,os,time; pathlib.Path(__import__('sys').argv[1]).write_text(str(os.getpid())); time.sleep(30)"
    params = StdioServerParameters(command=sys.executable, args=["-m", "superwait", "--db", str(tmp_path / "db"), "serve"], env=dict(os.environ))
    async with Client(params) as client:
        task = asyncio.create_task(client.call_tool("wait_for", {"request": {"targets": [
            {"kind": "command", "argv": [sys.executable, "-c", script, str(pidfile)]}
        ], "timeout": "2h"}}))
        async with asyncio.timeout(3):
            while not pidfile.exists():
                await asyncio.sleep(.02)


async def test_mcp_duration_strings_and_missing_hook_diagnostic(tmp_path):
    params = StdioServerParameters(command=sys.executable, args=["-m", "superwait", "--db", str(tmp_path / "db"), "serve"], env=dict(os.environ))
    async with Client(params) as client:
        health = (await client.call_tool("list_agents", {"provider": "codex"})).structured_content["health"]
        assert health["status"] == "no_observations"
        assert health["agent_events"] == 0
        result = await asyncio.wait_for(client.call_tool("wait_for", {"request": {
            "agents": ["missing"], "timeout": "1m30s", "interval": "10s"}}), 8)
        assert not result.is_error
        assert result.structured_content["status"] == "error"
        assert result.structured_content["pending"][0]["state"] == "unobserved"
        assert result.structured_content["continue_wait"] is None
        pid = int(pidfile.read_text())
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        async with asyncio.timeout(3):
            while True:
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    break
                await asyncio.sleep(.02)
