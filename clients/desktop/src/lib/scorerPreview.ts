// The local-mirror preview (WF-ADR-0042 §2). The embedded JS scorer is a byte-for-byte port of
// the Python scorer, trusted ONLY behind the green build-time parity gate — never a routed
// decision. It runs only when the gateway is unreachable, and its output is always framed as a
// preview ("local mirror — start the gateway"). If parity isn't verified, callers show
// "decisions unavailable" instead of a possibly-drifted local score.
import { scoreComplexity } from "@wayfinder/shared/scorer";
import type { Decision } from "@wayfinder/shared/gateway";

/** The CI parity job (tools/golden.py → parity.mjs, blocking) is the real enforcement; this
 *  flag reflects it into the bundle. Unset → false (safe: withhold the local scorer). */
export function parityVerified(): boolean {
  return import.meta.env.VITE_PARITY_OK === "1";
}

/** A LOCAL MIRROR of the decision — route + score only, from the scorer's default tiers (it
 *  cannot know the user's real cut without the gateway). Empty why: contributions are the
 *  gateway's job. null when parity is unverified or the text is empty. */
export function localPreview(text: string): Decision | null {
  if (!parityVerified() || !text.trim()) return null;
  const { score, recommendation } = scoreComplexity(text);
  return {
    model: recommendation,
    score,
    mode: "preview",
    isLocal: recommendation === "local",
    contributions: [],
    targets: ["local", "cloud"],
  };
}
