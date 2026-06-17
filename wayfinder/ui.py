"""Optional local calibration/explain/configure UI (WF-ADR-0005).

A thin consumer of the pure core: it scores prompts and explains the result, it
never invokes a model and never reimplements scoring or calibration. It binds
localhost and ships behind the ``wayfinder[ui]`` extra; ``fastapi``/``uvicorn``
are imported lazily so the core stays dependency-free.

Phase 1 is the Explain / Playground screen: paste a prompt, see its score, the
recommendation, the tier ladder, and each feature's contribution to the score —
and move a threshold slider to watch the routing change live.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .complexity import RoutingConfig, binary_tiers, explain_score, score_complexity
from .config import load_routing_config

if TYPE_CHECKING:  # type-only; the runtime imports these lazily inside build_ui_app
    from fastapi import FastAPI

_INSTALL_HINT = "the UI needs its extra: pip install 'wayfinder[ui]'"


class UIUnavailable(Exception):
    """The UI extra (fastapi / uvicorn) is not installed."""


def score_payload(prompt: str, start_dir: str = ".", threshold: float | None = None) -> dict:
    """Score ``prompt`` and return an explain-ready payload (pure; no model call)."""
    config = load_routing_config(start_dir)
    if threshold is not None:
        config = RoutingConfig(weights=config.weights, tiers=binary_tiers(threshold))
    result = score_complexity(prompt, config=config)
    payload = result.to_dict()
    payload["contributions"] = [
        fc.to_dict() for fc in explain_score(result.features, config.weights)
    ]
    return payload


def build_ui_app(start_dir: str = ".") -> FastAPI:
    """Build the FastAPI UI app."""
    try:
        from fastapi import Body, FastAPI
        from fastapi.responses import HTMLResponse
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise UIUnavailable(_INSTALL_HINT) from exc

    app = FastAPI(title="wayfinder-ui")

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return _PAGE

    @app.post("/api/score")
    def api_score(body: dict = Body(...)) -> dict:  # noqa: B008 - FastAPI default
        raw_prompt = body.get("prompt")
        prompt = raw_prompt if isinstance(raw_prompt, str) else ""
        raw_threshold = body.get("threshold")
        threshold = float(raw_threshold) if isinstance(raw_threshold, (int, float)) else None
        return score_payload(prompt, start_dir=start_dir, threshold=threshold)

    return app


def run_ui(  # pragma: no cover
    start_dir: str = ".", host: str = "127.0.0.1", port: int = 8099
) -> None:
    """Serve the UI with uvicorn (the `wayfinder ui` command)."""
    try:
        import uvicorn
    except ImportError as exc:
        raise UIUnavailable(_INSTALL_HINT) from exc
    uvicorn.run(build_ui_app(start_dir), host=host, port=port)


# A single no-build page: vanilla JS talks to /api/score. Kept inline so the UI
# ships as part of the package with no static-asset or frontend build step.
_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Wayfinder — Explain</title>
<style>
  :root { color-scheme: light dark; }
  body { font: 15px/1.5 system-ui, sans-serif; margin: 0; padding: 1.5rem;
         max-width: 880px; margin-inline: auto; }
  h1 { font-size: 1.2rem; margin: 0 0 1rem; }
  textarea { width: 100%; min-height: 150px; box-sizing: border-box; padding: .6rem;
             font: 13px/1.4 ui-monospace, monospace; }
  .row { display: flex; gap: 1rem; align-items: center; margin: .8rem 0; flex-wrap: wrap; }
  .rec { font-size: 1.5rem; font-weight: 700; }
  .muted { opacity: .65; }
  .bar { height: 10px; background: #4f8cff; border-radius: 5px; }
  .track { background: rgba(127,127,127,.18); border-radius: 5px; flex: 1; }
  table { width: 100%; border-collapse: collapse; margin-top: .5rem; }
  td, th { text-align: left; padding: .25rem .5rem; border-bottom: 1px solid rgba(127,127,127,.2);
           font-variant-numeric: tabular-nums; }
  th { font-weight: 600; opacity: .7; font-size: .85rem; }
  .tier { padding: .2rem .5rem; border-radius: 4px; }
  .tier.on { background: #4f8cff; color: #fff; font-weight: 600; }
  input[type=range] { flex: 1; }
</style>
</head>
<body>
  <h1>Wayfinder — Explain &amp; Playground</h1>
  <textarea id="prompt" placeholder="Paste a prompt to score it..."></textarea>
  <div class="row">
    <label>Threshold override: <output id="tval">off</output></label>
    <input type="range" id="threshold" min="0" max="1" step="0.01" value="-1" list="ticks">
    <button id="clear">use config</button>
  </div>
  <div class="row">
    <span class="rec" id="rec">—</span>
    <span class="muted" id="score"></span>
  </div>
  <div id="tiers" class="row"></div>
  <table>
    <thead><tr><th>Feature</th><th>Value</th><th>Norm</th><th>Weight</th>
      <th>Contribution</th><th></th></tr></thead>
    <tbody id="breakdown"></tbody>
  </table>
<script>
const $ = id => document.getElementById(id);
let timer;
function schedule() { clearTimeout(timer); timer = setTimeout(run, 150); }

async function run() {
  const prompt = $("prompt").value;
  const t = parseFloat($("threshold").value);
  const threshold = t >= 0 ? t : null;
  $("tval").textContent = threshold === null ? "off" : threshold.toFixed(2);
  const resp = await fetch("/api/score", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({prompt, threshold})
  });
  const data = await resp.json();
  $("rec").textContent = data.recommendation;
  $("score").textContent = "score " + data.score.toFixed(2) + " · " + data.mode;

  const tiers = $("tiers"); tiers.innerHTML = "";
  (data.tiers || []).forEach(t => {
    const el = document.createElement("span");
    el.className = "tier" + (t.model === data.recommendation ? " on" : "");
    el.textContent = "≥ " + t.min_score.toFixed(2) + " " + t.model;
    tiers.appendChild(el);
  });
  if (data.models) {
    const el = document.createElement("span");
    el.className = "muted";
    el.textContent = "candidates: " + data.models.join(", ");
    tiers.appendChild(el);
  }

  const body = $("breakdown"); body.innerHTML = "";
  const max = Math.max(0.0001, ...data.contributions.map(c => c.contribution));
  data.contributions.forEach(c => {
    const tr = document.createElement("tr");
    const pct = (100 * c.contribution / max).toFixed(0);
    tr.innerHTML = `<td>${c.name}</td><td>${c.value}</td><td>${c.normalized.toFixed(2)}</td>` +
      `<td>${c.weight}</td><td>${c.contribution.toFixed(3)}</td>` +
      `<td class="track"><div class="bar" style="width:${pct}%"></div></td>`;
    body.appendChild(tr);
  });
}
$("prompt").addEventListener("input", schedule);
$("threshold").addEventListener("input", schedule);
$("clear").addEventListener("click", () => { $("threshold").value = -1; run(); });
run();
</script>
</body>
</html>
"""
