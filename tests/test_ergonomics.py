import asyncio
import json
import os
import sys

import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters

from superwait.engine import wait_for
from superwait.hooks import record_hook
from superwait.models import WaitRequest
from superwait.store import Store


def codex_stop(store, root, uuid, handle, parent="session", result="reviewed"):
    transcript = root / (uuid + "-" + parent + ".jsonl")
    transcript.write_text(json.dumps({"type": "session_meta", "payload": {
        "id": uuid, "source": {"subagent": {"thread_spawn": {
            "parent_thread_id": parent, "agent_path": handle}}}}}) + "\nnot read as metadata\n")
    record_hook("codex", {"hook_event_name": "SubagentStop", "session_id": parent,
        "agent_id": uuid, "agent_transcript_path": str(transcript), "last_assistant_message": result}, store)
    return transcript


async def test_native_task_handle_resolves_when_worker_finishes_and_on_resume(tmp_path):
    store = Store(tmp_path / "events")
    task = asyncio.create_task(wait_for(WaitRequest(agents=["/root/reviewer"], session="session", timeout="2s", interval=.05), store))
    await asyncio.sleep(.05)
    assert not task.done()
    codex_stop(store, tmp_path, "uuid", "/root/reviewer", result="one finding")
    first = await task
    assert first["ready"][0]["result"] == "one finding"
    assert first["ready"][0]["handle"] == "/root/reviewer"
    assert not first["pending"]
    again = asyncio.create_task(wait_for(WaitRequest(targets=[{"kind": "agent", "provider": "codex",
        "id": "/root/reviewer", "after": first["ready"][0]["cursor"]}], session="session", timeout="2s", interval=.05), store))
    await asyncio.sleep(.06)
    assert not again.done()
    record_hook("codex", {"hook_event_name": "SubagentStart", "session_id": "session", "agent_id": "uuid"}, store)
    await asyncio.sleep(.06)
    assert not again.done()
    codex_stop(store, tmp_path, "uuid", "/root/reviewer", result="finding fixed")
    assert (await again)["ready"][0]["result"] == "finding fixed"


async def test_aliases_do_not_mix_sessions_or_count_one_worker_twice(tmp_path):
    store = Store(tmp_path / "events")
    codex_stop(store, tmp_path, "uuid-a", "/root/reviewer", parent="one")
    # An unscoped path must not match an old conversation, even before the
    # new worker has emitted any hook or a second alias mapping exists.
    old_only = await wait_for(WaitRequest(agents=["/root/reviewer"], timeout="1h"), store)
    assert old_only["status"] == "error"
    assert "session" in old_only["pending"][0]["error"]
    codex_stop(store, tmp_path, "uuid-b", "/root/reviewer", parent="two")
    ambiguous = await wait_for(WaitRequest(agents=["/root/reviewer"], timeout="1h"), store)
    assert ambiguous["status"] == "error"
    assert "session" in ambiguous["pending"][0]["error"]
    assert ambiguous["elapsed_seconds"] < 1
    scoped = await wait_for(WaitRequest(agents=["/root/reviewer"], session="two"), store)
    assert scoped["ready"][0]["id"] == "uuid-b"
    duplicate = await wait_for(WaitRequest(agents=["uuid-a", "/root/reviewer"], session="one",
        mode="quorum", quorum=2), store)
    assert duplicate["status"] == "error"
    assert "same agent" in duplicate["reason"]


def test_metadata_for_another_worker_cannot_assign_an_alias(tmp_path):
    store = Store(tmp_path / "events")
    transcript = codex_stop(store, tmp_path, "uuid-a", "/root/reviewer")
    record_hook("codex", {"hook_event_name": "SubagentStop", "session_id": "session", "agent_id": "uuid-b",
        "agent_transcript_path": str(transcript)}, store)
    assert not store.agent("uuid-b", "codex")["data"]["aliases"]
    assert store.agent("/root/reviewer", "codex", "session")["key"] == "uuid-a"


def test_session_context_does_not_approve_or_change_any_tool(tmp_path):
    output = record_hook("codex", {"hook_event_name": "SessionStart", "session_id": "parent-42"}, Store(tmp_path / "db"))
    data = output["hookSpecificOutput"]
    assert "parent-42" in data["additionalContext"]
    assert "permissionDecision" not in data
    assert "updatedInput" not in data


async def test_partial_quorum_and_copyable_continuation_keep_the_deadline(tmp_path):
    store = Store(tmp_path / "events")
    store.emit("signal", "a", "done", data={"report": "A ready"})
    store.emit("signal", "ci", "blocked")
    first = await wait_for(WaitRequest(targets=[{"kind": "signal", "key": k} for k in "abc"],
        mode="quorum", quorum=2, timeout="2s", interval=.05,
        wake_on=[{"kind": "signal", "key": "ci", "label": "CI failed", "state": "blocked"}]), store)
    assert first["status"] == "interrupted"
    assert first["triggered"][0]["label"] == "CI failed"
    assert first["ready"][0]["key"] == "a"
    assert {c["key"] for c in first["pending"]} == {"b", "c"}
    req = WaitRequest.model_validate(first["continue_wait"])
    assert req.deadline.isoformat() == first["deadline"]
    task = asyncio.create_task(wait_for(req, store))
    await asyncio.sleep(.06)
    assert not task.done(), "continuation must not repeat the already delivered blocker"
    store.emit("signal", "b", "done")
    second = await task
    assert second["status"] == "matched"
    assert [x["key"] for x in second["ready"]] == ["b"]
    assert [x["key"] for x in second["pending"]] == ["c"]
    assert second["deadline"] == first["deadline"]
    # Continuing after reaching quorum now waits for all remaining work.
    assert second["continue_wait"]["mode"] == "all"


async def test_continuation_observes_a_file_changed_between_calls(tmp_path):
    store = Store(tmp_path / "events")
    path = tmp_path / "result"
    path.write_text("old")
    store.emit("signal", "checkpoint", "ready")
    first = await wait_for(WaitRequest(targets=[{"kind": "file", "path": str(path), "event": "changed"}],
        wake_on=[{"kind": "signal", "key": "checkpoint"}], timeout="2s"), store)
    assert first["status"] == "interrupted"
    path.write_text("new data arrived while the agent was thinking")
    second = await wait_for(WaitRequest.model_validate(first["continue_wait"]), store)
    assert second["status"] == "matched"
    assert second["deadline"] == first["deadline"]


async def test_agent_wake_inherits_scope_and_continues_after_its_response(tmp_path):
    store = Store(tmp_path / "db")
    codex_stop(store, tmp_path, "uuid", "/root/blocker", parent="caller", result="needs input")
    first = await wait_for(WaitRequest(agents=["pending"], session="caller", timeout="2s", interval=.05,
        wake_on=[{"kind": "agent", "provider": "codex", "id": "/root/blocker"}]), store)
    assert first["status"] == "interrupted"
    assert first["triggered"][0]["result"] == "needs input"
    assert first["continue_wait"]["wake_on"][0]["session"] == "caller"
    again = asyncio.create_task(wait_for(WaitRequest.model_validate(first["continue_wait"]), store))
    await asyncio.sleep(.06)
    assert not again.done(), "the handled response must not trigger again"
    codex_stop(store, tmp_path, "uuid", "/root/blocker", parent="caller", result="another question")
    assert (await again)["triggered"][0]["result"] == "another question"


@pytest.mark.parametrize("report", ["", "full report"])
async def test_truncation_describes_the_report_actually_returned(tmp_path, report):
    store = Store(tmp_path / "db")
    store.emit("agent", "a", "stopped", provider="claude", session="s", data={
        "summary": "shortened", "summary_truncated": True, "report": report,
        "report_truncated": False, "transcript_path": "worker.jsonl"})
    value = (await wait_for(WaitRequest(agents=["a"], provider="claude"), store))["ready"][0]
    assert value["result"] == (report or "shortened")
    assert value.get("result_truncated", False) == (not report)


async def test_timeout_continuation_cannot_silently_extend_the_deadline(tmp_path):
    store = Store(tmp_path / "events")
    first = await wait_for(WaitRequest(agents=["unknown"], timeout="50ms"), store)
    assert first["status"] == "timed_out"
    second = await wait_for(WaitRequest.model_validate(first["continue_wait"]), store)
    assert second["status"] == "timed_out"
    assert second["deadline"] == first["deadline"]
    assert second["elapsed_seconds"] < .05


@pytest.mark.parametrize("winner", ["quorum", "blocker", "deadline"])
async def test_competing_conditions_in_both_orders_and_with_a_deadline(tmp_path, winner):
    store = Store(tmp_path / "events")
    store.emit("signal", "review-a", "ready")
    blocker = tmp_path / "ci.failed"
    req = WaitRequest(targets=[{"kind": "signal", "key": f"review-{k}"} for k in "abc"],
        mode="quorum", quorum=2, timeout="200ms", interval=.05,
        wake_on=[{"kind": "file", "path": str(blocker), "label": "CI failed"}])
    waiting = asyncio.create_task(wait_for(req, store))
    await asyncio.sleep(.06)
    if winner == "quorum":
        store.emit("signal", "review-b", "ready")
    elif winner == "blocker":
        blocker.write_text("build failed")
    value = await waiting
    assert value["status"] == {"quorum": "matched", "blocker": "interrupted", "deadline": "timed_out"}[winner]
    assert len(value["ready"]) == (2 if winner == "quorum" else 1)
    assert len(value["pending"]) == (1 if winner == "quorum" else 2)
    if winner == "blocker":
        assert value["triggered"][0]["label"] == "CI failed"
    else:
        assert value["triggered"] == []


async def test_cli_fills_task_path_scope_without_changing_uuid_or_explicit_scope(tmp_path):
    db = tmp_path / "db"
    store = Store(db)
    codex_stop(store, tmp_path, "uuid-a", "/root/reviewer", parent="caller")
    codex_stop(store, tmp_path, "uuid-b", "/root/reviewer", parent="other")
    for request, expected in [({"agents": ["/root/reviewer"]}, "uuid-a"),
                              ({"agents": ["/root/reviewer"], "session": "other"}, "uuid-b"),
                              ({"agents": ["uuid-b"]}, "uuid-b"),
                              ({"agents": ["pending"], "timeout": "2s", "wake_on": [
                                  {"kind": "agent", "provider": "codex", "id": "/root/reviewer"}]}, "uuid-a")]:
        proc = await asyncio.create_subprocess_exec(sys.executable, "-m", "superwait", "--db", str(db), "wait",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "CODEX_THREAD_ID": "caller"})
        out, err = await proc.communicate(json.dumps(request).encode())
        assert proc.returncode == 0, err.decode()
        value = json.loads(out)
        assert (value["triggered"] or value["ready"])[0]["id"] == expected


@pytest.mark.parametrize("provider", ["codex", "claude", "cursor"])
async def test_cli_null_provider_uses_the_installed_host(tmp_path, provider):
    db = tmp_path / "db"
    Store(db).emit("agent", "worker", "stopped", provider=provider, session="session", data={"summary": "done"})
    proc = await asyncio.create_subprocess_exec(sys.executable, "-m", "superwait", "--db", str(db),
        "--provider", provider, "wait", stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE)
    out, err = await proc.communicate(json.dumps({"agents": ["worker"], "provider": None, "timeout": "2s"}).encode())
    assert proc.returncode == 0, err.decode()
    assert json.loads(out)["ready"][0]["provider"] == provider


@pytest.mark.parametrize("provider", ["codex", "claude", "cursor"])
async def test_stdio_defaults_to_installed_host_and_returns_actionable_reports(tmp_path, provider):
    db = tmp_path / "db"
    Store(db).emit("agent", "worker", "stopped", provider=provider, session="session",
                   data={"summary": "Review complete: one finding"})
    params = StdioServerParameters(command=sys.executable, args=["-m", "superwait", "--db", str(db),
        "--provider", provider, "serve"], env=dict(os.environ))
    async with Client(params) as client:
        result = await client.call_tool("wait_for", {"request": {"agents": ["worker"], "timeout": "1s"}})
        assert not result.is_error
        value = result.structured_content
        assert value["status"] == "matched"
        assert value["ready"][0]["provider"] == provider
        assert value["ready"][0]["result"] == "Review complete: one finding"
        assert value["pending"] == []
        assert "targets" not in value
        detailed = await client.call_tool("wait_for", {"request": {"agents": ["worker"], "details": True}})
        assert detailed.structured_content["targets"][0]["event"]["key"] == "worker"
