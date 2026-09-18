"""Concurrent observations, monotonic deadlines, and cancellation."""

import asyncio
import os
import signal
import sqlite3
import stat
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx2 as httpx

from .models import Agent, Command, File, HTTP, Signal, WaitRequest
from .store import AmbiguousAgent, Store


AGENT_OBSERVATION_GRACE = 5.0


def file_stamp(path):
    try:
        stat = path.stat()
        return [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]
    except FileNotFoundError:
        return None


def baseline_for(target):
    if not isinstance(target, File) or target.event != "changed":
        return None
    if target.since is not None:
        return None if target.since == "missing" else target.since
    return file_stamp(Path(target.path).expanduser())


async def command_result(target):
    spawning = asyncio.create_task(asyncio.create_subprocess_exec(
        *target.argv, cwd=target.cwd, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, start_new_session=(os.name == "posix"),
    ))
    cancelled = False
    try:
        process = await asyncio.shield(spawning)
    except asyncio.CancelledError:
        # Obtain the handle even if another condition wins during process creation.
        process = await spawning
        cancelled = True

    async def tail(stream):
        value = b""
        while chunk := await stream.read(8192):
            value = (value + chunk)[-4096:]
        return value.decode(errors="replace")

    readers = [asyncio.create_task(tail(process.stdout)), asyncio.create_task(tail(process.stderr))]
    finished = False
    try:
        if cancelled:
            raise asyncio.CancelledError
        async with asyncio.timeout(target.probe_timeout):
            code = await process.wait()
            stdout, stderr = await asyncio.gather(*readers)
        finished = True
        return {"matched": code == target.exit_code, "exit_code": code, "stdout": stdout, "stderr": stderr}
    finally:
        # Only terminate probes we launched; never the agents or jobs being observed.
        if not finished:
            try:
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGKILL)
                elif process.returncode is None:
                    process.kill()
            except ProcessLookupError:
                pass
        await process.wait()
        for reader in readers:
            if not reader.done():
                reader.cancel()
        await asyncio.gather(*readers, return_exceptions=True)


async def observe(target, store, client, baseline=None):
    try:
        if isinstance(target, (Agent, Signal)):
            if isinstance(target, Agent):
                event = store.agent(target.id, target.provider, target.session)
                matched = event and event["state"] in target.states and event["seq"] > target.after
            else:
                event = store.signal(target.key, target.state, target.after)
                matched = bool(event)
            result = {"matched": bool(matched), "state": event["state"] if event else "unknown", "event": event}
            if not event and isinstance(target, Agent) and target.provider == "codex" and target.id.startswith("/"):
                # Codex task paths are learned at Stop, not Start. A live
                # worker in this parent can still supply the mapping.
                result["awaiting_alias"] = any(a["state"] == "running"
                    for a in store.agents("codex", target.session, None))
            return result
        if isinstance(target, File):
            path = Path(target.path).expanduser().absolute()
            stamp = file_stamp(path)
            if target.event == "contains":
                if stamp is None:
                    return {"matched": False, "state": "missing"}
                needle = target.text.encode()
                fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
                with os.fdopen(fd, "rb") as f:
                    if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
                        return {"matched": False, "error": "contains requires a regular file"}
                    overlap = b""
                    matched = False
                    while block := f.read(65536):
                        data = overlap + block
                        if needle in data:
                            matched = True
                            break
                        overlap = data[-(len(needle) - 1):] if len(needle) > 1 else b""
                        await asyncio.sleep(0)  # Large logs remain interruptible.
            else:
                matched = {"exists": stamp is not None, "missing": stamp is None, "changed": stamp != baseline}[target.event]
            return {"matched": matched, "path": str(path), "stamp": stamp}
        if isinstance(target, HTTP):
            async with client.stream("GET", target.url) as response:
                return {"matched": response.status_code == target.status, "status_code": response.status_code}
        if isinstance(target, Command):
            return await command_result(target)
    except AmbiguousAgent as exc:
        return {"matched": False, "state": "ambiguous", "error": str(exc)}
    except (OSError, ValueError, sqlite3.Error, httpx.HTTPError, TimeoutError) as exc:
        # Connection failures and nonzero probes are observations, not successful conditions.
        return {"matched": False, "error": str(exc)[:500] or type(exc).__name__}
    raise TypeError("unsupported target")


async def wait_for(request: WaitRequest, store: Store, progress=None):
    started = time.monotonic()
    duration = request.duration()
    end = started + duration
    deadline = request.deadline.isoformat() if request.deadline else (datetime.now(timezone.utc) + timedelta(seconds=duration)).isoformat()
    primary = request.conditions
    wake_conditions = request.wake_conditions
    targets = [*primary, *wake_conditions]
    baselines = [baseline_for(t) for t in targets]
    results = [{"matched": False, "state": "pending"} for _ in targets]
    needed = len(primary) if request.mode == "all" else (request.quorum if request.mode == "quorum" else 1)
    checks = 0
    async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
        changed = asyncio.Event()

        async def probe(i):
            nonlocal checks
            while True:
                checks += 1
                results[i] = await observe(targets[i], store, client, baselines[i])
                target, result = targets[i], results[i]
                if isinstance(target, Agent) and result.get("state") == "unknown":
                    result["hint"] = ("No lifecycle observation for this handle. Check superwait doctor "
                                      + target.provider + "; review hook trust and use native waiting until recording works.")
                    if time.monotonic() - started >= AGENT_OBSERVATION_GRACE:
                        if not result.get("awaiting_alias"):
                            result.update(state="unobserved", error=result["hint"])
                changed.set()
                delay = request.interval
                if isinstance(target, Agent) and result.get("state") == "unknown":
                    remaining = AGENT_OBSERVATION_GRACE - (time.monotonic() - started)
                    if remaining > 0:
                        delay = min(delay, max(.05, remaining))
                await asyncio.sleep(delay)

        async def keepalive():
            while True:
                await asyncio.sleep(15)
                await progress(time.monotonic() - started, duration)

        def card(t, r):
            identity = {Agent: "id", Signal: "key", File: "path", HTTP: "url", Command: "argv"}[type(t)]
            value = getattr(t, identity)
            c = {"label": t.label or value, "kind": t.kind, identity: value,
                 "state": r.get("state", "matched" if r["matched"] else "pending")}
            if isinstance(t, Agent):
                c["provider"] = t.provider
            if e := r.get("event"):
                c["cursor"] = e["seq"]
                if isinstance(t, Agent):
                    c.update(id=e["key"], handle=t.id, session=e["session"])
                if r["matched"]:
                    data = e["data"]
                    field = "report" if data.get("report") else "summary"
                    c["result"] = data.get(field, "") if isinstance(t, Agent) else data
                    if isinstance(t, Agent) and data.get(field + "_truncated"):
                        c["result_truncated"] = True
                        if data.get("transcript_path"):
                            c["transcript_path"] = data["transcript_path"]
                elif isinstance(t, (Agent, Signal)) and e["seq"] <= t.after:
                    c["state"] = "awaiting_new_event"
            for key in ("error", "hint", "status_code", "exit_code", "stdout", "stderr"):
                if key in r and r[key] != "":
                    c[key] = r[key]
            return c

        def continuing(t, r, baseline, wake=False):
            data = t.model_dump(exclude_none=True, exclude_defaults=True)
            if isinstance(t, File) and t.event == "changed":
                data["since"] = (r.get("stamp") if wake and r["matched"] else baseline) or "missing"
            if isinstance(t, (Agent, Signal)) and wake and r["matched"]:
                data["after"] = r["event"]["seq"]
            return data

        def outcome(status, error=None):
            main = list(zip(primary, results, baselines))
            wake = list(zip(wake_conditions, results[len(primary):], baselines[len(primary):]))
            ready = [card(t, r) for t, r, _ in main if r["matched"]]
            pending = [card(t, r) for t, r, _ in main if not r["matched"]]
            triggered = [card(t, r) for t, r, _ in wake if r["matched"]]
            reason = {
                "matched": f"{len(ready)} of {len(primary)} targets ready; {needed} required.",
                "interrupted": "Wake condition matched: " + ", ".join(str(c["label"]) for c in triggered),
                "timed_out": f"Deadline reached with {len(pending)} targets still pending.",
                "error": error or "Resolve the reported target error before waiting again.",
            }[status]
            continuation = None
            if pending and status != "error":
                remaining = needed - len(ready)
                continuation = {"targets": [continuing(t, r, b) for t, r, b in main if not r["matched"]],
                    "mode": "all", "deadline": deadline, "interval": request.interval,
                    "wake_on": [continuing(t, r, b, True) for t, r, b in wake]}
                if 0 < remaining < len(pending):
                    continuation.update(mode="quorum", quorum=remaining)
            result = {"status": status, "reason": reason, "ready": ready, "pending": pending,
                "triggered": triggered, "progress": {"ready": len(ready), "required": needed, "total": len(primary)},
                "deadline": deadline, "elapsed_seconds": round(time.monotonic() - started, 3),
                "continue_wait": continuation}
            if request.details:
                result.update(checks=checks,
                    targets=[{"target": t.model_dump(exclude_none=True), **r} for t, r, _ in main],
                    wake_on=[{"target": t.model_dump(exclude_none=True), **r} for t, r, _ in wake])
            return result

        try:
            async with asyncio.timeout_at(end):
                async with asyncio.TaskGroup() as group:
                    workers = [group.create_task(probe(i)) for i in range(len(targets))]
                    if progress:
                        workers.append(group.create_task(keepalive()))
                    try:
                        while True:
                            await changed.wait()
                            changed.clear()
                            # Each condition polls independently: a slow probe must
                            # not delay checking a fast-changing wake condition.
                            if any(r.get("state") == "ambiguous" for r in results):
                                return outcome("error")
                            identities = [(r["event"]["provider"], r["event"]["session"], r["event"]["key"])
                                          for t, r in zip(primary, results) if isinstance(t, Agent) and r.get("event")]
                            if len(identities) != len(set(identities)):
                                return outcome("error", "Two targets resolve to the same agent; remove the duplicate handle.")
                            if any(r["matched"] for r in results[len(primary):]):
                                return outcome("interrupted")
                            if sum(r["matched"] for r in results[:len(primary)]) >= needed:
                                return outcome("matched")
                            observable = sum(r.get("state") != "unobserved" for r in results[:len(primary)])
                            if observable < needed or any(r.get("state") == "unobserved" for r in results[len(primary):]):
                                return outcome("error", "An agent has no lifecycle observations. Check its handle and hook setup, or use native waiting.")
                    finally:
                        for worker in workers:
                            worker.cancel()
        except TimeoutError:
            return outcome("timed_out")
