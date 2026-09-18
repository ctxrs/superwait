"""The same validated request schema is used by MCP, Python, and the CLI."""

import math
import re
from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Condition(Model):
    label: str | None = Field(default=None, description="Optional name used in the result, e.g. CI failed.")


class Agent(Condition):
    kind: Literal["agent"]
    provider: Literal["codex", "claude", "cursor"]
    id: str = Field(min_length=1)
    session: str | None = None
    states: list[Literal["running", "stopped", "error", "aborted"]] = Field(
        default_factory=lambda: ["stopped", "error", "aborted"], min_length=1
    )
    after: int = Field(default=0, ge=0, description="Only match an event newer than this cursor.")


class Signal(Condition):
    kind: Literal["signal"]
    key: str = Field(min_length=1)
    state: str | None = None
    after: int = Field(default=0, ge=0)


class File(Condition):
    kind: Literal["file"]
    path: str = Field(min_length=1)
    event: Literal["exists", "missing", "changed", "contains"] = "exists"
    text: str | None = None
    since: list[int] | Literal["missing"] | None = Field(default=None,
        description="Returned in continue_wait to preserve a changed-file baseline. Omit for a new wait.")

    @model_validator(mode="after")
    def check_text(self):
        if self.event == "contains" and not self.text:
            raise ValueError("file contains requires nonempty text")
        return self


class HTTP(Condition):
    kind: Literal["http"]
    url: str = Field(pattern=r"^https?://")
    status: int = Field(default=200, ge=100, le=599)


class Command(Condition):
    kind: Literal["command"]
    argv: list[str] = Field(min_length=1, description="An observational command, repeated without a shell.")
    cwd: str | None = None
    exit_code: int = 0
    probe_timeout: float = Field(default=10, gt=0, allow_inf_nan=False)


Target = Annotated[Agent | Signal | File | HTTP | Command, Field(discriminator="kind")]


def seconds(value: str) -> float:
    number = r"\d+(?:\.\d+)?"
    if re.fullmatch(rf"\s*{number}\s*", value):
        result = float(value)
    else:
        part = rf"({number})\s*(ms|s|m|h|d)"
        if not re.fullmatch(rf"\s*(?:{part}\s*)+", value):
            raise ValueError("duration must be seconds or use ms, s, m, h, d (for example 4m30s)")
        result = sum(float(n) * {"ms": .001, "s": 1, "m": 60, "h": 3600, "d": 86400}[unit]
                     for n, unit in re.findall(part, value))
    if not math.isfinite(result) or result <= 0:
        raise ValueError("duration must be finite and positive")
    return result


class WaitRequest(Model):
    agents: list[str] = Field(default_factory=list, description="Native IDs, or Codex task paths with the parent session. No UUID lookup needed.")
    provider: Literal["codex", "claude", "cursor"] | None = Field(default=None,
        description="Defaults to the host selected during setup; standalone Python defaults to Codex.")
    session: str | None = Field(default=None, description="Parent session from SessionStart context; required for Codex task paths.")
    targets: list[Target] = Field(default_factory=list)
    timeout: str = "10m"
    mode: Literal["any", "all", "quorum"] = "all"
    quorum: int | None = Field(default=None, gt=0)
    wake_on: list[Target] = Field(default_factory=list)
    interval: float | str = Field(default=1, description="Seconds between probes, as a number or duration such as 3s; minimum 0.05.")
    deadline: datetime | None = Field(default=None, description="Absolute deadline; survives a caller retry. Overrides timeout.")
    details: bool = Field(default=False, description="Include raw probe observations for troubleshooting.")

    @field_validator("interval", mode="before")
    @classmethod
    def parse_interval(cls, value):
        if not isinstance(value, (str, int, float)) or isinstance(value, bool):
            raise ValueError("interval must be seconds or a duration string")
        value = seconds(value) if isinstance(value, str) else float(value)
        if not math.isfinite(value) or value < .05:
            raise ValueError("interval must be finite and at least 0.05 seconds")
        return value

    @property
    def conditions(self):
        provider = self.provider or "codex"
        return [*(Agent(kind="agent", provider=provider, id=id, session=self.session) for id in self.agents),
                *self._scoped(self.targets)]

    @property
    def wake_conditions(self):
        return self._scoped(self.wake_on)

    def _scoped(self, targets):
        return [t.model_copy(update={"session": self.session})
                if isinstance(t, Agent) and t.provider == (self.provider or "codex") and t.session is None and self.session
                else t for t in targets]

    @model_validator(mode="after")
    def validate_request(self):
        seconds(self.timeout)
        targets = self.conditions
        if not targets:
            raise ValueError("provide agents or targets to wait for")
        if self.deadline and self.deadline.tzinfo is None:
            raise ValueError("deadline must include a timezone")
        if self.mode == "quorum":
            if self.quorum is None or self.quorum > len(targets):
                raise ValueError("quorum must be between 1 and the number of targets")
        elif self.quorum is not None:
            raise ValueError("quorum is only valid with mode=quorum")
        if len({t.model_dump_json(exclude={"label"}) for t in targets}) != len(targets):
            raise ValueError("duplicate targets would count the same outcome twice")
        return self

    def duration(self) -> float:
        if self.deadline:
            return max(0, (self.deadline - datetime.now(timezone.utc)).total_seconds())
        return seconds(self.timeout)
