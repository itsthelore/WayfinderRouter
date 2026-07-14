#!/usr/bin/env python3
"""Compare Python and release-Rust decision throughput on the golden corpus.

Build the Rust benchmark first:

    cargo build --release --manifest-path rust/Cargo.toml \
      -p wayfinder-compat-tests --bin decision-bench

Then run this script from the repository root. Results are descriptive, not a
test gate: keep the machine, corpus, build profile, and iteration count beside
any number used in a migration decision.
"""

from __future__ import annotations

import argparse
import gc
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from wayfinder_router.complexity import score_complexity  # noqa: E402


CORPUS = ROOT / "rust/crates/wayfinder-compat-tests/fixtures/python-golden.json"
DEFAULT_RUST_BINARY = ROOT / "rust/target/release/decision-bench"


def python_result(texts: list[str], iterations: int) -> dict[str, object]:
    for _ in range(100):
        for text in texts:
            score_complexity(text)
    gc.collect()
    gc.disable()
    checksum = 0.0
    started = time.perf_counter()
    try:
        for _ in range(iterations):
            for text in texts:
                checksum += score_complexity(text).score
    finally:
        gc.enable()
    elapsed = time.perf_counter() - started
    operations = iterations * len(texts)
    return {
        "implementation": "python",
        "corpus_cases": len(texts),
        "iterations": iterations,
        "operations": operations,
        "elapsed_seconds": elapsed,
        "operations_per_second": operations / elapsed,
        "checksum": checksum,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=5_000)
    parser.add_argument("--rust-binary", type=Path, default=DEFAULT_RUST_BINARY)
    args = parser.parse_args()
    if args.iterations < 1:
        parser.error("--iterations must be positive")
    fixtures = json.loads(CORPUS.read_text(encoding="utf-8"))
    texts = [fixture["text"] for fixture in fixtures]
    python = python_result(texts, args.iterations)
    rust_process = subprocess.run(
        [str(args.rust_binary), "--iterations", str(args.iterations)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    rust = json.loads(rust_process.stdout)
    if python["checksum"] != rust["checksum"]:
        raise SystemExit("checksum mismatch: benchmark implementations did not make identical decisions")
    print(json.dumps({"python": python, "rust": rust}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
