"""A shell interface and stdio MCP server sharing one wait engine."""

import argparse
import asyncio
import json
import os
import sqlite3
import sys
from pathlib import Path

from .store import Store


def parser():
    root = argparse.ArgumentParser(description="Wait for conditions without repeated model calls.")
    root.add_argument("--db", type=Path, help="Local event database (or SUPERWAIT_DB).")
    root.add_argument("--provider", dest="default_provider", choices=["codex", "claude", "cursor"],
                      help="Default host for the agents shorthand.")
    commands = root.add_subparsers(dest="command", required=True)
    wait = commands.add_parser("wait", help="Wait once; print one final JSON result.")
    wait.add_argument("--request", default="-", help="JSON request file, or - for stdin.")
    wait.add_argument("--timeout", help="Override request duration: 30s, 10m, 2h, 1d.")
    wait.add_argument("--output", type=Path, help="Also atomically save the final result here.")
    signal = commands.add_parser("signal", help="Publish a local checkpoint or outcome.")
    signal.add_argument("key")
    signal.add_argument("--state", default="ready")
    signal.add_argument("--data", default="{}", help="Small JSON object.")
    agents = commands.add_parser("agents", help="List recent hook-observed subagents.")
    agents.add_argument("provider", choices=["codex", "claude", "cursor"])
    agents.add_argument("--session")
    doctor = commands.add_parser("doctor", help="Check whether lifecycle events are reaching this database.")
    doctor.add_argument("provider", choices=["codex", "claude", "cursor"])
    doctor.add_argument("--session", help="Check observations for one parent session.")
    hook = commands.add_parser("hook", help="Host lifecycle adapter; reads JSON on stdin.")
    hook.add_argument("provider", choices=["codex", "claude", "cursor"])
    commands.add_parser("serve", help="Serve the MCP API over stdio.")
    setup = commands.add_parser("setup", help="Configure hooks, MCP, and the skill for your host.")
    setup.add_argument("provider", choices=["codex", "claude", "cursor"])
    setup.add_argument("--project", type=Path, help="Only configure this project; default: all projects for your user.")
    setup.add_argument("--max-wait", default="24h", help="Host tool timeout, where supported.")
    prune = commands.add_parser("prune", help="Delete old local observations.")
    prune.add_argument("--days", type=float, default=30)
    return root


def main():
    args = parser().parse_args()
    code = 0
    try:
        if args.command == "setup":
            from .setup import configure
            result = configure(args.provider, args.project, args.db, args.max_wait)
        else:
            store = Store(args.db)
            if args.command == "wait":
                from .engine import wait_for
                from .models import Agent, WaitRequest
                raw = json.loads(sys.stdin.read() if args.request == "-" else Path(args.request).read_text())
                if not isinstance(raw, dict):
                    raise ValueError("wait request must be a JSON object")
                if args.default_provider and raw.get("provider") is None:
                    raw["provider"] = args.default_provider
                if args.timeout:
                    raw["timeout"] = args.timeout
                    raw.pop("deadline", None)
                request = WaitRequest.model_validate(raw)
                if (request.provider or "codex") == "codex" and request.session is None and os.environ.get("CODEX_THREAD_ID"):
                    if any(isinstance(t, Agent) and t.provider == "codex" and t.id.startswith("/")
                           and t.session is None for t in [*request.conditions, *request.wake_conditions]):
                        request = request.model_copy(update={"session": os.environ["CODEX_THREAD_ID"]})
                result = asyncio.run(wait_for(request, store))
                code = {"timed_out": 124, "error": 2}.get(result["status"], 0)
            elif args.command == "signal":
                data = json.loads(args.data)
                if not isinstance(data, dict) or not args.key or not args.state:
                    raise ValueError("signal requires nonempty key/state and object data")
                result = {"seq": store.emit("signal", args.key, args.state, data=data)}
            elif args.command == "agents":
                result = {"agents": store.agents(args.provider, args.session), "limit": 50}
            elif args.command == "doctor":
                from importlib.metadata import version
                result = {"version": version("superwait"), **store.health(args.provider, args.session)}
                code = 0 if result["status"] == "observed" else 1
            elif args.command == "hook":
                from .hooks import record_hook
                result = record_hook(args.provider, json.load(sys.stdin), store)
            elif args.command == "serve":
                from .server import serve
                serve(store, args.default_provider or "codex")
                return
            elif args.command == "prune":
                if args.days <= 0:
                    raise ValueError("days must be positive")
                result = {"deleted": store.prune(args.days)}
    except KeyboardInterrupt:
        result, code = {"status": "cancelled"}, 130
    except (ValueError, OSError, sqlite3.Error) as exc:
        if args.command == "hook":
            # A recorder never asks the host to retry, continue, or block an agent.
            print(f"superwait hook: {exc}", file=sys.stderr)
            result = {"permission": "allow"} if args.provider == "cursor" else {}
        else:
            result, code = {"status": "error", "error": str(exc)}, 2
    rendered = json.dumps(result, ensure_ascii=False)
    if args.command == "wait" and args.output:
        import tempfile
        args.output.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".superwait-", dir=args.output.parent)
        try:
            with os.fdopen(fd, "w") as f:
                f.write(rendered + "\n")
            os.replace(temporary, args.output)
        finally:
            Path(temporary).unlink(missing_ok=True)
    print(rendered)
    raise SystemExit(code)
