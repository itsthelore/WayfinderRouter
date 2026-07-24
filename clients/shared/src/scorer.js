// Shared decision preview (WF-ADR-0042), checked byte-for-byte against the frozen
// migration corpus (see ../test/parity.mjs). A *verified mirror* of the gateway's decision,
// used ONLY as the app's decision-only degraded mode when the gateway is unreachable — the
// Rust gateway remains the source of truth (WF-ADR-0001). Preserved from the parity-proven
// spike; do not edit the numeric path (constants, regexes, summation order, rounding) without
// re-running the parity gate.

export const FEATURE_ORDER = [
  'word_count', 'heading_count', 'max_heading_depth', 'list_item_count', 'link_count',
  'code_block_count', 'table_row_count', 'reasoning_term_count', 'math_symbol_count',
  'constraint_term_count', 'question_count',
];

// Insertion order matters: sum(weights.values()) is summed in this order in Python.
export const DEFAULT_WEIGHTS = {
  word_count: 3.0, list_item_count: 2.0, heading_count: 1.5, code_block_count: 1.5,
  table_row_count: 1.0, link_count: 1.0, max_heading_depth: 1.0,
  reasoning_term_count: 0.0, math_symbol_count: 0.0, constraint_term_count: 0.0, question_count: 0.0,
};

const SATURATION = {
  word_count: 400.0, heading_count: 8.0, max_heading_depth: 4.0, list_item_count: 15.0,
  link_count: 10.0, code_block_count: 4.0, table_row_count: 12.0, reasoning_term_count: 2.0,
  math_symbol_count: 6.0, constraint_term_count: 3.0, question_count: 3.0,
};

const REASONING_TERMS = new Set(['prove', 'proof', 'proofs', 'proven', 'derive', 'derives',
  'derivation', 'theorem', 'theorems', 'lemma', 'lemmas', 'corollary', 'axiom', 'axioms',
  'irrational', 'undecidable', 'undecidability', 'decidable', 'infinitely', 'asymptotic',
  'complexity', 'invariant', 'invariants', 'concurrency', 'concurrent', 'deadlock', 'induction',
  'contradiction', 'optimal', 'optimality', 'optimize', 'optimise', 'minimise', 'minimize',
  'maximise', 'maximize', 'recurrence', 'halting', 'eigenvalue', 'eigenvalues', 'integral',
  'derivative', 'polynomial', 'prime', 'primes', 'modulo', 'isomorphism', 'monotonic',
  'bijection', 'injective', 'surjective', 'combinatorial']);
const CONSTRAINT_TERMS = new Set(['must', 'without', 'only', 'ensure', 'exactly', 'guarantee',
  'constraint', 'constraints', 'subject', 'preserving', 'preserve']);

const HEADING_RE = /^(#{1,6})\s+\S/;
const LIST_RE = /^\s*(?:[-*+]|\d+[.)])\s+\S/;
const TABLE_ROW_RE = /^\s*\|.*\|\s*$/;
const FENCE_RE = /^\s*(?:```|~~~)/;
const LINK_RE = /\[[^\]]+\]\([^)]+\)/g;
const WORD_TOKEN_RE = /[a-zA-Z][a-zA-Z'\-]*/g;
// All glyphs are BMP (≤ U+FFFF), so a non-`u` class is safe.
const MATH_SYMBOL_RE = /[∑∫√≤≥≠≈∞∂∈∉∀∃⊆⊂∪∩∇±×÷πθλμσΣΠ]|\\[a-zA-Z]+/g;

// Python str.splitlines(): split on the same boundaries (\r\n first), no trailing empty.
function splitlines(s) {
  if (s === '') return [];
  const parts = s.split(/\r\n|[\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029]/u);
  if (parts.length && parts[parts.length - 1] === '') parts.pop();
  return parts;
}

// Python round(x, 2): round-half-to-EVEN (banker's), with an epsilon to guard float-mult error.
// Python round(x, 2): round-half-to-EVEN on the TRUE binary value of x. A scaled `x*100`
// rounds on a value with its own float error (wrong ~2% of the time on .xx5 inputs, as the
// parity spike found). toFixed(20) exposes the true digits past place 2, so we round on
// those — half-up when the tail is non-zero, ties-to-even only on a genuine exact tie.
export function pyRound2(x) {
  if (!isFinite(x)) return x;
  const neg = x < 0;
  const s = Math.abs(x).toFixed(20); // e.g. 0.005 -> "0.00500000000000000010"
  const dot = s.indexOf('.');
  const dec = s.slice(dot + 1);
  let n = parseInt(s.slice(0, dot) + dec.slice(0, 2), 10); // floor(|x| * 100)
  const tail = dec.slice(2); // true digits beyond the 2nd decimal place
  const third = tail.charCodeAt(0) - 48;
  if (third > 5) n += 1;
  else if (third === 5) {
    if (/[1-9]/.test(tail.slice(1))) n += 1; // strictly above .xx5 -> up
    else if (n % 2 === 1) n += 1; // exact tie -> even
  }
  return (neg ? -n : n) / 100;
}

function stripFrontmatter(text) {
  const lines = text.split('\n'); // Python uses split("\n") here, NOT splitlines
  if (!lines.length || lines[0].trim() !== '---') return text;
  for (let i = 1; i < lines.length; i++) {
    if (lines[i].trim() === '---' || lines[i].trim() === '...') return lines.slice(i + 1).join('\n');
  }
  return text;
}

export function extractFeatures(text) {
  const body = stripFrontmatter(text);
  const word_count = (body.match(/\S+/gu) || []).length; // == len(body.split())
  let heading_count = 0, max_heading_depth = 0, list_item_count = 0, table_row_count = 0;
  let code_block_count = 0, link_count = 0, in_fence = false;
  for (const line of splitlines(body)) {
    if (FENCE_RE.test(line)) { if (!in_fence) code_block_count += 1; in_fence = !in_fence; continue; }
    if (in_fence) continue;
    const h = line.match(HEADING_RE);
    if (h) { heading_count += 1; max_heading_depth = Math.max(max_heading_depth, h[1].length); }
    else if (LIST_RE.test(line)) list_item_count += 1;
    else if (TABLE_ROW_RE.test(line)) table_row_count += 1;
    link_count += (line.match(LINK_RE) || []).length;
  }
  const tokens = body.toLowerCase().match(WORD_TOKEN_RE) || [];
  let reasoning_term_count = 0, constraint_term_count = 0;
  for (const t of tokens) {
    if (REASONING_TERMS.has(t)) reasoning_term_count += 1;
    if (CONSTRAINT_TERMS.has(t)) constraint_term_count += 1;
  }
  const math_symbol_count = (body.match(MATH_SYMBOL_RE) || []).length;
  const question_count = (body.match(/\?/g) || []).length;
  return {
    word_count, heading_count, max_heading_depth, list_item_count, link_count,
    code_block_count, table_row_count, reasoning_term_count, math_symbol_count,
    constraint_term_count, question_count,
  };
}

export function scalarScore(features, weights = DEFAULT_WEIGHTS) {
  const norm = {};
  for (const name of FEATURE_ORDER) norm[name] = Math.min(features[name] / SATURATION[name], 1.0);
  // total_weight = sum(weights.values()) — DEFAULT_WEIGHTS insertion order
  let total_weight = 0;
  for (const k of Object.keys(weights)) total_weight += weights[k];
  if (!total_weight) return 0.0;
  let accumulated = 0;
  for (const name of FEATURE_ORDER) accumulated += (weights[name] || 0.0) * norm[name];
  return pyRound2(accumulated / total_weight);
}

export function recommendTier(score, tiers = [[0.0, 'local'], [0.5, 'cloud']]) {
  let chosen = tiers[0][1];
  for (const [min_score, model] of tiers) {
    if (score >= min_score) chosen = model;
    else break;
  }
  return chosen;
}

export function scoreComplexity(text) {
  const features = extractFeatures(text);
  const score = scalarScore(features);
  return { score, recommendation: recommendTier(score), features };
}
