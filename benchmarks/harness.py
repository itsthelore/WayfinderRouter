"""Evaluate routers on a labeled dataset with RouteLLM / RouterArena-aligned metrics.

Each dataset row carries per-model correctness labels (did ``local`` / ``cloud`` get
this prompt right?) and a difficulty tag. A router maps a prompt to a model; the
harness scores the chosen model against the labels and reports the standard routing
axes. Everything is deterministic and offline — no model is called to produce these
numbers (the labels are the oracle).
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

LOCAL = "local"
CLOUD = "cloud"

# Relative cost units: local ~ self-hosted (cheap); cloud ~ a hosted API (1.0).
COST = {"local": 0.2, "cloud": 1.0}

Router = Callable[[str], str]


@dataclass(frozen=True)
class Row:
    prompt: str
    difficulty: str
    label: dict[str, int]  # {"local": 0/1, "cloud": 0/1} correctness
    cost: dict[str, float] | None = None  # optional real per-call cost; falls back to COST


@dataclass(frozen=True)
class Metrics:
    name: str
    quality: float  # mean correctness of the chosen model (0..1)
    cost: float  # mean cost of the chosen model
    frac_cloud: float  # call fraction to the strong model
    pgr: float  # performance gap recovered: 0 == always-local, 1 == always-cloud
    cost_savings: float  # vs always-cloud
    latency_us: float  # mean decision latency in microseconds (0 if not measured)


def load_dataset(path: str | Path) -> list[Row]:
    rows: list[Row] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        data = json.loads(line)
        rows.append(
            Row(
                prompt=data["prompt"],
                difficulty=data.get("difficulty", ""),
                label=data["label"],
                cost=data.get("cost"),
            )
        )
    return rows


def _reference(rows: list[Row]) -> tuple[float, float]:
    n = len(rows)
    q_local = sum(r.label["local"] for r in rows) / n
    q_cloud = sum(r.label["cloud"] for r in rows) / n
    return q_local, q_cloud


def _cost(row: Row, choice: str) -> float:
    """The per-call cost of ``choice`` on ``row`` — real cost when present, else the flat default."""
    return row.cost[choice] if row.cost else COST[choice]


def _cloud_ref_cost(rows: list[Row]) -> float:
    """Mean cost of always routing to the strong model — the always-cloud baseline for savings."""
    return sum(_cost(r, CLOUD) for r in rows) / len(rows)


def evaluate(name: str, router: Router, rows: list[Row], *, measure_latency: bool = False) -> Metrics:
    q_local, q_cloud = _reference(rows)
    choices: list[str] = []
    elapsed = 0.0
    for row in rows:
        start = time.perf_counter()
        choice = router(row.prompt)
        elapsed += time.perf_counter() - start
        choices.append(choice)
    n = len(rows)
    quality = sum(row.label[choice] for row, choice in zip(rows, choices, strict=True)) / n
    cost = sum(_cost(row, choice) for row, choice in zip(rows, choices, strict=True)) / n
    frac_cloud = sum(choice == CLOUD for choice in choices) / n
    denom = (q_cloud - q_local) or 1.0
    cloud_ref = _cloud_ref_cost(rows)
    return Metrics(
        name=name,
        quality=quality,
        cost=cost,
        frac_cloud=frac_cloud,
        pgr=(quality - q_local) / denom,
        cost_savings=(cloud_ref - cost) / cloud_ref if cloud_ref else 0.0,
        latency_us=(elapsed / n) * 1e6 if measure_latency else 0.0,
    )


def evaluate_oracle(rows: list[Row]) -> Metrics:
    """Upper bound: for each row pick the cheapest model that is correct."""
    q_local, q_cloud = _reference(rows)
    n = len(rows)
    quality = cost = frac_cloud = 0.0
    for row in rows:
        choice = LOCAL if row.label["local"] else (CLOUD if row.label["cloud"] else LOCAL)
        quality += row.label[choice]
        cost += _cost(row, choice)
        frac_cloud += choice == CLOUD
    quality, cost, frac_cloud = quality / n, cost / n, frac_cloud / n
    denom = (q_cloud - q_local) or 1.0
    cloud_ref = _cloud_ref_cost(rows)
    return Metrics(
        name="oracle (upper bound, not a real router)",
        quality=quality,
        cost=cost,
        frac_cloud=frac_cloud,
        pgr=(quality - q_local) / denom,
        cost_savings=(cloud_ref - cost) / cloud_ref if cloud_ref else 0.0,
        latency_us=0.0,
    )


def _efficiency(m: Metrics) -> float:
    """Cost-aware knee objective: the product of quality-recovered and cost-saved.

    Maximising accuracy alone is degenerate when the strong model is the quality
    ceiling (it just picks always-cloud, 0% savings); maximising savings alone picks
    always-local (0 quality recovered). ``pgr * cost_savings`` rewards *both*, so it
    lands on the balanced knee of the cost-quality curve rather than at either end or
    on a noise boundary.
    """
    return m.pgr * m.cost_savings


def sweep(rows: list[Row], make: Callable[[float], Router], values: list[float]) -> list[tuple[float, Metrics]]:
    """Evaluate ``make(v)`` for each ``v`` — one point on the cost-quality curve per value."""
    return [(v, evaluate(f"{v}", make(v), rows)) for v in values]


def knee(points: list[tuple[float, Metrics]]) -> tuple[float, Metrics]:
    """The cost-aware operating point (the curve's efficient knee)."""
    return max(points, key=lambda point: _efficiency(point[1]))
