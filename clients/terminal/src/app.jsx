// the assembled interactive Ink TUI. One running app that is a thin client of the
// gateway (Strategy D): it routes prompts, streams replies, and switches to the reproduced
// panels (/cost /models /keys /settings /threads /help) on slash commands — pulling live data
// from the gateway. Reuses ./gateway.js for the wire contract. Scrollback skipped.
import React, {useState, useEffect, useRef, useCallback} from 'react';
import {Box, Text, useInput, useApp} from 'ink';
import {dark as T} from './theme.js';
import {routeTurnStream, cheapestModel} from './gateway.js';
import {writeFileSync} from 'node:fs';

// ---------- helpers ----------
const vis = (segs) => segs.reduce((n, s) => n + [...s.t].length, 0);
const M = (t, dim = false) => ({t, c: T.muted, dim});
const A = (t) => ({t, c: T.accent});
const X = (t) => ({t, c: T.text});
const C = (t) => ({t, c: T.cloud});
const W = (t) => ({t, c: T.warn});
const gnum = (w) => Number(w).toString();
const fnum = (n) => '~$' + Number(n || 0).toFixed(4);

const SLASH = ['/init', '/models', '/keys', '/cost', '/new', '/threads', '/open', '/route', '/auto',
  '/local', '/cloud', '/threshold', '/scope', '/sticky', '/why', '/stream', '/theme', '/settings',
  '/help', '/quit'];

// /init presets — scaffold a starter wayfinder-router.toml (mirrors `wayfinder-router init`).
const PRESETS = {
  hybrid: {key: 'ANTHROPIC_API_KEY', models: [['local', 'llama3.2 (Ollama)', 'keyless'], ['cloud', 'claude-sonnet-4-6', 'ANTHROPIC_API_KEY']],
    toml: '[routing]\nthreshold = 0.5\n\n[gateway.models.local]\nbase_url = "http://localhost:11434/v1"\nmodel = "llama3.2"\n\n[gateway.models.cloud]\nbase_url = "https://api.anthropic.com/v1"\nmodel = "claude-sonnet-4-6"\napi_key_env = "ANTHROPIC_API_KEY"\n'},
  openai: {key: 'OPENAI_API_KEY', models: [['local', 'gpt-4o-mini', 'OPENAI_API_KEY'], ['cloud', 'gpt-4o', 'OPENAI_API_KEY']],
    toml: '[routing]\nthreshold = 0.5\n\n[gateway.models.local]\nbase_url = "https://api.openai.com/v1"\nmodel = "gpt-4o-mini"\napi_key_env = "OPENAI_API_KEY"\n\n[gateway.models.cloud]\nbase_url = "https://api.openai.com/v1"\nmodel = "gpt-4o"\napi_key_env = "OPENAI_API_KEY"\n'},
  gemini: {key: 'GEMINI_API_KEY', models: [['local', 'gemini-2.5-flash', 'GEMINI_API_KEY'], ['cloud', 'gemini-2.5-pro', 'GEMINI_API_KEY']],
    toml: '[routing]\nthreshold = 0.5\n\n[gateway.models.local]\nbase_url = "https://generativelanguage.googleapis.com/v1beta/openai"\nmodel = "gemini-2.5-flash"\napi_key_env = "GEMINI_API_KEY"\n\n[gateway.models.cloud]\nbase_url = "https://generativelanguage.googleapis.com/v1beta/openai"\nmodel = "gemini-2.5-pro"\napi_key_env = "GEMINI_API_KEY"\n'},
};

function parseCommand(line) {
  if (!line.startsWith('/')) return [null, line];
  const rest = line.slice(1).trim();
  if (!rest) return ['', ''];
  const sp = rest.indexOf(' ');
  return sp < 0 ? [rest.toLowerCase(), ''] : [rest.slice(0, sp).toLowerCase(), rest.slice(sp + 1).trim()];
}

async function getJSON(baseUrl, path) {
  try { return await (await fetch(baseUrl + path)).json(); } catch { return null; }
}

const WORDMARK = [
  '██╗    ██╗ █████╗ ██╗   ██╗███████╗██╗███╗   ██╗██████╗ ███████╗██████╗ ',
  '██║    ██║██╔══██╗╚██╗ ██╔╝██╔════╝██║████╗  ██║██╔══██╗██╔════╝██╔══██╗',
  '██║ █╗ ██║███████║ ╚████╔╝ █████╗  ██║██╔██╗ ██║██║  ██║█████╗  ██████╔╝',
  '██║███╗██║██╔══██║  ╚██╔╝  ██╔══╝  ██║██║╚██╗██║██║  ██║██╔══╝  ██╔══██╗',
  '╚███╔███╔╝██║  ██║   ██║   ██║     ██║██║ ╚████║██████╔╝███████╗██║  ██║',
  ' ╚══╝╚══╝ ╚═╝  ╚═╝   ╚═╝   ╚═╝     ╚═╝╚═╝  ╚═══╝╚═════╝ ╚══════╝╚═╝  ╚═╝',
].join('\n');

// ---------- reusable views ----------
function Panel({title, lines}) {
  const PAD = 2;
  const contentW = Math.max(title.length + 2, ...lines.map(vis), 1);
  const innerW = contentW + PAD * 2;
  const blank = (k) => <Text key={k}><Text color={T.accent}>│</Text>{' '.repeat(innerW)}<Text color={T.accent}>│</Text></Text>;
  return (
    <Box flexDirection="column" marginBottom={1}>
      <Text color={T.accent}>{'╭─ ' + title + ' ' + '─'.repeat(Math.max(0, innerW - title.length - 3)) + '╮'}</Text>
      {blank('t')}
      {lines.map((segs, i) => (
        <Text key={i}><Text color={T.accent}>│</Text>{'  '}
          {segs.map((s, j) => <Text key={j} color={s.c} dimColor={s.dim} bold={s.b}>{s.t}</Text>)}
          {' '.repeat(Math.max(0, contentW - vis(segs)) + PAD)}<Text color={T.accent}>│</Text></Text>
      ))}
      {blank('b')}
      <Text color={T.accent}>{'╰' + '─'.repeat(innerW) + '╯'}</Text>
    </Box>
  );
}

function DecisionLine({d, expanded}) {
  const glyph = d.isLocal ? '●' : '◆', role = d.isLocal ? 'LOCAL' : 'CLOUD';
  const rc = d.isLocal ? T.accent : T.cloud;
  const rows = [...(d.contributions || [])].sort((a, b) => b.share - a.share).filter((r) => r.value > 0).slice(0, 5);
  const nameW = Math.max(1, ...rows.map((r) => r.name.length));
  return (
    <Box flexDirection="column">
      <Text>
        <Text color={rc} bold>{glyph} {role}</Text>
        <Text color={T.text}>  {d.model}</Text>
        <Text color={T.muted}>   score {d.score.toFixed(2)}</Text>
        {d.isLocal ? <Text color={T.muted}>  · kept local</Text> : null}
        {rows.length && expanded ? <Text color={T.muted}>   /why ⌃</Text> : (rows.length ? <Text color={T.muted}>   /why ⌄</Text> : null)}
      </Text>
      {expanded && rows.map((r, i) => (
        <Text key={i} color={T.muted}>{r.name.padEnd(nameW)}{'  '}{String(r.value).padStart(4)}{'  '}
          {('█'.repeat(Math.round(r.share * 12)).padEnd(12, '░'))}</Text>
      ))}
    </Box>
  );
}

// ---------- render_reply: a small Markdown renderer (bold, inline code, lists, headings, fences) ----------
function inlineSegs(text) {
  const out = []; let last = 0; let m;
  const re = /\*\*([^*]+)\*\*|`([^`]+)`/g;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push({t: text.slice(last, m.index)});
    if (m[1] != null) out.push({t: m[1], b: true});
    else out.push({t: m[2], c: T.cloud});
    last = re.lastIndex;
  }
  if (last < text.length) out.push({t: text.slice(last)});
  return out.length ? out : [{t: text}];
}
function Markdown({text}) {
  const lines = (text || '').split('\n');
  const out = []; let inFence = false; let buf = [];
  const flush = (key) => {
    if (!buf.length) { return; }
    out.push(<Box key={'c' + key} borderStyle="round" borderColor={T.line} paddingX={1} flexDirection="column" alignSelf="flex-start">
      {buf.map((l, i) => <Text key={i} color={T.text}>{l || ' '}</Text>)}</Box>);
    buf = [];
  };
  lines.forEach((ln, idx) => {
    if (/^\s*```/.test(ln)) { if (inFence) { flush(idx); inFence = false; } else { inFence = true; } return; }
    if (inFence) { buf.push(ln); return; }
    if (/^#{1,6}\s/.test(ln)) { out.push(<Text key={idx} color={T.text} bold>{ln.replace(/^#{1,6}\s/, '')}</Text>); return; }
    const b = ln.match(/^(\s*)[-*]\s+(.*)/);
    const body = b ? b[2] : ln;
    const segs = inlineSegs(body);
    out.push(<Text key={idx} color={T.text}>{b ? b[1] + '• ' : ''}{segs.map((s, i) => <Text key={i} color={s.c || T.text} bold={s.b}>{s.t}</Text>)}</Text>);
  });
  if (inFence) flush('end');
  return <Box flexDirection="column">{out}</Box>;
}

// ---------- data → panel lines ----------
function modelsLines(m) {
  if (!m || !m.models || !m.models.length) return [[M('no models configured — type /init to scaffold one')]];
  const rows = m.models.map((x) => {
    const key = x.api_key_env == null ? A('● keyless ✓')
      : x.key_ok ? A('● ' + x.api_key_env + ' ✓ set')
        : {t: '● ' + x.api_key_env + ' ✗ not set', c: T.warn};
    return [X((x.name + '      ').slice(0, 7)), M((x.model + '                 ').slice(0, 18)), M('  '),
      M((x.endpoint.replace(/^https?:\/\//, '') + '                      ').slice(0, 22)), key];
  });
  return [...rows, [], [M('keys live in your environment · /init to add models · /route to pin')]];
}
function keysLines(m) {
  if (!m || !m.models || !m.models.length) return [[M('no models configured — type /init to scaffold one')]];
  const out = []; const missing = [];
  for (const x of m.models) {
    if (x.api_key_env == null) out.push([X((x.name + '      ').slice(0, 7)), A('● '), M('keyless — no key needed')]);
    else if (x.key_ok) out.push([X((x.name + '      ').slice(0, 7)), A('● '), A(x.api_key_env + '  ✓ set in environment')]);
    else { out.push([X((x.name + '      ').slice(0, 7)), {t: '● ', c: T.cloud}, W(x.api_key_env + '  ✗ not set')]); missing.push(x.api_key_env); }
  }
  if (missing.length) {
    out.push([], [M('to fix — read at request time, never written to disk:')]);
    for (const v of [...new Set(missing)]) out.push([X('  export ' + v + '=…')]);
  }
  out.push([], [M('/keys re-checks · keys live in your environment or your secret store')]);
  return out;
}
function costLines(savings, sess) {
  const pct = sess.calls ? Math.round(100 * sess.local / sess.calls) : 0;
  const lines = [[M('this session')], [M('model calls'), M('   '), X(String(sess.calls))],
    [M(' kept local'), M('   '), X(`${sess.local}  (${pct}%)`)]];
  if (sess.priced) lines.push([M(' est. saved'), M('   '), X(fnum(sess.saved) + '  vs always-cloud')]);
  const saved = savings && savings.saved != null ? savings.saved : null;
  lines.push([], [M('all time'), M('   '), saved == null ? M('—  (set cost_per_1k for $ figures)') : A(fnum(saved))]);
  lines.push([], [M('estimated from ~4 chars/token')]);
  return lines;
}
function settingsLines(s) {
  const row = (k, v) => [M(k.padStart(13)), M('   '), X(v)];
  return [
    row('forced route', s.pinned || 'auto (routing)'),
    row('threshold', s.threshold != null ? s.threshold.toFixed(2) : 'auto (config)'),
    row('routing scope', s.scope),
    row('sticky', s.sticky ? `on · cooldown ${s.cooldown}` : 'off'),
    row('why breakdown', s.show_why ? 'expanded' : 'collapsed'),
    row('streaming', s.stream ? 'on' : 'off'),
    row('theme', s.theme),
    [],
    [M('change:  /route /local /cloud /threshold /scope /sticky /why /stream /theme · /help')],
  ];
}
function threadsLines(threads) {
  if (!threads.length) return [[M('no saved conversations yet — they save automatically as you chat')]];
  const rows = threads.map((t, i) => [A(String(i + 1)), M('   '), X((t.title || '(untitled)').padEnd(26)), M('   '), M(t.when)]);
  return [...rows, [], [M('/open <n> to reopen · /new to start fresh')]];
}
const HELP = [
  [A('/models'), M('  configured models + key status')],
  [A('/keys'), M('    re-check keys, fix hints')],
  [A('/cost'), M('    session routing mix + savings')],
  [A('/threads'), M(' list saved conversations · /open <n>')],
  [A('/local /cloud'), M('  pin the cheapest / most-capable tier · /auto clears')],
  [A('/threshold <0..1>'), M('  set the cut · /scope · /sticky · /why · /stream · /theme')],
  [A('/settings'), M(' show settings · '), A('/help'), M(' · '), A('/quit')],
  [], [M('↑↓ history · tab expand the last why · enter routes · ^c cancel/quit')],
];

// ---------- the app ----------
export default function App({rows = 30, cols = 96, baseUrl = 'http://127.0.0.1:8088'} = {}) {
  const {exit} = useApp();
  const [items, setItems] = useState([]); // transcript: {kind, ...}
  const [input, setInput] = useState('');
  const [streaming, setStreaming] = useState(false);
  const [note, setNote] = useState(null);
  const [scroll, setScroll] = useState(0); // items hidden below the viewport (0 = stuck to bottom)
  const [st, setSt] = useState({pinned: null, threshold: null, scope: 'turn', sticky: false, cooldown: 0, show_why: false, stream: true, theme: 'dark'});
  const cheapest = useRef(null);
  const history = useRef([]); const hIdx = useRef(null);
  const threads = useRef([]);
  const patchLast = (k, patch) => setItems((m) => m.map((x, i) => (i === m.length - 1 && x.kind === k ? {...x, ...patch} : x)));
  const push = (it) => { setScroll(0); setItems((m) => [...m, it]); }; // new content sticks to bottom

  useEffect(() => { cheapestModel(baseUrl).then((c) => { cheapest.current = c; }); }, [baseUrl]);

  const route = useCallback(async (text, pin) => {
    push({kind: 'user', text});
    push({kind: 'turn', decision: null, reply: '', expanded: st.show_why});
    setStreaming(true); setNote('routing… (gateway)');
    try {
      await routeTurnStream([{role: 'user', content: text}], {
        baseUrl, model: pin || st.pinned || 'auto', threshold: st.threshold, cheapest: cheapest.current,
        onDecision: (d) => patchLast('turn', {decision: d}),
        onToken: (_t, full) => patchLast('turn', {reply: full}),
      });
    } catch (e) { patchLast('turn', {reply: `⚠ ${e.message}`}); }
    setStreaming(false); setNote(null);
  }, [baseUrl, st]);

  const run = useCallback(async (line) => {
    const [cmd, arg] = parseCommand(line);
    if (cmd === null) return route(line);
    switch (cmd) {
      case 'quit': return exit();
      case 'new': threads.current = [{title: items.find((x) => x.kind === 'user')?.text?.slice(0, 30), when: '2026-06-28 19:40'}, ...threads.current]; return setItems([]);
      case 'init': {
        const name = (arg || 'hybrid').split(/\s+/)[0] || 'hybrid';
        const p = PRESETS[name] || PRESETS.hybrid;
        let wrote = true;
        try { writeFileSync('wayfinder-router.toml', p.toml); } catch { wrote = false; }
        const lines = [
          wrote ? [A('✓ '), X('scaffolded wayfinder-router.toml'), M(`  (${name} preset)`)] : [W('✗ '), X("couldn't write wayfinder-router.toml")],
          [],
          ...p.models.map(([n, m, k]) => [X((n + '      ').slice(0, 7)), M((m + '                     ').slice(0, 22)), k === 'keyless' ? A('keyless ✓') : M(k)]),
          [],
          [M('next:  '), X(`export ${p.key}=…`), M('   then '), A('/keys'), M(' to check')],
          [M('the gateway hot-reloads this file — run '), A('serve'), M(' from this directory')],
        ];
        return push({kind: 'panel', title: 'init', lines});
      }
      case 'help': return push({kind: 'panel', title: 'help', lines: HELP});
      case 'settings': return push({kind: 'panel', title: 'settings', lines: settingsLines(st)});
      case 'threads': return push({kind: 'panel', title: 'threads', lines: threadsLines(threads.current)});
      case 'models': return push({kind: 'panel', title: 'models', lines: modelsLines(await getJSON(baseUrl, '/router/models'))});
      case 'keys': return push({kind: 'panel', title: 'keys', lines: keysLines(await getJSON(baseUrl, '/router/models'))});
      case 'cost': return push({kind: 'panel', title: 'cost', lines: costLines(await getJSON(baseUrl, '/v1/savings?period=all'), sess.current)});
      case 'why': { const v = arg === 'on' ? true : arg === 'off' ? false : !st.show_why; setSt((s) => ({...s, show_why: v})); return setNote(`why ${v ? 'expanded' : 'collapsed'}`); }
      case 'stream': { const v = arg !== 'off'; setSt((s) => ({...s, stream: v})); return setNote(`streaming ${v ? 'on' : 'off'}`); }
      case 'theme': { const v = arg || 'dark'; setSt((s) => ({...s, theme: v})); return setNote(`theme ${v}`); }
      case 'scope': { const v = arg || 'turn'; setSt((s) => ({...s, scope: v})); return setNote(`scope ${v}`); }
      case 'sticky': { const v = arg !== 'off'; setSt((s) => ({...s, sticky: v})); return setNote(`sticky ${v ? 'on' : 'off'}`); }
      case 'threshold': { const v = parseFloat(arg); if (!Number.isNaN(v)) { setSt((s) => ({...s, threshold: v})); return setNote(`threshold ${v.toFixed(2)}`); } return setNote('usage: /threshold 0.6'); }
      case 'auto': setSt((s) => ({...s, pinned: null})); return setNote('routing: auto');
      case 'local': if (arg) return route(arg, 'prefer-local'); setSt((s) => ({...s, pinned: 'prefer-local'})); return setNote('pinned → local');
      case 'cloud': if (arg) return route(arg, 'prefer-hosted'); setSt((s) => ({...s, pinned: 'prefer-hosted'})); return setNote('pinned → cloud');
      case 'route': setSt((s) => ({...s, pinned: arg || null})); return setNote(arg ? `pinned → ${arg}` : 'routing: auto');
      default: return setNote(`unknown command: /${cmd} — try /help`);
    }
  }, [route, exit, baseUrl, st, items]);

  // session cost tally (for /cost)
  const sess = useRef({calls: 0, local: 0, saved: 0, priced: false});
  useEffect(() => {
    const turns = items.filter((x) => x.kind === 'turn' && x.decision);
    sess.current = {calls: turns.length, local: turns.filter((x) => x.decision.isLocal).length, saved: 0, priced: false};
  }, [items]);

  const submit = useCallback(() => {
    if (streaming) return;
    const line = input.trim(); setInput(''); hIdx.current = null;
    if (!line) return;
    if (history.current[history.current.length - 1] !== line) history.current.push(line);
    run(line);
  }, [input, streaming, run]);

  useInput((ch, key) => {
    if (key.ctrl && ch === 'c') return exit();
    if (key.ctrl && ch === 'd') return exit();
    if (key.return) return submit();
    if (key.tab) return setItems((m) => m.map((x, i) => {
      const lastTurn = m.map((y) => y.kind).lastIndexOf('turn');
      return i === lastTurn ? {...x, expanded: !x.expanded} : x;
    }));
    if (key.pageUp) return setScroll((s) => Math.min(Math.max(0, items.length - 1), s + 2));
    if (key.pageDown) return setScroll((s) => Math.max(0, s - 2));
    if (key.upArrow) { if (!history.current.length) return; hIdx.current = hIdx.current == null ? history.current.length - 1 : Math.max(0, hIdx.current - 1); return setInput(history.current[hIdx.current]); }
    if (key.downArrow) { if (hIdx.current == null) return; hIdx.current++; if (hIdx.current >= history.current.length) { hIdx.current = null; return setInput(''); } return setInput(history.current[hIdx.current]); }
    if (key.backspace || key.delete) return setInput((s) => s.slice(0, -1));
    if (ch && !key.ctrl && !key.meta) return setInput((s) => s + ch);
  });

  // ---------- render ----------
  const matches = input.startsWith('/') && !input.includes(' ') ? SLASH.filter((c) => c.startsWith(input.toLowerCase())).slice(0, 8) : [];
  const statusLeft = note ? [C('⠿ '), M(note)]
    : st.pinned ? [W(`forced → ${st.pinned === 'prefer-local' ? 'local' : st.pinned === 'prefer-hosted' ? 'cloud' : st.pinned}`), M('  ·  /auto to resume routing')]
      : [A('decision-first routing'), M(`  ·  threshold ${st.threshold != null ? st.threshold.toFixed(2) : 'auto'}  ·  scope ${st.scope}`)];
  const statusRight = [A('● local'), M('  /  '), C('◆ cloud')];
  const sFill = Math.max(1, (cols - 4) - vis(statusLeft) - vis(statusRight));

  // scrollbar-less viewport: show a window of recent items; PgUp/PgDn scroll, new content sticks to bottom
  const VIS = 6;
  const wEnd = Math.max(VIS, items.length - scroll);
  const wStart = Math.max(0, wEnd - VIS);
  const windowItems = items.slice(wStart, wEnd);
  const moreAbove = wStart, moreBelow = items.length - wEnd;
  const renderItem = (it, i) => {
    if (it.kind === 'user') return <Text key={i}><Text color={T.accent}>› </Text><Text color={T.text}>{it.text}</Text></Text>;
    if (it.kind === 'panel') return <Panel key={i} title={it.title} lines={it.lines} />;
    if (it.kind === 'turn') return (
      <Box key={i} flexDirection="column" marginBottom={1}>
        {it.decision ? <DecisionLine d={it.decision} expanded={it.expanded} /> : <Text color={T.muted}>routing…</Text>}
        {it.reply ? <Markdown text={it.reply} /> : (it.decision ? <Text color={T.muted}>…</Text> : null)}
      </Box>
    );
    return null;
  };

  return (
    <Box flexDirection="column" width={cols} height={rows} paddingX={2}>
      <Box flexDirection="column" flexGrow={1}>
        {items.length === 0 ? (
          <Box flexDirection="column" alignItems="center" marginTop={1}>
            <Text color={T.accent}>{WORDMARK}</Text>
            <Box marginTop={1}><Text color={T.muted}>v2026.6.10 · deterministic LLM routing — local vs cloud</Text></Box>
            <Box marginTop={1}><Text color={T.text}>type a prompt — Wayfinder routes it and shows the score + why</Text></Box>
            <Box marginTop={1}><Text color={T.muted}>local </Text><Text color={T.accent}>✓</Text><Text color={T.muted}>   cloud </Text><Text color={T.cloud}>✓</Text><Text color={T.muted}>   offline routing </Text><Text color={T.accent}>✓</Text></Box>
            <Box marginTop={1}><Text color={T.muted} dimColor>/help for commands</Text></Box>
          </Box>
        ) : (
          <Box flexDirection="column">
            {moreAbove > 0 ? <Text color={T.muted} dimColor>{`  ↑ ${moreAbove} earlier  ·  PgUp/PgDn to scroll`}</Text> : null}
            {windowItems.map((it, i) => renderItem(it, wStart + i))}
            {moreBelow > 0 ? <Text color={T.muted} dimColor>{`  ↓ ${moreBelow} newer`}</Text> : null}
          </Box>
        )}
      </Box>

      {/* status bar */}
      <Text>{statusLeft.map((s, i) => <Text key={i} color={s.c}>{s.t}</Text>)}{' '.repeat(sFill)}{statusRight.map((s, i) => <Text key={'r' + i} color={s.c}>{s.t}</Text>)}</Text>

      {/* slash autocomplete */}
      {matches.length ? <Text color={T.muted} dimColor>{matches.join('  ')}</Text> : null}

      {/* composer */}
      <Box borderStyle="round" borderColor={streaming ? T.cloud : T.accent} paddingX={1}>
        <Text color={T.accent}>› </Text>
        {input ? <Text color={T.text}>{input}</Text> : <Text color={T.muted} dimColor>Send a message — Wayfinder routes it…</Text>}
        <Text color={T.accent}>{streaming ? '' : '▌'}</Text>
      </Box>

      {/* footer */}
      <Box justifyContent="space-between">
        <Text color={T.muted} dimColor>/help   ·   ↑↓ history   ·   ctrl-c cancel / quit</Text>
        <Text color={T.muted} dimColor>no model call to decide</Text>
      </Box>
    </Box>
  );
}
