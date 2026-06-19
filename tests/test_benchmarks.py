"""Tests for the offline benchmark harness (WF-ADR-0015).

These assert the metrics are correct and the run is deterministic (apart from the
wall-clock latency column), so the published numbers in benchmarks/results.md are
trustworthy and reproducible.
"""

from __future__ import annotations

from pathlib import Path

from benchmarks import harness
from benchmarks import run as runner
from benchmarks.routers import (
    always_cloud,
    always_local,
    deterministic_random,
    wayfinder,
)

DATASET = Path(__file__).parent.parent / "benchmarks" / "dataset.jsonl"


def test_dataset_loads_with_per_model_labels():
    rows = harness.load_dataset(DATASET)
    assert len(rows) == 24
    assert all(set(r.label) == {"local", "cloud"} for r in rows)


def test_always_cloud_is_the_quality_ceiling():
    rows = harness.load_dataset(DATASET)
    m = harness.evaluate("cloud", always_cloud, rows)
    assert m.quality == 1.0 and m.pgr == 1.0 and m.cost_savings == 0.0


def test_always_local_recovers_no_gap():
    rows = harness.load_dataset(DATASET)
    m = harness.evaluate("local", always_local, rows)
    assert m.pgr == 0.0 and m.frac_cloud == 0.0


def test_oracle_is_perfect_and_cheaper_than_always_cloud():
    rows = harness.load_dataset(DATASET)
    m = harness.evaluate_oracle(rows)
    assert m.quality == 1.0
    assert 0.0 < m.cost_savings < 1.0


def test_metrics_stay_in_range():
    rows = harness.load_dataset(DATASET)
    for router in (always_local, always_cloud, deterministic_random, lambda p: wayfinder(p, 0.1)):
        m = harness.evaluate("x", router, rows)
        assert 0.0 <= m.quality <= 1.0
        assert 0.0 <= m.frac_cloud <= 1.0


def test_deterministic_random_is_reproducible():
    rows = harness.load_dataset(DATASET)
    a = harness.evaluate("r", deterministic_random, rows)
    b = harness.evaluate("r", deterministic_random, rows)
    assert a.quality == b.quality and a.frac_cloud == b.frac_cloud


def test_wayfinder_default_collapses_to_local_on_this_set():
    # Honest and documented: the default 0.5 cut is above every structural score here.
    rows = harness.load_dataset(DATASET)
    assert harness.evaluate("wf", wayfinder, rows).frac_cloud == 0.0


def test_knee_finds_a_cost_aware_operating_point():
    rows = harness.load_dataset(DATASET)
    points = harness.sweep(rows, lambda t: (lambda p: wayfinder(p, t)), [0.0, 0.05, 0.2, 1.0])
    _, m = harness.knee(points)
    # The knee is strictly inside the curve: it recovers some gap *and* saves some cost.
    assert m.pgr > 0.0 and m.cost_savings > 0.0


def test_run_report_has_the_expected_sections():
    report = runner.run(DATASET)
    assert "# Benchmark results" in report
    assert "cost-quality curve" in report
    assert "by difficulty" in report
