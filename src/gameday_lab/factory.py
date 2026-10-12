"""Builds clients / executors / alerters by name. Used by scripts and the grader."""

from __future__ import annotations

from typing import Any

from gameday_lab.sim.config import LabConfig
from gameday_lab.sim.contracts import BaseClient


def build_client(name: str, cfg: LabConfig, seed: int = 1) -> BaseClient:
    if name == "naive":
        from gameday_lab.naive_client import NaiveClient

        return NaiveClient(cfg.fallback_order[0])
    if name == "resilient":
        from gameday_lab.client import ResilientClient

        return ResilientClient(cfg.provider_infos(), cfg.fallback_order, cfg.client, seed=seed)
    if name == "cached":
        from gameday_lab.cache import CachingClient, SemanticCache

        return CachingClient(build_client("resilient", cfg, seed), SemanticCache(**cfg.cache))
    raise ValueError(f"unknown client {name!r}")


def build_executor_factory(name: str) -> Any:
    if name == "naive":
        from gameday_lab.workflows.naive_executor import NaiveExecutor

        return NaiveExecutor
    if name == "yours":
        from gameday_lab.workflows.executor import WorkflowExecutor

        return WorkflowExecutor
    raise ValueError(f"unknown executor {name!r}")


def build_alerter(name: str, slo: float = 0.995) -> Any:
    if name == "naive":
        from gameday_lab.slo.scoring import NaiveThresholdAlerter

        return NaiveThresholdAlerter()
    if name == "burnrate":
        from gameday_lab.slo.alerting import BurnRateAlerter

        return BurnRateAlerter(slo)
    raise ValueError(f"unknown alerter {name!r}")
