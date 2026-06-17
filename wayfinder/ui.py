"""Optional local calibration/explain/configure UI (WF-ADR-0005).

A thin consumer of the pure core: it scores prompts, calibrates from pasted data,
and edits the config file — it never invokes a model and never reimplements
scoring, calibration, or config parsing. It binds localhost and ships behind the
``wayfinder[ui]`` extra; ``fastapi``/``uvicorn`` are imported lazily so the core
stays dependency-free.

Four screens, all backed by core functions:

- **Explain / Playground** — paste a prompt; see score, recommendation, tier
  ladder, and per-feature contributions; drag a threshold slider live.
- **Calibrate** — paste a labeled JSONL dataset; run a mode; see accuracy, the
  threshold-sweep curve, and the resulting config fragment.
- **Configure** — edit ``wayfinder.toml`` with live validation (the real loaders)
  and save. Secrets never appear: a gateway model carries ``api_key_env`` (the
  variable *name*), and the key is read from the environment elsewhere.
- **Onboard** — A/B a local vs hosted model on sample prompts in the browser,
  judge each, and record labels (WF-ADR-0006). The A/B run uses the gateway
  invoker (BYO key); recording and calibrating reuse the pure feedback/calibrate
  functions.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from .calibrate import CalibrationError, calibrate, parse_dataset, sweep_curve
from .complexity import RoutingConfig, binary_tiers, explain_score, score_complexity
from .config import (
    WayfinderConfigError,
    dump_routing_toml,
    find_config_file,
    load_routing_config,
    routing_config_from_toml,
)
from .feedback import DEFAULT_LOG, read_labels, record_label
from .gateway import (
    GatewayUnavailable,
    gateway_config_from_toml,
    invoke_model,
    load_gateway_config,
)

if TYPE_CHECKING:  # type-only; the runtime imports these lazily inside build_ui_app
    from fastapi import FastAPI

_INSTALL_HINT = "the UI needs its extra: pip install 'wayfinder[ui]'"
_MODES = ("threshold", "tiers", "classifier")


class UIUnavailable(Exception):
    """The UI extra (fastapi / uvicorn) is not installed."""


# --- pure helpers (testable without the extra) ------------------------------


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


def calibrate_payload(
    dataset_text: str, mode: str = "threshold", models: list[str] | None = None
) -> dict:
    """Calibrate from pasted JSONL and return the fragment, summary, and curve."""
    samples = parse_dataset(dataset_text)
    result = calibrate(samples, mode, models_order=models)
    payload: dict = {"toml": result.toml, "summary": result.summary}
    if mode == "threshold":
        payload["curve"] = [{"threshold": t, "accuracy": a} for t, a in sweep_curve(samples)]
    return payload


def current_config_text(start_dir: str = ".") -> str:
    """The current ``wayfinder.toml`` text, or a dumped default when none exists."""
    path = find_config_file(start_dir)
    if path is not None:
        return path.read_text(encoding="utf-8")
    return dump_routing_toml(RoutingConfig())


def validate_config_text(text: str) -> str | None:
    """Validate config text through the real loaders; return an error or None."""
    try:
        routing_config_from_toml(text)
        gateway_config_from_toml(text)
    except WayfinderConfigError as exc:
        return str(exc)
    return None


def save_config_text(text: str, start_dir: str = ".") -> str | None:
    """Validate then write ``wayfinder.toml``; return an error string or None."""
    error = validate_config_text(text)
    if error is not None:
        return error
    (Path(start_dir) / "wayfinder.toml").write_text(text, encoding="utf-8")
    return None


def _log_path(start_dir: str) -> str:
    return str(Path(start_dir) / DEFAULT_LOG)


def onboard_arms(start_dir: str = ".") -> list[str]:
    """The first two configured gateway models — the local/hosted arms to A/B."""
    return list(load_gateway_config(start_dir).models)[:2]


def onboard_run(start_dir: str, prompt: str, arms: list[str] | None = None) -> dict[str, str]:
    """Run ``prompt`` through each arm and return its output (invokes models, BYO key)."""
    gateway = load_gateway_config(start_dir)
    chosen = arms or list(gateway.models)[:2]
    return {arm: invoke_model(gateway.models[arm], prompt) for arm in chosen}


def record_onboard_label(start_dir: str, prompt: str, label: str) -> int:
    """Append a judgment to the feedback log; return the running label count."""
    record_label(_log_path(start_dir), prompt, label)
    return len(read_labels(_log_path(start_dir)))


def onboard_dataset_text(start_dir: str) -> str:
    """The feedback log as JSONL dataset text, for the Calibrate flow."""
    rows = read_labels(_log_path(start_dir))
    return "\n".join(json.dumps(r, ensure_ascii=False) for r in rows)


def _models_list(value: object) -> list[str] | None:
    if isinstance(value, list):
        return [str(m).strip() for m in value if str(m).strip()] or None
    if isinstance(value, str) and value.strip():
        return [m.strip() for m in value.split(",") if m.strip()] or None
    return None


# --- web app ----------------------------------------------------------------


def build_ui_app(start_dir: str = ".") -> FastAPI:
    """Build the FastAPI UI app."""
    try:
        from fastapi import Body, FastAPI
        from fastapi.responses import HTMLResponse, JSONResponse
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

    @app.post("/api/calibrate")
    def api_calibrate(body: dict = Body(...)) -> object:  # noqa: B008 - FastAPI default
        raw_dataset = body.get("dataset")
        dataset = raw_dataset if isinstance(raw_dataset, str) else ""
        raw_mode = body.get("mode")
        mode = raw_mode if isinstance(raw_mode, str) and raw_mode in _MODES else "threshold"
        try:
            return calibrate_payload(dataset, mode, _models_list(body.get("models")))
        except CalibrationError as exc:
            return JSONResponse(status_code=400, content={"error": str(exc)})

    @app.get("/api/config")
    def api_get_config() -> dict:
        return {"toml": current_config_text(start_dir)}

    @app.post("/api/config/validate")
    def api_validate(body: dict = Body(...)) -> dict:  # noqa: B008 - FastAPI default
        raw_toml = body.get("toml")
        error = validate_config_text(raw_toml if isinstance(raw_toml, str) else "")
        return {"ok": error is None, "error": error}

    @app.post("/api/config/save")
    def api_save(body: dict = Body(...)) -> object:  # noqa: B008 - FastAPI default
        raw_toml = body.get("toml")
        error = save_config_text(raw_toml if isinstance(raw_toml, str) else "", start_dir)
        if error is not None:
            return JSONResponse(status_code=400, content={"error": error})
        return {"ok": True}

    @app.get("/api/onboard")
    def api_onboard_state() -> dict:
        return {"arms": onboard_arms(start_dir), "count": len(read_labels(_log_path(start_dir)))}

    @app.post("/api/onboard/run")
    def api_onboard_run(body: dict = Body(...)) -> object:  # noqa: B008 - FastAPI default
        raw_prompt = body.get("prompt")
        prompt = raw_prompt if isinstance(raw_prompt, str) else ""
        if not prompt:
            return JSONResponse(status_code=400, content={"error": "missing 'prompt'"})
        try:
            return {"outputs": onboard_run(start_dir, prompt, _models_list(body.get("arms")))}
        except GatewayUnavailable as exc:
            return JSONResponse(status_code=400, content={"error": str(exc)})
        except RuntimeError as exc:  # an upstream model error
            return JSONResponse(status_code=502, content={"error": str(exc)})

    @app.post("/api/onboard/record")
    def api_onboard_record(body: dict = Body(...)) -> object:  # noqa: B008 - FastAPI default
        raw_prompt, raw_label = body.get("prompt"), body.get("label")
        if not isinstance(raw_prompt, str) or not raw_prompt:
            return JSONResponse(status_code=400, content={"error": "missing 'prompt'"})
        if not isinstance(raw_label, str) or not raw_label:
            return JSONResponse(status_code=400, content={"error": "missing 'label'"})
        return {"ok": True, "count": record_onboard_label(start_dir, raw_prompt, raw_label)}

    @app.get("/api/onboard/dataset")
    def api_onboard_dataset() -> dict:
        return {"dataset": onboard_dataset_text(start_dir)}

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


# A single no-build page: vanilla JS talks to the /api endpoints. Kept inline so
# the UI ships as part of the package with no static-asset or frontend build step.
_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Wayfinder</title>
<style>
  :root { color-scheme: light dark; }
  body { font: 15px/1.5 system-ui, sans-serif; margin: 0; padding: 1.5rem;
         max-width: 880px; margin-inline: auto; }
  h1 { font-size: 1.2rem; margin: 0 0 .5rem; }
  nav button { font: inherit; padding: .4rem .8rem; border: 0; cursor: pointer;
               background: transparent; border-bottom: 2px solid transparent; }
  nav button.on { border-bottom-color: #4f8cff; font-weight: 600; }
  section { display: none; margin-top: 1rem; }
  section.on { display: block; }
  textarea { width: 100%; box-sizing: border-box; padding: .6rem;
             font: 13px/1.4 ui-monospace, monospace; }
  #prompt, #dataset { min-height: 130px; }
  #toml { min-height: 220px; }
  .row { display: flex; gap: 1rem; align-items: center; margin: .8rem 0; flex-wrap: wrap; }
  .rec { font-size: 1.4rem; font-weight: 700; }
  .muted { opacity: .65; }
  .ok { color: #1a9a4b; } .err { color: #d33; white-space: pre-wrap; }
  .bar { height: 10px; background: #4f8cff; border-radius: 5px; }
  .track { background: rgba(127,127,127,.18); border-radius: 5px; flex: 1; }
  table { width: 100%; border-collapse: collapse; margin-top: .5rem; }
  td, th { text-align: left; padding: .25rem .5rem; border-bottom: 1px solid rgba(127,127,127,.2);
           font-variant-numeric: tabular-nums; }
  th { font-weight: 600; opacity: .7; font-size: .85rem; }
  .tier { padding: .2rem .5rem; border-radius: 4px; }
  .tier.on { background: #4f8cff; color: #fff; font-weight: 600; }
  pre { background: rgba(127,127,127,.12); padding: .6rem; overflow:auto; border-radius: 6px; }
  input[type=range] { flex: 1; }
  button.act { font: inherit; padding: .4rem .9rem; cursor: pointer; }
</style>
</head>
<body>
  <h1>Wayfinder</h1>
  <nav>
    <button data-tab="explain" class="on">Explain</button>
    <button data-tab="calibrate">Calibrate</button>
    <button data-tab="configure">Configure</button>
    <button data-tab="onboard">Onboard</button>
  </nav>

  <section id="explain" class="on">
    <textarea id="prompt" placeholder="Paste a prompt to score it..."></textarea>
    <div class="row">
      <label>Threshold override: <output id="tval">off</output></label>
      <input type="range" id="threshold" min="0" max="1" step="0.01" value="-1">
      <button class="act" id="clear">use config</button>
    </div>
    <div class="row"><span class="rec" id="rec">—</span><span class="muted" id="score"></span></div>
    <div id="tiers" class="row"></div>
    <table><thead><tr><th>Feature</th><th>Value</th><th>Norm</th><th>Weight</th>
      <th>Contribution</th><th></th></tr></thead><tbody id="breakdown"></tbody></table>
  </section>

  <section id="calibrate">
    <p class="muted">Paste a labeled dataset, one JSON object per line:
      <code>{"text": "...", "label": "local"}</code></p>
    <textarea id="dataset" placeholder='{"text": "summarise this", "label": "local"}'></textarea>
    <div class="row">
      <label>Mode <select id="mode">
        <option value="threshold">threshold</option>
        <option value="tiers">tiers</option>
        <option value="classifier">classifier</option>
      </select></label>
      <input id="models" placeholder="models order (optional, comma-separated)">
      <button class="act" id="runcal">Calibrate</button>
    </div>
    <div id="calsummary" class="muted"></div>
    <div id="calerr" class="err"></div>
    <div id="curve"></div>
    <pre id="calout" hidden></pre>
    <button class="act" id="tocfg" hidden>Send to Configure →</button>
  </section>

  <section id="configure">
    <p class="muted">Edit <code>wayfinder.toml</code>. Keys are never stored here —
      a gateway model names an <code>api_key_env</code> and the secret stays in the
      environment.</p>
    <textarea id="toml"></textarea>
    <div class="row">
      <button class="act" id="validate">Validate</button>
      <button class="act" id="save">Save</button>
      <span id="cfgstatus"></span>
    </div>
  </section>

  <section id="onboard">
    <p class="muted">A/B a local vs hosted model on sample prompts, judge each, and
      record labels. Needs two <code>[gateway.models]</code> and the
      <code>[gateway]</code> extra. Arms: <span id="arms">—</span> · labels so far:
      <span id="lblcount">0</span></p>
    <textarea id="prompts" placeholder='one prompt per line, or {"text": "..."}'></textarea>
    <div class="row">
      <button class="act" id="startob">Start</button>
      <span class="muted" id="obprogress"></span>
    </div>
    <div id="obcurrent" hidden>
      <pre id="obprompt"></pre>
      <div class="row" id="obarms"></div>
      <div class="row" id="objudge"></div>
    </div>
    <div id="oberr" class="err"></div>
    <div class="row"><button class="act" id="obcal">Calibrate from log →</button></div>
  </section>

<script>
const $ = id => document.getElementById(id);
async function post(url, body) {
  const r = await fetch(url, {method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify(body)});
  return {ok: r.ok, data: await r.json()};
}

// --- tabs ---
document.querySelectorAll("nav button").forEach(b => b.addEventListener("click", () => {
  document.querySelectorAll("nav button").forEach(x => x.classList.remove("on"));
  document.querySelectorAll("section").forEach(x => x.classList.remove("on"));
  b.classList.add("on");
  $(b.dataset.tab).classList.add("on");
  if (b.dataset.tab === "configure" && !$("toml").value) loadConfig();
  if (b.dataset.tab === "onboard") loadOnboardState();
}));

// --- explain ---
let timer;
function scheduleScore() { clearTimeout(timer); timer = setTimeout(score, 150); }
async function score() {
  const t = parseFloat($("threshold").value);
  const threshold = t >= 0 ? t : null;
  $("tval").textContent = threshold === null ? "off" : threshold.toFixed(2);
  const {data} = await post("/api/score", {prompt: $("prompt").value, threshold});
  $("rec").textContent = data.recommendation;
  $("score").textContent = "score " + data.score.toFixed(2) + " · " + data.mode;
  const tiers = $("tiers"); tiers.innerHTML = "";
  (data.tiers || []).forEach(t => {
    const el = document.createElement("span");
    el.className = "tier" + (t.model === data.recommendation ? " on" : "");
    el.textContent = "≥ " + t.min_score.toFixed(2) + " " + t.model;
    tiers.appendChild(el);
  });
  if (data.models) { const el = document.createElement("span"); el.className = "muted";
    el.textContent = "candidates: " + data.models.join(", "); tiers.appendChild(el); }
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
$("prompt").addEventListener("input", scheduleScore);
$("threshold").addEventListener("input", scheduleScore);
$("clear").addEventListener("click", () => { $("threshold").value = -1; score(); });

// --- calibrate ---
$("runcal").addEventListener("click", async () => {
  $("calerr").textContent = ""; $("curve").innerHTML = "";
  const {ok, data} = await post("/api/calibrate",
    {dataset: $("dataset").value, mode: $("mode").value, models: $("models").value});
  if (!ok) { $("calsummary").textContent = ""; $("calout").hidden = true;
    $("tocfg").hidden = true; $("calerr").textContent = data.error; return; }
  $("calsummary").textContent = Object.entries(data.summary)
    .map(([k, v]) => k + "=" + v).join(" · ");
  if (data.curve) {
    const max = Math.max(...data.curve.map(p => p.accuracy));
    data.curve.forEach(p => {
      const row = document.createElement("div"); row.className = "row";
      const pct = (100 * p.accuracy).toFixed(0);
      row.innerHTML = `<span class="muted" style="width:4rem">${p.threshold.toFixed(2)}</span>` +
        `<span class="track"><span class="bar" style="width:${pct}%"></span></span>` +
        `<span style="width:3rem">${p.accuracy.toFixed(2)}</span>`;
      $("curve").appendChild(row);
    });
  }
  $("calout").textContent = data.toml; $("calout").hidden = false; $("tocfg").hidden = false;
});
$("tocfg").addEventListener("click", () => {
  $("toml").value = $("calout").textContent;
  document.querySelector('nav button[data-tab="configure"]').click();
  $("cfgstatus").textContent = "pasted from calibrate — review and save";
});

// --- configure ---
async function loadConfig() {
  const r = await fetch("/api/config"); const data = await r.json();
  $("toml").value = data.toml;
}
$("validate").addEventListener("click", async () => {
  const {data} = await post("/api/config/validate", {toml: $("toml").value});
  $("cfgstatus").innerHTML = data.ok
    ? '<span class="ok">valid</span>' : '<span class="err">' + data.error + '</span>';
});
$("save").addEventListener("click", async () => {
  const {ok, data} = await post("/api/config/save", {toml: $("toml").value});
  $("cfgstatus").innerHTML = ok
    ? '<span class="ok">saved</span>' : '<span class="err">' + data.error + '</span>';
});

// --- onboard ---
let obQueue = [], obIndex = 0, obArms = [];
function parsePrompts(text) {
  return text.split("\\n").map(l => l.trim()).filter(Boolean).map(l => {
    if (l.startsWith("{")) {
      try { const o = JSON.parse(l); if (o && typeof o.text === "string") return o.text; }
      catch (e) { /* fall through to raw line */ }
    }
    return l;
  });
}
async function loadOnboardState() {
  const r = await fetch("/api/onboard"); const d = await r.json();
  obArms = d.arms;
  $("arms").textContent = d.arms.length >= 2 ? d.arms.join(" vs ")
    : "(configure two [gateway.models])";
  $("lblcount").textContent = d.count;
}
$("startob").addEventListener("click", () => {
  $("oberr").textContent = "";
  obQueue = parsePrompts($("prompts").value); obIndex = 0;
  if (!obQueue.length) { $("obprogress").textContent = "no prompts"; return; }
  if (obArms.length < 2) {
    $("oberr").textContent = "configure two [gateway.models] first"; return;
  }
  $("obcurrent").hidden = false; showCurrent();
});
async function showCurrent() {
  if (obIndex >= obQueue.length) {
    $("obcurrent").hidden = true;
    $("obprogress").textContent = "done — " + obQueue.length + " judged";
    return;
  }
  const prompt = obQueue[obIndex];
  $("obprogress").textContent = (obIndex + 1) + " / " + obQueue.length;
  $("obprompt").textContent = prompt;
  $("obarms").textContent = "running both arms…"; $("objudge").innerHTML = "";
  const {ok, data} = await post("/api/onboard/run", {prompt});
  if (!ok) { $("obarms").textContent = ""; $("oberr").textContent = data.error; return; }
  $("oberr").textContent = ""; $("obarms").innerHTML = "";
  obArms.forEach(arm => {
    const col = document.createElement("div"); col.style.flex = "1";
    const h = document.createElement("strong"); h.textContent = arm;
    const pre = document.createElement("pre"); pre.textContent = data.outputs[arm] || "";
    col.append(h, pre); $("obarms").appendChild(col);
  });
  const [primary, fallback] = obArms;
  const good = document.createElement("button");
  good.className = "act"; good.textContent = "‘" + primary + "’ good enough";
  good.onclick = () => recordJudgment(prompt, primary);
  const need = document.createElement("button");
  need.className = "act"; need.textContent = "needs ‘" + fallback + "’";
  need.onclick = () => recordJudgment(prompt, fallback);
  $("objudge").innerHTML = ""; $("objudge").append(good, need);
}
async function recordJudgment(prompt, label) {
  const {data} = await post("/api/onboard/record", {prompt, label});
  $("lblcount").textContent = data.count;
  obIndex++; showCurrent();
}
$("obcal").addEventListener("click", async () => {
  const r = await fetch("/api/onboard/dataset"); const d = await r.json();
  if (!d.dataset) { $("oberr").textContent = "no labels recorded yet"; return; }
  $("dataset").value = d.dataset;
  document.querySelector('nav button[data-tab="calibrate"]').click();
});

score();
</script>
</body>
</html>
"""
