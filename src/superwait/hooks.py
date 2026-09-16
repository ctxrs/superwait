"""Translate documented host lifecycle payloads into local observations."""

import hashlib
import json
from pathlib import Path

from .store import Store


def task_key(payload):
    value = [payload.get("task"), payload.get("subagent_type")]
    return hashlib.sha256(json.dumps(value, ensure_ascii=False).encode()).hexdigest()


def codex_aliases(payload):
    """Read only the metadata header of the exact transcript supplied by Codex.

    Codex 0.153 exposes task paths in session metadata but not lifecycle hooks.
    UUID and parent checks prevent correlating a different worker's transcript.
    No history scan, body parsing, model call, or host-state mutation is needed.
    """
    path = payload.get("agent_transcript_path")
    if not path:
        return []
    try:
        with Path(path).open("rb") as f:
            line = f.readline()
        record = json.loads(line)
        meta = record.get("payload", {})
        source = meta.get("source", {})
        spawn = source.get("subagent", {}).get("thread_spawn", {}) if isinstance(source, dict) else {}
        if (record.get("type") != "session_meta" or meta.get("id") != payload.get("agent_id")
                or spawn.get("parent_thread_id") != payload.get("session_id")):
            return []
        path = spawn.get("agent_path")
        return [path] if isinstance(path, str) and path else []
    except (OSError, ValueError, AttributeError):
        return []


def record_hook(provider: str, payload: dict, store: Store):
    if not isinstance(payload, dict):
        raise ValueError("hook payload must be a JSON object")
    event = payload.get("hook_event_name", "")
    name = event.lower()
    session = payload.get("session_id") or ""
    if provider == "codex" and name == "sessionstart" and session:
        return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext":
            f"Superwait parent session: {session}. When waiting on Codex task paths such as /root/reviewer, "
            "set request.session to this value. Native UUIDs do not require it."}}
    agent = payload.get("agent_id")
    data = {"agent_type": payload.get("agent_type", ""), "source": event}
    state = None
    if provider == "cursor":
        session = payload.get("parent_conversation_id") or payload.get("conversation_id") or session
        agent = payload.get("subagent_id")
        data["agent_type"] = payload.get("subagent_type", "")
        data["task_key"] = task_key(payload)
        if name == "subagentstop" and not agent:
            # Cursor's documented stop payload omits the start ID. Never guess
            # between concurrent identical tasks, even when one finished first.
            candidates = [a for a in store.agents("cursor", session, None)
                          if a["state"] == "running" and a["data"].get("task_key") == data["task_key"]]
            if payload.get("task") and len(candidates) == 1:
                agent = candidates[0]["key"]
            else:
                store.emit("diagnostic", "cursor-unmatched-stop", "unmatched", provider="cursor", session=session,
                           data={"reason": "stop event has no unambiguous subagent ID; use an explicit signal", "candidates": len(candidates)})
                return {}
        summary = str(payload.get("summary") or "")
        data["summary"] = summary[:4096]
        data["summary_truncated"] = len(summary) > 4096
        if path := payload.get("agent_transcript_path"):
            data["transcript_path"] = path
        state = {"completed": "stopped", "error": "error", "aborted": "aborted"}.get(payload.get("status"))
    else:
        summary = str(payload.get("last_assistant_message") or "")
        data["summary"] = summary[:4096]
        data["summary_truncated"] = len(summary) > 4096
        if path := payload.get("agent_transcript_path"):
            data["transcript_path"] = path
    if name == "subagentstart":
        state = "running"
    elif name == "subagentstop":
        state = state or "stopped"
    elif name == "posttooluse" and payload.get("tool_name") == "SubagentHandback" and agent:
        # A delivered report is not itself a completion event.
        prior = store.latest("agent", agent, provider=provider, session=session)
        state = prior["state"] if prior else "running"
        report = str(payload.get("tool_input", {}).get("message") or "")
        data = {**(prior["data"] if prior else {}), "report": report[:8192], "report_truncated": len(report) > 8192}
    else:
        return {}
    if not session or not agent:
        raise ValueError("lifecycle event is missing its session or agent ID")
    prior = store.latest("agent", agent, provider=provider, session=session)
    if provider == "codex":
        data["aliases"] = codex_aliases(payload) or (prior["data"].get("aliases", []) if prior else [])
    if name != "subagentstart" and prior:
        data = {**prior["data"], **data}
    store.emit("agent", agent, state, provider=provider, session=session, data=data)
    return {"permission": "allow"} if provider == "cursor" and name == "subagentstart" else {}
