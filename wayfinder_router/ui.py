"""Local calibrate / explain / configure web UI (WF-ADR-0005).

This module is a *thin* browser front-end over the pure core. It never invokes a
model on the scoring path and never re-implements scoring, calibration, or config
parsing — each helper adapts exactly one core function into a JSON-shaped dict. The
web stack ships behind the ``wayfinder-router[ui]`` extra, so ``fastapi``/``uvicorn``
are imported *lazily* inside :func:`build_ui_app` / :func:`run_ui`; importing this
module (or the package) must stay dependency-light.

Screens, each backed by a core function:

- **Explain** — score a pasted prompt; show recommendation, tier ladder, and the
  per-feature contribution breakdown; a live threshold slider re-scores in place.
- **Calibrate** — fit a config fragment from a pasted labeled JSONL dataset and show
  the accuracy summary plus (for ``threshold`` mode) the sweep curve.
- **Configure** — edit ``wayfinder-router.toml`` with live validation through the real
  loaders. Secrets never surface: a gateway model names an ``api_key_env`` only.
- **Onboard** — A/B a local vs hosted arm in the browser, record judgments, then
  recalibrate from the shared feedback log (WF-ADR-0006). Only this screen invokes a
  model, and it does so through the gateway (bring-your-own key).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from .calibrate import CalibrationError, calibrate, parse_dataset, sweep_curve
from .complexity import RoutingConfig, binary_tiers, explain_score, score_complexity
from .config import (
    CONFIG_FILE,
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
from .recalibrate import recalibrate

if TYPE_CHECKING:  # type-only; runtime pulls fastapi lazily inside build_ui_app
    from fastapi import FastAPI

# The [ui] extra's install line, surfaced when fastapi/uvicorn are absent.
_INSTALL_HINT = "the UI needs its extra: pip install 'wayfinder-router[ui]'"
# The calibration modes the endpoints accept; anything else coerces to "threshold".
_MODES = ("threshold", "tiers", "classifier")


class UIUnavailable(Exception):
    """Raised when the UI extra (fastapi / uvicorn) is not installed."""


# --- pure helpers (importable and testable without the [ui] extra) ----------


def score_payload(prompt: str, start_dir: str = ".", threshold: float | None = None) -> dict:
    """Return an explain-ready score payload for ``prompt`` (no model call).

    The config is discovered by walking up from ``start_dir``. A ``threshold`` override
    replaces the tier ladder with a binary one and *drops the classifier* — only the
    weights carry over — so the reported mode/tiers reflect the override.
    """
    config = load_routing_config(start_dir)
    if threshold is not None:
        config = RoutingConfig(weights=config.weights, tiers=binary_tiers(threshold))
    result = score_complexity(prompt, config=config)
    payload = result.to_dict()
    # Contributions in FEATURE_ORDER; their rounded sum equals the reported score.
    payload["contributions"] = [
        fc.to_dict() for fc in explain_score(result.features, config.weights)
    ]
    return payload


def calibrate_payload(
    dataset_text: str, mode: str = "threshold", models: list[str] | None = None
) -> dict:
    """Fit a config fragment from pasted JSONL; return toml, summary, and (threshold) curve.

    ``CalibrationError`` propagates out to the caller (the endpoint maps it to 400). The
    ``curve`` key exists only for ``threshold`` mode.
    """
    samples = parse_dataset(dataset_text)
    result = calibrate(samples, mode, models_order=models)
    payload: dict = {"toml": result.toml, "summary": result.summary}
    if mode == "threshold":
        payload["curve"] = [{"threshold": t, "accuracy": a} for t, a in sweep_curve(samples)]
    return payload


def current_config_text(start_dir: str = ".") -> str:
    """The resolved ``wayfinder-router.toml`` text, or a dumped default when none exists."""
    path = find_config_file(start_dir)
    if path is not None:
        return path.read_text(encoding="utf-8")
    return dump_routing_toml(RoutingConfig())


def validate_config_text(text: str) -> str | None:
    """Validate ``text`` through both real loaders; return an error string or None if valid."""
    try:
        routing_config_from_toml(text)
        gateway_config_from_toml(text)
    except WayfinderConfigError as exc:
        return str(exc)
    return None


def save_config_text(text: str, start_dir: str = ".") -> str | None:
    """Validate then persist ``text``; return an error string on invalid, else None.

    Writes back to the file the loaders actually resolve — ``find_config_file`` walks up
    to a parent — so editing from a subdirectory updates the shared parent config rather
    than creating a second, ignored file. Only with no config anywhere up-tree is a fresh
    one created directly under ``start_dir``. Invalid text is refused before any write.
    """
    error = validate_config_text(text)
    if error is not None:
        return error
    target = find_config_file(start_dir) or (Path(start_dir) / CONFIG_FILE)
    target.write_text(text, encoding="utf-8")
    return None


def _log_path(start_dir: str) -> str:
    """The feedback log path under ``start_dir`` (not walked up — onboarding is local)."""
    return str(Path(start_dir) / DEFAULT_LOG)


def onboard_arms(start_dir: str = ".") -> list[str]:
    """The first two configured gateway models, in insertion order — the A/B arms."""
    return list(load_gateway_config(start_dir).models)[:2]


def onboard_run(start_dir: str, prompt: str, arms: list[str] | None = None) -> dict[str, str]:
    """Invoke each chosen arm on ``prompt`` and return arm->output (BYO key).

    Reaches the network through ``invoke_model`` -> the gateway module's ``forward_request``
    global, so tests patching ``gateway.forward_request`` take effect. An arm name absent
    from the gateway config is a clean ``GatewayUnavailable`` (mapped to 400), never a 500.
    """
    gateway = load_gateway_config(start_dir)
    chosen = arms or list(gateway.models)[:2]
    unknown = [arm for arm in chosen if arm not in gateway.models]
    if unknown:
        raise GatewayUnavailable(f"unknown model arm(s): {', '.join(unknown)}")
    return {arm: invoke_model(gateway.models[arm], prompt) for arm in chosen}


def record_onboard_label(start_dir: str, prompt: str, label: str) -> int:
    """Append a judgment to the feedback log; return the freshly re-read label count."""
    record_label(_log_path(start_dir), prompt, label)
    return len(read_labels(_log_path(start_dir)))


def onboard_dataset_text(start_dir: str) -> str:
    """The feedback log rendered as JSONL for the Calibrate flow.

    Default ``json.dumps`` separators (the space after each colon is pinned by a test) and
    ``ensure_ascii=False`` so the text round-trips through ``parse_dataset``.
    """
    rows = read_labels(_log_path(start_dir))
    return "\n".join(json.dumps(row, ensure_ascii=False) for row in rows)


def recalibrate_payload(start_dir: str = ".", mode: str = "threshold") -> dict:
    """Re-fit and write the config from the feedback log; return the outcome record.

    Writes to ``start_dir``'s ``wayfinder-router.toml`` directly (no walk-up — intentionally
    asymmetric with :func:`save_config_text`).
    """
    result = recalibrate(
        _log_path(start_dir), str(Path(start_dir) / "wayfinder-router.toml"), mode=mode
    )
    return {
        "written": result.written,
        "label_count": result.label_count,
        "summary": result.summary,
        "reason": result.reason,
    }


def _models_list(value: object) -> list[str] | None:
    """Coerce a request field to a model-order list: a list or comma string, else None."""
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()] or None
    if isinstance(value, str) and value.strip():
        return [item.strip() for item in value.split(",") if item.strip()] or None
    return None


# --- web app (fastapi imported lazily; never on the package import path) -----


def build_ui_app(start_dir: str = ".") -> FastAPI:
    """Construct the FastAPI app; ``start_dir`` is closed over by every route."""
    try:
        from fastapi import Body, FastAPI
        from fastapi.responses import HTMLResponse, JSONResponse
    except ImportError as exc:  # pragma: no cover - only without the extra
        raise UIUnavailable(_INSTALL_HINT) from exc

    app = FastAPI(title="wayfinder-router-ui")

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return _PAGE

    @app.post("/api/score")
    def api_score(body: dict = Body(...)) -> dict:  # noqa: B008 - FastAPI default
        raw_prompt = body.get("prompt")
        prompt = raw_prompt if isinstance(raw_prompt, str) else ""
        raw_threshold = body.get("threshold")
        # bool is a subclass of int and is accepted here (matches legacy behavior).
        threshold = float(raw_threshold) if isinstance(raw_threshold, (int, float)) else None
        return score_payload(prompt, start_dir=start_dir, threshold=threshold)

    @app.post("/api/calibrate")
    def api_calibrate(body: dict = Body(...)) -> object:  # noqa: B008 - FastAPI default
        raw_dataset = body.get("dataset")
        dataset = raw_dataset if isinstance(raw_dataset, str) else ""
        raw_mode = body.get("mode")
        # Unknown/non-str modes silently become "threshold" (not a 400).
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
        # Always 200; invalid config is reported as ok:false, not an HTTP error.
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
        return {
            "arms": onboard_arms(start_dir),
            "count": len(read_labels(_log_path(start_dir))),
        }

    @app.post("/api/onboard/run")
    def api_onboard_run(body: dict = Body(...)) -> object:  # noqa: B008 - FastAPI default
        raw_prompt = body.get("prompt")
        prompt = raw_prompt if isinstance(raw_prompt, str) else ""
        if not prompt:
            return JSONResponse(status_code=400, content={"error": "missing 'prompt'"})
        try:
            return {"outputs": onboard_run(start_dir, prompt, _models_list(body.get("arms")))}
        except GatewayUnavailable as exc:  # unknown arm / bad gateway config
            return JSONResponse(status_code=400, content={"error": str(exc)})
        except RuntimeError as exc:  # an upstream model error
            return JSONResponse(status_code=502, content={"error": str(exc)})

    @app.post("/api/onboard/record")
    def api_onboard_record(body: dict = Body(...)) -> object:  # noqa: B008 - FastAPI default
        # Both fields are required; either missing is a distinct 400 message.
        raw_prompt, raw_label = body.get("prompt"), body.get("label")
        if not isinstance(raw_prompt, str) or not raw_prompt:
            return JSONResponse(status_code=400, content={"error": "missing 'prompt'"})
        if not isinstance(raw_label, str) or not raw_label:
            return JSONResponse(status_code=400, content={"error": "missing 'label'"})
        return {"ok": True, "count": record_onboard_label(start_dir, raw_prompt, raw_label)}

    @app.get("/api/onboard/dataset")
    def api_onboard_dataset() -> dict:
        return {"dataset": onboard_dataset_text(start_dir)}

    @app.post("/api/recalibrate")
    def api_recalibrate(body: dict = Body(...)) -> object:  # noqa: B008 - FastAPI default
        raw_mode = body.get("mode")
        mode = raw_mode if isinstance(raw_mode, str) and raw_mode in _MODES else "threshold"
        try:
            return recalibrate_payload(start_dir, mode)
        except (CalibrationError, WayfinderConfigError) as exc:  # both map to 400 here
            return JSONResponse(status_code=400, content={"error": str(exc)})

    return app


def run_ui(  # pragma: no cover
    start_dir: str = ".", host: str = "127.0.0.1", port: int = 8099
) -> None:
    """Serve the UI with uvicorn (backs the ``wayfinder-router ui`` command)."""
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
  :root {
    color-scheme: light dark;
    --bg: #f4efe6; --surface: #fbf8f2; --surface-2: #efe8da;
    --text: #1b1f1d; --muted: #5c635f; --hairline: #ddd4c4;
    --accent: #0f7d73; --accent-hover: #0c655d; --accent-weak: #d8ede9; --on-accent: #ffffff;
    --cloud: #9a5b15;
    --ok: #1a7d4b; --ok-weak: #d9efe1; --err: #c0392b; --err-weak: #f7e0dc;
    --bar: #0f7d73; --track: #e4dccc;
    --radius: 10px; --radius-sm: 6px; --radius-pill: 999px;
    --sp-1: .25rem; --sp-2: .5rem; --sp-3: .75rem; --sp-4: 1rem; --sp-5: 1.5rem;
    --shadow-sm: 0 1px 2px rgba(20,25,22,.08);
    --ring: 0 0 0 3px rgba(15,125,115,.35);
    --font-ui: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    --font-mono: ui-monospace, "SF Mono", "Cascadia Code", "JetBrains Mono", Menlo, Consolas, monospace;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #0e1614; --surface: #15211e; --surface-2: #0a100f;
      --text: #eef2ee; --muted: #9aa6a0; --hairline: #28332f;
      --accent: #2bb6a6; --accent-hover: #46c8b9; --accent-weak: #142e2a; --on-accent: #06201d;
      --cloud: #d99a4e;
      --ok: #43c483; --ok-weak: #12281d; --err: #f08a7d; --err-weak: #2c1714;
      --bar: #2bb6a6; --track: #20302c;
      --shadow-sm: 0 1px 2px rgba(0,0,0,.4);
      --ring: 0 0 0 3px rgba(43,182,166,.45);
    }
  }
  * { box-sizing: border-box; }
  body { font: 15px/1.55 var(--font-ui); color: var(--text); background: var(--bg);
         margin: 0; padding: var(--sp-5); max-width: 920px; margin-inline: auto; }
  @media (max-width: 640px) { body { padding: var(--sp-4); } }

  .brand { display: flex; align-items: baseline; gap: var(--sp-3); flex-wrap: wrap;
           padding-bottom: var(--sp-4); margin-bottom: var(--sp-2);
           border-bottom: 1px solid var(--hairline); }
  .brand h1 { font-size: 1.35rem; font-weight: 700; letter-spacing: -.01em;
              line-height: 1.1; margin: 0; }
  .brand .tag { font: 500 .8rem/1.3 var(--font-mono); color: var(--muted);
                letter-spacing: .02em; margin-left: auto; }
  @media (max-width: 640px) { .brand .tag { margin-left: 0; width: 100%; } }

  nav { display: flex; gap: var(--sp-1); margin: var(--sp-5) 0; flex-wrap: wrap;
        border-bottom: 1px solid var(--hairline); }
  nav button { font: 600 .92rem var(--font-ui); color: var(--muted); cursor: pointer;
               padding: var(--sp-3) var(--sp-4); border: 0; background: transparent;
               border-bottom: 2px solid transparent; margin-bottom: -1px;
               border-radius: var(--radius-sm) var(--radius-sm) 0 0;
               transition: color .15s, background .15s; }
  nav button:hover { color: var(--text); background: var(--accent-weak); }
  nav button.on { color: var(--accent); border-bottom-color: var(--accent); }
  nav button:focus-visible { outline: none; box-shadow: var(--ring); }

  section { display: none; }
  section.on { display: block; }
  .card { background: var(--surface); border: 1px solid var(--hairline);
          border-radius: var(--radius); box-shadow: var(--shadow-sm); padding: var(--sp-5); }
  .card > :first-child { margin-top: 0; }
  p.muted { margin-top: 0; }

  .row { display: flex; gap: var(--sp-4); align-items: center; margin: var(--sp-4) 0; flex-wrap: wrap; }
  .muted { color: var(--muted); }
  label { font-weight: 600; font-size: .9rem; }

  textarea, input[type=text], input:not([type]), select {
    width: 100%; box-sizing: border-box; font: inherit; color: var(--text);
    background: var(--surface); border: 1px solid var(--hairline);
    border-radius: var(--radius-sm); padding: var(--sp-3);
    transition: border-color .15s, box-shadow .15s; }
  textarea { font: .82rem/1.5 var(--font-mono); resize: vertical; }
  #prompt, #dataset, #prompts { min-height: 140px; }
  #toml { min-height: 240px; }
  textarea:focus, input:focus, select:focus { outline: none;
    border-color: var(--accent); box-shadow: var(--ring); }
  #models { max-width: 340px; }
  select { width: auto; cursor: pointer; padding-right: var(--sp-5);
    appearance: none; -webkit-appearance: none;
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12'%3E%3Cpath d='M2 4l4 4 4-4' stroke='%230f7d73' stroke-width='1.6' fill='none' stroke-linecap='round'/%3E%3C/svg%3E");
    background-repeat: no-repeat; background-position: right var(--sp-3) center; }

  button.act { font: 600 .9rem var(--font-ui); cursor: pointer;
    padding: var(--sp-2) var(--sp-4); border-radius: var(--radius-sm);
    border: 1px solid var(--hairline); background: var(--surface); color: var(--text);
    transition: background .15s, border-color .15s, box-shadow .15s, transform .02s; }
  button.act:hover { background: var(--accent-weak); border-color: var(--accent); }
  button.act:active { transform: translateY(1px); }
  button.act:focus-visible { outline: none; box-shadow: var(--ring); }
  button.act:disabled { opacity: .5; cursor: not-allowed; }
  button.act.primary { background: var(--accent); border-color: var(--accent); color: var(--on-accent); }
  button.act.primary:hover { background: var(--accent-hover); border-color: var(--accent-hover); }

  input[type=range] { flex: 1; appearance: none; -webkit-appearance: none;
    height: 22px; background: transparent; cursor: pointer; }
  input[type=range]::-webkit-slider-runnable-track { height: 6px;
    border-radius: var(--radius-pill); background: var(--track); }
  input[type=range]::-webkit-slider-thumb { -webkit-appearance: none;
    width: 18px; height: 18px; margin-top: -6px; border-radius: 50%;
    background: var(--accent); border: 2px solid var(--surface);
    box-shadow: var(--shadow-sm); transition: transform .1s; }
  input[type=range]:hover::-webkit-slider-thumb { transform: scale(1.1); }
  input[type=range]:focus-visible { outline: none; }
  input[type=range]:focus-visible::-webkit-slider-thumb { box-shadow: var(--ring); }
  input[type=range]::-moz-range-track { height: 6px;
    border-radius: var(--radius-pill); background: var(--track); }
  input[type=range]::-moz-range-thumb { width: 18px; height: 18px;
    border: 2px solid var(--surface); border-radius: 50%;
    background: var(--accent); box-shadow: var(--shadow-sm); }
  input[type=range]:focus-visible::-moz-range-thumb { box-shadow: var(--ring); }
  output { font-variant-numeric: tabular-nums; }

  .rec { display: inline-flex; align-items: center; gap: var(--sp-2);
    font: 700 1.05rem var(--font-ui); padding: var(--sp-2) var(--sp-4);
    border-radius: var(--radius-pill); background: var(--accent-weak);
    color: var(--accent); letter-spacing: .01em; }
  #score { color: var(--muted); font-size: .9rem; font-variant-numeric: tabular-nums; }

  .tier { font: 600 .82rem var(--font-mono); padding: var(--sp-1) var(--sp-3);
    border-radius: var(--radius-pill); background: var(--surface-2);
    color: var(--muted); border: 1px solid var(--hairline); font-variant-numeric: tabular-nums; }
  .tier.on { background: var(--accent); color: var(--on-accent); border-color: var(--accent); }

  .track { background: var(--track); border-radius: var(--radius-pill);
    flex: 1; min-width: 60px; height: 10px; overflow: hidden; display: block; }
  td.track { padding: 0; vertical-align: middle; }
  span.track { height: 10px; }
  .bar { height: 10px; background: var(--bar); border-radius: var(--radius-pill); display: block; }

  table { width: 100%; border-collapse: collapse; margin-top: var(--sp-3);
    font-variant-numeric: tabular-nums; }
  th, td { text-align: left; padding: var(--sp-2) var(--sp-3);
    border-bottom: 1px solid var(--hairline); }
  th { font: 600 .78rem var(--font-ui); color: var(--muted);
    text-transform: uppercase; letter-spacing: .04em; }
  tbody tr:last-child td { border-bottom: 0; }
  tbody tr:hover { background: var(--accent-weak); }

  pre { background: var(--surface-2); color: var(--text);
    border: 1px solid var(--hairline); border-radius: var(--radius-sm);
    padding: var(--sp-4); overflow: auto; font: .82rem/1.5 var(--font-mono); }
  code { background: var(--surface-2); border-radius: 4px; padding: .1em .4em;
    font: .85em var(--font-mono); }

  .ok, .err { display: inline-block; font-weight: 600; font-size: .85rem;
    padding: var(--sp-1) var(--sp-3); border-radius: var(--radius-pill); }
  .ok { color: var(--ok); background: var(--ok-weak); }
  .err { color: var(--err); background: var(--err-weak); white-space: pre-wrap; }
  .ok:empty, .err:empty { display: none; padding: 0; }

  @media (prefers-reduced-motion: reduce) { * { transition: none !important; } }
</style>
</head>
<body>
  <header class="brand">
    <h1>Wayfinder</h1>
    <span class="tag">Deterministic. Calibrated. No&nbsp;RAG, no&nbsp;guessing.</span>
  </header>
  <nav>
    <button data-tab="explain" class="on">Explain</button>
    <button data-tab="calibrate">Calibrate</button>
    <button data-tab="configure">Configure</button>
    <button data-tab="onboard">Onboard</button>
  </nav>

  <section id="explain" class="on">
  <div class="card">
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
  </div>
  </section>

  <section id="calibrate">
  <div class="card">
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
      <button class="act primary" id="runcal">Calibrate</button>
    </div>
    <div id="calsummary" class="muted"></div>
    <div id="calerr" class="err"></div>
    <div id="curve"></div>
    <pre id="calout" hidden></pre>
    <button class="act" id="tocfg" hidden>Send to Configure →</button>
  </div>
  </section>

  <section id="configure">
  <div class="card">
    <p class="muted">Edit <code>wayfinder-router.toml</code>. Keys are never stored here —
      a gateway model names an <code>api_key_env</code> and the secret stays in the
      environment.</p>
    <textarea id="toml"></textarea>
    <div class="row">
      <button class="act" id="validate">Validate</button>
      <button class="act primary" id="save">Save</button>
      <span id="cfgstatus"></span>
    </div>
  </div>
  </section>

  <section id="onboard">
  <div class="card">
    <p class="muted">A/B a local vs hosted model on sample prompts, judge each, and
      record labels. Needs two <code>[gateway.models]</code> and the
      <code>[gateway]</code> extra. Arms: <span id="arms">—</span> · labels so far:
      <span id="lblcount">0</span></p>
    <textarea id="prompts" placeholder='one prompt per line, or {"text": "..."}'></textarea>
    <div class="row">
      <button class="act primary" id="startob">Start</button>
      <span class="muted" id="obprogress"></span>
    </div>
    <div id="obcurrent" hidden>
      <pre id="obprompt"></pre>
      <div class="row" id="obarms"></div>
      <div class="row" id="objudge"></div>
    </div>
    <div id="oberr" class="err"></div>
    <div class="row">
      <button class="act" id="obcal">Calibrate from log →</button>
      <button class="act primary" id="obrecal">Recalibrate &amp; save</button>
      <span id="obrecalstatus" class="muted"></span>
    </div>
  </div>
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
$("obrecal").addEventListener("click", async () => {
  $("obrecalstatus").textContent = "recalibrating…";
  const {ok, data} = await post("/api/recalibrate", {mode: "threshold"});
  if (!ok) { $("obrecalstatus").innerHTML = '<span class="err">' + data.error + '</span>'; return; }
  if (!data.written) { $("obrecalstatus").textContent = "skipped — " + data.reason; return; }
  const s = Object.entries(data.summary).map(([k, v]) => k + "=" + v).join(" · ");
  $("obrecalstatus").innerHTML = '<span class="ok">saved wayfinder-router.toml</span> · ' + s;
  loadOnboardState();
});

score();
</script>
</body>
</html>
"""
