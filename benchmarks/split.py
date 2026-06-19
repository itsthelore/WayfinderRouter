"""Deterministic, leakage-safe train/test partitioning for the benchmark harness.

Calibrating a router and then scoring it requires a held-out split: the threshold,
weights, or classifier are fit on *train* and the metrics are read on *test*, with no
overlap. The published `benchmarks/routerbench-results.md` numbers are in-sample (the
threshold was swept on the same rows it reports), so they overstate; this module is the
fix.

Two determinism rules, both load-bearing:

  * **Split on the prompt string, not the row index.** RouterBench has duplicate prompts
    (36,497 rows / 36,481 unique); a row-index split could put identical prompts on both
    sides, leaking the answer. Hashing the prompt keeps every copy of a prompt together.
  * **Use a stable hash, never the builtin ``hash()``** (which is salted per process via
    PYTHONHASHSEED). We reuse the FNV-1a 64-bit hash already proven stable in
    ``benchmarks/routers.py`` so splits are identical across runs, processes, and machines.
"""
from __future__ import annotations

from benchmarks.harness import Row

# FNV-1a 64-bit — the same constants as benchmarks/routers.py:deterministic_random.
_FNV_OFFSET = 14695981039346656037
_FNV_PRIME = 1099511628211
_MASK = 0xFFFFFFFFFFFFFFFF


def stable_hash(text: str) -> int:
    """FNV-1a 64-bit of ``text`` — identical across runs/processes (never the salted builtin)."""
    h = _FNV_OFFSET
    for byte in text.encode("utf-8"):
        h = ((h ^ byte) * _FNV_PRIME) & _MASK
    return h


def split_rows(
    rows: list[Row], *, test_frac: float = 0.5, salt: str = ""
) -> tuple[list[Row], list[Row]]:
    """Partition ``rows`` into ``(train, test)`` by a stable hash of the *prompt*.

    All rows sharing a prompt land on the same side (no train/test leakage on duplicates).
    Deterministic: depends only on prompt text, ``test_frac``, and ``salt`` — change the
    salt to get an independent split for variance estimates.
    """
    bound = int(test_frac * (_MASK + 1))
    train: list[Row] = []
    test: list[Row] = []
    for row in rows:
        (test if stable_hash(salt + row.prompt) < bound else train).append(row)
    return train, test


def train_order(rows: list[Row], *, salt: str = "order") -> list[Row]:
    """``rows`` in a stable, prompt-derived order (a reproducible shuffle).

    Sorting by ``(stable_hash, prompt)`` is a total order independent of input position, so
    deterministic prefixes ``train_order(train)[:N]`` are the learning-curve subsamples.
    """
    return sorted(rows, key=lambda row: (stable_hash(salt + row.prompt), row.prompt))
