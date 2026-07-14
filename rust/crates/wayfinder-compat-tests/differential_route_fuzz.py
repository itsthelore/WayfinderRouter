#!/usr/bin/env python3
"""Deterministically compare synthetic Python and Rust routing decisions.

This is a development/CI stress runner, not a replacement for checked-in
fixtures. It never edits a config or calls a provider. Run after building the
Rust CLI; `/dev/null` selects identical default routing in both implementations.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from wayfinder_router.complexity import score_complexity  # noqa: E402


DEFAULT_BINARY = ROOT / "rust/target/debug/wayfinder-router"
FRAGMENTS = (
    "plain words",
    "# heading",
    "## second",
    "- list item",
    "1. numbered",
    "| a | b |",
    "[link](https://example.invalid)",
    "prove theorem induction recurrence",
    "must only exactly without constraint",
    "∑ x ≤ ∞ ∫ f dx \\alpha",
    "```python",
    "~~~",
    "x = 1",
    "```",
    "---",
    "...",
    "请证明这个定理 😀",
    "أثبت النظرية باستخدام الاستقراء",
    "word\u00a0word\tend",
    "? ??",
    "",
)
SEPARATORS = ("\n", "\r\n", "\r", "\v", "\f", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029")


def prompts(seed: int, count: int) -> list[str]:
    rng = random.Random(seed)
    cases = [
        "",
        "---\ntitle: x\n---\n# real",
        "---\nunterminated",
        "```\n# hidden\n- hidden\n```\n# visible",
        "a\u2028# heading\u2029- item",
        "😀" * 400,
    ]
    for _ in range(count):
        pieces = [rng.choice(FRAGMENTS) for _ in range(rng.randrange(0, 80))]
        separator = rng.choice(SEPARATORS)
        text = separator.join(pieces)
        if rng.random() < 0.15:
            text = "---" + separator + "meta = 1" + separator + "---" + separator + text
        if rng.random() < 0.1:
            text += separator
        cases.append(text)
    return cases


def rust_decision(binary: Path, text: str) -> dict[str, object]:
    environment = os.environ.copy()
    environment["WAYFINDER_CONFIG"] = "/dev/null"
    environment.pop("WAYFINDER_ROUTER_THRESHOLD", None)
    process = subprocess.run(
        [str(binary), "route", "-", "--json"],
        input=text,
        text=True,
        capture_output=True,
        check=False,
        cwd=ROOT,
        env=environment,
    )
    if process.returncode != 0:
        raise RuntimeError(
            f"Rust CLI exited {process.returncode}: {process.stderr.strip()}"
        )
    return json.loads(process.stdout)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=0x57A7F1)
    parser.add_argument("--cases", type=int, default=500)
    parser.add_argument("--rust-binary", type=Path, default=DEFAULT_BINARY)
    args = parser.parse_args()
    if args.cases < 0:
        parser.error("--cases must be non-negative")

    for index, text in enumerate(prompts(args.seed, args.cases)):
        expected = score_complexity(text).to_dict()
        actual = rust_decision(args.rust_binary, text)
        if actual != expected:
            print(
                json.dumps(
                    {
                        "case": index,
                        "seed": args.seed,
                        "text": repr(text),
                        "python": expected,
                        "rust": actual,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                file=sys.stderr,
            )
            return 1
    print(f"pass: {args.cases + 6} Python/Rust synthetic routing decisions (seed={args.seed})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
