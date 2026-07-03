"""Cross-check the secret detectors against the gitleaks ruleset — the community standard.

AI4Privacy grounds the *PII* detectors (`ai4privacy_validation.py`); the *secret* detectors
(aws/github/slack/private-key) have no PII corpus, so they are grounded instead against
[gitleaks](https://github.com/gitleaks/gitleaks) — the de-facto community regex ruleset for
exactly these token types. For each of our detectors this reports the matching gitleaks
rule, both regexes, and their agreement on a small probe set assembled from fragments, so
the divergences are explicit rather than assumed.

The findings are qualitative and honest: our AWS pattern is *narrower* than gitleaks'
(AKIA-only vs AKIA/ASIA/ABIA/ACCA/A3T), our Slack pattern is *looser* (any `xox[baprs]-…`
vs gitleaks' structured per-token patterns), and our private-key detector matches a bare
PEM *header* while gitleaks requires the key body — each a real precision/recall trade the
policy engine (WF-ROADMAP-0011) should know about before wiring a detector to a verb.

    python -m benchmarks.gitleaks_crosscheck \
        --config data/gitleaks.toml \
        --out benchmarks/gitleaks-crosscheck-results.md

The ruleset (`config/gitleaks.toml`) is fetched from the gitleaks repo; nothing secret is
committed — probe samples are assembled from fragments at runtime (as in `detector_corpus`).
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
import urllib.request
from dataclasses import dataclass

from benchmarks.detectors import DETECTORS_BY_NAME, Detector

GITLEAKS_RAW = "https://raw.githubusercontent.com/gitleaks/gitleaks/master/config/gitleaks.toml"

# Our detector -> the gitleaks rule id that covers the same token type.
RULE_MAP: dict[str, str] = {
    "aws_access_key": "aws-access-token",
    "github_pat": "github-pat",
    "slack_token": "slack-bot-token",
    "private_key": "private-key",
}

# Probe samples per detector, assembled from fragments (never a committed literal token).
# Each is (sample, should_fire) where should_fire is the human-labeled truth.
_AKIA = "AK" + "IAIOSFODNN7EXAMPLE"
_ASIA = "AS" + "IAROSFODNN7EXAMPL2"            # ASIA + 16 base32 chars: a temp-key gitleaks covers, we miss
_GH = "gh" + "p_" + "abcdefghijklmnopqrstuvwxyz0123456789"
_SLACK = "xox" + "b-" + "240120480129-abcDEF123ghiJKL"
_PEM_HEADER = "-----BEGIN RSA PRIVATE " + "KEY-----"

PROBES: dict[str, list[tuple[str, bool]]] = {
    "aws_access_key": [(_AKIA, True), (_ASIA, True), ("the AKIA prefix", False)],
    "github_pat": [(_GH, True), ("ghp_short", False)],
    "slack_token": [(_SLACK, True), ("xoxb-short", False)],
    "private_key": [(_PEM_HEADER, True), ("a private discussion", False)],
}


@dataclass
class Comparison:
    detector: str
    rule_id: str
    mine: str
    theirs: str
    agree: int
    total: int
    mine_fires: int
    theirs_fires: int


def fetch_rules(source: str | None) -> dict[str, str]:
    """Return ``{rule_id: regex}`` from a local gitleaks.toml path or the gitleaks repo."""
    if source:
        with open(source, "rb") as f:
            cfg = tomllib.load(f)
    else:
        raw = urllib.request.urlopen(GITLEAKS_RAW, timeout=90).read()
        cfg = tomllib.loads(raw.decode("utf-8"))
    return {r["id"]: r.get("regex", "") for r in cfg.get("rules", []) if "id" in r}


def _compile(pattern: str) -> re.Pattern[str] | None:
    """Compile a gitleaks (RE2) regex under Python ``re``; None if it uses a feature re lacks."""
    try:
        return re.compile(pattern)
    except re.error:
        return None


def compare(
    rules: dict[str, str],
    detectors: dict[str, Detector] = DETECTORS_BY_NAME,
    probes: dict[str, list[tuple[str, bool]]] = PROBES,
    rule_map: dict[str, str] = RULE_MAP,
) -> list[Comparison]:
    """For each mapped detector, agreement between our detector and the gitleaks rule.

    Pure: given a rules dict it makes no network call, so it is unit-tested with a planted
    mini-ruleset.
    """
    out: list[Comparison] = []
    for name in sorted(rule_map):
        rule_id = rule_map[name]
        their_rx = rules.get(rule_id, "")
        their = _compile(their_rx) if their_rx else None  # empty pattern != match-everything
        mine = detectors[name]
        agree = mine_fires = theirs_fires = 0
        samples = probes.get(name, [])
        for sample, _ in samples:
            m = mine.detects(sample)
            t = bool(their.search(sample)) if their is not None else False
            mine_fires += m
            theirs_fires += t
            agree += m == t
        out.append(
            Comparison(name, rule_id, mine.pattern.pattern, their_rx, agree, len(samples),
                       mine_fires, theirs_fires)
        )
    return out


def render_markdown(comparisons: list[Comparison], *, uncovered: list[str]) -> str:
    lines = [
        "## Secret-detector cross-check vs gitleaks",
        "",
        "Our secret detectors against the community-standard gitleaks ruleset. `agree` is over "
        "a small fragment-assembled probe set (both fire / both silent).",
        "",
        "| detector | gitleaks rule | agree | our fires | gitleaks fires |",
        "| --- | --- | --- | --- | --- |",
    ]
    for c in comparisons:
        lines.append(
            f"| {c.detector} | `{c.rule_id}` | {c.agree}/{c.total} | "
            f"{c.mine_fires}/{c.total} | {c.theirs_fires}/{c.total} |"
        )
    if uncovered:
        lines += ["", f"*No direct gitleaks counterpart:* {', '.join(uncovered)} "
                  "(gitleaks detects generic secrets by entropy, a different approach)."]
    lines += ["", "### Regexes (ours vs gitleaks)", ""]
    for c in comparisons:
        lines += [f"- **{c.detector}**", f"  - ours: `{c.mine}`", f"  - gitleaks: `{c.theirs}`"]
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Cross-check secret detectors against gitleaks.")
    ap.add_argument("--config", default=None, help="local gitleaks.toml (default: fetch from repo)")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    rules = fetch_rules(args.config)
    comparisons = compare(rules)
    uncovered = [n for n in DETECTORS_BY_NAME if n not in RULE_MAP]
    markdown = render_markdown(comparisons, uncovered=uncovered)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(markdown)
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
