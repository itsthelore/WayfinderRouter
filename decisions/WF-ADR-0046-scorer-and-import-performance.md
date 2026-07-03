---
schema_version: 1
id: WF-ADR-0046
type: decision
tags: [performance, complexity, import, scorer, schema-version, parity]
---

# WF-ADR-0046: Contract-superseding scorer and import performance

## Status

Proposed

## Category

Technical

## Context

The rebuild's honest performance finding is a null result: **the contract-pinned
rewrite did not move wall-clock.** Interleaved-median benchmarks put scoring,
config load, `build_app`, and cold import all within ~0–4% of legacy — inside
noise. The rebuild cut profiled call-count by 64.9% (fusing the two lexicon
generator passes, trimming allocations), but that did not show up on the clock
because **the hot path is dominated by regex and string scanning, not call
overhead**. Fewer Python-level calls over the same character-scanning work buys
almost nothing.

That leaves the real speedups on the far side of two frozen contracts. They
could not land on the rebuild branch and each needs its own decision:

- **Scoring is scan-bound and parity-frozen.** `extract_features` is ~86% of
  `score_complexity` cumulative time. It makes **four whole-text passes** over
  each prompt body: `body.split()` for word_count, `splitlines()` + four
  structural line regexes, `_WORD_TOKEN_RE.findall(body.lower())` for the
  lexicon, and `strip_frontmatter`'s own split. The contract-safe genexpr fusion
  the rebuild already did only touched *call count*; the scanning cost is
  untouched. Collapsing the passes is where the time is — and it almost certainly
  perturbs edge-case numerics (`str.split` vs `splitlines` vs the word-token
  regex differ on CRLF, form-feed, and unicode), which the WF-ADR-0042/0043
  parity freeze forbids without a versioned schema bump. `schema_version` is
  "3" today.

- **Cold import is 58ms and 43ms of it is dead weight for a scoring caller.**
  `import wayfinder_router` eagerly pulls `.recalibrate`, whose module-level
  `from .gateway import …` drags in the 2404-line gateway subtree — **~21ms of
  which is `import asyncio`** alone (asyncio → ssl → …), pulled only because
  `gateway.py` imports asyncio at module top for the async forward path. Nothing
  on the pure scoring path needs any of it at import time. `build_app` itself
  (16.4ms) is **94% FastAPI/pydantic per-typed-param schema generation** —
  framework-inherent, not Wayfinder code, and explicitly *not* what this ADR
  targets.

The naive fix for the import problem is unsafe: a plain PEP 562 module
`__getattr__` that lazily resolves `recalibrate` collides with the fact that
**`recalibrate` is both a submodule and a re-exported name**, and WF-ADR-0007
owns the `recalibrate` name while WF-ADR-0043 pins module attributes as
monkeypatch contract. A lazy facade must preserve `from wayfinder_router import
recalibrate` *and* any `recalibrate.gateway` / `recalibrate.GatewayConfig`
monkeypatch seam — which is why this is an ADR, not a silent refactor.

## Decision

Two performance items, each superseding a frozen contract, each with a
falsifying harness command.

1. **Single-pass feature scanner behind a `schema_version` bump.** Replace the
   four whole-text passes with one linear walk of the body that simultaneously
   accumulates structural line features and token/lexicon counts. Because
   collapsing the tokenizations changes feature/`score` bytes on some inputs
   (CRLF/form-feed/unicode edge cases), the new scanner ships **only under a new
   scorer/`schema_version`** — it inherits the versioned-scorer contract from
   WF-ADR-0045 (version "3" keeps the four-pass numerics byte-identical; the
   single-pass scanner is the version-"4" implementation, and its parity corpus
   is regenerated for that version with the JS mirror re-ported in lockstep).
   Projected `score_complexity` **~24.9µs → ~18–20µs/decision** (profile box;
   the fused-loop lexicon step alone is measured 6.2µs → 4.98µs, and the pass
   collapse extends that to the structural scan). If any feature-dict byte
   differs from version "3", the version bump *must* own it and re-baseline the
   quality baseline — that is the contract this supersedes.

2. **Lazy scoring-only import facade.** Split `__init__` so the pure scoring
   surface (`score_complexity`, `RoutingConfig`, `config`) imports with **no**
   calibrate/recalibrate/gateway subtree, resolving `recalibrate` and its
   re-exported gateway names lazily on first attribute access — implemented so
   the submodule/re-export collision is handled explicitly (bind the lazy names
   as real module attributes, not a bare PEP-562 shadow, so
   `from wayfinder_router import recalibrate` and any `recalibrate.<seam>`
   monkeypatch still resolve). Projected cold import **58ms → ~22ms** by
   dropping the asyncio-dominated gateway subtree; a scoring-only import
   (`from wayfinder_router import score_complexity`) projects to **~8–10ms**
   (complexity + config legs only). The gateway construction path is unchanged —
   it still pays asyncio once when the app is actually built. This needs an ADR
   because it reshapes the documented import graph and what
   `import wayfinder_router` eagerly binds — a public-surface / monkeypatch-seam
   contract under WF-ADR-0043, and the `recalibrate` ownership under
   WF-ADR-0007.

Explicitly **not** in scope: chasing `build_app` below ~16ms (94% is FastAPI
schema gen, framework-inherent) and shaving config parse below ~18µs (74% is
`tomllib.loads`, stdlib). The rebuild proved those are architecture-pinned; this
ADR does not pretend otherwise.

## Consequences

- **Positive.** The two items target the two costs that are actually movable and
  actually large: the scan-bound scorer hot path and the 43ms of import dead
  weight. Neither was reachable on the frozen branch.
- **Positive.** The lazy facade decouples the scoring core from the gateway
  subtree, which also makes the architecture legible independent of the runtime.
- **Negative.** The single-pass scanner is only shippable *with* a version bump
  and its own parity corpus — it cannot land as a "pure speed" refactor, because
  the rebuild proved the safe (call-count) version buys nothing on the clock and
  the fast (pass-collapsing) version perturbs bytes. Its value is coupled to
  WF-ADR-0045's versioned-scorer machinery.
- **Negative.** The lazy facade is a subtle contract change: any test or user
  that relies on `import wayfinder_router` eagerly binding gateway-side names
  will observe a lazy resolution instead. The `test_packaging` "no
  fastapi/httpx/rich/textual at import" guard already passes (heavy deps are lazy
  inside `build_app`); asyncio is stdlib and unpinned, but the seam grep must be
  run before shipping.
- **Honest limit.** These are microbench and import-time wins, not
  request-latency wins. Wayfinder never owns the upstream model round-trip
  (50ms–60s); no scorer speedup changes end-to-end request time. The scorer win
  matters for throughput of the decision itself and for batch scoring; the import
  win matters for CLI startup and for callers embedding only the scorer.

## Alternatives Considered

- **Ship the single-pass scanner as a contract-safe refactor.** Rejected: the
  rebuild already did the byte-safe version (genexpr fusion) and it did not move
  wall-clock, because the cost is scanning, not calls. The version that *does*
  move the clock collapses tokenizations and perturbs edge-case bytes — a parity
  violation without a version bump.
- **Naive PEP 562 `__getattr__` for lazy recalibrate.** Rejected as unsafe as
  written: the submodule/re-export name collision and the WF-ADR-0043
  monkeypatch seams mean a bare lazy shadow can break
  `from wayfinder_router import recalibrate` or a `recalibrate.gateway` patch.
  The facade must bind lazy names as real attributes — which is a deliberate
  design, hence an ADR.
- **Drop asyncio from the gateway import top.** Not sufficient alone: the async
  forward path genuinely needs asyncio, so any process running the gateway pays
  it once. The lever is removing it from the *cold-core* import, which the lazy
  facade does; excising it from gateway itself is neither possible nor the point.

## Success Measures

- **Scanner:** `python -m benchmarks.run` decide-µs ≤ ~20µs (profile-box knee),
  down from ~24.9µs, under `scorer_version = "4"`; version "3" decide-µs and
  `python tools/golden.py` bytes unchanged. A new-vs-old feature-dict diff over
  `benchmarks/dataset.jsonl` plus a fuzz corpus is the falsifier — any "4" diff
  from "3" must be owned by the version bump, and "3" must show zero diff.
- **Import:** `python -X importtime -c "import wayfinder_router" 2>&1 | tail -1`
  shows the wayfinder subtree ~22ms with asyncio no longer under it
  (`... | grep -E "wayfinder_router$|asyncio"`); a scoring-only import subtree
  sums < 12ms; `pytest -q tests/test_packaging.py tests/test_recalibrate.py`
  green (packaging guard holds, recalibrate still functions and its seams still
  patch).

## Related

- WF-ADR-0043 (scorer numerics + JS parity freeze, and module-attribute
  monkeypatch contract — both superseded here under a version bump / lazy facade)
- WF-ADR-0045 (versioned scorer — the `schema_version` machinery the single-pass
  scanner rides on)
- WF-ADR-0007 (scheduled recalibration — owns the `recalibrate` name the lazy
  facade must preserve)
- WF-ADR-0042 (JS decision-core parity — the byte-mirror the scanner version
  must re-port in lockstep)
- WF-ROADMAP-0010 (Phase E measurements: the wall-clock null result and the
  attributed costs)
