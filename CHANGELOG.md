# Changelog

User-visible changes to Wayfinder, by release. Follows the spirit of
[Keep a Changelog](https://keepachangelog.com/): user impact over implementation
details, release history over commit history.

## v0.1.4 — 2026-06-18

### Added

- **Streaming responses** (WF-ADR-0013). A request with `stream: true` is relayed back
  as Server-Sent-Events so chat clients (LibreChat, Open WebUI, …) render tokens
  progressively. The gateway now forwards asynchronously (`httpx.AsyncClient`), so
  concurrent requests no longer block one another.
- `wayfinder-router serve --dry-run` returns the routing decision (model, score, mode)
  without calling any upstream — try the router with no backends configured.
- A configurable upstream timeout via `WAYFINDER_ROUTER_TIMEOUT` or `serve --timeout`
  (default 60s), and an optional `WAYFINDER_ROUTER_FEEDBACK_TOKEN` that gates the
  `/v1/feedback` write behind a bearer token to prevent label-log poisoning.
- Every response carries an `x-wayfinder-router-request-id`; routing decisions, upstream
  errors, and config-reload failures are logged. `GET /healthz` reports `degraded` and
  lists `missing_keys` when a configured `api_key_env` is unset.

### Changed

- Upstream transport failures (timeout, connection refused) now return an OpenAI-shaped
  `wayfinder_router_upstream_error` (a `502`, or a terminal SSE error event for a stream)
  instead of a bare `500` with a traceback. Scoring and the WF-ADR-0001/0004 boundary are
  unchanged.

## v0.1.3 — 2026-06-18

### Added

- The gateway serves **`GET /v1/models`**, an OpenAI-compatible discovery list of
  the selectable routing options — `auto`, `prefer-local` / `prefer-hosted` (for a
  tiered/binary router), and each configured `[gateway.models]` endpoint. Any
  OpenAI-compatible client now auto-populates its model dropdown with the routing
  modes, so no hand-written model list is needed. Like `/healthz` it reads config
  only — no key, no model call, no network (WF-ADR-0012).
- Integration examples under `examples/` for putting a chat UI in front of the
  gateway with no fork: a LibreChat custom-endpoint config (`librechat.yaml`) and a
  Compose override that runs the gateway as a LibreChat sidecar, plus Open WebUI
  connection notes. They lean on the per-request override (WF-ADR-0011) so a UI's
  model dropdown becomes a per-conversation routing-mode picker.

### Changed

- The high-end routing directive is now **`prefer-hosted`** (was `prefer-cloud`),
  matching the local/hosted language used elsewhere and because the high end of a
  router is not always literally "cloud". `prefer-cloud` keeps working as a silent
  back-compat alias. `prefer-local` / `prefer-hosted` apply to a tiered/binary
  router; under a classifier (which has no ordered ladder) they now fall through to
  scoring rather than pinning (WF-ADR-0011 amendment).

## v0.1.2 — 2026-06-18

### Added

- The gateway accepts a **per-request routing override** so a client can steer a
  single call without changing the deployment's `wayfinder-router.toml`
  (WF-ADR-0011). The OpenAI `model` field is a routing directive — `auto` (or any
  ordinary model id) scores per config, a configured endpoint name pins the call
  to that endpoint, and `prefer-local` / `prefer-cloud` pin to the low / high end
  of the router — and an `X-Wayfinder-Threshold` header (a number in `0.0`–`1.0`)
  re-cuts a binary router for that one request. Responses gain an
  `x-wayfinder-router-mode` header (`scored` / `pinned` / `threshold-override`)
  alongside the existing `-model` / `-score` headers. The override only changes
  which endpoint a request routes to; scoring stays deterministic and key-free
  (WF-ADR-0001/0004).

## v0.1.1 — 2026-06-18

### Added

- An `all` install extra that pulls in the gateway and the UI in one step:
  `pip install "wayfinder-router[all]"` (equivalent to `[gateway,ui]`). The
  deterministic core stays zero-dependency (WF-ADR-0001); `all` is only a
  convenience aggregate of the existing optional extras.

### Changed

- Redesigned the local UI (`wayfinder-router ui`) as a branded, modern surface:
  a teal-on-cream/navy palette derived from the project banners, automatic light
  and dark themes (`prefers-color-scheme`), a wordmark + tagline header, carded
  sections, primary/secondary buttons, a custom-styled threshold slider, a
  recommendation pill, and refined tables and contribution bars. Presentation
  only — no behavior, endpoint, or dependency change (WF-ADR-0005).
- README install guidance now leads with `[gateway]` — the extra you need to
  route traffic through the proxy — clarifies that the bare install is the
  zero-dependency scorer/CLI/library, and documents `[all]`. Install snippets use
  the published `pip install "wayfinder-router[...]"` form.
- README gains a **"Where Wayfinder sits"** section — a diagram and explanation
  that Wayfinder is transparent middleware behind any OpenAI-compatible client
  (e.g. Open WebUI), and that local and hosted are *backends*, not separate UIs.

## v0.1.0 — 2026-06-18

The first public release. **Wayfinder** is a deterministic prompt-complexity
router: it scores a prompt's *structure* and recommends a **local** or **cloud**
model — offline, reproducible, and with no model call to make the decision. It
ships as its own product, **fully independent of RAC**: no `rac` import, no
`.rac/` reads, stdlib-only core, Python 3.11+. It was prototyped inside
requirements-as-code and split out because routing is a runtime *inference*
concern rather than recorded knowledge (RAC ADR-069/ADR-064); the scoring
boundary it inherits is RAC ADR-070, carried over intact. Apache-2.0.

### Added

- **Deterministic structural scorer and recommendation** (WF-ADR-0001).
  `wayfinder-router route` takes a prompt (file or stdin) and returns a bounded
  `0.0–1.0` complexity score over text *structure* — word count, headings and
  their depth, list items, links, code blocks, table rows — plus a local/cloud
  recommendation, as human output or JSON (`schema_version 2`). The same prompt
  and threshold always give the same answer; there is no model call, no API key,
  and no network in the scored path. A small `score_complexity` Python API backs
  the CLI, and the package ships typed (`py.typed`).

- **Tiered routing, a fitted classifier, and offline calibration**
  (WF-ADR-0002, WF-ADR-0003). Beyond the default binary cut, route by ordered
  score **tiers** to any number of models, or by a **multinomial-logistic
  classifier**. `wayfinder-router calibrate` turns a labeled JSONL dataset into a
  `wayfinder-router.toml` fragment — threshold sweep, tier sweep, or a classifier
  fit by deterministic L2-regularized Newton/IRLS (pure Python, no dependency,
  converges in a handful of iterations). Config is layered: `wayfinder-router.toml`
  walk-up, `--threshold`, and `WAYFINDER_ROUTER_THRESHOLD`.

- **OpenAI-compatible routing gateway, bring-your-own-key** (WF-ADR-0004).
  `wayfinder-router serve` runs a proxy that scores each incoming prompt and
  forwards it to the chosen upstream — point any OpenAI-compatible client's
  `base_url` at it, no application code change. Responses carry
  `x-wayfinder-router-model` and `x-wayfinder-router-score`. Keys are read from
  the environment at request time (each model's `api_key_env`), never stored in
  config or the scored path. Ships behind the `gateway` extra, lazily imported.

- **Local calibration / explain / configure UI** (WF-ADR-0005).
  `wayfinder-router ui` serves a local web app — **Explain** (per-feature
  contribution bars and a live threshold slider), **Calibrate** (paste a dataset,
  see accuracy and the sweep curve), **Configure** (edit `wayfinder-router.toml`
  with live validation), and **Onboard**. A thin consumer of the same pure core;
  no secret ever appears in it. Behind the `ui` extra.

- **Feedback loop and A/B onboarding** (WF-ADR-0006). `wayfinder-router onboard`
  A/B-tests a local vs hosted model on sample prompts and records your
  good-enough judgment; the gateway's `/v1/feedback` endpoint captures
  steady-state judgments. The label log *is* the `calibrate` dataset, so feedback
  turns straight into an updated config — collect → calibrate → route.

- **Scheduled recalibration with live hot-reload** (WF-ADR-0007).
  `wayfinder-router recalibrate` re-fits the routing config from the feedback log
  (run it from cron or a k8s CronJob, or click *Recalibrate & save* in the UI);
  it rewrites only the `[routing]` section and **preserves** your `[gateway]`
  endpoints. A running gateway hot-reloads the new config with no restart, and a
  malformed mid-flight write keeps the last-good config.

- **Deployable packaging** (WF-ADR-0008). A slim `Dockerfile` runs the gateway as
  a sidecar or service (only the `gateway` extra), with a `docker-compose`
  example that persists config + the feedback log and shows the recalibrate
  one-shot. The library runs in-process; the CLI, UI, and onboarding are the
  operator/bootstrap surfaces. Install extras: `gateway`, `ui`, `dev`.

### Boundary

- Wayfinder scores deterministically and **recommends**; it never invokes a
  model, selects a provider, reads a credential, or tokenizes per a vendor model
  — the caller runs inference (WF-ADR-0001 / RAC ADR-070). The deterministic core
  imports no web or SDK code; only the optional gateway and UI layers touch the
  network or keys, and only from the environment.
