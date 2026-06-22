# Hosting a Wayfinder demo

Two ways to put a "try it live" Wayfinder in front of people. The recipe below is the
quick one — a **decision-only gateway in a container**. For a zero-server, zero-cost
static demo (the scorer compiled into the browser), see
[WF-DESIGN-0002](../designs/WF-DESIGN-0002-static-serverless-demo.md).

## Why decision-only

The whole pitch — route (`● LOCAL` / `◆ CLOUD`), structural score, *why*, cost saved, the
live threshold slider — is computed by the deterministic scorer with **no model call and no
keys** (WF-ADR-0001). So host the gateway in `--dry-run`: it serves `/demo` and answers the
routing decision without ever calling an upstream. That means **no keys, no spend, no abuse
surface** (it can't run up a bill because it never calls a paid model), and it's the honest
core of the product — "no model call to decide."

## The recipe (dry-run container)

The repo ships a `Dockerfile` (the `[gateway]` extra) and a `docker-compose.example.yml`.
For a public demo, run `serve` (or `webchat`) with `--dry-run`:

```bash
# Build
docker build -t wayfinder-demo .

# Run the decision-only demo on :8088 (no keys, no upstream calls)
docker run --rm -p 8088:8088 wayfinder-demo \
  wayfinder-router serve --host 0.0.0.0 --port 8088 --dry-run
# open http://localhost:8088/demo
```

- Bind `0.0.0.0` (the image already does) so the container is reachable.
- `/healthz` is a cheap health check for the platform.
- It's **stateless** — demo threads live in the browser's localStorage (WF-ADR-0026) — so a
  single small instance is enough and restarts/scale-out are free.

Deploy that container to anything that runs one: Fly.io, Render, Railway, Cloud Run, or a
small VPS. The dry-run scorer is microseconds of pure CPU and `/demo` is one small HTML page,
so a tiny instance handles a launch spike; cache the static HTML and add basic per-IP rate
limiting at the proxy to be safe.

## What it costs (honestly)

The resource footprint is tiny; the cost is really about the platform's pricing model:

| Path | Cost | Catch |
| --- | --- | --- |
| Static + WASM/JS (WF-DESIGN-0002) | **$0, forever** | no cold start, infinite scale — but needs the client-side scorer build |
| Cloud Run / HF Spaces (free tier) | ~$0 for bursty traffic | scales-to-zero → **cold start** on the first hit; pre-warm before posting |
| Render / Railway (free) | $0 | sleeps after ~15 min idle — worst case for a launch spike |
| $5 VPS, or a warm Render/Fly instance | **~$4–7/mo** | none — always warm, survives the front-page hug |

For a launch window, the simplest reliable option is **a ~$5/mo warm instance** (cancel it
after); the genuinely-free-and-robust option is the **static/WASM demo** in WF-DESIGN-0002.
Avoid the sleep-after-15-min free tiers — the cold start lands exactly when the post does.

## If you must show real replies

A demo that returns model output needs keys and therefore guardrails. Run the gateway
**without** `--dry-run`, with `[gateway.models]` configured, and:

- keys via the platform's secret store (env vars, per `api_key_env` — never baked into the
  image);
- a hard **budget cap** on the provider account;
- **per-IP rate limiting** and a small context/token cap at the proxy;
- lock the OpenAI `model` field (don't let visitors pin everything to the expensive arm);
- ideally a cheap or self-hosted local tier so most traffic costs nothing.

This is real ops and ongoing cost. For a launch, the decision-only demo above (or the static
one) makes the point without any of it.
