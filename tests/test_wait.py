import asyncio
import json
import os
import sys
import time
from pathlib import Path

import pytest
from pydantic import ValidationError

from superwait.engine import wait_for
from superwait.hooks import record_hook
from superwait.models import WaitRequest
from superwait.store import Store


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "events.sqlite3")


def request(targets, **kwargs):
    return WaitRequest(targets=targets, timeout="1s", interval=.05, **kwargs)


def agent(provider, id, **kwargs):
    return {"kind": "agent", "provider": provider, "id": id, **kwargs}


def lifecycle(store, provider, id, event="SubagentStart", **kwargs):
    return record_hook(provider, {"hook_event_name": event, "session_id": "parent", "agent_id": id, **kwargs}, store)


async def test_all_subagents_finish_with_reports_and_no_repeated_client_calls(store):
    for provider, id in [("codex", "a"), ("claude", "b")]:
        lifecycle(store, provider, id)
    task = asyncio.create_task(wait_for(request([agent("codex", "a"), agent("claude", "b")]), store))
    await asyncio.sleep(.08)
    lifecycle(store, "codex", "a", "SubagentStop", last_assistant_message="Found one bug")
    await asyncio.sleep(.08)
    assert not task.done()
    lifecycle(store, "claude", "b", "SubagentStop", last_assistant_message="No findings")
    result = await task
    assert result["status"] == "matched"
    assert {t["result"] for t in result["ready"]} == {"Found one bug", "No findings"}


async def test_wake_condition_interrupts_all_without_stopping_agents(store):
    lifecycle(store, "codex", "still-working")
    req = request([agent("codex", "still-working")], wake_on=[{"kind": "signal", "key": "run1/blocker", "state": "blocked"}])
    task = asyncio.create_task(wait_for(req, store))
    await asyncio.sleep(.08)
    store.emit("signal", "run1/blocker", "blocked", data={"reason": "need input"})
    result = await task
    assert result["status"] == "interrupted"
    assert result["pending"][0]["id"] == "still-working"
    assert store.latest("agent", "still-working", provider="codex")["state"] == "running"


async def test_quorum_and_timeout_partial_progress(store):
    store.emit("signal", "a", "ready")
    store.emit("signal", "b", "ready")
    targets = [{"kind": "signal", "key": x} for x in "abc"]
    assert (await wait_for(request(targets, mode="quorum", quorum=2), store))["status"] == "matched"
    req = WaitRequest(targets=targets, timeout="100ms", interval=.05)
    result = await wait_for(req, store)
    assert result["status"] == "timed_out"
    assert len(result["ready"]) == 2
    assert result["pending"][0]["key"] == "c"


async def test_resumed_agent_does_not_match_old_stop(store):
    lifecycle(store, "codex", "a", "SubagentStop")
    cursor = store.latest("agent", "a", provider="codex")["seq"]
    task = asyncio.create_task(wait_for(request([agent("codex", "a", after=cursor)]), store))
    await asyncio.sleep(.06)
    assert not task.done()
    lifecycle(store, "codex", "a")
    await asyncio.sleep(.06)
    assert not task.done()
    lifecycle(store, "codex", "a", "SubagentStop")
    assert (await task)["status"] == "matched"


async def test_unknown_or_ambiguous_agent_never_looks_finished(store):
    store.emit("agent", "same", "stopped", provider="claude", session="parent1")
    store.emit("agent", "same", "stopped", provider="claude", session="parent2")
    req = WaitRequest(targets=[agent("claude", "same"), agent("codex", "missing")], timeout="100ms", interval=.05)
    result = await wait_for(req, store)
    assert result["status"] == "error"
    assert result["pending"][0]["error"]
    assert result["pending"][1]["state"] == "unknown"
    assert (await wait_for(request([agent("claude", "same", session="parent2")]), store))["status"] == "matched"


async def test_any_does_not_wait_for_slow_probe(store):
    store.emit("signal", "done", "ready")
    slow = {"kind": "command", "argv": [sys.executable, "-c", "import time; time.sleep(30)"]}
    start = time.monotonic()
    result = await wait_for(request([slow, {"kind": "signal", "key": "done"}], mode="any"), store)
    assert result["status"] == "matched"
    assert time.monotonic() - start < 1


async def test_slow_probe_does_not_delay_new_wake_event(store):
    slow = {"kind": "command", "argv": [sys.executable, "-c", "import time; time.sleep(30)"]}
    task = asyncio.create_task(wait_for(request([slow], wake_on=[{"kind": "signal", "key": "abort"}]), store))
    await asyncio.sleep(.1)
    store.emit("signal", "abort", "ready")
    result = await asyncio.wait_for(task, .5)
    assert result["status"] == "interrupted"


async def test_signal_event_is_not_lost_between_polls(store):
    store.emit("signal", "checkpoint", "blocked")
    store.emit("signal", "checkpoint", "ready")
    req = request([{"kind": "signal", "key": "checkpoint", "state": "blocked"}])
    assert (await wait_for(req, store))["status"] == "matched"


async def test_expired_absolute_deadline_does_not_start_a_probe(store, tmp_path):
    path = tmp_path / "should-not-exist"
    req = WaitRequest(targets=[{"kind": "command", "argv": [sys.executable, "-c", f"open({str(path)!r}, 'w').close()"]}],
                      deadline="2000-01-01T00:00:00Z")
    result = await wait_for(req, store)
    assert result["status"] == "timed_out"
    assert not path.exists()


async def test_deadline_includes_hung_probe_and_kills_its_child(store, tmp_path):
    pidfile = tmp_path / "pid"
    script = "import os,time,pathlib; pathlib.Path(os.environ['PROBE_PID_FILE']).write_text(str(os.getpid())); time.sleep(30)"
    old = os.environ.get("PROBE_PID_FILE")
    os.environ["PROBE_PID_FILE"] = str(pidfile)
    try:
        req = WaitRequest(targets=[{"kind": "command", "argv": [sys.executable, "-c", script]}], timeout="250ms")
        start = time.monotonic()
        result = await wait_for(req, store)
        assert result["status"] == "timed_out"
        assert time.monotonic() - start < 1
        if os.name == "posix":
            with pytest.raises(ProcessLookupError):
                os.kill(int(pidfile.read_text()), 0)
    finally:
        if old is None:
            os.environ.pop("PROBE_PID_FILE", None)
        else:
            os.environ["PROBE_PID_FILE"] = old


async def test_cancellation_does_not_stop_observed_work(store):
    lifecycle(store, "codex", "a")
    task = asyncio.create_task(wait_for(request([agent("codex", "a")]), store))
    await asyncio.sleep(.06)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert store.latest("agent", "a", provider="codex")["state"] == "running"


async def test_file_change_observed_after_entry(store, tmp_path):
    path = tmp_path / "result"
    path.write_text("old")
    task = asyncio.create_task(wait_for(request([{"kind": "file", "path": str(path), "event": "changed"}]), store))
    await asyncio.sleep(.08)
    assert not task.done()
    path.write_text("new content")
    assert (await task)["status"] == "matched"


async def test_file_contains_across_read_boundary_in_large_log(store, tmp_path):
    path = tmp_path / "log"
    path.write_bytes(b"x" * (65536 * 20 - 3) + "ready ✓".encode() + b"y" * 100)
    result = await wait_for(request([{"kind": "file", "path": str(path), "event": "contains", "text": "ready ✓"}]), store)
    assert result["status"] == "matched"


async def test_http_recovers_from_not_ready(store):
    ready = False
    async def handle(reader, writer):
        await reader.readuntil(b"\r\n\r\n")
        writer.write(("HTTP/1.1 200 OK" if ready else "HTTP/1.1 503 Unavailable").encode() + b"\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        await writer.drain()
        writer.close()
        await writer.wait_closed()
    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        task = asyncio.create_task(wait_for(request([{"kind": "http", "url": f"http://127.0.0.1:{port}/health"}]), store))
        await asyncio.sleep(.08)
        assert not task.done()
        ready = True
        assert (await task)["ready"][0]["status_code"] == 200
    finally:
        server.close()
        await server.wait_closed()


async def test_command_output_bounded_and_nonzero_is_pending(store):
    req = request([{"kind": "command", "argv": [sys.executable, "-c", "print('x'*100000); raise SystemExit(4)"], "exit_code": 4}])
    result = await wait_for(req, store)
    assert result["status"] == "matched"
    assert len(result["ready"][0]["stdout"]) <= 4096


def test_invalid_request_rejected_before_any_work():
    with pytest.raises(ValidationError):
        WaitRequest(targets=[{"kind": "signal", "key": "a"}], mode="quorum", quorum=2)
    with pytest.raises(ValidationError):
        WaitRequest(targets=[{"kind": "file", "path": "/x", "event": "contains"}])
    with pytest.raises(ValidationError):
        WaitRequest(targets=[{"kind": "signal", "key": "a"}] * 2)
