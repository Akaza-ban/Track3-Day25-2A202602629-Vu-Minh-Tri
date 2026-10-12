"""Config loading for the simulator. DO NOT EDIT."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from gameday_lab.sim.contracts import ProviderInfo


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    family: str
    region: str
    supports_tools: bool
    cost_per_call: float
    servers: int  # concurrent requests the deployment can process
    service_median_s: float  # median processing time of one request
    service_sigma: float = 0.35  # log-normal spread
    max_queue_wait_s: float = 10.0  # server sheds load (529) beyond this queue wait
    rate_limit_rps: float | None = None  # per-tenant requests/second (fixed 1s window)
    base_error_rate: float = 0.002  # background 500s

    def info(self) -> ProviderInfo:
        return ProviderInfo(self.name, self.family, self.region, self.supports_tools, self.cost_per_call)


@dataclass(frozen=True)
class Effect:
    """An incident effect applied to a provider (or a whole family) for a time window."""

    start: float
    end: float
    provider: str | None = None
    family: str | None = None
    capacity_factor: float = 1.0  # <1: each request takes longer -> lower throughput
    extra_latency_s: float = 0.0  # added to responses without using capacity
    error_rate: float = 0.0  # extra 500s
    outage: str | None = None  # "refuse" (fast 503) or "hang" (no response at all)
    quota_exhausted: bool = False  # 429 insufficient_quota, no retry-after
    rate_limit_rps: float | None = None  # override the tenant rate limit

    def applies(self, spec: ProviderSpec, now: float) -> bool:
        if not (self.start <= now < self.end):
            return False
        if self.provider is not None and self.provider != spec.name:
            return False
        if self.family is not None and self.family != spec.family:
            return False
        return True


@dataclass(frozen=True)
class Window:
    start: float
    end: float
    value: float

    def contains(self, t: float) -> bool:
        return self.start <= t < self.end


@dataclass(frozen=True)
class Scenario:
    key: str
    title: str
    duration_s: float
    effects: tuple[Effect, ...] = ()
    traffic: tuple[Window, ...] = ()  # value = arrival-rate multiplier
    malformed: tuple[Window, ...] = ()  # value = share of malformed calls (bad deploy)
    primary_only: tuple[Window, ...] = ()  # windows where failover is NOT needed
    restore_at: float | None = None  # when the provider-side incident ends
    checks: dict[str, Any] = field(default_factory=dict)  # pass criteria


@dataclass(frozen=True)
class Workload:
    turns_per_s: float = 4.0
    turn_deadline_s: float = 30.0
    tool_exec_s: float = 0.3
    steps: tuple[str, ...] = ("tool", "chat")
    base_malformed_rate: float = 0.0


@dataclass(frozen=True)
class LabConfig:
    seed: int
    workload: Workload
    providers: tuple[ProviderSpec, ...]
    fallback_order: tuple[str, ...]
    client: dict[str, Any]
    scenarios: dict[str, Scenario]

    def provider_infos(self) -> dict[str, ProviderInfo]:
        return {p.name: p.info() for p in self.providers}


def _windows(raw: list[dict[str, Any]] | None, key: str) -> tuple[Window, ...]:
    return tuple(Window(float(w["start"]), float(w["end"]), float(w[key])) for w in raw or [])


def _scenario(key: str, raw: dict[str, Any]) -> Scenario:
    return Scenario(
        key=key,
        title=raw.get("title", key),
        duration_s=float(raw["duration_s"]),
        effects=tuple(Effect(**e) for e in raw.get("effects", [])),
        traffic=_windows(raw.get("traffic"), "multiplier"),
        malformed=_windows(raw.get("malformed"), "rate"),
        primary_only=tuple(
            Window(float(w["start"]), float(w["end"]), 1.0) for w in raw.get("primary_only", [])
        ),
        restore_at=raw.get("restore_at"),
        checks=dict(raw.get("checks", {})),
    )


def load_config(path: str | Path = "configs/gameday.yaml",
                scenarios_path: str | Path | None = "configs/scenarios.yaml") -> LabConfig:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    scen_raw: dict[str, Any] = dict(raw.get("scenarios", {}))
    if scenarios_path is not None and Path(scenarios_path).exists():
        scen_raw.update(yaml.safe_load(Path(scenarios_path).read_text(encoding="utf-8"))["scenarios"])
    wl = raw.get("workload", {})
    workload = Workload(
        turns_per_s=float(wl.get("turns_per_s", 4.0)),
        turn_deadline_s=float(wl.get("turn_deadline_s", 30.0)),
        tool_exec_s=float(wl.get("tool_exec_s", 0.3)),
        steps=tuple(wl.get("steps", ["tool", "chat"])),
        base_malformed_rate=float(wl.get("base_malformed_rate", 0.0)),
    )
    return LabConfig(
        seed=int(raw.get("seed", 25)),
        workload=workload,
        providers=tuple(ProviderSpec(**p) for p in raw["providers"]),
        fallback_order=tuple(raw["fallback_order"]),
        client=dict(raw.get("client", {})),
        scenarios={k: _scenario(k, v) for k, v in scen_raw.items()},
    )
