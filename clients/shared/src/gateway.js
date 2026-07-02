// The thin-client contract, mirrored from wayfinder_router/chat_core.decision_from_debug +
// tui.remote_reply: POST /v1/chat/completions with X-Wayfinder-Debug:1; the server returns the
// routing DECISION (and a reply, when a model is configured). The client never scores.
export function decisionFromDebug(w) {
  const tiers = (w.tiers || []).slice().sort((a, b) => a.min_score - b.min_score);
  const score = w.score;
  let natIdx = 0;
  tiers.forEach((t, i) => { if (score >= t.min_score) natIdx = i; });
  const targets = tiers.map((t) => t.model);
  // Render where the server ACTUALLY routed (w.model honours a threshold/pin override),
  // falling back to the natural structural route when w.model is absent.
  const model = w.model || (tiers.length ? tiers[natIdx].model : '?');
  return {
    model, score, mode: w.mode || '',
    isLocal: targets.length ? model === targets[0] : true,
    contributions: (w.contributions || []).map((c) => ({ name: c.name, value: c.value, share: c.normalized })),
    targets,
    // WF-ADR-0042: the gateway can return a decision with no reply — a live gateway with no
    // models configured (decision_only) or an explicit dry run. The UI renders the decision and
    // a "connect a model" nudge rather than a reply.
    decisionOnly: !!w.decision_only,
    dryRun: !!w.dry_run,
  };
}
export async function routeTurn(messages, { baseUrl = 'http://127.0.0.1:8088', model = 'auto', threshold = null, signal = null } = {}) {
  const res = await fetch(`${baseUrl}/v1/chat/completions`, {
    method: 'POST',
    signal,
    headers: { 'Content-Type': 'application/json', 'X-Wayfinder-Debug': '1',
      ...(threshold != null ? { 'X-Wayfinder-Threshold': String(threshold) } : {}) },
    body: JSON.stringify({ model, messages, stream: false }),
  });
  const data = await res.json();
  return {
    decision: data.wayfinder ? decisionFromDebug(data.wayfinder) : null,
    reply: (data.choices && data.choices[0] && data.choices[0].message
      && data.choices[0].message.content) || null,
    requestId: res.headers.get('x-wayfinder-router-request-id'),
  };
}

// /router/models lists tiers cheapest-first, so models[0] is the local tier — used to colour
// the route glyph from the response headers before the full decision event arrives.
export async function cheapestModel(baseUrl = 'http://127.0.0.1:8088') {
  try {
    const d = await (await fetch(`${baseUrl}/router/models`)).json();
    return (d.models && d.models[0] && d.models[0].name) || null;
  } catch { return null; }
}

export function decisionFromHeaders(headers, cheapest) {
  const model = headers.get('x-wayfinder-router-model') || '?';
  return {
    model, score: parseFloat(headers.get('x-wayfinder-router-score') || '0'),
    mode: headers.get('x-wayfinder-router-mode') || '',
    isLocal: cheapest ? model === cheapest : true,
    contributions: [], targets: cheapest ? [cheapest] : [],
  };
}

// Streamed reply (WF-ADR-0013): the route/score arrive in headers immediately (onDecision), the
// upstream's OpenAI delta chunks stream token-by-token (onToken), and the gateway's trailing
// `event: wayfinder` enriches the decision with the full "why" (onDecision again). Returns the
// final reply text. The client still never scores — the gateway does.
export async function routeTurnStream(messages, opts = {}) {
  const {baseUrl = 'http://127.0.0.1:8088', model = 'auto', threshold = null,
    cheapest = null, onDecision, onToken, signal = null} = opts;
  const res = await fetch(`${baseUrl}/v1/chat/completions`, {
    method: 'POST',
    signal,
    headers: {'Content-Type': 'application/json', 'X-Wayfinder-Debug': '1',
      ...(threshold != null ? {'X-Wayfinder-Threshold': String(threshold)} : {})},
    body: JSON.stringify({model, messages, stream: true}),
  });
  if (onDecision) onDecision(decisionFromHeaders(res.headers, cheapest));
  let reply = '';
  const td = new TextDecoder();
  let buf = '';
  for await (const chunk of res.body) {
    buf += td.decode(chunk, {stream: true});
    let i;
    while ((i = buf.indexOf('\n\n')) >= 0) {
      const block = buf.slice(0, i); buf = buf.slice(i + 2);
      const dataLine = block.split('\n').find((l) => l.startsWith('data:'));
      if (!dataLine) continue;
      const payload = dataLine.slice(5).trim();
      if (payload === '[DONE]') continue;
      let obj; try { obj = JSON.parse(payload); } catch { continue; }
      if (/^event:\s*wayfinder/m.test(block)) {           // trailing full decision
        if (onDecision) onDecision(decisionFromDebug(obj));
        continue;
      }
      const delta = obj.choices && obj.choices[0] && obj.choices[0].delta
        && obj.choices[0].delta.content;
      if (delta) { reply += delta; if (onToken) onToken(delta, reply); }
    }
  }
  return reply;
}
