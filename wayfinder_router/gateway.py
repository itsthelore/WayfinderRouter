"""Optional OpenAI-compatible routing gateway (WF-ADR-0004).

This is the impure layer: it holds bring-your-own keys and calls upstream models.
It ships behind the ``wayfinder-router[gateway]`` extra; ``fastapi`` / ``uvicorn`` /
``httpx`` are imported lazily so the deterministic core stays dependency-free.

A client points its OpenAI-compatible ``base_url`` at this gateway. For each
request the gateway scores the prompt with the pure core, maps the recommended
model name to a configured upstream, and forwards the call with the user's key.
Keys are read from the environment at request time and never appear in
``wayfinder-router.toml``, in the scored path, or in any test fixture.

Streaming is first-class (WF-ADR-0013): a request with ``stream: true`` is relayed
back as a Server-Sent-Events stream so chat clients render tokens progressively.
The forward path is async (``httpx.AsyncClient``) so concurrent requests do not
block one another. Upstream transport failures become an OpenAI-shaped
``wayfinder_router_upstream_error`` rather than a bare 500, every request carries
an ``x-wayfinder-router-request-id`` for tracing, and the upstream timeout is
configurable (``WAYFINDER_ROUTER_TIMEOUT``).

A request may steer the routing decision per call without changing any
application code, through OpenAI-compatible channels (WF-ADR-0011). This only
moves *which threshold/decision applies*; it never adds inference, so the
WF-ADR-0001/0004 boundary holds:

- the OpenAI ``model`` field is a routing directive — ``auto`` (or any
  unrecognized value) scores per config, an exact configured endpoint name
  pins the call to that endpoint, and ``prefer-local`` / ``prefer-hosted`` pin
  to the low / high end of the configured router (``prefer-cloud`` is a
  back-compat alias of ``prefer-hosted``);
- an ``X-Wayfinder-Threshold`` header (a number in ``0.0``–``1.0``) re-decides
  the call at that binary cut, reusing the configured scoring weights.

Note the score is a *structural* proxy (length, headings, lists, code, links), not
a verdict on semantic difficulty: a short but hard prompt scores low. Calibrate the
threshold on your own traffic (``wayfinder-router calibrate``); the default is only
a starting point.

Every response carries the decision signal: ``x-wayfinder-router-model`` (the
chosen endpoint), ``x-wayfinder-router-score`` (the structural score, always
computed), ``x-wayfinder-router-mode`` (``scored`` / ``pinned`` /
``threshold-override``), and ``x-wayfinder-router-request-id``. ``GET /router`` shows
recent decisions at a glance and ``X-Wayfinder-Debug: true`` surfaces the decision in
the response body (WF-ADR-0014).

``GET /metrics`` exposes the same decisions as Prometheus counters and histograms —
metadata only, never prompt text, off the scored path — hand-rolled with no extra
dependency (WF-ADR-0018).

``GET /v1/models`` advertises the selectable options — ``auto``,
``prefer-local`` / ``prefer-hosted`` (for a tiered router), and the configured
endpoint names — so a client discovers them without a hand-written list
(WF-ADR-0012).

Config (`wayfinder-router.toml`)::

    [gateway.models.local]
    base_url = "http://localhost:11434/v1"
    model = "llama3.2"

    [gateway.models.cloud]
    base_url = "https://api.example.com/v1"
    model = "big-model"
    api_key_env = "EXAMPLE_API_KEY"   # name of the env var holding the key
"""

from __future__ import annotations

import json
import logging
import os
import time
import tomllib
import uuid
from collections import deque
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING

from .complexity import (
    RoutingConfig,
    Tier,
    explain_score,
    recommend_tier,
    score_complexity,
)
from .config import (
    WayfinderConfigError,
    dump_routing_toml,
    find_config_file,
    load_routing_config,
)
from .feedback import DEFAULT_LOG, record_label
from .profiles import PROFILES

if TYPE_CHECKING:  # type-only; the runtime imports these lazily inside build_app
    from fastapi import FastAPI, Response

logger = logging.getLogger("wayfinder_router.gateway")

_INSTALL_HINT = "the gateway needs its extra: pip install 'wayfinder-router[gateway]'"
_TIMEOUT_ENV = "WAYFINDER_ROUTER_TIMEOUT"
_FEEDBACK_TOKEN_ENV = "WAYFINDER_ROUTER_FEEDBACK_TOKEN"
_DEFAULT_TIMEOUT = 60.0
_RECENT_MAX = 200  # routing decisions kept in memory for the /router view (metadata only)

# A tiny, self-contained "is routing working?" dashboard (WF-ADR-0014). No CDN, no
# build step, no prompt text — it polls /router/recent (decision metadata only).
_DASHBOARD_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Wayfinder routing</title><style>
body{font:14px ui-sans-serif,system-ui,sans-serif;margin:2rem;color:#1b1f1d;background:#f4efe6}
h1{font-size:1.1rem;margin:0 0 .25rem}#counts{color:#5c635f;margin-bottom:1rem}
table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:.35rem .6rem;border-bottom:1px solid #ddd4c4}
th{font-size:.7rem;text-transform:uppercase;letter-spacing:.04em;color:#5c635f}
td{font-variant-numeric:tabular-nums}code{font-family:ui-monospace,monospace;color:#0c655d}
.pill{display:inline-block;padding:.05rem .55rem;border-radius:999px;background:#d8ede9;color:#0c655d;font-size:.8rem}
@media(prefers-color-scheme:dark){body{background:#0e1614;color:#eef2ee}th,#counts{color:#9aa6a0}
td,th{border-color:#28332f}code{color:#46c8b9}.pill{background:#142e2a;color:#46c8b9}}
</style></head><body>
<h1>Wayfinder routing <span id="total" class="pill">…</span></h1>
<div id="counts"></div>
<table><thead><tr><th>when</th><th>model</th><th>score</th><th>mode</th><th>request id</th></tr></thead>
<tbody id="rows"></tbody></table>
<script>
async function tick(){
  try{
    const d=await (await fetch('/router/recent?limit=50')).json();
    total.textContent=d.total+' routed';
    counts.textContent=Object.entries(d.by_model).map(([k,v])=>k+': '+v).join('  ·  ');
    rows.innerHTML=d.recent.map(x=>`<tr><td>${new Date(x.ts*1000).toLocaleTimeString()}</td>`+
      `<td>${x.model}</td><td>${x.score.toFixed(2)}</td><td>${x.mode}</td>`+
      `<td><code>${x.request_id}</code></td></tr>`).join('');
  }catch(e){counts.textContent='gateway unreachable';}
}
tick();setInterval(tick,2000);
</script></body></html>"""

# The decision-first chat demo (WF-ADR-0020). One self-contained page: no build, no CDN,
# no fonts fetched (system stack only). It calls /v1/chat/completions with model="auto" +
# X-Wayfinder-Debug so it can show the decision (model / score / why / cost); pair with
# --dry-run for a keyless demo. Richer chat features are the trigger to upstream into
# LibreChat, not to grow this page.
_DEMO_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Wayfinder</title><style>
:root{
  color-scheme: light dark;
  --bg:#ffffff; --panel:#f9f9fa; --elev:#ffffff; --text:#0d0d0d; --muted:#6b6b78;
  --line:#ececef; --line-strong:#e2e2e6; --user:#f4f4f5; --accent:#10a37f; --accent-weak:#eaf6f2;
  --cloud:#bd6a13; --cloud-weak:#fbf0e3; --btn:#0d0d0d; --btn-text:#ffffff; --track:#ececed;
  --radius:18px; --radius-sm:13px; --pill:999px;
  --shadow:0 1px 2px rgba(13,13,13,.05),0 1px 1px rgba(13,13,13,.03);
  --ring:0 0 0 3px color-mix(in srgb,var(--accent) 30%,transparent);
  --font:ui-sans-serif,-apple-system,"SF Pro Text","Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  --mono:ui-monospace,"SF Mono","Cascadia Code",Menlo,Consolas,monospace;
}
@media(prefers-color-scheme:dark){:root{
  --bg:#1e1e20; --panel:#262629; --elev:#2a2a2d; --text:#ececec; --muted:#9a9aa6;
  --line:rgba(255,255,255,.08); --line-strong:rgba(255,255,255,.13); --user:#2d2d31;
  --accent:#19c8a4; --accent-weak:#15302a; --cloud:#e0a25c; --cloud-weak:#332610;
  --btn:#ececec; --btn-text:#0d0d0d; --track:#39393d;
  --shadow:0 1px 2px rgba(0,0,0,.35);
}}
*{box-sizing:border-box}
html,body{height:100%}
body{margin:0;background:var(--bg);color:var(--text);font-family:var(--font);
  font-size:15px;line-height:1.55;display:flex;flex-direction:column;height:100vh;overflow:hidden;
  -webkit-font-smoothing:antialiased;-moz-osx-font-smoothing:grayscale}
.lower{flex:1;min-height:0;display:flex;flex-direction:row}
.content{flex:1;min-width:0;min-height:0;display:flex;flex-direction:column}
/* Sidebar: a persistent in-flow panel that collapses to an icon rail (no overlay). */
.sidebar{flex:none;width:256px;box-sizing:border-box;display:flex;flex-direction:column;gap:.5rem;
  padding:.6rem .55rem;background:var(--panel);border-right:1px solid var(--line);overflow:hidden;transition:width .18s ease}
body.sidebar-collapsed .sidebar{width:58px}
.side-top{flex:none}
.side-search{width:100%;box-sizing:border-box;font:inherit;font-size:.8rem;color:var(--text);background:var(--bg);
  border:1px solid var(--line-strong);border-radius:9px;padding:.42rem .55rem;outline:none}
.side-search:focus{border-color:color-mix(in srgb,var(--accent) 55%,var(--line-strong))}
body.sidebar-collapsed .side-search,body.sidebar-collapsed .side-scroll{display:none}
.side-scroll{display:flex;flex-direction:column;gap:.1rem;overflow-y:auto;overflow-x:hidden;flex:1;min-height:0}
.side-label{font-size:.62rem;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);padding:.45rem .3rem .15rem}
.folder-head{display:flex;align-items:center;gap:.35rem;padding:.42rem .5rem;border-radius:9px;cursor:pointer;
  font-size:.79rem;font-weight:600;color:var(--text);user-select:none}
.folder-head:hover{background:var(--elev)}
.folder-caret{font-size:.6rem;color:var(--muted);transition:transform .15s;width:.7rem;text-align:center}
.folder.open .folder-caret{transform:rotate(90deg)}
.folder-count{margin-left:auto;color:var(--muted);font-size:.66rem;font-weight:600}
.folder-del{flex:none;border:0;background:transparent;color:var(--muted);font-size:1rem;line-height:1;cursor:pointer;opacity:0;padding:0 .1rem}
.folder-head:hover .folder-del{opacity:.6}
.folder-del:hover{opacity:1;color:var(--text)}
.folder-chats{display:flex;flex-direction:column;gap:.1rem;padding-left:.55rem}
.folder:not(.open) .folder-chats{display:none}
.thread{display:flex;align-items:center;gap:.3rem;padding:.45rem .55rem;border-radius:9px;cursor:pointer;font-size:.82rem;color:var(--text)}
.thread:hover{background:var(--elev)}
.thread.active{background:var(--accent-weak)}
.thread.dragging{opacity:.4}
.thread.drag-over,.folder-head.drag-over,.folder-chats.drag-over,.side-label.drag-over{
  outline:2px dashed color-mix(in srgb,var(--accent) 55%,transparent);outline-offset:-2px;border-radius:9px}
.t-pin{flex:none;color:var(--accent);font-size:.66rem;line-height:1}
.t-rename{flex:1;min-width:0;font:inherit;font-size:.82rem;color:var(--text);background:var(--bg);
  border:1px solid var(--accent);border-radius:6px;padding:.1rem .3rem;outline:none}
.t-title{flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0}
.t-menu{flex:none;border:0;background:transparent;color:var(--muted);font-size:1rem;line-height:1;cursor:pointer;opacity:0;padding:0 .15rem}
.thread:hover .t-menu,.thread.active .t-menu{opacity:.6}
.t-menu:hover{opacity:1;color:var(--text)}
.side-empty{color:var(--muted);font-size:.78rem;padding:.5rem .4rem;opacity:.85}
.side-foot{flex:none;display:flex;flex-direction:column;gap:.12rem;border-top:1px solid var(--line);padding-top:.4rem;margin-top:.1rem}
.nav{display:flex;align-items:center;gap:.6rem;width:100%;box-sizing:border-box;font:inherit;font-size:.82rem;font-weight:500;
  color:var(--text);background:transparent;border:0;border-radius:9px;padding:.5rem .55rem;cursor:pointer;text-align:left;white-space:nowrap}
.nav:hover{background:var(--elev)}
.nav.on{background:var(--accent-weak);color:var(--accent)}
.nav .ico{flex:none;width:18px;height:18px;display:grid;place-items:center}
.nav .ico svg{width:18px;height:18px;stroke:currentColor;fill:none;stroke-width:1.7;stroke-linecap:round;stroke-linejoin:round}
.nav-label{overflow:hidden;text-overflow:ellipsis}
.nav.primary{background:var(--btn);color:var(--btn-text)}
.nav.primary:hover{background:var(--btn);opacity:.92}
.searchIcon{display:none}
body.sidebar-collapsed .nav{justify-content:center;padding:.5rem 0;gap:0}
body.sidebar-collapsed .nav-label{display:none}
body.sidebar-collapsed .searchIcon{display:flex}
.side-toggle{flex:none;width:30px;height:30px;border:1px solid var(--line);background:var(--panel);color:var(--muted);
  border-radius:9px;cursor:pointer;display:grid;place-items:center;font-size:1rem;margin-right:.1rem}
.side-toggle:hover{color:var(--text);border-color:var(--line-strong)}
.menu{position:fixed;z-index:60;min-width:158px;background:var(--elev);border:1px solid var(--line-strong);
  border-radius:10px;box-shadow:0 10px 28px rgba(0,0,0,.2);padding:.3rem;display:flex;flex-direction:column;gap:.04rem;font-size:.78rem}
.menu[hidden]{display:none}
.menu button{text-align:left;font:inherit;font-size:.78rem;color:var(--text);background:transparent;border:0;border-radius:7px;padding:.4rem .5rem;cursor:pointer;white-space:nowrap}
.menu button:hover{background:var(--accent-weak)}
.menu button.danger:hover{background:color-mix(in srgb,#d97706 18%,transparent);color:#9a3412}
.menu .mlabel{font-size:.58rem;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);padding:.3rem .5rem .1rem}
.menu .sep{height:1px;background:var(--line);margin:.18rem .25rem}
.modal{position:fixed;inset:0;z-index:70;display:grid;place-items:center;padding:1rem;background:rgba(20,20,22,.42)}
.modal[hidden]{display:none}
.modal-card{width:min(560px,100%);max-height:min(82vh,660px);overflow-y:auto;background:var(--elev);
  border:1px solid var(--line-strong);border-radius:16px;box-shadow:0 24px 60px rgba(0,0,0,.3);padding:1.3rem 1.5rem 1.5rem}
.modal-head{display:flex;align-items:center;justify-content:space-between;gap:1rem;margin-bottom:.2rem}
.modal-head h2{margin:0;font-size:1.1rem;letter-spacing:-.01em}
.modal-x{flex:none;border:0;background:transparent;color:var(--muted);font-size:1.4rem;line-height:1;cursor:pointer;padding:0 .2rem}
.modal-x:hover{color:var(--text)}
.modal-card h3{margin:1.15rem 0 .3rem;font-size:.66rem;text-transform:uppercase;letter-spacing:.07em;color:var(--muted)}
.modal-card p,.modal-card li{font-size:.86rem;color:var(--text);line-height:1.55}
.modal-card ul{margin:.2rem 0;padding-left:1.15rem}
.modal-card li{margin:.12rem 0}
.modal-card code{font-family:var(--mono);font-size:.85em;background:var(--panel);padding:.05rem .3rem;border-radius:5px}
.modal-card a{color:var(--accent)}
.modal-links{margin-top:1.1rem;border-top:1px solid var(--line);padding-top:.8rem;color:var(--muted)}
::selection{background:color-mix(in srgb,var(--accent) 22%,transparent)}
main::-webkit-scrollbar{width:11px}
main::-webkit-scrollbar-thumb{background:var(--line-strong);border-radius:999px;border:3px solid var(--bg)}
.bar{position:sticky;top:0;z-index:5;display:flex;align-items:center;gap:.6rem;
  padding:.7rem 1.1rem;background:color-mix(in srgb,var(--bg) 72%,transparent);
  backdrop-filter:saturate(1.8) blur(14px);border-bottom:1px solid var(--line)}
.brand{font-weight:650;letter-spacing:-.02em;font-size:.98rem}
.brand .dot{color:var(--accent)}
.mode{font-size:.6rem;font-weight:600;color:var(--muted);background:var(--panel);
  border:1px solid var(--line);border-radius:var(--pill);padding:.13rem .5rem;
  text-transform:uppercase;letter-spacing:.09em}
.saved{margin-left:auto;font-size:.78rem;color:var(--muted);font-variant-numeric:tabular-nums;text-align:right}
.saved b{color:var(--text);font-weight:600}
.gear{margin-left:.4rem;flex:none;width:30px;height:30px;border-radius:9px;border:1px solid var(--line);
  background:var(--panel);color:var(--muted);font-size:.95rem;cursor:pointer;display:grid;place-items:center;
  transition:color .15s,border-color .15s,background .15s}
.gear:hover{color:var(--text);border-color:var(--line-strong)}
.gear.on{background:var(--accent-weak);color:var(--accent);
  border-color:color-mix(in srgb,var(--accent) 40%,var(--line-strong))}
.settings{position:fixed;z-index:55;
  width:min(300px,calc(100vw - 2rem));background:var(--elev);border:1px solid var(--line-strong);
  border-radius:var(--radius-sm);box-shadow:0 12px 32px rgba(13,13,13,.16),var(--shadow);
  padding:.9rem 1rem;display:flex;flex-direction:column;gap:.85rem;font-size:.8rem;color:var(--muted);
  max-height:min(26rem,calc(100vh - 5rem));overflow-y:auto;overscroll-behavior:contain;
  scrollbar-width:thin;scrollbar-color:var(--line-strong) transparent;
  animation:pop .14s cubic-bezier(.2,.7,.3,1) both}
@keyframes pop{from{opacity:0;transform:translateY(-6px)}to{opacity:1;transform:none}}
.settings[hidden]{display:none}
.settings::-webkit-scrollbar{width:10px}
.settings::-webkit-scrollbar-thumb{background:var(--line-strong);border-radius:999px;border:3px solid var(--elev)}
.set-row{display:flex;flex-direction:column;gap:.4rem}
.set-head{display:flex;align-items:center;gap:.4rem}
.help{flex:none;width:15px;height:15px;padding:0;border-radius:50%;border:1px solid var(--line-strong);
  background:transparent;color:var(--muted);font:600 .66rem/1 var(--font);text-transform:none;
  letter-spacing:0;cursor:help;display:inline-grid;place-items:center}
.help:hover,.help:focus-visible{color:var(--accent);border-color:var(--accent);outline:none}
.tip{position:fixed;z-index:60;max-width:230px;background:var(--text);color:var(--bg);
  font:400 .72rem/1.45 var(--font);padding:.5rem .62rem;border-radius:8px;
  box-shadow:0 10px 28px rgba(0,0,0,.24);pointer-events:none;opacity:0;transition:opacity .12s}
.tip.show{opacity:1}
.tip[hidden]{display:none}
.set-name{display:flex;align-items:center;gap:.45rem;color:var(--text);font-weight:550;cursor:pointer;user-select:none}
.set-name input{accent-color:var(--accent)}
.set-ctl{display:flex;align-items:center;gap:.6rem}
.settings input[type=range]{flex:1;accent-color:var(--accent);height:4px}
.settings input[type=range]:disabled{opacity:.4;cursor:not-allowed}
.settings output{font-variant-numeric:tabular-nums;color:var(--muted);min-width:3em;font-weight:600;text-align:right}
.settings output.on{color:var(--text)}
.settings select{width:100%;font:inherit;font-size:.8rem;color:var(--text);background:var(--panel);
  border:1px solid var(--line-strong);border-radius:9px;padding:.35rem .5rem;cursor:pointer}
.settings select:disabled{opacity:.4;cursor:not-allowed}
.set-hint{font-size:.72rem;color:var(--muted);opacity:.9;line-height:1.4}
.set-foot{font-size:.72rem;color:var(--muted);opacity:.8;border-top:1px solid var(--line);padding-top:.6rem}
.models{display:flex;flex-direction:column}
.mrow{display:flex;align-items:center;gap:.45rem;padding:.32rem 0;font-size:.73rem;border-top:1px solid var(--line)}
.mrow:first-child{border-top:0}
.mdot{width:8px;height:8px;border-radius:50%;flex:none;background:var(--muted)}
.mdot.ok{background:var(--accent)}
.mdot.warn{background:#d97706}
.mname{font-weight:600;color:var(--text)}
.mendpoint{color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0}
.mkey{margin-left:auto;white-space:nowrap;font-size:.66rem;color:var(--muted)}
.mkey.warn{color:#d97706}
.adv{border-top:1px solid var(--line);padding-top:.6rem}
.adv>summary{cursor:pointer;color:var(--text);font-weight:600;list-style:none;display:flex;align-items:center;gap:.4rem}
.adv>summary::-webkit-details-marker{display:none}
.adv>summary::before{content:"\\25B8";color:var(--muted);font-size:.7rem;transition:transform .15s}
.adv[open]>summary::before{transform:rotate(90deg)}
.adv-body{display:flex;flex-direction:column;gap:.7rem;margin-top:.7rem}
.adv-grp{display:flex;align-items:center;gap:.4rem;font-size:.62rem;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);margin-top:.2rem}
.wrow{display:grid;grid-template-columns:6.3rem 1fr 2.1rem;align-items:center;gap:.5rem;font-size:.74rem}
.wrow span{color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.wrow output{font-variant-numeric:tabular-nums;color:var(--text);text-align:right;font-weight:600}
.settings textarea{width:100%;font:inherit;font-size:.74rem;color:var(--text);background:var(--panel);
  border:1px solid var(--line-strong);border-radius:9px;padding:.4rem .5rem;resize:vertical;min-height:2.4rem;line-height:1.4}
.settings textarea::placeholder{color:var(--muted);opacity:.8}
.export{font:inherit;font-size:.78rem;font-weight:600;color:var(--btn-text);background:var(--btn);border:0;
  border-radius:9px;padding:.42rem .7rem;cursor:pointer;align-self:flex-start;margin-top:.2rem}
.export:active{transform:translateY(1px)}
.cfg{margin:0;font-family:var(--mono);font-size:.68rem;color:var(--text);background:var(--panel);
  border:1px solid var(--line);border-radius:9px;padding:.6rem .7rem;white-space:pre-wrap;word-break:break-word;
  max-height:11rem;overflow:auto}
main{flex:1;overflow-y:auto;padding:1.5rem 1.1rem 2rem;scroll-behavior:smooth}
/* Launch state: centre the heading + composer as a group; drops to the bottom on the first message. */
body.intro .content{justify-content:center}
body.intro main{flex:0 1 auto;overflow:visible}
body.intro form{background:none}
.wrap{max-width:760px;margin:0 auto;display:flex;flex-direction:column;gap:1.4rem}
.empty{margin:0 auto 1.4rem;max-width:32rem;text-align:center;color:var(--muted)}
.empty h2{color:var(--text);font-size:1.2rem;font-weight:650;letter-spacing:-.01em;margin:0 0 .4rem}
.empty code{font-family:var(--mono);font-size:.85em;background:var(--panel);padding:.1rem .35rem;border-radius:6px}
.eg{font:inherit;font-size:.78rem;color:var(--muted);background:transparent;border:1px solid var(--line-strong);
  border-radius:var(--pill);padding:.32rem .7rem;cursor:pointer;white-space:nowrap;max-width:100%;
  overflow:hidden;text-overflow:ellipsis;transition:border-color .15s,background .15s,color .15s}
.eg:hover{border-color:var(--accent);background:var(--accent-weak);color:var(--text)}
.turn{display:flex;flex-direction:column;gap:.6rem;animation:rise .18s cubic-bezier(.2,.7,.3,1) both}
@keyframes rise{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
.msg{padding:.7rem 1rem;border-radius:var(--radius);max-width:84%;white-space:pre-wrap;word-wrap:break-word;line-height:1.5}
.msg.user{align-self:flex-end;background:var(--user)}
.msg.bot{align-self:flex-start;background:var(--elev);border:1px solid var(--line)}
.msg.note{align-self:flex-start;color:var(--muted);font-size:.82rem;background:transparent;padding:.1rem 0}
.answer{align-self:flex-start;max-width:84%;display:flex;flex-direction:column;gap:.45rem}
.answer .msg.bot{max-width:100%;align-self:stretch}
.msg.bot.dry{color:var(--muted);font-size:.88rem;background:var(--panel)}
.routing{position:relative;display:flex;align-items:center;gap:.5rem;padding-left:.15rem;
  font-size:.78rem;color:var(--muted)}
.pill{font-weight:600;border-radius:var(--pill);padding:.14rem .62rem;font-size:.78rem;text-transform:capitalize;
  background:var(--accent-weak);color:var(--accent);display:inline-flex;align-items:center;gap:.4rem}
.pill.cloud{color:var(--cloud);background:var(--cloud-weak)}
.pill .dot{width:.46rem;height:.46rem;border-radius:50%;background:currentColor}
.meta{color:var(--muted);font-variant-numeric:tabular-nums}
.tag{font-size:.6rem;font-weight:600;letter-spacing:.09em;text-transform:uppercase;
  color:var(--muted);border:1px solid var(--line-strong);border-radius:var(--pill);padding:.1rem .45rem}
.tag.sticky{color:var(--cloud);border-color:color-mix(in srgb,var(--cloud) 45%,var(--line-strong))}
.why-btn{margin-left:auto;flex:none;font:inherit;font-size:.72rem;font-weight:700;width:1.2rem;height:1.2rem;
  border-radius:50%;border:1px solid var(--line-strong);background:var(--panel);color:var(--muted);
  cursor:pointer;display:grid;place-items:center;line-height:1;padding:0;transition:border-color .15s,color .15s}
.why-btn:hover,.why-btn[aria-expanded=true]{border-color:var(--accent);color:var(--accent)}
.why-pop{position:absolute;top:calc(100% + .5rem);left:0;z-index:8;min-width:17rem;max-width:21rem;
  background:var(--elev);border:1px solid var(--line-strong);border-radius:var(--radius-sm);
  box-shadow:0 8px 28px rgba(13,13,13,.14),var(--shadow);padding:.85rem .95rem;font-size:.82rem;
  opacity:0;visibility:hidden;transform:translateY(-4px);transition:opacity .15s,transform .15s,visibility .15s}
.routing:hover .why-pop,.why-pop.open{opacity:1;visibility:visible;transform:none}
.why{font-size:.62rem;font-weight:600;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);margin-bottom:.4rem}
.rows{display:flex;flex-direction:column;gap:.4rem;margin:.1rem 0 .7rem}
.row{display:grid;grid-template-columns:9rem 1fr 2.6rem;align-items:center;gap:.6rem}
.row .nm{color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;text-transform:capitalize}
.row .val{font-variant-numeric:tabular-nums;text-align:right;color:var(--text);font-weight:600}
.track{height:6px;background:var(--track);border-radius:999px;overflow:hidden}
.track i{display:block;height:100%;border-radius:999px;
  background:linear-gradient(90deg,color-mix(in srgb,var(--accent) 70%,transparent),var(--accent));
  transition:width .35s cubic-bezier(.2,.7,.3,1)}
.cost{display:flex;justify-content:space-between;gap:.5rem;color:var(--muted);
  border-top:1px solid var(--line);padding-top:.6rem;font-variant-numeric:tabular-nums}
.cost b{color:var(--text);font-weight:600}
.cost.solo{border-top:0;padding-top:0}
form{position:sticky;bottom:0;background:linear-gradient(to top,var(--bg) 72%,transparent);padding:.5rem 1.1rem 1.1rem}
.composer{max-width:760px;margin:0 auto;display:flex;flex-direction:column;gap:.35rem;
  background:var(--elev);border:1px solid var(--line-strong);border-radius:26px;
  padding:.7rem .9rem .6rem;box-shadow:var(--shadow);transition:box-shadow .15s,border-color .15s}
.composer:focus-within{border-color:color-mix(in srgb,var(--accent) 60%,var(--line-strong))}
.composer-bar{display:flex;align-items:center;gap:.5rem;justify-content:space-between}
.tools{display:flex;align-items:center;gap:.4rem;flex-wrap:wrap;min-width:0}
.composer.started .tools{display:none}
textarea{width:100%;border:0;background:transparent;color:var(--text);font:inherit;resize:none;
  max-height:40vh;padding:.4rem .25rem .15rem;outline:none;line-height:1.5}
textarea:focus-visible{box-shadow:none}
textarea::placeholder{color:var(--muted)}
#send{flex:none;margin-left:auto;width:34px;height:34px;border:0;border-radius:50%;background:var(--btn);color:var(--btn-text);
  font-size:1.05rem;line-height:1;cursor:pointer;display:grid;place-items:center;transition:transform .05s,opacity .15s}
#send:active{transform:translateY(1px)}
#send:disabled{opacity:.35;cursor:default}
:focus-visible{outline:none;box-shadow:var(--ring);border-radius:10px}
@media(prefers-reduced-motion:reduce){*{transition:none!important;animation:none!important;scroll-behavior:auto!important}}
</style></head><body class="intro">
<div class="bar">
  <button class="side-toggle" id="sideToggle" type="button" aria-label="Toggle sidebar" title="Toggle sidebar">&#9776;</button>
  <span class="brand">Wayfinder<span class="dot">.</span></span>
  <span class="mode" id="mode">ready</span>
  <span class="saved" id="saved"></span>
</div>
<div class="lower">
<aside class="sidebar" id="sidebar">
  <div class="side-top">
    <button class="nav searchIcon" id="searchIcon" type="button" data-tip="Search"><span class="ico"><svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path d="M20 20l-3.6-3.6"/></svg></span></button>
    <input class="side-search" id="search" type="search" placeholder="Search chats" aria-label="Search chats">
  </div>
  <div class="side-scroll" id="threads"></div>
  <div class="side-foot">
    <button class="nav" id="newfolder" type="button" data-tip="New folder"><span class="ico"><svg viewBox="0 0 24 24"><path d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2z"/><path d="M12 11v5M9.5 13.5h5"/></svg></span><span class="nav-label">New folder</span></button>
    <button class="nav primary" id="newchat" type="button" data-tip="New chat"><span class="ico"><svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg></span><span class="nav-label">New chat</span></button>
    <button class="nav" id="settingsBtn" type="button" data-tip="Settings"><span class="ico"><svg viewBox="0 0 24 24"><path d="M4 7h9M17 7h3M4 17h3M11 17h9"/><circle cx="15" cy="7" r="2.2"/><circle cx="9" cy="17" r="2.2"/></svg></span><span class="nav-label">Settings</span></button>
    <button class="nav" id="helpBtn" type="button" data-tip="Help"><span class="ico"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M9.8 9.3a2.3 2.3 0 014.3 1c0 1.6-2.1 2-2.1 3.2"/><circle cx="12" cy="17.2" r="0.7" fill="currentColor" stroke="none"/></svg></span><span class="nav-label">Help</span></button>
  </div>
</aside>
<div class="content">
  <div class="settings" id="settings" hidden>
    <div class="set-row">
      <div class="set-head">
        <label class="set-name"><input type="checkbox" id="useT"> Threshold</label>
        <button class="help" type="button" data-tip="Move the local-to-cloud cut for this chat. Higher keeps more on the local model; lower sends more to cloud. Unchecked uses the server's configured threshold.">?</button>
      </div>
      <div class="set-ctl"><input type="range" id="t" min="0" max="100" value="50" disabled><output id="tv">config</output></div>
    </div>
    <div class="set-row">
      <div class="set-head">
        <label class="set-name" for="scope">Routing Scope</label>
        <button class="help" type="button" data-tip="Which text is scored in a multi-turn chat: the current turn (system + latest message) by default, or the latest message only, all your messages, or the whole transcript.">?</button>
      </div>
      <select id="scope">
        <option value="">Server Config</option>
        <option value="turn">Turn &mdash; System + Latest</option>
        <option value="last_user">Last User &mdash; Latest Only</option>
        <option value="user">User &mdash; All Your Messages</option>
        <option value="all">All &mdash; Entire Transcript</option>
      </select>
    </div>
    <div class="set-row">
      <div class="set-head">
        <label class="set-name"><input type="checkbox" id="sticky"> Sticky</label>
        <button class="help" type="button" data-tip="Once any turn needs the big model, keep the whole chat there &mdash; so a short follow-up after a hard question doesn't drop back to local.">?</button>
      </div>
    </div>
    <div class="set-row">
      <div class="set-head">
        <label class="set-name" for="cooldown">Cool-Down</label>
        <button class="help" type="button" data-tip="Release a sticky chat back to local after this many calm (low-score) turns. 'Never' keeps it latched for the rest of the conversation.">?</button>
      </div>
      <select id="cooldown" disabled>
        <option value="0">Never Decay</option>
        <option value="1">After 1 Calm Turn</option>
        <option value="2">After 2 Calm Turns</option>
        <option value="3">After 3 Calm Turns</option>
      </select>
    </div>
    <div class="set-row">
      <div class="set-head">
        <span class="set-name">Models</span>
        <button class="help" type="button" data-tip="Endpoints this gateway routes to, and whether each model's API key is present. Keys live in environment variables &mdash; set the named var and restart. They're never entered here.">?</button>
      </div>
      <div class="models" id="models"><div class="set-hint">Loading&hellip;</div></div>
    </div>
    <details class="adv">
      <summary>Advanced Tuning</summary>
      <div class="adv-body">
        <div class="set-row">
          <div class="set-head">
            <label class="set-name"><input type="checkbox" id="lex"> Lexical Signals</label>
            <button class="help" type="button" data-tip="Score difficulty vocabulary (prove, theorem, &sum;) so a short, structureless prompt can still route up. Off by default &mdash; it detects vocabulary, not meaning.">?</button>
          </div>
          <div class="set-ctl"><input type="range" id="lexw" min="0" max="100" value="40" disabled><output id="lexv">4.0</output></div>
        </div>
        <div class="adv-grp">Feature Weights <button class="help" type="button" data-tip="How much each structural feature adds to the score. Drag to re-weight; the decision and its 'why' update on your next message.">?</button></div>
        <div id="weights"></div>
        <div class="adv-grp">Lexicon Terms <button class="help" type="button" data-tip="Trigger words for the lexical signal. Load a starter profile, then edit; leave blank to use the built-in defaults.">?</button></div>
        <select id="profile"><option value="">&mdash; Starter Profile &mdash;</option></select>
        <span class="set-hint" id="profnote"></span>
        <label class="set-name" for="rterms">Reasoning</label>
        <textarea id="rterms" rows="2" placeholder="prove, theorem, derive, induction&hellip;"></textarea>
        <label class="set-name" for="cterms">Constraint</label>
        <textarea id="cterms" rows="2" placeholder="must, exactly, without, guarantee&hellip;"></textarea>
        <button class="export" id="export" type="button">Export config</button>
        <pre class="cfg" id="cfg" hidden></pre>
      </div>
    </details>
    <div class="set-foot">Applies to your next message.</div>
  </div>
<main><div class="wrap" id="wrap">
  <div class="empty" id="empty"><h2>Ask anything</h2>
  <div>Every reply shows where it routed; open the <b>?</b> for the score, the features behind it, and the cost saved. Run the gateway with <code>--dry-run</code> for a keyless demo.</div>
  </div>
</div></main>
<form id="composer"><div class="composer">
  <textarea id="in" rows="1" placeholder="Message Wayfinder&hellip;" autofocus></textarea>
  <div class="composer-bar">
    <div class="tools" id="tools">
      <button class="eg" type="button" data-eg="trivial">What's 2 + 2?</button>
      <button class="eg" type="button" data-eg="plan">A structured migration plan</button>
    </div>
    <button id="send" type="submit" aria-label="Send" title="Send">&#8593;</button>
  </div>
</div></form>
</div>
</div>
<div class="modal" id="help" hidden>
  <div class="modal-card">
    <div class="modal-head"><h2>Wayfinder &mdash; quick guide</h2><button class="modal-x" id="helpX" type="button" aria-label="Close">&times;</button></div>
    <p>Wayfinder is a <b>structural router</b>: it scores the shape of your prompt &mdash; length, lists, code, headings, tables &mdash; and sends easy turns to a cheap <b>local</b> model and hard ones to a capable <b>cloud</b> model. The decision is deterministic, with no model call to make it.</p>
    <h3>Reading a reply</h3>
    <ul>
      <li><b>Pill</b> &mdash; where it routed (local or cloud).</li>
      <li><b>score</b> &mdash; structural complexity (0&ndash;1) against the threshold.</li>
      <li><b>?</b> &mdash; the features behind the score and the cost saved vs always-cloud.</li>
      <li><b>latched</b> &mdash; the conversation latch kept a hard chat on the big model.</li>
    </ul>
    <h3>Settings</h3>
    <ul>
      <li><b>Threshold</b> &mdash; move the local&#8596;cloud cut for this chat.</li>
      <li><b>Routing scope</b> &mdash; what gets scored in a multi-turn chat.</li>
      <li><b>Sticky / Cool-down</b> &mdash; keep a hard chat on cloud, then let it drift back.</li>
      <li><b>Advanced</b> &mdash; tune feature weights, enable lexical signals, load a lexicon profile, and export it as config.</li>
      <li><b>Models</b> &mdash; which endpoints are wired and whether each API key is set.</li>
    </ul>
    <h3>Chats &amp; folders</h3>
    <p>Conversations live in your browser &mdash; nothing is stored server-side. Start chats, drag them into folders (or use the &#8943; menu), pin, and rename from the sidebar.</p>
    <h3>API keys</h3>
    <p>Keys are never entered here: set the environment variable named in <b>Settings &rarr; Models</b> and restart. Run the gateway with <code>--dry-run</code> for a keyless demo.</p>
    <p class="modal-links">Full docs: <a href="https://github.com/itsthelore/wayfinder-router" target="_blank" rel="noopener">README</a> &middot; <a href="https://github.com/itsthelore/wayfinder-router/blob/HEAD/docs/faq.md" target="_blank" rel="noopener">FAQ</a></p>
  </div>
</div>
<script>
const wrap=document.getElementById('wrap'),empty=document.getElementById('empty');
const inEl=document.getElementById('in'),sendBtn=document.getElementById('send');
const card=document.querySelector('.composer');
const useT=document.getElementById('useT'),tEl=document.getElementById('t'),tv=document.getElementById('tv');
const modeEl=document.getElementById('mode'),savedEl=document.getElementById('saved');
const settingsBtn=document.getElementById('settingsBtn'),settings=document.getElementById('settings');
const helpBtn=document.getElementById('helpBtn'),helpModal=document.getElementById('help'),helpX=document.getElementById('helpX');
const searchIcon=document.getElementById('searchIcon');
const scopeEl=document.getElementById('scope'),stickyEl=document.getElementById('sticky');
const cooldownEl=document.getElementById('cooldown');
function syncSticky(){cooldownEl.disabled=!stickyEl.checked;}
stickyEl.addEventListener('change',syncSticky); syncSticky();
let savedTotal=0, savedUnit='', pretty=s=>s.replace(/_/g,' ');
const newchat=document.getElementById('newchat'),searchEl=document.getElementById('search');
const listEl=document.getElementById('threads'),sideToggle=document.getElementById('sideToggle');
const newfolder=document.getElementById('newfolder');
const titleCase=s=>s.replace(/\\b[a-z]/g,c=>c.toUpperCase());

function syncT(){const on=useT.checked; tEl.disabled=!on; tv.textContent=on?(tEl.value/100).toFixed(2):'config'; tv.classList.toggle('on',on);}
useT.addEventListener('change',syncT); tEl.addEventListener('input',syncT); syncT();
// Advanced tuning (WF-ADR-0023): per-request scoring overrides + export.
const lex=document.getElementById('lex'),lexw=document.getElementById('lexw'),lexv=document.getElementById('lexv');
const rterms=document.getElementById('rterms'),cterms=document.getElementById('cterms');
const exportBtn=document.getElementById('export'),cfgEl=document.getElementById('cfg'),weightsEl=document.getElementById('weights');
let advTouched=false; const touch=()=>{advTouched=true;};
const FEATS=[['word_count','word count',3],['heading_count','headings',1.5],['max_heading_depth','heading depth',1],['list_item_count','list items',2],['link_count','links',1],['code_block_count','code blocks',1.5],['table_row_count','table rows',1]];
FEATS.forEach(([feat,label,def])=>{
  const row=el('wrow'),sp=document.createElement('span');sp.textContent=label;row.appendChild(sp);
  const s=document.createElement('input');s.type='range';s.min=0;s.max=100;s.dataset.feat=feat;s.setAttribute('value',Math.round(def*10));
  const o=document.createElement('output');o.textContent=def.toFixed(1);
  s.addEventListener('input',()=>{o.textContent=(s.value/10).toFixed(1);touch();});
  row.appendChild(s);row.appendChild(o);weightsEl.appendChild(row);
});
function syncLex(){lexw.disabled=!lex.checked;lexv.textContent=lex.checked?(lexw.value/10).toFixed(1):'off';}
lex.addEventListener('change',()=>{syncLex();touch();});
lexw.addEventListener('input',()=>{syncLex();touch();}); syncLex();
rterms.addEventListener('input',touch); cterms.addEventListener('input',touch);

// Read-only Models / key status (WF-ADR-0025): names, endpoints, and whether each
// model's key env var is set — never the secret itself.
const mhost=u=>{try{return new URL(u).host;}catch(e){return u;}};
fetch('/router/models').then(r=>r.json()).then(d=>{
  const box=document.getElementById('models'); box.innerHTML='';
  if(!d.models||!d.models.length){box.innerHTML='<div class="set-hint">'+(d.dry_run?'Dry-run &mdash; no models configured.':'No models configured.')+'</div>'; return;}
  d.models.forEach(m=>{const ok=m.key_ok,row=el('mrow');
    row.appendChild(el('mdot '+(ok?'ok':'warn')));
    const nm=document.createElement('span');nm.className='mname';nm.textContent=m.name;row.appendChild(nm);
    const ep=document.createElement('span');ep.className='mendpoint';ep.textContent=mhost(m.endpoint);row.appendChild(ep);
    const k=document.createElement('span');k.className='mkey'+(ok?'':' warn');
    k.textContent=m.api_key_env?(m.api_key_env+(ok?' ✓':' · missing')):'no key';
    row.appendChild(k);box.appendChild(row);});
}).catch(()=>{const b=document.getElementById('models');if(b)b.innerHTML='<div class="set-hint">Status unavailable.</div>';});

const profileEl=document.getElementById('profile'),profnote=document.getElementById('profnote'),PROF={};
fetch('/router/profiles').then(r=>r.json()).then(d=>{
  const labels={curated:'Curated',mined:'RouterBench (mined)'},groups={};
  (d.profiles||[]).forEach(p=>{PROF[p.id]=p;
    const g=groups[p.source]||(groups[p.source]=Object.assign(document.createElement('optgroup'),{label:labels[p.source]||p.source}));
    const o=document.createElement('option');o.value=p.id;o.textContent=p.name;g.appendChild(o);});
  Object.keys(groups).forEach(k=>profileEl.appendChild(groups[k]));
}).catch(()=>{});
profileEl.addEventListener('change',()=>{const p=PROF[profileEl.value];if(!p)return;
  rterms.value=p.reasoning_terms.join(', ');cterms.value=(p.constraint_terms||[]).join(', ');
  if(!lex.checked){lex.checked=true;syncLex();}
  profnote.textContent=p.note||'';touch();});
const splitTerms=s=>s.split(/[,\\n]/).map(x=>x.trim()).filter(Boolean);
function buildTuning(){
  const weights={};
  weightsEl.querySelectorAll('input[type=range]').forEach(s=>{weights[s.dataset.feat]=+(s.value/10).toFixed(1);});
  const lw=lex.checked?+(lexw.value/10).toFixed(1):0;
  ['reasoning_term_count','math_symbol_count','constraint_term_count','question_count'].forEach(f=>weights[f]=lw);
  const t={weights},r=splitTerms(rterms.value),c=splitTerms(cterms.value);
  if(r.length||c.length){t.lexicon={};if(r.length)t.lexicon.reasoning_terms=r;if(c.length)t.lexicon.constraint_terms=c;}
  return t;
}
exportBtn.addEventListener('click',async()=>{
  try{const res=await fetch('/router/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(advTouched?buildTuning():{})});
    cfgEl.textContent=await res.text();}catch(e){cfgEl.textContent='export failed: '+e.message;}
  cfgEl.hidden=false;
});

// Help tooltips: one floating element positioned with JS so it escapes the
// settings popover's scroll clipping. Driven by hover and keyboard focus.
const tip=document.createElement('div');tip.className='tip';tip.setAttribute('role','tooltip');tip.hidden=true;document.body.appendChild(tip);
function showTip(btn){const text=btn.getAttribute('data-tip');if(!text)return;
  tip.textContent=text;tip.hidden=false;
  const r=btn.getBoundingClientRect(),tw=tip.offsetWidth,th=tip.offsetHeight;
  const left=Math.max(8,Math.min(r.left+r.width/2-tw/2,innerWidth-tw-8));
  let top=r.bottom+6; if(top+th>innerHeight-8) top=r.top-th-6;
  tip.style.left=left+'px';tip.style.top=top+'px';tip.classList.add('show');}
function hideTip(){tip.classList.remove('show');tip.hidden=true;}
document.addEventListener('pointerover',e=>{const b=e.target.closest('.help');if(b)showTip(b);});
document.addEventListener('pointerout',e=>{if(e.target.closest('.help'))hideTip();});
document.addEventListener('focusin',e=>{const b=e.target.closest('.help');if(b)showTip(b);});
document.addEventListener('focusout',e=>{if(e.target.closest('.help'))hideTip();});
document.querySelectorAll('.help').forEach(b=>b.addEventListener('click',e=>{e.preventDefault();e.stopPropagation();showTip(b);}));

function setSettings(open){
  settings.toggleAttribute('hidden',!open);settingsBtn.classList.toggle('on',open);settingsBtn.setAttribute('aria-expanded',open?'true':'false');
  if(open){const r=settingsBtn.getBoundingClientRect(),pw=settings.offsetWidth,ph=settings.offsetHeight;
    let left=r.right+8; if(left+pw>innerWidth-8)left=Math.max(8,r.left-pw-8);
    let top=Math.min(r.bottom-ph,innerHeight-ph-8); top=Math.max(8,top);
    settings.style.left=left+'px';settings.style.top=top+'px';}}
settingsBtn.addEventListener('click',e=>{e.stopPropagation();setSettings(settings.hasAttribute('hidden'));});
document.addEventListener('click',e=>{if(!settings.hasAttribute('hidden')&&!settings.contains(e.target))setSettings(false);});
document.addEventListener('keydown',e=>{if(e.key==='Escape')setSettings(false);});

inEl.addEventListener('input',()=>{inEl.style.height='auto';inEl.style.height=Math.min(inEl.scrollHeight,window.innerHeight*0.4)+'px';});
inEl.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();composer.requestSubmit();}});

function el(cls,txt){const d=document.createElement('div');d.className=cls;if(txt!=null)d.textContent=txt;return d;}
function turn(){const t=el('turn');wrap.appendChild(t);return t;}
function scroll(){requestAnimationFrame(()=>{const m=document.querySelector('main');m.scrollTop=m.scrollHeight;});}

// A compact routing strip under each reply: model + score always visible, with the
// full "why" breakdown and cost tucked behind a hover/click "?" popover.
function routing(wf){
  const r=el('routing');
  const pill=el('pill '+(wf.model==='cloud'?'cloud':''));
  pill.appendChild(el('dot')); pill.appendChild(document.createTextNode(' '+wf.model));
  r.appendChild(pill);
  r.appendChild(el('meta','score '+Number(wf.score).toFixed(2)+(wf.mode!=='scored'&&wf.mode!=='sticky'?' · '+titleCase(wf.mode):'')));
  if(wf.mode==='sticky') r.appendChild(el('tag sticky','latched'));
  if(wf.dry_run) r.appendChild(el('tag','dry-run'));

  const cons=(wf.contributions||[]).filter(x=>x.contribution>0).sort((a,b)=>b.contribution-a.contribution).slice(0,4);
  if(cons.length||wf.cost){
    const btn=document.createElement('button');btn.className='why-btn';btn.type='button';btn.textContent='?';
    btn.setAttribute('aria-label','Why this route'); btn.setAttribute('aria-expanded','false');
    const pop=el('why-pop');
    if(cons.length){
      pop.appendChild(el('why','why'));
      const max=cons[0].contribution||1, rows=el('rows');
      cons.forEach(x=>{
        const row=el('row');
        row.appendChild(el('nm',pretty(x.name)));
        const tr=el('track'),bar=document.createElement('i');bar.style.width=Math.round(100*x.contribution/max)+'%';tr.appendChild(bar);
        row.appendChild(tr);
        row.appendChild(el('val',String(x.value)));
        rows.appendChild(row);
      });
      pop.appendChild(rows);
    }
    if(wf.cost){
      const k=wf.cost, u=k.estimated?'units':'$';
      const cost=el('cost'+(cons.length?'':' solo')); cost.title=(k.unit||'')+(k.estimated?' (estimated)':'');
      const left=el(''); left.innerHTML='&#8776; <b>'+(+k.per_call).toFixed(3)+'</b> '+u;
      const right=el(''); right.innerHTML='saved <b>'+(+k.saved).toFixed(3)+'</b>';
      cost.appendChild(left); cost.appendChild(right); pop.appendChild(cost);
    }
    btn.addEventListener('click',()=>{const open=pop.classList.toggle('open');btn.setAttribute('aria-expanded',open?'true':'false');if(open)scroll();});
    r.appendChild(btn); r.appendChild(pop);
  }
  return r;
}

// --- conversation threads + folders, persisted client-side (WF-ADR-0026) ---
const LS='wf.threads', LF='wf.folders';
let threads=[], folders=[], currentId=null, lastTurn=null;
try{threads=JSON.parse(localStorage.getItem(LS)||'[]')||[];}catch(e){threads=[];}
try{folders=JSON.parse(localStorage.getItem(LF)||'[]')||[];}catch(e){folders=[];}
const persist=()=>{try{localStorage.setItem(LS,JSON.stringify(threads));localStorage.setItem(LF,JSON.stringify(folders));}catch(e){}};
const cur=()=>threads.find(t=>t.id===currentId)||null;
const apiMessages=t=>(t?t.items:[]).filter(i=>i.role==='user'||(i.role==='assistant'&&i.content)).map(i=>({role:i.role,content:i.content}));
const titleFrom=text=>{const s=(text||'').replace(/\\s+/g,' ').trim();return s.length>42?s.slice(0,42)+'…':(s||'New chat');};

function renderItem(it){
  if(it.role==='user'){lastTurn=turn();lastTurn.appendChild(el('msg user',it.content));return;}
  const ans=el('answer');
  if(it.role==='note')ans.appendChild(el('msg note',it.content));
  else if(it.dry)ans.appendChild(el('msg bot dry',it.content));
  else ans.appendChild(el('msg bot',it.content));
  if(it.wf)ans.appendChild(routing(it.wf));
  (lastTurn||(lastTurn=turn())).appendChild(ans);
}
function recomputeSaved(){const t=cur();let tot=0,unit='$';
  if(t)t.items.forEach(i=>{if(i.wf&&i.wf.cost&&typeof i.wf.cost.saved==='number'){tot+=i.wf.cost.saved;unit=i.wf.cost.estimated?'units':'$';}});
  savedTotal=tot;savedUnit=unit;savedEl.innerHTML=tot?('Saved <b>'+tot.toFixed(3)+'</b> '+unit+' vs always-cloud'):'';}

// inline rename: swap a title element for an input, commit on Enter/blur
function renameInline(titleEl,current,commit){
  const inp=document.createElement('input');inp.className='t-rename';inp.value=current;
  titleEl.replaceWith(inp);inp.focus();inp.select();let done=false;
  const fin=save=>{if(done)return;done=true;const v=inp.value.trim();if(save&&v)commit(v);renderSidebar();};
  inp.addEventListener('keydown',e=>{e.stopPropagation();if(e.key==='Enter'){e.preventDefault();fin(true);}else if(e.key==='Escape'){e.preventDefault();fin(false);}});
  inp.addEventListener('blur',()=>fin(true));
  inp.addEventListener('click',e=>e.stopPropagation());
  inp.addEventListener('dblclick',e=>e.stopPropagation());
}

// floating per-chat menu: pin, rename, move to a folder, delete
const menu=document.createElement('div');menu.className='menu';menu.hidden=true;document.body.appendChild(menu);
const closeMenu=()=>{menu.hidden=true;menu.innerHTML='';};
function openMenu(btn,t){menu.innerHTML='';
  const mk=(name,fn,cls)=>{const b=document.createElement('button');b.type='button';b.textContent=name;if(cls)b.className=cls;
    b.addEventListener('click',e=>{e.stopPropagation();closeMenu();fn();});return b;};
  menu.appendChild(mk(t.pinned?'Unpin':'Pin',()=>{t.pinned=!t.pinned;persist();renderSidebar();}));
  menu.appendChild(mk('Rename',()=>{const row=btn.closest('.thread'),ttl=row&&row.querySelector('.t-title');
    if(ttl)renameInline(ttl,t.title||'',v=>{t.title=v;t.named=true;persist();});}));
  menu.appendChild(el('sep')); menu.appendChild(el('mlabel','Move to'));
  if(t.folder)menu.appendChild(mk('— No folder —',()=>{t.folder=null;persist();renderSidebar();}));
  folders.forEach(f=>{if(f.id!==t.folder)menu.appendChild(mk(f.name,()=>{t.folder=f.id;f.open=true;persist();renderSidebar();}));});
  menu.appendChild(mk('+ New folder…',()=>{const f=addFolder();if(f){t.folder=f.id;persist();renderSidebar();}}));
  menu.appendChild(el('sep'));
  menu.appendChild(mk('Delete chat',()=>deleteThread(t.id),'danger'));
  menu.hidden=false;
  const r=btn.getBoundingClientRect(),mw=menu.offsetWidth,mh=menu.offsetHeight;
  let top=r.bottom+4; if(top+mh>innerHeight-8)top=r.top-mh-4;
  menu.style.left=Math.max(8,Math.min(r.left,innerWidth-mw-8))+'px'; menu.style.top=top+'px';}
document.addEventListener('click',e=>{if(!menu.hidden&&!menu.contains(e.target))closeMenu();});

// drag & drop: file a chat into a folder, unfile to "Chats", or reorder
let dragId=null;
const clearDrag=()=>document.querySelectorAll('.drag-over').forEach(n=>n.classList.remove('drag-over'));
function dropFolder(e,folderId){e.preventDefault();e.stopPropagation();clearDrag();const t=threads.find(x=>x.id===dragId);
  if(t){t.folder=folderId;if(folderId){const f=folders.find(y=>y.id===folderId);if(f)f.open=true;}persist();renderSidebar();}}
function makeDrop(node,folderId){
  node.addEventListener('dragover',e=>{e.preventDefault();node.classList.add('drag-over');});
  node.addEventListener('dragleave',()=>node.classList.remove('drag-over'));
  node.addEventListener('drop',e=>dropFolder(e,folderId));}

const matchThread=(t,q)=>!q||(t.title||'').toLowerCase().includes(q)||(t.items||[]).some(i=>(i.content||'').toLowerCase().includes(q));
function threadRow(t){const row=el('thread'+(t.id===currentId?' active':''));row.draggable=true;
  if(t.pinned)row.appendChild(el('t-pin','★'));
  row.appendChild(el('t-title',t.title||'New chat'));
  const m=document.createElement('button');m.className='t-menu';m.type='button';m.textContent='⋯';m.setAttribute('aria-label','Chat options');m.title='Chat options';
  m.addEventListener('click',e=>{e.stopPropagation();openMenu(m,t);});
  row.appendChild(m); row.addEventListener('click',()=>openThread(t.id));
  row.addEventListener('dragstart',e=>{dragId=t.id;e.dataTransfer.effectAllowed='move';try{e.dataTransfer.setData('text/plain',t.id);}catch(_){}row.classList.add('dragging');});
  row.addEventListener('dragend',()=>{row.classList.remove('dragging');dragId=null;clearDrag();});
  row.addEventListener('dragover',e=>{e.preventDefault();row.classList.add('drag-over');});
  row.addEventListener('dragleave',()=>row.classList.remove('drag-over'));
  row.addEventListener('drop',e=>{e.preventDefault();e.stopPropagation();clearDrag();
    const t2=threads.find(x=>x.id===dragId);if(!t2||t2.id===t.id)return;
    t2.folder=t.folder;t2.pinned=t.pinned;threads=threads.filter(x=>x.id!==t2.id);
    const idx=threads.findIndex(x=>x.id===t.id);threads.splice(idx,0,t2);persist();renderSidebar();});
  return row;}
function renderSidebar(){const q=(searchEl.value||'').toLowerCase();listEl.innerHTML='';let any=false;
  const pinned=threads.filter(t=>t.pinned&&matchThread(t,q));
  if(pinned.length){any=true;listEl.appendChild(el('side-label','Pinned'));pinned.forEach(t=>listEl.appendChild(threadRow(t)));}
  folders.forEach(f=>{const chats=threads.filter(t=>!t.pinned&&t.folder===f.id&&matchThread(t,q));
    if(q&&!chats.length)return; any=any||chats.length>0;
    const fd=el('folder'+(f.open?' open':'')),head=el('folder-head');
    head.appendChild(el('folder-caret','\\u25B8')); head.appendChild(el('t-title',f.name)); head.appendChild(el('folder-count',String(chats.length)));
    const del=document.createElement('button');del.className='folder-del';del.type='button';del.textContent='×';del.title='Delete folder';
    del.addEventListener('click',e=>{e.stopPropagation();deleteFolder(f.id);});
    head.appendChild(del);
    head.addEventListener('click',()=>{f.open=!f.open;persist();renderSidebar();});
    head.addEventListener('dblclick',e=>{e.stopPropagation();const ttl=head.querySelector('.t-title');if(ttl)renameInline(ttl,f.name,v=>{f.name=v;persist();});});
    fd.appendChild(head);
    const box=el('folder-chats'); chats.forEach(t=>box.appendChild(threadRow(t))); fd.appendChild(box);
    makeDrop(head,f.id); makeDrop(box,f.id);
    listEl.appendChild(fd);});
  const loose=threads.filter(t=>!t.pinned&&!t.folder&&matchThread(t,q));
  const lbl=el('side-label','Chats'); makeDrop(lbl,null);
  if(loose.length||folders.length){any=any||loose.length>0; listEl.appendChild(lbl); loose.forEach(t=>listEl.appendChild(threadRow(t)));}
  if(!threads.length)listEl.appendChild(el('side-empty','No chats yet'));
  else if(!any)listEl.appendChild(el('side-empty','No matches'));}
function addFolder(){const name=(prompt('Folder name')||'').trim();if(!name)return null;
  const f={id:'f'+Date.now().toString(36),name:name,open:true};folders.unshift(f);persist();renderSidebar();return f;}
function deleteFolder(id){threads.forEach(t=>{if(t.folder===id)t.folder=null;});folders=folders.filter(f=>f.id!==id);persist();renderSidebar();}

function openThread(id){currentId=id;const t=cur();wrap.querySelectorAll('.turn').forEach(n=>n.remove());lastTurn=null;
  if(!t||!t.items.length){document.body.classList.add('intro');empty.style.display='';card.classList.remove('started');modeEl.textContent='ready';}
  else{document.body.classList.remove('intro');empty.style.display='none';card.classList.add('started');t.items.forEach(renderItem);}
  recomputeSaved();renderSidebar();scroll();}
function newThread(){const t={id:'t'+Date.now().toString(36)+Math.random().toString(36).slice(2,5),title:'New chat',created:Date.now(),items:[],folder:null};
  threads.unshift(t);persist();openThread(t.id);inEl.focus();}
function deleteThread(id){threads=threads.filter(t=>t.id!==id);persist();
  if(currentId===id){threads.length?openThread(threads[0].id):newThread();}else renderSidebar();}

async function send(text){
  let t=cur(); if(!t){newThread();t=cur();}
  empty.style.display='none'; card.classList.add('started'); document.body.classList.remove('intro');
  lastTurn=turn(); lastTurn.appendChild(el('msg user',text)); scroll();
  const first=!t.items.some(i=>i.role==='user');
  t.items.push({role:'user',content:text}); if(first)t.title=titleFrom(text);
  persist(); renderSidebar();
  sendBtn.disabled=true;
  const headers={'Content-Type':'application/json','X-Wayfinder-Debug':'true'};
  if(useT.checked) headers['X-Wayfinder-Threshold']=(tEl.value/100).toFixed(2);
  if(scopeEl.value) headers['X-Wayfinder-Route-On']=scopeEl.value;
  headers['X-Wayfinder-Sticky']=stickyEl.checked?'true':'false';
  if(stickyEl.checked) headers['X-Wayfinder-Sticky-Cooldown']=cooldownEl.value;
  try{
    const payload={model:'auto',messages:apiMessages(t),stream:false};
    if(advTouched) payload.wayfinder_tuning=buildTuning();
    const res=await fetch('/v1/chat/completions',{method:'POST',headers,body:JSON.stringify(payload)});
    const data=await res.json().catch(()=>({}));
    const wf=data.wayfinder||null;
    if(wf) modeEl.textContent=wf.dry_run?'dry-run':'live';
    const content=data&&data.choices&&data.choices[0]&&data.choices[0].message&&data.choices[0].message.content;
    const ans=el('answer'); let item;
    if(content){ans.appendChild(el('msg bot',content));item={role:'assistant',content:content,wf};}
    else if(data&&data.error){const m=data.error.message||'error';ans.appendChild(el('msg note',m));item={role:'note',content:m};}
    else if(wf&&wf.dry_run){const m='Routed to the '+wf.model+' model — no model was called in --dry-run mode. Configure a model (or drop --dry-run) to see the reply.';
      ans.appendChild(el('msg bot dry',m));item={role:'assistant',content:'',wf,dry:true};}
    else{const m='No content returned.';ans.appendChild(el('msg note',m));item={role:'note',content:m};}
    if(wf) ans.appendChild(routing(wf));
    lastTurn.appendChild(ans);
    t.items.push(item); persist(); recomputeSaved(); renderSidebar();
  }catch(e){const m='Gateway unreachable: '+e.message;const ans=el('answer');ans.appendChild(el('msg note',m));lastTurn.appendChild(ans);t.items.push({role:'note',content:m});persist();}
  sendBtn.disabled=false; scroll(); inEl.focus();
}
composer.addEventListener('submit',e=>{e.preventDefault();const v=inEl.value.trim();if(!v)return;
  inEl.value='';inEl.style.height='auto';send(v);});
const EGS={
  trivial:"What's 2 + 2?",
  plan:"# Migration plan\\n\\nWrite a zero-downtime plan to migrate our Postgres database to a new region.\\n\\n## Requirements\\n\\n- enumerate prerequisites and risks\\n- detail the cutover sequence\\n- provide rollback steps\\n- estimate the maintenance window\\n\\n```sql\\nSELECT pg_create_logical_replication_slot('mig','pgoutput');\\n```\\n\\n| phase | risk |\\n| --- | --- |\\n| dual-write | medium |\\n| cutover | high |"
};
document.querySelectorAll('.eg').forEach(b=>b.addEventListener('click',()=>send(EGS[b.dataset.eg]||b.dataset.eg)));

newchat.addEventListener('click',newThread);
newfolder.addEventListener('click',addFolder);
searchEl.addEventListener('input',renderSidebar);

// collapsible sidebar (persisted); search icon expands the rail and focuses search
function setCollapsed(c){document.body.classList.toggle('sidebar-collapsed',c);try{localStorage.setItem('wf.sidebar',c?'1':'0');}catch(e){}}
sideToggle.addEventListener('click',()=>setCollapsed(!document.body.classList.contains('sidebar-collapsed')));
searchIcon.addEventListener('click',()=>{setCollapsed(false);searchEl.focus();});
const savedCollapse=(()=>{try{return localStorage.getItem('wf.sidebar');}catch(e){return null;}})();
if(savedCollapse==='1'||(savedCollapse===null&&matchMedia('(max-width:760px)').matches))document.body.classList.add('sidebar-collapsed');

// help modal
function setHelp(open){helpModal.toggleAttribute('hidden',!open);}
helpBtn.addEventListener('click',()=>setHelp(true));
helpX.addEventListener('click',()=>setHelp(false));
helpModal.addEventListener('click',e=>{if(e.target===helpModal)setHelp(false);});
document.addEventListener('keydown',e=>{if(e.key==='Escape'){setHelp(false);setSettings(false);}});

if(threads.length)openThread(threads[0].id); else newThread();
</script></body></html>"""

# --- metrics (WF-ADR-0018) --------------------------------------------------
# Prometheus histogram bucket bounds, in seconds. Decision latency is a text scan
# with no model call, so its buckets are sub-millisecond; upstream latency spans a
# model round-trip, so its buckets are coarse.
_DECISION_BUCKETS = (0.0001, 0.00025, 0.0005, 0.001, 0.0025, 0.005, 0.01, 0.025, 0.05)
_UPSTREAM_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0)


def _new_hist(bounds: tuple[float, ...]) -> dict:
    return {"bounds": bounds, "counts": [0] * len(bounds), "sum": 0.0, "count": 0}


def _observe(hist: dict, value: float) -> None:
    hist["sum"] += value
    hist["count"] += 1
    for i, bound in enumerate(hist["bounds"]):
        if value <= bound:
            hist["counts"][i] += 1


def _label_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _render_histogram(name: str, hist: dict, label_pairs: str = "") -> list[str]:
    sep = "," if label_pairs else ""
    out: list[str] = []
    for bound, count in zip(hist["bounds"], hist["counts"], strict=True):
        out.append(f'{name}_bucket{{{label_pairs}{sep}le="{bound:g}"}} {count}')
    out.append(f'{name}_bucket{{{label_pairs}{sep}le="+Inf"}} {hist["count"]}')
    braces = f"{{{label_pairs}}}" if label_pairs else ""
    out.append(f"{name}_sum{braces} {hist['sum']:g}")
    out.append(f"{name}_count{braces} {hist['count']}")
    return out


class Metrics:
    """In-memory gateway metrics rendered in the Prometheus text format (WF-ADR-0018).

    Metadata only — ``model`` and ``mode`` labels, never prompt text — mirroring
    the /router ring's privacy stance. Pure in-process counters; the /metrics
    endpoint that reads them is off the scored path (no key, no model call, no
    network). Counters reset on restart, as Prometheus expects.
    """

    def __init__(self, version: str) -> None:
        self.version = version
        self.requests: dict[tuple[str, str], int] = {}  # (model, mode) -> count
        self.upstream_errors: dict[str, int] = {}  # model -> count
        self.reload_failures = 0
        self.decision = _new_hist(_DECISION_BUCKETS)
        self.upstream: dict[str, dict] = {}  # model -> histogram
        self.model_costs: dict[str, float] = {}  # model -> cost_per_1k (WF-ADR-0017)

    def set_model_costs(self, costs: dict[str, float]) -> None:
        """Record per-model cost metadata to surface as a gauge (informational)."""
        self.model_costs = dict(costs)

    def observe_decision(self, model: str, mode: str, seconds: float) -> None:
        key = (model, mode)
        self.requests[key] = self.requests.get(key, 0) + 1
        _observe(self.decision, seconds)

    def observe_upstream(self, model: str, seconds: float) -> None:
        hist = self.upstream.get(model)
        if hist is None:
            hist = self.upstream[model] = _new_hist(_UPSTREAM_BUCKETS)
        _observe(hist, seconds)

    def observe_upstream_error(self, model: str) -> None:
        self.upstream_errors[model] = self.upstream_errors.get(model, 0) + 1

    def record_reload_failure(self) -> None:
        self.reload_failures += 1

    def render(self) -> str:
        lines: list[str] = []
        lines.append("# HELP wayfinder_router_build_info Build information.")
        lines.append("# TYPE wayfinder_router_build_info gauge")
        lines.append(f'wayfinder_router_build_info{{version="{_label_escape(self.version)}"}} 1')

        lines.append("# HELP wayfinder_router_requests_total Routed requests by model and mode.")
        lines.append("# TYPE wayfinder_router_requests_total counter")
        for (model, mode), n in sorted(self.requests.items()):
            labels = f'model="{_label_escape(model)}",mode="{_label_escape(mode)}"'
            lines.append(f"wayfinder_router_requests_total{{{labels}}} {n}")

        lines.append(
            "# HELP wayfinder_router_upstream_errors_total Upstream transport failures by model."
        )
        lines.append("# TYPE wayfinder_router_upstream_errors_total counter")
        for model, n in sorted(self.upstream_errors.items()):
            lines.append(
                f'wayfinder_router_upstream_errors_total{{model="{_label_escape(model)}"}} {n}'
            )

        lines.append(
            "# HELP wayfinder_router_config_reload_failures_total "
            "Config reloads that failed and kept the last-good config."
        )
        lines.append("# TYPE wayfinder_router_config_reload_failures_total counter")
        lines.append(f"wayfinder_router_config_reload_failures_total {self.reload_failures}")

        if self.model_costs:
            lines.append(
                "# HELP wayfinder_router_model_cost_per_1k "
                "Configured per-1k-token cost by model (informational, WF-ADR-0017)."
            )
            lines.append("# TYPE wayfinder_router_model_cost_per_1k gauge")
            for model, cost in sorted(self.model_costs.items()):
                lines.append(
                    f'wayfinder_router_model_cost_per_1k{{model="{_label_escape(model)}"}} {cost:g}'
                )

        lines.append(
            "# HELP wayfinder_router_decision_latency_seconds "
            "Time to score a prompt and pick a model (no model call)."
        )
        lines.append("# TYPE wayfinder_router_decision_latency_seconds histogram")
        lines += _render_histogram("wayfinder_router_decision_latency_seconds", self.decision)

        lines.append(
            "# HELP wayfinder_router_upstream_latency_seconds "
            "Upstream model round-trip time by model."
        )
        lines.append("# TYPE wayfinder_router_upstream_latency_seconds histogram")
        for model, hist in sorted(self.upstream.items()):
            lines += _render_histogram(
                "wayfinder_router_upstream_latency_seconds",
                hist,
                f'model="{_label_escape(model)}"',
            )
        return "\n".join(lines) + "\n"


class GatewayUnavailable(Exception):
    """The gateway extra (fastapi / uvicorn / httpx) is not installed."""


class UpstreamError(Exception):
    """An upstream call failed at the transport level (timeout, connection)."""


@dataclass(frozen=True)
class GatewayModel:
    """An upstream endpoint a recommended model name maps to."""

    base_url: str  # OpenAI-compatible base, e.g. http://localhost:11434/v1
    model: str  # the upstream model id to send in the forwarded request
    api_key_env: str | None = None  # env var holding the key, or None for no auth
    cost_per_1k: float | None = None  # optional cost metadata (WF-ADR-0017), informational


@dataclass(frozen=True)
class GatewayConfig:
    """Maps recommended model names to upstream endpoints (from `[gateway.models]`).

    ``route_on`` selects which part of a multi-turn chat the router scores
    (WF-ADR-0021); see :func:`extract_prompt`. Default ``"turn"``. ``sticky``
    latches a conversation to the highest tier any of its turns has needed
    (WF-ADR-0022), so a chat that goes hard stays on the big model. Default off.
    ``sticky_cooldown`` is the number of calm turns after which the latch decays
    back down (``0`` = never; monotonic).
    """

    models: dict[str, GatewayModel] = field(default_factory=dict)
    route_on: str = "turn"
    sticky: bool = False
    sticky_cooldown: int = 0


# Which chat-message text the router scores. The deterministic core scores
# whatever string it is handed (WF-ADR-0001); this only chooses that string so a
# multi-turn chat does not drift toward cloud as the transcript grows.
ROUTE_ON_SCOPES = ("turn", "last_user", "user", "all")


def load_gateway_config(start_dir: str = ".") -> GatewayConfig:
    """Read `[gateway.models.<name>]` from the nearest ``wayfinder-router.toml``."""
    path = find_config_file(start_dir)
    if path is None:
        return GatewayConfig()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise WayfinderConfigError(f"cannot read {path}: {exc}") from exc
    return gateway_config_from_toml(text, where=str(path))


def gateway_config_from_toml(text: str, where: str = "wayfinder-router.toml") -> GatewayConfig:
    """Parse a :class:`GatewayConfig` from ``wayfinder-router.toml`` text (file-free)."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise WayfinderConfigError(f"{where}: invalid TOML: {exc}") from exc
    gateway = data.get("gateway")
    if gateway is None:
        return GatewayConfig()
    if not isinstance(gateway, dict):
        raise WayfinderConfigError(f"{where}: '[gateway]' must be a table")
    route_on = gateway.get("route_on", "turn")
    if route_on not in ROUTE_ON_SCOPES:
        raise WayfinderConfigError(
            f"{where}: 'gateway.route_on' must be one of {', '.join(ROUTE_ON_SCOPES)}"
        )
    sticky = gateway.get("sticky", False)
    if not isinstance(sticky, bool):
        raise WayfinderConfigError(f"{where}: 'gateway.sticky' must be a boolean")
    cooldown = gateway.get("sticky_cooldown", 0)
    if isinstance(cooldown, bool) or not isinstance(cooldown, int) or cooldown < 0:
        raise WayfinderConfigError(f"{where}: 'gateway.sticky_cooldown' must be a non-negative integer")
    raw_models = gateway.get("models") or {}
    if not isinstance(raw_models, dict):
        raise WayfinderConfigError(f"{where}: '[gateway.models]' must be a table")
    models: dict[str, GatewayModel] = {}
    for name, entry in raw_models.items():
        if not isinstance(entry, dict):
            raise WayfinderConfigError(f"{where}: '[gateway.models.{name}]' must be a table")
        base_url = entry.get("base_url")
        model = entry.get("model")
        api_key_env = entry.get("api_key_env")
        if not isinstance(base_url, str) or not base_url:
            raise WayfinderConfigError(
                f"{where}: 'gateway.models.{name}.base_url' must be a string"
            )
        if not isinstance(model, str) or not model:
            raise WayfinderConfigError(f"{where}: 'gateway.models.{name}.model' must be a string")
        if api_key_env is not None and (not isinstance(api_key_env, str) or not api_key_env):
            raise WayfinderConfigError(
                f"{where}: 'gateway.models.{name}.api_key_env' must be a non-empty string"
            )
        cost_per_1k = entry.get("cost_per_1k")
        if cost_per_1k is not None and (
            isinstance(cost_per_1k, bool)
            or not isinstance(cost_per_1k, (int, float))
            or cost_per_1k < 0
        ):
            raise WayfinderConfigError(
                f"{where}: 'gateway.models.{name}.cost_per_1k' must be a non-negative number"
            )
        models[name] = GatewayModel(
            base_url=base_url,
            model=model,
            api_key_env=api_key_env,
            cost_per_1k=float(cost_per_1k) if cost_per_1k is not None else None,
        )
    return GatewayConfig(models=models, route_on=route_on, sticky=sticky, sticky_cooldown=cooldown)


def dump_gateway_toml(gateway: GatewayConfig) -> str:
    """Serialize a :class:`GatewayConfig` back to ``[gateway.models.*]`` TOML.

    Used by recalibration to preserve the endpoint mapping when it rewrites the
    routing section. Emits ``api_key_env`` (the env-var *name*) — never a secret.
    """
    blocks: list[str] = []
    if gateway.route_on != "turn" or gateway.sticky or gateway.sticky_cooldown:
        lines = ["[gateway]"]
        if gateway.route_on != "turn":
            lines.append(f'route_on = "{gateway.route_on}"')
        if gateway.sticky:
            lines.append("sticky = true")
        if gateway.sticky_cooldown:
            lines.append(f"sticky_cooldown = {gateway.sticky_cooldown}")
        blocks.append("\n".join(lines))
    for name, model in gateway.models.items():
        lines = [
            f"[gateway.models.{name}]",
            f'base_url = "{model.base_url}"',
            f'model = "{model.model}"',
        ]
        if model.api_key_env:
            lines.append(f'api_key_env = "{model.api_key_env}"')
        if model.cost_per_1k is not None:
            lines.append(f"cost_per_1k = {round(model.cost_per_1k, 6)!r}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _message_text(message: dict) -> str | None:
    """Text of one OpenAI-style message — plain string or array-of-parts — or None."""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [p["text"] for p in content if isinstance(p, dict) and isinstance(p.get("text"), str)]
        return "\n".join(parts) if parts else None
    return None


def extract_prompt(messages: object, *, route_on: str = "turn") -> str:
    """Deterministically join the chat-message text the router should score.

    ``route_on`` selects the scope (WF-ADR-0021), so a multi-turn chat does not
    drift toward cloud as its transcript (and the assistant's own replies) grows:

    - ``"turn"`` (default): the system message(s) plus the latest user message —
      the standing instructions and the new ask. Stable across turns.
    - ``"last_user"``: the latest user message only.
    - ``"user"``: every user message (excludes system and assistant).
    - ``"all"``: every message, all roles (legacy; the score ratchets upward over
      a conversation).

    Falls back to the last message when role-filtering finds nothing (e.g. a
    role-less or assistant-only payload), so the router never scores an empty
    string and silently routes local. Handles string and array-of-parts content.
    """
    if not isinstance(messages, list):
        return ""
    typed = [m for m in messages if isinstance(m, dict)]

    if route_on == "all":
        chosen: list[dict] = typed
    elif route_on == "user":
        chosen = [m for m in typed if m.get("role") == "user"]
    elif route_on == "last_user":
        last = next((m for m in reversed(typed) if m.get("role") == "user"), None)
        chosen = [last] if last is not None else []
    else:  # "turn" (default): standing system context + the new ask
        systems = [m for m in typed if m.get("role") == "system"]
        last = next((m for m in reversed(typed) if m.get("role") == "user"), None)
        chosen = systems + ([last] if last is not None else [])

    if not chosen and typed and route_on != "all":
        chosen = [typed[-1]]

    return "\n".join(t for t in (_message_text(m) for m in chosen) if t is not None)


# Per-request override transport (WF-ADR-0011). These are pure and offline: they
# only move which threshold/decision applies, never invoke a model.
THRESHOLD_HEADER = "x-wayfinder-threshold"
_AUTO = "auto"  # the OpenAI `model` sentinel meaning "Wayfinder decides"
_PREFER_LOW = "prefer-local"
_PREFER_HIGH = "prefer-hosted"  # canonical high-end directive (v0.1.3+)
_PREFER_HIGH_ALIASES = ("prefer-cloud",)  # back-compat: shipped in v0.1.2, still resolves


class BadOverride(Exception):
    """A per-request override was supplied but is malformed or not applicable."""


def resolve_pin(model_field: object, routing: RoutingConfig, gateway: GatewayConfig) -> str | None:
    """Resolve an explicit endpoint pin from the OpenAI ``model`` field, or ``None``.

    ``auto``, an empty value, or any string that is neither a configured endpoint
    name nor a ``prefer-*`` directive returns ``None`` — the request asks Wayfinder
    to score and decide (kept tolerant so ordinary OpenAI ``model`` ids pass
    through). ``prefer-local`` / ``prefer-hosted`` resolve to the low / high end of
    the configured router (its first / last tier's model); ``prefer-cloud`` is a
    back-compat alias of ``prefer-hosted``. They apply only to a tiered/binary
    router — a classifier has no ordered ladder, so ``prefer-*`` falls through to
    scoring there.
    """
    if not isinstance(model_field, str):
        return None
    name = model_field.strip()
    if not name or name == _AUTO:
        return None
    if routing.classifier is None and routing.tiers:
        if name == _PREFER_LOW:
            return routing.tiers[0].model
        if name == _PREFER_HIGH or name in _PREFER_HIGH_ALIASES:
            return routing.tiers[-1].model
    return name if name in gateway.models else None


def parse_threshold_header(value: str | None) -> float | None:
    """Parse the ``X-Wayfinder-Threshold`` header into a ``0.0``–``1.0`` cut, or ``None``.

    Raises :class:`BadOverride` when the header is present but not a number in range.
    """
    if value is None:
        return None
    try:
        threshold = float(value)
    except ValueError as exc:
        raise BadOverride(
            f"{THRESHOLD_HEADER} must be a number in 0.0-1.0, got {value!r}"
        ) from exc
    if not 0.0 <= threshold <= 1.0:
        raise BadOverride(f"{THRESHOLD_HEADER} must be in 0.0-1.0, got {threshold}")
    return threshold


def threshold_tiers(routing: RoutingConfig, threshold: float) -> tuple[Tier, ...]:
    """Binary tiers at ``threshold`` reusing the configured router's endpoint names.

    The threshold override is only well-defined for a binary (two-tier) router;
    a classifier or a multi-tier router has no single cut to move, so this raises
    :class:`BadOverride`.
    """
    if routing.classifier is not None or len(routing.tiers) != 2:
        raise BadOverride(
            f"{THRESHOLD_HEADER} applies only to a binary (two-tier) router; this "
            "gateway is configured for classifier or multi-tier routing"
        )
    return (Tier(0.0, routing.tiers[0].model), Tier(threshold, routing.tiers[1].model))


# Two more per-request overrides (WF-ADR-0011), mirroring the threshold header: they
# let a client (e.g. the demo's settings) move the routing scope / latch for one
# request without touching server config. Still pure and offline.
ROUTE_ON_HEADER = "x-wayfinder-route-on"
STICKY_HEADER = "x-wayfinder-sticky"
STICKY_COOLDOWN_HEADER = "x-wayfinder-sticky-cooldown"
_TRUE_TOKENS = frozenset({"1", "true", "yes", "on"})
_FALSE_TOKENS = frozenset({"0", "false", "no", "off"})


def parse_route_on_header(value: str | None) -> str | None:
    """Parse ``X-Wayfinder-Route-On`` into a scope, or ``None`` when absent.

    Raises :class:`BadOverride` for an unknown scope.
    """
    if value is None or not value.strip():
        return None
    scope = value.strip().lower()
    if scope not in ROUTE_ON_SCOPES:
        raise BadOverride(
            f"{ROUTE_ON_HEADER} must be one of {', '.join(ROUTE_ON_SCOPES)}, got {value!r}"
        )
    return scope


def resolve_sticky(value: str | None, default: bool) -> bool:
    """Resolve the conversation-latch flag from ``X-Wayfinder-Sticky``, else the config default."""
    if value is None or not value.strip():
        return default
    token = value.strip().lower()
    if token in _TRUE_TOKENS:
        return True
    if token in _FALSE_TOKENS:
        return False
    raise BadOverride(f"{STICKY_HEADER} must be true or false, got {value!r}")


def resolve_sticky_cooldown(value: str | None, default: int) -> int:
    """Resolve the latch cool-down (calm turns to release) from the header, else the default.

    ``0`` means the latch never decays (monotonic). Raises :class:`BadOverride` for a
    non-integer or negative value.
    """
    if value is None or not value.strip():
        return default
    try:
        cooldown = int(value.strip())
    except ValueError as exc:
        raise BadOverride(
            f"{STICKY_COOLDOWN_HEADER} must be a non-negative integer, got {value!r}"
        ) from exc
    if cooldown < 0:
        raise BadOverride(f"{STICKY_COOLDOWN_HEADER} must be >= 0, got {cooldown}")
    return cooldown


# In-demo scoring overrides (WF-ADR-0023): a request body field that tunes the
# scoring *function* (feature weights + lexicon terms) for this request only. Unlike
# the header overrides above (which move which threshold/scope/latch applies), this
# changes how the score is computed — but the scorer stays pure: the gateway only
# chooses the config it hands over (WF-ADR-0001). Opt-in, additive, never forwarded
# upstream. Production tuning is still `calibrate`; this is the demo's live knob.
TUNING_FIELD = "wayfinder_tuning"
_MAX_LEXICON_TERMS = 2000


def apply_scoring_overrides(routing: RoutingConfig, override: object) -> RoutingConfig:
    """Return a ``RoutingConfig`` variant with per-request weight/lexicon tuning applied.

    ``override`` is the parsed ``wayfinder_tuning`` body field: an optional partial
    ``weights`` map (merged over the configured weights) and an optional ``lexicon``
    with ``reasoning_terms`` / ``constraint_terms`` lists (which replace those sets).
    Returns ``routing`` unchanged when absent; raises :class:`BadOverride` on malformed
    input so the demo gets a clear 400.
    """
    if override is None:
        return routing
    if not isinstance(override, dict):
        raise BadOverride(f"{TUNING_FIELD} must be an object")
    weights = dict(routing.weights)
    raw_weights = override.get("weights")
    if raw_weights is not None:
        if not isinstance(raw_weights, dict):
            raise BadOverride(f"{TUNING_FIELD}.weights must be an object")
        for name, value in raw_weights.items():
            if name not in weights:
                raise BadOverride(f"{TUNING_FIELD}.weights: unknown feature {name!r}")
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                raise BadOverride(f"{TUNING_FIELD}.weights.{name} must be a non-negative number")
            weights[name] = float(value)
    lexicon = routing.lexicon
    raw_lexicon = override.get("lexicon")
    if raw_lexicon is not None:
        if not isinstance(raw_lexicon, dict):
            raise BadOverride(f"{TUNING_FIELD}.lexicon must be an object")
        changes: dict[str, frozenset[str]] = {}
        for key in ("reasoning_terms", "constraint_terms"):
            if key not in raw_lexicon:
                continue
            terms = raw_lexicon[key]
            if not isinstance(terms, list) or not all(isinstance(t, str) for t in terms):
                raise BadOverride(f"{TUNING_FIELD}.lexicon.{key} must be a list of strings")
            cleaned = frozenset(t.strip().lower() for t in terms if t.strip())
            if len(cleaned) > _MAX_LEXICON_TERMS:
                raise BadOverride(f"{TUNING_FIELD}.lexicon.{key} exceeds {_MAX_LEXICON_TERMS} terms")
            changes[key] = cleaned
        if changes:
            lexicon = replace(routing.lexicon, **changes)
    return replace(routing, weights=weights, lexicon=lexicon)


def _tier_rank(model: str, tiers: tuple[Tier, ...]) -> int:
    """Index of ``model`` in the ordered tier ladder, or -1 if it is not a tier."""
    for i, tier in enumerate(tiers):
        if tier.model == model:
            return i
    return -1


def conversation_high_water(
    messages: object, routing: RoutingConfig, tiers: tuple[Tier, ...], *, cooldown: int = 0
) -> str | None:
    """The tier the conversation latches to (WF-ADR-0022).

    Each user turn is scored on its own (with the standing system context), so this
    is computed from per-turn tiers — a *max over turns*, not a sum, so it does not
    inflate with conversation length the way concatenating the transcript would.

    With ``cooldown == 0`` the latch is monotonic: the highest tier any turn reached,
    and it never steps down. With ``cooldown == N`` (N >= 1) the latch *decays*: after
    ``N`` consecutive turns below the current latch, it steps down to that lower tier —
    so a chat that goes hard then stays light drifts back toward local. Returns the
    tier's model name, or ``None`` when there are no user turns to score.
    """
    if not isinstance(messages, list):
        return None
    systems = [m for m in messages if isinstance(m, dict) and m.get("role") == "system"]
    ranks: list[int] = []
    for message in messages:
        if isinstance(message, dict) and message.get("role") == "user":
            text = extract_prompt(systems + [message], route_on="turn")
            model = recommend_tier(score_complexity(text, config=routing).score, tiers)
            ranks.append(max(0, _tier_rank(model, tiers)))
    if not ranks:
        return None
    # Walk the turns oldest->newest: a turn at or above the latch holds (and resets the
    # calm counter); a turn below it counts as calm, and once `cooldown` calm turns
    # accumulate the latch steps down to that turn's tier.
    latched, calm = 0, 0
    for rank in ranks:
        if rank >= latched:
            latched, calm = rank, 0
        else:
            calm += 1
            if cooldown and calm >= cooldown:
                latched, calm = rank, 0
    return tiers[latched].model


def forward_request(
    url: str, headers: dict[str, str], json_body: dict, timeout: float = _DEFAULT_TIMEOUT
) -> tuple[int, bytes, str]:
    """POST ``json_body`` to ``url``; return ``(status, content, content_type)``.

    The synchronous forwarder used by :func:`invoke_model` (the onboarding A/B
    caller, which runs outside the async server). Isolated so tests can substitute
    it without a real upstream.
    """
    try:
        import httpx
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise GatewayUnavailable(_INSTALL_HINT) from exc
    response = httpx.post(url, headers=headers, json=json_body, timeout=timeout)
    return response.status_code, response.content, response.headers.get(
        "content-type", "application/json"
    )


async def aforward_request(
    url: str, headers: dict[str, str], json_body: dict, timeout: float = _DEFAULT_TIMEOUT
) -> tuple[int, bytes, str]:
    """Async, non-streaming forward used by the server; returns the buffered reply.

    Transport failures (timeout, connection refused) raise :class:`UpstreamError`
    so the handler can return an OpenAI-shaped error instead of a bare 500.
    Isolated so tests can substitute it without a real upstream.
    """
    try:
        import httpx
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise GatewayUnavailable(_INSTALL_HINT) from exc
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, headers=headers, json=json_body)
    except httpx.HTTPError as exc:
        raise UpstreamError(str(exc) or exc.__class__.__name__) from exc
    return response.status_code, response.content, response.headers.get(
        "content-type", "application/json"
    )


async def aforward_stream(
    url: str, headers: dict[str, str], json_body: dict, timeout: float = _DEFAULT_TIMEOUT
) -> AsyncIterator[bytes]:
    """Async generator relaying an upstream Server-Sent-Events stream chunk by chunk.

    Transport failures raise :class:`UpstreamError`, which the handler turns into a
    terminal SSE error event. Isolated so tests can substitute it.
    """
    try:
        import httpx
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise GatewayUnavailable(_INSTALL_HINT) from exc
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("POST", url, headers=headers, json=json_body) as response:
                async for chunk in response.aiter_bytes():
                    yield chunk
    except httpx.HTTPError as exc:
        raise UpstreamError(str(exc) or exc.__class__.__name__) from exc


def invoke_model(model: GatewayModel, prompt: str, timeout: float = _DEFAULT_TIMEOUT) -> str:
    """Run ``prompt`` through one upstream model and return its text (BYO key).

    The single-prompt call the onboarding harness uses to A/B a local vs hosted
    model. It forwards an OpenAI-compatible chat request with the model's key
    (read from the environment) and returns the assistant content. Reuses
    :func:`forward_request`, so tests substitute the network the same way.
    """
    headers = {"Content-Type": "application/json"}
    if model.api_key_env:
        key = os.environ.get(model.api_key_env)
        if key:
            headers["Authorization"] = f"Bearer {key}"
    body = {"model": model.model, "messages": [{"role": "user", "content": prompt}]}
    url = model.base_url.rstrip("/") + "/chat/completions"
    status, content, _ = forward_request(url, headers, body, timeout)
    if status >= 400:
        raise RuntimeError(f"{model.model} upstream returned {status}: {content[:200]!r}")
    try:
        data = json.loads(content)
        return str(data["choices"][0]["message"]["content"])
    except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"{model.model} returned an unexpected response shape: {exc}") from exc


def _resolve_timeout() -> float:
    """The upstream timeout in seconds, from ``WAYFINDER_ROUTER_TIMEOUT`` or the default."""
    raw = os.environ.get(_TIMEOUT_ENV)
    if raw:
        try:
            return float(raw)
        except ValueError:
            logger.warning("ignoring invalid %s=%r", _TIMEOUT_ENV, raw)
    return _DEFAULT_TIMEOUT


class _ConfigHolder:
    """Caches routing + gateway config, reloading when ``wayfinder-router.toml`` changes.

    Lets a recalibration (CLI, cron, or UI) take effect on the running gateway
    with no restart: each request checks the config file's mtime and re-reads only
    when it moved. A malformed mid-flight write keeps the last-good config (the
    marker advances so it is not retried every request), is logged rather than
    failing serving silently, and increments the reload-failure metric.
    """

    def __init__(
        self, start_dir: str, *, on_reload_failure: Callable[[], None] | None = None
    ) -> None:
        self.start_dir = start_dir
        self._on_reload_failure = on_reload_failure
        self._routing = load_routing_config(start_dir)
        self._gateway = load_gateway_config(start_dir)
        self._mtime = self._mtime_now()

    def _mtime_now(self) -> float | None:
        path = find_config_file(self.start_dir)
        if path is None:
            return None
        try:
            return path.stat().st_mtime
        except OSError:
            return None

    def current(self) -> tuple[RoutingConfig, GatewayConfig]:
        mtime = self._mtime_now()
        if mtime != self._mtime:
            self._mtime = mtime
            try:
                self._routing = load_routing_config(self.start_dir)
                self._gateway = load_gateway_config(self.start_dir)
            except WayfinderConfigError as exc:
                # Keep last-good config; the marker advanced so we do not thrash.
                logger.warning("config reload failed, keeping last-good config: %s", exc)
                if self._on_reload_failure is not None:
                    self._on_reload_failure()
        return self._routing, self._gateway


def build_app(
    start_dir: str = ".", *, dry_run: bool = False, timeout: float | None = None
) -> FastAPI:
    """Build the FastAPI gateway app; config hot-reloads on ``wayfinder-router.toml`` change.

    ``dry_run`` makes ``/v1/chat/completions`` return the routing decision without
    calling any upstream — try the router with no backends. ``timeout`` overrides the
    upstream timeout (else ``WAYFINDER_ROUTER_TIMEOUT`` or 60s).
    """
    try:
        from fastapi import Body, FastAPI, Header, Response
        from fastapi.responses import (
            HTMLResponse,
            JSONResponse,
            PlainTextResponse,
            StreamingResponse,
        )
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise GatewayUnavailable(_INSTALL_HINT) from exc

    from . import __version__  # local import: avoids a circular import at module load

    metrics = Metrics(__version__)
    holder = _ConfigHolder(start_dir, on_reload_failure=metrics.record_reload_failure)
    request_timeout = timeout if timeout is not None else _resolve_timeout()
    feedback_token = os.environ.get(_FEEDBACK_TOKEN_ENV)
    recent: deque[dict] = deque(maxlen=_RECENT_MAX)  # decision metadata only, no prompt text
    app = FastAPI(title="wayfinder-router-gateway")

    # Startup diagnostics: surface the misconfigurations that otherwise only show up
    # as a confusing first-request failure.
    _, gw0 = holder.current()
    metrics.set_model_costs(
        {name: model.cost_per_1k for name, model in gw0.models.items()
         if model.cost_per_1k is not None}
    )
    for name, model in gw0.models.items():
        if model.api_key_env and not os.environ.get(model.api_key_env):
            logger.warning("gateway model '%s' references unset env var %s", name, model.api_key_env)
    if not gw0.models and not dry_run:
        logger.warning(
            "no [gateway.models] configured; requests will fail until you add an endpoint "
            "(or run with --dry-run to see routing decisions without backends)"
        )
    if feedback_token is None:
        logger.info(
            "/v1/feedback is unauthenticated; set %s to require a bearer token", _FEEDBACK_TOKEN_ENV
        )

    def _missing_keys(gw: GatewayConfig) -> list[str]:
        return sorted(
            name
            for name, model in gw.models.items()
            if model.api_key_env and not os.environ.get(model.api_key_env)
        )

    @app.get("/healthz")
    def healthz() -> dict:
        _, gw = holder.current()
        missing = _missing_keys(gw)
        body: dict = {"status": "degraded" if missing else "ok", "models": sorted(gw.models)}
        if missing:
            body["missing_keys"] = missing
        return body

    @app.get("/metrics", response_class=PlainTextResponse)
    def metrics_endpoint() -> Response:
        """Prometheus text exposition of routing metrics (WF-ADR-0018).

        Metadata only (model / mode labels), never prompt text; a pure read of
        in-memory counters, off the scored path — no key, no model call, no network.
        """
        return PlainTextResponse(
            metrics.render(), media_type="text/plain; version=0.0.4; charset=utf-8"
        )

    @app.get("/v1/models")
    def list_models() -> dict:
        """Advertise the selectable routing options as an OpenAI-compatible list.

        Pure and offline like ``/healthz``: reads the current config only — no key,
        no model call, no network — so any OpenAI client auto-populates its model
        dropdown with the routing directives and configured endpoints instead of a
        hand-written list (WF-ADR-0012). ``prefer-*`` appears only for a
        tiered/binary router; a classifier has no ordered ladder to lean on.
        """
        routing, gw = holder.current()
        ids = [_AUTO]
        if routing.classifier is None and routing.tiers:
            ids += [_PREFER_LOW, _PREFER_HIGH]
        ids += list(gw.models)
        return {
            "object": "list",
            "data": [
                {"id": mid, "object": "model", "created": 0, "owned_by": "wayfinder"}
                for mid in ids
            ],
        }

    @app.get("/router/recent")
    def router_recent(limit: int = 50) -> dict:
        """Read-only view of recent routing decisions (WF-ADR-0014).

        Metadata only — model, score, mode, request id, timestamp — never prompt
        text. The visibility half of the control surface: see *that* routing is
        happening and where, without inspecting per-request headers. Pure and offline.
        """
        items = list(recent)
        by_model: dict[str, int] = {}
        for entry in items:
            by_model[entry["model"]] = by_model.get(entry["model"], 0) + 1
        clamped = max(1, min(limit, _RECENT_MAX))
        return {"total": len(items), "by_model": by_model, "recent": items[-clamped:][::-1]}

    @app.get("/router", response_class=HTMLResponse)
    def router_dashboard() -> str:
        """A tiny self-contained dashboard that polls /router/recent."""
        return _DASHBOARD_HTML

    @app.get("/demo", response_class=HTMLResponse)
    def demo_page() -> str:
        """The decision-first chat demo (WF-ADR-0020): shows the routing decision,
        the score and why, and the cost saved, with a live threshold slider. Pairs
        with ``--dry-run`` for a keyless demo. Self-contained; no build, no CDN."""
        return _DEMO_HTML

    @app.get("/router/profiles")
    def lexicon_profiles() -> dict:
        """Stock lexicon profiles (WF-ADR-0024) the demo can load to seed the term lists.
        Static, read-only metadata — no model call, no prompt text."""
        return {"profiles": [p.to_dict() for p in PROFILES]}

    @app.get("/router/models")
    def router_models() -> dict:
        """Read-only view of the configured endpoints and whether each model's key is
        present (WF-ADR-0025). Returns only the env-var *name* and a boolean — never a
        secret. Keys live in the environment: set the named var and restart."""
        _, gw = holder.current()
        models = [
            {
                "name": name,
                "endpoint": m.base_url,
                "model": m.model,
                "api_key_env": m.api_key_env,
                "key_ok": m.api_key_env is None or bool(os.environ.get(m.api_key_env)),
            }
            for name, m in gw.models.items()
        ]
        return {"models": models, "dry_run": dry_run}

    @app.post("/router/config", response_class=PlainTextResponse)
    def export_config(body: dict = Body(default={})) -> Response:  # noqa: B008 - FastAPI default
        """Render the configured router as `[routing]` TOML, with any `wayfinder_tuning`
        body applied (WF-ADR-0023) — the demo's "Export config" so a tuned setup becomes
        a real, paste-able config. Pure: builds and serializes a config, no model call."""
        routing, _ = holder.current()
        try:
            tuned = apply_scoring_overrides(routing, body.get(TUNING_FIELD, body or None))
        except BadOverride as exc:
            return JSONResponse(
                status_code=400,
                content={"error": {"message": str(exc), "type": "wayfinder_router_bad_override"}},
            )
        return PlainTextResponse(dump_routing_toml(tuned), media_type="text/plain; charset=utf-8")

    @app.post("/v1/feedback")
    def feedback(  # noqa: B008 - FastAPI default
        body: dict = Body(...),
        authorization: str | None = Header(default=None),
    ) -> object:
        # Steady-state escalate loop: the caller records which model was good
        # enough for a prompt; the label feeds the next recalibration. Writing the
        # label log is guarded by an optional bearer token to prevent poisoning.
        if feedback_token is not None and authorization != f"Bearer {feedback_token}":
            return JSONResponse(status_code=401, content={"error": "unauthorized"})
        raw_text, raw_label = body.get("text"), body.get("label")
        if not isinstance(raw_text, str) or not raw_text:
            return JSONResponse(status_code=400, content={"error": "missing 'text'"})
        if not isinstance(raw_label, str) or not raw_label:
            return JSONResponse(status_code=400, content={"error": "missing 'label'"})
        record_label(str(Path(start_dir) / DEFAULT_LOG), raw_text, raw_label)
        return {"ok": True}

    @app.post("/v1/chat/completions")
    async def chat_completions(  # noqa: B008 - FastAPI default
        body: dict = Body(...),
        x_wayfinder_threshold: str | None = Header(default=None),
        x_wayfinder_route_on: str | None = Header(default=None),
        x_wayfinder_sticky: str | None = Header(default=None),
        x_wayfinder_sticky_cooldown: str | None = Header(default=None),
        x_wayfinder_debug: str | None = Header(default=None),
    ) -> Response:
        request_id = uuid.uuid4().hex[:12]
        routing, gw = holder.current()

        def _reject(exc: BadOverride) -> JSONResponse:
            logger.info("request %s rejected: %s", request_id, exc)
            return JSONResponse(
                status_code=400,
                content={"error": {"message": str(exc), "type": "wayfinder_router_bad_override"}},
                headers={"x-wayfinder-router-request-id": request_id},
            )

        # Resolve scope/latch overrides + any per-request scoring tuning before scoring.
        # The tuning field is popped so it is never forwarded to the upstream model.
        tuning = body.pop(TUNING_FIELD, None)
        try:
            route_on = parse_route_on_header(x_wayfinder_route_on) or gw.route_on
            sticky = resolve_sticky(x_wayfinder_sticky, gw.sticky)
            cooldown = resolve_sticky_cooldown(x_wayfinder_sticky_cooldown, gw.sticky_cooldown)
            routing = apply_scoring_overrides(routing, tuning)
        except BadOverride as exc:
            return _reject(exc)

        # Score once (always reported); a per-request override only changes which
        # endpoint the score routes to, never how it is computed (WF-ADR-0011). The
        # scoring time is the decision-latency metric (WF-ADR-0018).
        score_started = time.perf_counter()
        messages = body.get("messages")
        decision = score_complexity(extract_prompt(messages, route_on=route_on), config=routing)
        decision_seconds = time.perf_counter() - score_started

        pin = resolve_pin(body.get("model"), routing, gw)
        if pin is not None:
            chosen, mode = pin, "pinned"
        else:
            try:
                threshold = parse_threshold_header(x_wayfinder_threshold)
                if threshold is not None:
                    effective_tiers = threshold_tiers(routing, threshold)
                    chosen, mode = recommend_tier(decision.score, effective_tiers), "threshold-override"
                else:
                    effective_tiers = routing.tiers
                    chosen, mode = decision.recommendation, "scored"
                # Conversation latch (WF-ADR-0022): escalate to the highest tier any single
                # turn in this chat has needed, so a hard conversation stays on the big model.
                if sticky and routing.classifier is None and len(effective_tiers) >= 2:
                    latched = conversation_high_water(
                        messages, routing, effective_tiers, cooldown=cooldown
                    )
                    if latched is not None and _tier_rank(latched, effective_tiers) > _tier_rank(
                        chosen, effective_tiers
                    ):
                        chosen, mode = latched, "sticky"
            except BadOverride as exc:
                return _reject(exc)

        wf_headers = {
            "x-wayfinder-router-model": chosen,
            "x-wayfinder-router-score": f"{decision.score:.2f}",
            "x-wayfinder-router-mode": mode,
            "x-wayfinder-router-request-id": request_id,
        }
        logger.info(
            "request %s -> %s (score %.2f, mode %s)", request_id, chosen, decision.score, mode
        )
        recent.append(
            {
                "request_id": request_id,
                "model": chosen,
                "score": round(decision.score, 2),
                "mode": mode,
                "ts": time.time(),
            }
        )
        metrics.observe_decision(chosen, mode, decision_seconds)
        # Opt-in: surface the decision in the response so a client can show it
        # (default stays byte-clean for strict clients). The headers always carry it.
        debug = (x_wayfinder_debug or "").strip().lower() in ("1", "true", "yes")

        # The decision payload for the demo UI / debug clients. Built ONLY here and in the
        # debug/dry-run branches below — explain_score never runs on the scored relay path
        # (WF-ADR-0001/0020), so the default response stays byte-clean.
        def _cost_block() -> dict:
            wc = int(decision.features.get("word_count", 0))
            costs: dict[str, float] = {
                name: model.cost_per_1k
                for name, model in gw.models.items()
                if model.cost_per_1k is not None
            }
            if decision.tiers:
                for tier in decision.tiers:
                    if tier.cost is not None:
                        costs.setdefault(tier.model, tier.cost)
            estimated = not costs
            if estimated:
                # No cost metadata configured (e.g. a keyless dry-run demo): fall back to
                # the benchmark's relative units across the tier ladder (cheapest 0.2 ..
                # dearest 1.0) so the saved-vs-cloud story still renders. Clearly flagged.
                ladder = [t.model for t in (decision.tiers or ())] or [chosen]
                lo, hi = 0.2, 1.0
                step = (hi - lo) / max(1, len(ladder) - 1)
                costs = {m: round(lo + i * step, 3) for i, m in enumerate(ladder)}
            scale = wc / 1000.0
            chosen_per1k = costs.get(chosen, max(costs.values()))
            baseline_per1k = max(costs.values())  # always-route-to-the-dearest = "always-cloud"
            per_call = round(chosen_per1k * scale, 6)
            baseline = round(baseline_per1k * scale, 6)
            return {
                "per_call": per_call,
                "baseline": baseline,
                "saved": round(baseline - per_call, 6),
                "unit": "relative units / 1k words" if estimated else "$ / 1k words",
                "estimated": estimated,
                "word_count": wc,
            }

        def _explain_payload() -> dict:
            return {
                "model": chosen,
                "score": round(decision.score, 2),
                "mode": mode,
                "request_id": request_id,
                "features": dict(decision.features),
                "contributions": [fc.to_dict() for fc in explain_score(decision.features, routing.weights)],
                "tiers": (
                    [
                        {"min_score": t.min_score, "model": t.model}
                        | ({"cost": t.cost} if t.cost is not None else {})
                        for t in decision.tiers
                    ]
                    if decision.tiers
                    else None
                ),
                "cost": _cost_block(),
            }

        if dry_run:
            return JSONResponse(
                status_code=200,
                content={"wayfinder": {**_explain_payload(), "dry_run": True}},
                headers=wf_headers,
            )

        target = gw.models.get(chosen)
        if target is None:
            logger.error("request %s: no endpoint configured for model '%s'", request_id, chosen)
            return JSONResponse(
                status_code=500,
                content={
                    "error": {
                        "message": f"no gateway endpoint configured for model '{chosen}'",
                        "type": "wayfinder_router_misconfigured",
                    }
                },
                headers=wf_headers,
            )
        headers = {"Content-Type": "application/json"}
        if target.api_key_env:
            key = os.environ.get(target.api_key_env)
            if key:
                headers["Authorization"] = f"Bearer {key}"
        forward_body = {**body, "model": target.model}
        url = target.base_url.rstrip("/") + "/chat/completions"

        if body.get("stream") is True:

            async def sse() -> AsyncIterator[bytes]:
                upstream_started = time.perf_counter()
                try:
                    async for chunk in aforward_stream(url, headers, forward_body, request_timeout):
                        yield chunk
                    metrics.observe_upstream(chosen, time.perf_counter() - upstream_started)
                    if debug:
                        meta = json.dumps(_explain_payload())
                        yield f"event: wayfinder\ndata: {meta}\n\n".encode()
                except UpstreamError as exc:
                    metrics.observe_upstream_error(chosen)
                    logger.warning("request %s upstream stream error: %s", request_id, exc)
                    err = json.dumps(
                        {"error": {"message": str(exc), "type": "wayfinder_router_upstream_error"}}
                    )
                    yield f"data: {err}\n\n".encode()
                    yield b"data: [DONE]\n\n"

            return StreamingResponse(sse(), media_type="text/event-stream", headers=wf_headers)

        upstream_started = time.perf_counter()
        try:
            status, content, content_type = await aforward_request(
                url, headers, forward_body, request_timeout
            )
        except UpstreamError as exc:
            metrics.observe_upstream_error(chosen)
            logger.warning("request %s upstream error: %s", request_id, exc)
            return JSONResponse(
                status_code=502,
                content={"error": {"message": str(exc), "type": "wayfinder_router_upstream_error"}},
                headers=wf_headers,
            )
        metrics.observe_upstream(chosen, time.perf_counter() - upstream_started)
        if debug and content and "json" in content_type:
            try:
                data = json.loads(content)
            except json.JSONDecodeError:
                data = None
            if isinstance(data, dict):
                data["wayfinder"] = _explain_payload()
                content = json.dumps(data).encode()
        return Response(
            content=content, status_code=status, media_type=content_type, headers=wf_headers
        )

    return app


def run(  # pragma: no cover
    start_dir: str = ".",
    host: str = "127.0.0.1",
    port: int = 8088,
    *,
    dry_run: bool = False,
    timeout: float | None = None,
) -> None:
    """Serve the gateway with uvicorn (the `wayfinder-router serve` command)."""
    try:
        import uvicorn
    except ImportError as exc:
        raise GatewayUnavailable(_INSTALL_HINT) from exc
    uvicorn.run(build_app(start_dir, dry_run=dry_run, timeout=timeout), host=host, port=port)
