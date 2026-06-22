---
schema_version: 1
id: WF-DESIGN-0002
type: design
tags: [demo, ui, static, wasm, hosting, distribution]
---

# WF-DESIGN-0002: A Static, Serverless `/demo` (client-side scoring)

## Status

Proposed

> A zero-server, zero-cost "try it live" demo for the launch / Show HN: the deterministic
> scorer runs **in the browser**, so `/demo` becomes static files on a CDN — no gateway, no
> keys, no upstream calls, no cold start, infinite scale. Companion to the simpler
> warm-host recipe (`docs/hosting.md`) and the terminal chat (WF-DESIGN-0001).

## Context

`wayfinder_router/demo.html` (the canonical `/demo`, WF-ADR-0020) is a *thin client* over
the gateway: it `fetch`es `POST /v1/chat/completions` (with `X-Wayfinder-Debug` to read the
decision), `POST /router/config` (the live threshold/tuning preview), and
`GET /router/models` / `/router/profiles`. The scoring therefore happens **server-side**, in
the Python deterministic core. A hosted *live* demo consequently needs a running gateway —
a server to provision, a cold-start to eat on the first HN click, and (if it returns real
replies) keys, spend, and an abuse surface.

But the entire decision-first pitch — the route (`● LOCAL` / `◆ CLOUD`), the structural
score, the *why* breakdown, the cost-saved estimate, and the live threshold slider — is
computed by the **stdlib-only, deterministic scorer with no model call and no keys**
(WF-ADR-0001). If that scorer runs in the browser, `/demo` reduces to static files. That is
the most robust possible launch artifact (survives a front-page spike at $0) and the most
*on-brand* one: the actual routing decision, deterministic and offline, running in the
visitor's own browser.

## User Need

A link-and-go demo for the launch: no signup, no keys, no install, no server to fall over.
It must (a) cost ~$0 and shrug off an HN/Show HN traffic spike, (b) show the same decision
the installed tool would (credibility — this is a *deterministic* router), and (c) need no
secrets and present no abuse surface.

## Design

### Scope: decision-only

A static page cannot hold an API key or call an upstream, and shouldn't. So the static demo
is **decision-only** — exactly the experience `--dry-run` already gives: type a prompt and
see the route, score, *why*, cost-saved, and live tuning; the localStorage threads
(WF-ADR-0026) and scoring overrides (WF-ADR-0023) are already client-side and carry over
unchanged. Model *replies* are replaced by a clear call to action ("decision-only —
`pip install wayfinder-router` for replies"). This is the honest core: "no model call to
decide" is literally what a server-free demo proves.

### Client-side scoring — two implementations

**Option 1 — a JavaScript port of the scorer.** Reimplement feature extraction
(`word_count`, `heading_count`, `list_item_count`, `code_block_count`, `max_heading_depth`,
`link_count`, lexicon term counts), the normalized weighted sum, and the tier/threshold
logic in a few hundred lines of dependency-free JS, inlined into the page.

- *Pro:* tiny, instant load — the demo stays one small static file.
- *Con:* a **second implementation** of the deterministic core, which can drift from the
  Python source — corrosive for a tool whose whole promise is "deterministic and the same
  every time."
- *Mandatory drift gate:* a **golden parity corpus** — a JSON of prompts → the exact
  features / score / decision produced by the Python core, regenerated from the core in CI.
  A CI job runs the JS scorer under Node against the corpus and asserts byte-for-byte parity
  to fixed precision; any divergence fails CI and forces the port back in sync. This keeps
  the determinism guarantee: the JS scorer is *proven* identical to the Python one over the
  corpus, not merely "close."

**Option 2 — Pyodide (CPython → WebAssembly).** Load Pyodide, load the stdlib-only
`wayfinder_router` core, and call `score_complexity` / `explain_score` directly.

- *Pro:* the **exact same code** — zero drift by construction; the most honest story ("the
  real scorer, compiled to WASM, running in your browser").
- *Con:* Pyodide is a multi-MB runtime (~6–15 MB) → seconds of first-load latency and a
  heavier page — a real bounce risk for drive-by launch traffic. Mitigate by lazy-loading
  (render the UI immediately, enable scoring when ready) with a loading state, and lean on
  CDN caching of the Pyodide assets. The core itself is stdlib-only, so nothing heavy beyond
  Pyodide is bundled.

**Recommendation.** Lead with **Option 1 (JS port) + the CI parity corpus** for launch: it
loads instantly, stays a single small file, and the parity gate preserves determinism. Keep
**Option 2 (Pyodide)** documented as the zero-drift-by-construction alternative for when
load weight is acceptable or maintaining the port becomes a tax. The choice is a genuine
trade-off (load-time UX vs. single source of truth) and is left as an Open Question.

### One HTML, two decision sources (no fork)

`demo.html` is canonical (WF-ADR-0020) and must not be hand-forked. Introduce a small JS
**seam** — `decide(prompt, config) -> decision` — with two implementations: the existing
*remote* one (`fetch` the gateway) and a new *local* one (the JS port / Pyodide). The active
implementation is selected at build (or by a flag the page reads). The static build then is
"the same `demo.html` + the local `decide`," and the served gateway keeps using the remote
one. The threshold slider and tuning call `decide` locally too (instant; no `/router/config`
round-trip).

### Config from the core, not copied by hand

The defaults the scorer needs — `DEFAULT_WEIGHTS`, `DEFAULT_LEXICON`, `DEFAULT_TIERS`,
`FEATURE_ORDER`, and the stock profiles — are emitted to a JSON bundle **by a build step
that imports the core**, so the static demo uses the same constants as the gateway. No
copy-pasted weights.

### Build & deploy

A build script (`scripts/build_static_demo.py`) imports the core to emit the config JSON,
takes the canonical `demo.html`, selects the local `decide`, inlines the JS scorer (or the
Pyodide bootstrap) and the config JSON, and writes a `site/` directory of static files. CI
builds it, runs the parity corpus, and publishes to GitHub Pages (or Netlify) on tag/merge.
Because the output is static, it can also be opened from disk (double-click `index.html`) —
a tidy "it's just files, no server" story.

## Constraints

- **Decision-only** by nature (no replies, no keys, no upstream).
- **Exact parity** with the Python core — enforced by the corpus (Option 1) or guaranteed by
  same-code (Option 2). A demo that disagreed with the installed tool would undercut the
  whole pitch.
- **Single source for `demo.html`** (WF-ADR-0020): generate the static build via the seam;
  never maintain a second copy.
- **Defaults from the core** (WF-ADR-0001): no hand-copied weights/lexicon/tiers.

## Rationale

$0 hosting, infinite CDN scale, no cold start, no secrets, no abuse surface — and it embodies
the product thesis by running the genuine deterministic decision in the browser. For a launch
spike it is strictly more robust than any single server, and it is the cleanest possible
proof of "deterministic, offline, no model call to decide."

## Alternatives

- **Dry-run hosted gateway** (`docs/hosting.md`): deploy the existing container with
  `serve --dry-run`. Simplest path, reuses everything, but it is a server — cost, ops, and a
  cold start at the worst moment on free tiers. A fine interim; documented separately.
- **Live hosted gateway (real replies):** the gateway with keys behind rate-limiting and a
  budget cap. Returns replies, but adds spend, abuse management, and ops. Rejected for a
  public launch demo; the decision-only demo already sells it.
- **Keep it server-rendered only:** defeats the zero-server goal.

## Accessibility

The static build inherits the canonical `/demo`'s accessibility (WF-ADR-0020): the
local/cloud distinction is carried by glyph **and** label (`● LOCAL` / `◆ CLOUD`), not colour
alone; the page is keyboard-navigable and uses the brand palette's contrast pair. Two
build-specific points: under the Pyodide option, the first-load "loading…" state must be
announced (an `aria-live` region), since scoring is briefly unavailable; and the demo must
degrade gracefully where WASM or JS is unavailable (a static note explaining the install
path), so the page is never a blank screen.

## Open Questions

- **JS port vs. Pyodide** — the central trade-off (instant load + a parity gate vs. heavier
  load + zero drift by construction). Decide before building.
- **Host** — GitHub Pages vs. Netlify; custom domain (e.g. `demo.wayfinder…`).
- **Replies teaser** — leave purely decision-only, or stub a canned "reply" to illustrate the
  full chat shape (clearly labelled, no model call)?

## Success Measures

- Opened with the network cut after load, the static demo returns the **same** route / score
  / *why* as `wayfinder-router route` across the parity corpus.
- $0 hosting; survives a front-page spike (static CDN); no secrets, no upstream calls.
- Built from the canonical `demo.html` via the `decide` seam (no forked HTML); all scorer
  constants emitted from the core.

## Related

- WF-ADR-0020 (the canonical `/demo` this derives from, via the `decide` seam)
- WF-ADR-0001 (the deterministic, stdlib-only core — what makes client-side scoring possible)
- WF-ADR-0026 (client-side threads — already serverless, carried over)
- WF-ADR-0023 (in-demo scoring overrides — now computed entirely client-side)
- WF-DESIGN-0001 (the terminal-chat sibling surface)
- WF-ROADMAP-0004 (packaging & distribution — the hosted-demo initiative)
