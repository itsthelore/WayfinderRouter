"""Tests for the held-out calibration evaluation (benchmarks/routerbench_calibrate.py).

These guard the two things that make the experiment trustworthy: a deterministic,
leakage-free split, and a faithful reuse of the shipped scoring/calibration paths.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks import routerbench_calibrate as rc
from benchmarks.harness import Row, evaluate, evaluate_oracle
from benchmarks.routers import deterministic_random
from benchmarks.split import split_rows, stable_hash, train_order
from wayfinder_router.calibrate import CalibrationError, calibrate

_STRUCT = "# Heading\n\n- one\n- two\n\n```\ncode\n```\n" + ("word " * 60)


def _rows() -> list[Row]:
    """Synthetic set: short prompts → local-correct, structural prompts → cloud-only."""
    rows: list[Row] = []
    for i in range(40):
        if i % 2 == 0:
            rows.append(Row(f"short question {i}?", "general", {"local": 1.0, "cloud": 1.0},
                            {"local": 0.001, "cloud": 0.03}))
        else:
            rows.append(Row(f"{_STRUCT} {i}", "math", {"local": 0.0, "cloud": 1.0},
                            {"local": 0.001, "cloud": 0.03}))
    return rows


def test_split_is_deterministic_and_order_independent():
    rows = _rows()
    a_train, a_test = split_rows(rows, salt="x")
    b_train, b_test = split_rows(list(reversed(rows)), salt="x")
    assert {r.prompt for r in a_train} == {r.prompt for r in b_train}
    assert {r.prompt for r in a_test} == {r.prompt for r in b_test}


def test_split_has_no_prompt_leakage_including_duplicates():
    rows = _rows()
    rows += [Row(rows[0].prompt, "general", {"local": 1.0, "cloud": 1.0})]  # a duplicate prompt
    train, test = split_rows(rows, salt="x")
    assert set(p.prompt for p in train).isdisjoint(p.prompt for p in test)


def test_train_order_is_stable_and_a_total_order():
    rows = _rows()
    assert [r.prompt for r in train_order(rows)] == [r.prompt for r in train_order(list(reversed(rows)))]


def test_stable_hash_is_not_the_builtin():
    # FNV-1a is fixed across processes; just assert it's pure + stable here.
    assert stable_hash("abc") == stable_hash("abc")
    assert stable_hash("abc") != stable_hash("abd")


def test_oracle_label_matches_evaluate_oracle():
    rows = _rows()
    manual = sum(r.label[rc.oracle_label(r)] for r in rows) / len(rows)
    assert abs(manual - evaluate_oracle(rows).quality) < 1e-9


@pytest.mark.parametrize("loc,clo,expected", [(0.5, 0.5, "local"), (0.4, 0.6, "cloud"),
                                              (1.0, 1.0, "local"), (0.0, 0.3, "cloud")])
def test_oracle_label_on_fractional_fixtures(loc, clo, expected):
    assert rc.oracle_label(Row("p", "d", {"local": loc, "cloud": clo})) == expected


@pytest.mark.parametrize("mode", ["threshold", "tiers", "classifier"])
def test_router_from_result_round_trips_every_mode(mode):
    router = rc.router_from_result(calibrate(rc.to_samples(_rows()), mode=mode))
    assert all(router(r.prompt) in ("local", "cloud") for r in _rows())


def test_config_router_matches_score_complexity():
    from wayfinder_router import score_complexity
    from wayfinder_router.config import routing_config_from_toml
    rows = _rows()
    for mode in ("threshold", "classifier"):
        cfg = routing_config_from_toml(calibrate(rc.to_samples(rows), mode=mode).toml)
        cached = rc.config_router(cfg)
        assert all(cached(r.prompt) == score_complexity(r.prompt, config=cfg).recommendation
                   for r in rows)


def test_one_class_train_is_handled_not_crashing():
    rows = _rows()
    one_class = [r for r in rows if rc.oracle_label(r) == "local"]
    with pytest.raises(CalibrationError):
        calibrate(rc.to_samples(one_class), mode="threshold")
    # the driver must degrade gracefully: calibrate configs become None, baselines still run.
    out = rc.evaluate_configs(one_class, rows)
    assert out["calibrate classifier"] is None
    assert out["random"] is not None


def test_skill_is_pgr_minus_frac_cloud():
    m = evaluate("r", deterministic_random, _rows())
    assert rc._skill(m) == m.pgr - m.frac_cloud


def test_domain_of_is_total_and_representative():
    domains = {"code", "math", "multilingual", "science", "commonsense", "humanities", "general"}
    for name in ["grade-school-math", "mmlu-college-physics", "hellaswag", "mbpp",
                 "chinese_poem", "mmlu-professional-law", "mmlu-miscellaneous", "??unknown??"]:
        assert rc.domain_of(name) in domains
    assert rc.domain_of("grade-school-math") == "math"
    assert rc.domain_of("mmlu-high-school-physics") == "science"
    assert rc.domain_of("hellaswag") == "commonsense"
    assert rc.domain_of("totally-unknown") == "general"


def test_seed_set_is_valid_and_two_class():
    seed = Path(__file__).parent.parent / "benchmarks" / "seed" / "domain-seed.jsonl"
    labels = [json.loads(line)["label"] for line in seed.read_text().splitlines() if line.strip()]
    assert set(labels) == {"local", "cloud"}
    assert len(labels) >= 20
