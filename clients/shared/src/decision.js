// Framework-agnostic decision-render helpers (WF-ADR-0042), shared by the desktop popover and
// the terminal client. These format/select over a decision object as produced by
// `gateway.decisionFromDebug` / `decisionFromHeaders` — `{ model, score, mode, isLocal,
// contributions: [{ name, value, share }], targets, decisionOnly, dryRun }`. They NEVER score
// (WF-ADR-0001); they only shape what the gateway already decided.
//
// The decision-render contract is pinned by the recorded golden gateway fixtures
// (tools/record-fixtures.mjs → clients/desktop/src/test/fixtures), not by any Python module —
// these helpers are kept deliberately small and literal so those fixtures fully cover them.

export const LOCAL_GLYPH = '●';
export const CLOUD_GLYPH = '◆';

/** 'local' | 'cloud' — the route kind, from the gateway's `isLocal`. */
export function routeKind(decision) {
  return decision && decision.isLocal ? 'local' : 'cloud';
}

/** Uppercase route label for a pill/badge, e.g. 'LOCAL' / 'CLOUD'. */
export function routeLabel(decision) {
  return routeKind(decision).toUpperCase();
}

/** The route glyph: filled dot for local, diamond for cloud. */
export function routeGlyph(decision) {
  return decision && decision.isLocal ? LOCAL_GLYPH : CLOUD_GLYPH;
}

/** Score as a fixed 2-dp string (tabular-nums in the UI so it doesn't jitter). */
export function formatScore(score) {
  return (Number.isFinite(score) ? score : 0).toFixed(2);
}

/**
 * Top-N contributions by share, largest first — the "why" rows. Ties keep input order
 * (stable sort). Returns a new array; never mutates the decision.
 */
export function topContributions(decision, n = 5) {
  const rows = (decision && decision.contributions) || [];
  return rows
    .map((c, i) => ({ ...c, _i: i }))
    .sort((a, b) => (b.share - a.share) || (a._i - b._i))
    .slice(0, n)
    .map(({ _i, ...c }) => c);
}

/**
 * A one-line routing badge, e.g. "cloud · score 0.82" (+ " · cache hit" / " · offline" /
 * " · decision only") — the decision card's sub-line.
 */
export function routingBadge(decision, { cache = false, offline = false } = {}) {
  if (!decision) return '';
  const parts = [`${routeKind(decision)} · score ${formatScore(decision.score)}`];
  if (decision.decisionOnly) parts.push('decision only');
  if (offline) parts.push('offline');
  if (cache) parts.push('cache hit');
  return parts.join(' · ');
}

/** A single contribution's raw `value` by feature name, or 0 when the feature is absent. */
function featureValue(decision, name) {
  const rows = (decision && decision.contributions) || [];
  const hit = rows.find((c) => c.name === name);
  return hit ? hit.value : 0;
}

/** Bucket a count into low/medium/high by the two boundaries [lo, hi). */
function bucket(n, lo, hi) {
  if (n >= hi) return 'high';
  if (n >= lo) return 'medium';
  return 'low';
}

/**
 * The five prompt-analysis rows behind the score (WF-DESIGN-0014 amendment), for the popover
 * Overview card. Display-only — it reads the contributions the gateway already returned and NEVER
 * scores (WF-ADR-0001). Each row is `{ key, label, value }` where `value` is a short human string:
 *   Word count → the raw count; Lists → 'none' or the item count; Code blocks / Structured
 *   sections → 'no' / 'yes'; Lexical signals → 'low' | 'medium' | 'high' bucketed from the
 *   reasoning + math + constraint term counts (the three keyword-family features).
 */
export function featureRows(decision) {
  const words = featureValue(decision, 'word_count');
  const lists = featureValue(decision, 'list_item_count');
  const code = featureValue(decision, 'code_block_count');
  const sections = featureValue(decision, 'heading_count') + featureValue(decision, 'table_row_count');
  const lexical =
    featureValue(decision, 'reasoning_term_count') +
    featureValue(decision, 'math_symbol_count') +
    featureValue(decision, 'constraint_term_count');
  return [
    { key: 'words', label: 'Word count', value: String(words) },
    { key: 'lists', label: 'Lists', value: lists ? String(lists) : 'none' },
    { key: 'code', label: 'Code blocks', value: code ? 'yes' : 'no' },
    { key: 'sections', label: 'Structured sections', value: sections ? 'yes' : 'no' },
    { key: 'lexical', label: 'Lexical signals', value: bucket(lexical, 3, 12) },
  ];
}

/**
 * A short "why" sentence mirroring the mockup, assembled from the same counts `featureRows` uses —
 * e.g. "short prompt, no code, no structured sections." Length, code, and structured-sections
 * clauses always appear (so the sentence reads the same shape every turn); a "technical terms"
 * clause is appended only when the lexical signal is high. Display-only, never scores.
 */
export function whyLine(decision) {
  const words = featureValue(decision, 'word_count');
  const code = featureValue(decision, 'code_block_count');
  const sections = featureValue(decision, 'heading_count') + featureValue(decision, 'table_row_count');
  const lexical =
    featureValue(decision, 'reasoning_term_count') +
    featureValue(decision, 'math_symbol_count') +
    featureValue(decision, 'constraint_term_count');
  const parts = [words < 40 ? 'short prompt' : words < 200 ? 'medium-length prompt' : 'long prompt'];
  parts.push(code ? 'code detected' : 'no code');
  parts.push(sections ? 'structured sections' : 'no structured sections');
  if (lexical >= 12) parts.push('technical terms');
  return `${parts.join(', ')}.`;
}
