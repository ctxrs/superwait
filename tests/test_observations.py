import asyncio
import json

import pytest
from pydantic import ValidationError

from superwait import engine
from superwait.cli import main
from superwait.engine import wait_for
from superwait.hooks import record_hook
from superwait.models import WaitRequest, seconds
from superwait.store import Store


@pytest.mark.parametrize("value,expected", [("4m30s", 270), ("1h 2m 3.5s", 3723.5),
    ("200ms", .2), (" 1.5 ", 1.5), ("0m1s", 1)])
def test_compound_durations(value, expected):
    assert seconds(value) == expected


@pytest.mark.parametrize("value", ["", "0s", "-1s", "1m nope", "1m30", "nan", "1e3s", "1..5s"])
def test_duration_rejects_malformed_or_nonpositive_input(value):
    with pytest.raises(ValueError):
        seconds(value)


@pytest.mark.parametrize("interval", [3, "3s", "3000ms"])
def test_interval_duration_uses_shared_request_schema(interval):
    request = WaitRequest(agents=["worker"], timeout="4m30s", interval=interval)
    assert request.interval == 3
    assert request.duration() == 270
    assert WaitRequest.model_validate_json(request.model_dump_json()).interval == 3


@pytest.mark.parametrize("interval", [None, True, {}, "0s", "1ms", float("inf"), float("nan")])
def test_interval_rejects_invalid_values(interval):
    with pytest.raises(ValidationError):
        WaitRequest(agents=["worker"], interval=interval)


async def test_missing_agent_errors_without_spending_the_whole_deadline(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "AGENT_OBSERVATION_GRACE", .1)
    result = await asyncio.wait_for(wait_for(WaitRequest(agents=["missing"], timeout="1h", interval=30),
                                           Store(tmp_path / "db")), 1)
    assert result["status"] == "error"
    assert result["pending"][0]["state"] == "unobserved"
    assert "doctor" in result["pending"][0]["error"]
    assert result["continue_wait"] is None


async def test_late_hook_recovers_during_grace_and_observed_worker_can_run_longer(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "AGENT_OBSERVATION_GRACE", .15)
    store = Store(tmp_path / "db")
    waiting = asyncio.create_task(wait_for(WaitRequest(agents=["worker"], timeout="2s", interval=.05), store))
    await asyncio.sleep(.06)
    store.emit("agent", "worker", "running", provider="codex", session="parent")
    await asyncio.sleep(.2)
    assert not waiting.done()
    store.emit("agent", "worker", "stopped", provider="codex", session="parent", data={"summary":"finished"})
    assert (await waiting)["ready"][0]["result"] == "finished"


async def test_task_path_waits_for_stop_mapping_while_parent_has_running_worker(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "AGENT_OBSERVATION_GRACE", .1)
    store = Store(tmp_path / "db")
    store.emit("agent", "uuid", "running", provider="codex", session="parent")
    waiting = asyncio.create_task(wait_for(WaitRequest(agents=["/root/reviewer"], session="parent",
                                                      timeout="2s", interval=.05), store))
    await asyncio.sleep(.2)
    assert not waiting.done()
    transcript = tmp_path / "worker.jsonl"
    transcript.write_text(json.dumps({"type":"session_meta","payload":{"id":"uuid",
        "source":{"subagent":{"thread_spawn":{"parent_thread_id":"parent","agent_path":"/root/reviewer"}}}}})+"\n")
    record_hook("codex", {"hook_event_name":"SubagentStop", "session_id":"parent", "agent_id":"uuid",
                          "agent_transcript_path":str(transcript),"last_assistant_message":"done"}, store)
    assert (await waiting)["ready"][0]["result"] == "done"


async def test_other_parent_cannot_keep_unknown_path_wait_alive(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "AGENT_OBSERVATION_GRACE", .1)
    store = Store(tmp_path / "db")
    store.emit("agent", "uuid", "running", provider="codex", session="other")
    result = await wait_for(WaitRequest(agents=["/root/reviewer"], session="parent", timeout="2s", interval=.05), store)
    assert result["status"] == "error"


async def test_unknown_agent_does_not_override_ready_quorum_or_wake(tmp_path):
    store = Store(tmp_path / "db")
    store.emit("signal", "ready", "ready")
    for request in [WaitRequest(agents=["unknown"], targets=[{"kind":"signal","key":"ready"}], mode="any"),
                    WaitRequest(agents=["unknown"], wake_on=[{"kind":"signal","key":"ready"}])]:
        assert (await wait_for(request, store))["status"] in ("matched", "interrupted")


def test_doctor_distinguishes_missing_scoped_observations(tmp_path, monkeypatch, capsys):
    db = tmp_path / "db"
    store = Store(db)
    for expected in (1, 0):
        monkeypatch.setattr("sys.argv", ["superwait", "--db", str(db), "doctor", "codex", "--session", "parent"])
        with pytest.raises(SystemExit) as exit:
            main()
        assert exit.value.code == expected
        result = json.loads(capsys.readouterr().out)
        assert result["agent_events"] == (0 if expected else 1)
        store.emit("agent", "worker", "running", provider="codex", session="parent")
    assert store.health("codex", "other")["status"] == "no_observations"
