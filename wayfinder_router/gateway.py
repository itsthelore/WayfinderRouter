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
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from .complexity import RoutingConfig, Tier, explain_score, recommend_tier, score_complexity
from .config import WayfinderConfigError, find_config_file, load_routing_config
from .feedback import DEFAULT_LOG, record_label

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
  font-size:15px;line-height:1.55;display:flex;flex-direction:column;height:100vh;
  -webkit-font-smoothing:antialiased;-moz-osx-font-smoothing:grayscale}
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
.controls{display:flex;align-items:center;gap:.7rem;padding:.5rem 1.1rem .65rem;
  font-size:.8rem;color:var(--muted);flex-wrap:wrap}
.controls input[type=range]{flex:1;min-width:140px;max-width:300px;accent-color:var(--accent);height:4px}
.controls input[type=range]:disabled{opacity:.4;cursor:not-allowed}
.controls output{font-variant-numeric:tabular-nums;color:var(--muted);min-width:3em;font-weight:600}
.controls output.on{color:var(--text)}
.controls label{display:flex;align-items:center;gap:.4rem;cursor:pointer;user-select:none}
.controls label input{accent-color:var(--accent)}
.hint{font-size:.72rem;color:var(--muted);opacity:.85}
main{flex:1;overflow-y:auto;padding:1.5rem 1.1rem 2rem;scroll-behavior:smooth}
.wrap{max-width:760px;margin:0 auto;display:flex;flex-direction:column;gap:1.4rem}
.empty{margin:13vh auto 0;max-width:32rem;text-align:center;color:var(--muted)}
.empty h2{color:var(--text);font-size:1.2rem;font-weight:650;letter-spacing:-.01em;margin:0 0 .4rem}
.empty code{font-family:var(--mono);font-size:.85em;background:var(--panel);padding:.1rem .35rem;border-radius:6px}
.egs{display:flex;flex-wrap:wrap;gap:.5rem;justify-content:center;margin-top:1.2rem}
.eg{font:inherit;font-size:.82rem;color:var(--text);background:var(--panel);border:1px solid var(--line-strong);
  border-radius:var(--pill);padding:.4rem .85rem;cursor:pointer;transition:border-color .15s,background .15s}
.eg:hover{border-color:var(--accent);background:var(--accent-weak)}
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
.composer{max-width:760px;margin:0 auto;display:flex;gap:.5rem;align-items:flex-end;
  background:var(--elev);border:1px solid var(--line-strong);border-radius:24px;
  padding:.4rem .4rem .4rem .95rem;box-shadow:var(--shadow);transition:box-shadow .15s,border-color .15s}
.composer:focus-within{border-color:color-mix(in srgb,var(--accent) 60%,var(--line-strong))}
textarea{flex:1;border:0;background:transparent;color:var(--text);font:inherit;resize:none;
  max-height:40vh;padding:.5rem 0;outline:none;line-height:1.5}
textarea:focus-visible{box-shadow:none}
textarea::placeholder{color:var(--muted)}
#send{flex:none;width:34px;height:34px;border:0;border-radius:50%;background:var(--btn);color:var(--btn-text);
  font-size:1.05rem;line-height:1;cursor:pointer;display:grid;place-items:center;transition:transform .05s,opacity .15s}
#send:active{transform:translateY(1px)}
#send:disabled{opacity:.35;cursor:default}
:focus-visible{outline:none;box-shadow:var(--ring);border-radius:10px}
@media(prefers-reduced-motion:reduce){*{transition:none!important;animation:none!important;scroll-behavior:auto!important}}
</style></head><body>
<div class="bar">
  <span class="brand">Wayfinder<span class="dot">.</span></span>
  <span class="mode" id="mode">ready</span>
  <span class="saved" id="saved"></span>
</div>
<div class="controls">
  <label><input type="checkbox" id="useT"> threshold</label>
  <input type="range" id="t" min="0" max="100" value="50" disabled>
  <output id="tv">config</output>
  <span class="hint">applies to your next message</span>
</div>
<main><div class="wrap" id="wrap">
  <div class="empty" id="empty"><h2>Ask anything</h2>
  <div>Every reply shows where it routed; open the <b>?</b> for the score, the features behind it, and the cost saved. Run the gateway with <code>--dry-run</code> for a keyless demo.</div>
  <div class="egs">
    <button class="eg" data-eg="trivial">What's 2 + 2?</button>
    <button class="eg" data-eg="plan">A structured migration plan</button>
  </div></div>
</div></main>
<form id="composer"><div class="composer">
  <textarea id="in" rows="1" placeholder="Message Wayfinder..." autofocus></textarea>
  <button id="send" type="submit" aria-label="Send" title="Send">&#8593;</button>
</div></form>
<script>
const wrap=document.getElementById('wrap'),empty=document.getElementById('empty');
const inEl=document.getElementById('in'),sendBtn=document.getElementById('send');
const useT=document.getElementById('useT'),tEl=document.getElementById('t'),tv=document.getElementById('tv');
const modeEl=document.getElementById('mode'),savedEl=document.getElementById('saved');
const messages=[]; let savedTotal=0, savedUnit='', pretty=s=>s.replace(/_/g,' ');

function syncT(){const on=useT.checked; tEl.disabled=!on; tv.textContent=on?(tEl.value/100).toFixed(2):'config'; tv.classList.toggle('on',on);}
useT.addEventListener('change',syncT); tEl.addEventListener('input',syncT); syncT();

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
  r.appendChild(el('meta','score '+Number(wf.score).toFixed(2)+(wf.mode!=='scored'?' · '+wf.mode:'')));
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
  if(wf.cost&&typeof wf.cost.saved==='number'){savedTotal+=wf.cost.saved;savedUnit=wf.cost.estimated?'units':'$';
    savedEl.innerHTML='Saved <b>'+savedTotal.toFixed(3)+'</b> '+savedUnit+' vs always-cloud';}
  return r;
}

async function send(text){
  empty.style.display='none';
  const t=turn(); t.appendChild(el('msg user',text)); scroll();
  messages.push({role:'user',content:text});
  sendBtn.disabled=true;
  const headers={'Content-Type':'application/json','X-Wayfinder-Debug':'true'};
  if(useT.checked) headers['X-Wayfinder-Threshold']=(tEl.value/100).toFixed(2);
  try{
    const res=await fetch('/v1/chat/completions',{method:'POST',headers,
      body:JSON.stringify({model:'auto',messages,stream:false})});
    const data=await res.json().catch(()=>({}));
    const wf=data.wayfinder||null;
    if(wf) modeEl.textContent=wf.dry_run?'dry-run':'live';
    const content=data&&data.choices&&data.choices[0]&&data.choices[0].message&&data.choices[0].message.content;
    const ans=el('answer');
    if(content){ans.appendChild(el('msg bot',content));messages.push({role:'assistant',content});}
    else if(data&&data.error){ans.appendChild(el('msg note',(data.error.message||'error')));}
    else if(wf&&wf.dry_run){ans.appendChild(el('msg bot dry',
      'Routed to the '+wf.model+' model — no model was called in --dry-run mode. Configure a model (or drop --dry-run) to see the reply.'));}
    else{ans.appendChild(el('msg note','No content returned.'));}
    if(wf) ans.appendChild(routing(wf));
    t.appendChild(ans);
  }catch(e){t.appendChild(el('msg note','Gateway unreachable: '+e.message));}
  sendBtn.disabled=false; scroll(); inEl.focus();
}
composer.addEventListener('submit',e=>{e.preventDefault();const v=inEl.value.trim();if(!v)return;
  inEl.value='';inEl.style.height='auto';send(v);});
const EGS={
  trivial:"What's 2 + 2?",
  plan:"# Migration plan\\n\\nWrite a zero-downtime plan to migrate our Postgres database to a new region.\\n\\n## Requirements\\n\\n- enumerate prerequisites and risks\\n- detail the cutover sequence\\n- provide rollback steps\\n- estimate the maintenance window\\n\\n```sql\\nSELECT pg_create_logical_replication_slot('mig','pgoutput');\\n```\\n\\n| phase | risk |\\n| --- | --- |\\n| dual-write | medium |\\n| cutover | high |"
};
document.querySelectorAll('.eg').forEach(b=>b.addEventListener('click',()=>send(EGS[b.dataset.eg]||b.dataset.eg)));
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
    (WF-ADR-0021); see :func:`extract_prompt`. Default ``"turn"``.
    """

    models: dict[str, GatewayModel] = field(default_factory=dict)
    route_on: str = "turn"


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
    return GatewayConfig(models=models, route_on=route_on)


def dump_gateway_toml(gateway: GatewayConfig) -> str:
    """Serialize a :class:`GatewayConfig` back to ``[gateway.models.*]`` TOML.

    Used by recalibration to preserve the endpoint mapping when it rewrites the
    routing section. Emits ``api_key_env`` (the env-var *name*) — never a secret.
    """
    blocks: list[str] = []
    if gateway.route_on != "turn":
        blocks.append(f'[gateway]\nroute_on = "{gateway.route_on}"')
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
        x_wayfinder_debug: str | None = Header(default=None),
    ) -> Response:
        request_id = uuid.uuid4().hex[:12]
        routing, gw = holder.current()
        # Score once (always reported); a per-request override only changes which
        # endpoint the score routes to, never how it is computed (WF-ADR-0011). The
        # scoring time is the decision-latency metric (WF-ADR-0018).
        score_started = time.perf_counter()
        decision = score_complexity(
            extract_prompt(body.get("messages"), route_on=gw.route_on), config=routing
        )
        decision_seconds = time.perf_counter() - score_started

        pin = resolve_pin(body.get("model"), routing, gw)
        if pin is not None:
            chosen, mode = pin, "pinned"
        else:
            try:
                threshold = parse_threshold_header(x_wayfinder_threshold)
                if threshold is not None:
                    chosen = recommend_tier(decision.score, threshold_tiers(routing, threshold))
                    mode = "threshold-override"
                else:
                    chosen, mode = decision.recommendation, "scored"
            except BadOverride as exc:
                logger.info("request %s rejected: %s", request_id, exc)
                return JSONResponse(
                    status_code=400,
                    content={"error": {"message": str(exc), "type": "wayfinder_router_bad_override"}},
                    headers={"x-wayfinder-router-request-id": request_id},
                )

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
