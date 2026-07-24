#!/usr/bin/env node
// Record the golden gateway fixtures that pin the desktop client's decision-render contract
// (WF-DESIGN-0012 "Testing the contract"; WF-ROADMAP-0009 Phase 2). This is the recorded-truth
// replacement for the never-landed menubar_core parity idea: every fixture is a REAL gateway
// response, captured over HTTP from `wayfinder-router serve` running against a deterministic
// in-process fake upstream — never hand-written.
//
//   node tools/record-fixtures.mjs            # writes clients/desktop/src/test/fixtures/
//
// Scenarios (each spawns its own gateway in a temp dir; the fake upstream streams a fixed
// reply so transcripts and savings are byte-stable):
//   dry-run    -> decision-local.json, decision-cloud.json      (X-Wayfinder-Debug payloads)
//   degraded   -> healthz-degraded.json                         (missing_keys verbatim)
//   healthy    -> healthz-ok.json, sse-transcript.txt,
//                 sse-headers.json, savings.json, recent.json
//   offline    -> healthz-offline.json, decision-offline.json   ([gateway] offline = true)
//   no-models  -> decision-only.json                            (skipped until the gateway
//                                                                supports decision_only — PR #68)
//
// Determinism: the ONLY normalization applied is request ids (uuid -> "fx-<scenario>") and any
// `ts`/timestamp fields (-> 0); everything else is the gateway's bytes. Re-running the recorder
// against the same gateway version must produce an empty git diff.

import { createServer } from "node:http";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { spawn } from "node:child_process";

const ROOT = dirname(dirname(fileURLToPath(import.meta.url)));
const OUT = join(ROOT, "clients", "desktop", "src", "test", "fixtures");
const ROUTER =
  process.env.WAYFINDER_ROUTER_BIN ||
  join(ROOT, "rust", "target", "debug", "wayfinder-router");
const GW_PORT = 8090; // never the real service's 8088
const UP_PORT = 9909;
const GW = `http://127.0.0.1:${GW_PORT}`;

const SIMPLE_PROMPT = "hi there";
// Structured, mathy, constraint-heavy, repeated x3 — scores 0.62 under the default 0.5 cut
// (calibrated against the real scorer), so the decision deterministically routes cloud.
const COMPLEX_SECTION = [
  "# Optimize the multi-tier routing plan",
  "## Constraints",
  "You must prove the greedy assignment is optimal. Derive, analyze, and justify each step;",
  "therefore consider the dual formulation because the primal is degenerate. Ensure that",
  "latency <= 200ms at P95 and never exceed the budget. Also verify the invariant holds.",
  "## Model",
  "Let x_i ∈ {0,1}, minimize Σ c_i·x_i subject to Σ a_ij·x_i ≥ b_j ∀j, with λ ≥ 0 duals.",
  "∂L/∂x = c - A^T λ; complementary slackness implies x_i(c_i - λ^T a_i) = 0.",
  "```python",
  "def assign(tiers, prompts):",
  "    for p in prompts:",
  "        yield argmin(c[t] for t in tiers if feasible(t, p))",
  "```",
  "| tier | cost | latency |",
  "|------|------|---------|",
  "| local | 0.2 | 20ms |",
  "First, formalize the problem. Second, prove the exchange argument. Third, analyze",
  "worst-case complexity; explain why the integrality gap is 1. Justify rigorously.",
].join("\n");
const COMPLEX_PROMPT = [COMPLEX_SECTION, COMPLEX_SECTION, COMPLEX_SECTION].join("\n\n");

// ---------------------------------------------------------------- fake upstream (deterministic)
const REPLY_DELTAS = ["Routing", " decisions", " stay", " local."];
function fakeUpstream() {
  const srv = createServer((req, res) => {
    let body = "";
    req.on("data", (c) => (body += c));
    req.on("end", () => {
      const wantsStream = /"stream"\s*:\s*true/.test(body);
      if (wantsStream) {
        res.writeHead(200, { "content-type": "text/event-stream" });
        for (const [i, delta] of REPLY_DELTAS.entries()) {
          res.write(
            `data: ${JSON.stringify({
              id: "chatcmpl-fixture",
              object: "chat.completion.chunk",
              created: 0,
              model: "fake-model",
              choices: [{ index: 0, delta: i === 0 ? { role: "assistant", content: delta } : { content: delta }, finish_reason: null }],
            })}\n\n`,
          );
        }
        res.write(
          `data: ${JSON.stringify({
            id: "chatcmpl-fixture",
            object: "chat.completion.chunk",
            created: 0,
            model: "fake-model",
            choices: [{ index: 0, delta: {}, finish_reason: "stop" }],
          })}\n\n`,
        );
        res.write("data: [DONE]\n\n");
        res.end();
      } else {
        res.writeHead(200, { "content-type": "application/json" });
        res.end(
          JSON.stringify({
            id: "chatcmpl-fixture",
            object: "chat.completion",
            created: 0,
            model: "fake-model",
            choices: [
              { index: 0, message: { role: "assistant", content: REPLY_DELTAS.join("") }, finish_reason: "stop" },
            ],
            usage: { prompt_tokens: 12, completion_tokens: 4, total_tokens: 16 },
          }),
        );
      }
    });
  });
  return new Promise((resolve) => srv.listen(UP_PORT, "127.0.0.1", () => resolve(srv)));
}

// ------------------------------------------------------------------------- gateway per scenario
const BASE_TOML = `
[routing]
threshold = 0.5

[[routing.tiers]]
min_score = 0.0
model = "local"
cost = 0.2

[[routing.tiers]]
min_score = 0.5
model = "cloud"
cost = 1.0
`;

const MODELS_TOML = `
[gateway.models.local]
base_url = "http://127.0.0.1:${UP_PORT}/v1"
model = "fake-local"
cost_per_1k = 0.2

[gateway.models.cloud]
base_url = "http://127.0.0.1:${UP_PORT}/v1"
model = "fake-cloud"
cost_per_1k = 1.0
`;

async function withGateway({ toml, args = [] }, fn) {
  const dir = mkdtempSync(join(tmpdir(), "wf-fixture-"));
  writeFileSync(join(dir, "wayfinder-router.toml"), toml);
  const child = spawn(
    ROUTER,
    ["serve", "--host", "127.0.0.1", "--port", String(GW_PORT), ...args],
    { cwd: dir, stdio: ["ignore", "ignore", "pipe"] },
  );
  let stderr = "";
  child.stderr.on("data", (c) => (stderr += c));
  try {
    for (let i = 0; i < 100; i++) {
      try {
        await fetch(`${GW}/healthz`);
        break;
      } catch {
        if (child.exitCode !== null) throw new Error(`gateway exited early:\n${stderr}`);
        await new Promise((r) => setTimeout(r, 100));
      }
    }
    return await fn();
  } finally {
    child.kill("SIGTERM");
    await new Promise((r) => child.on("exit", r));
    rmSync(dir, { recursive: true, force: true });
  }
}

// -------------------------------------------------------------------------------- normalization
function normalize(value, scenario) {
  // Request ids are 12-hex short ids (uuid4().hex[:12]) — normalize them ONLY where they
  // live (the request_id JSON key and the x-wayfinder-router-request-id header), never by a
  // bare hex regex: price_table_version is also 12 hex chars and must stay verbatim.
  const walk = (v) => {
    if (Array.isArray(v)) return v.map(walk);
    if (v && typeof v === "object") {
      const out = {};
      for (const [k, val] of Object.entries(v)) {
        if (k === "request_id" || k === "x-wayfinder-router-request-id") out[k] = `fx-${scenario}`;
        else if (k === "ts" || k === "timestamp") out[k] = 0;
        else out[k] = walk(val);
      }
      return out;
    }
    return v;
  };
  return walk(value);
}

function pickWfHeaders(headers) {
  const out = {};
  for (const [k, v] of headers.entries()) if (k.startsWith("x-wayfinder-")) out[k] = v;
  return out;
}

function save(name, data) {
  const path = join(OUT, name);
  writeFileSync(path, typeof data === "string" ? data : JSON.stringify(data, null, 2) + "\n");
  console.log(`  recorded ${name}`);
}

async function debugTurn(prompt, { stream = false, headers = {} } = {}) {
  const res = await fetch(`${GW}/v1/chat/completions`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Wayfinder-Debug": "1", ...headers },
    body: JSON.stringify({ model: "auto", messages: [{ role: "user", content: prompt }], stream }),
  });
  return res;
}

// ------------------------------------------------------------------------------------ scenarios
async function main() {
  mkdirSync(OUT, { recursive: true });
  const upstream = await fakeUpstream();
  const skipped = [];
  try {
    console.log("scenario: dry-run (decision payloads)");
    await withGateway({ toml: BASE_TOML + MODELS_TOML, args: ["--dry-run"] }, async () => {
      for (const [name, prompt] of [
        ["decision-local", SIMPLE_PROMPT],
        ["decision-cloud", COMPLEX_PROMPT],
      ]) {
        const res = await debugTurn(prompt);
        const body = await res.json();
        const wf = body.wayfinder;
        if (!wf) throw new Error(`${name}: no wayfinder payload`);
        const wantLocal = name === "decision-local";
        const routedLocal = wf.model === "local";
        if (wantLocal !== routedLocal)
          throw new Error(`${name}: expected ${wantLocal ? "local" : "cloud"}, gateway scored ${wf.model} (score ${wf.score})`);
        save(`${name}.json`, normalize({ headers: pickWfHeaders(res.headers), wayfinder: wf }, name));
      }
    });

    console.log("scenario: degraded (missing key)");
    const degradedToml =
      BASE_TOML +
      MODELS_TOML.replace('model = "fake-cloud"', 'model = "fake-cloud"\napi_key_env = "WAYFINDER_FIXTURE_MISSING"');
    await withGateway({ toml: degradedToml }, async () => {
      save("healthz-degraded.json", normalize(await (await fetch(`${GW}/healthz`)).json(), "degraded"));
    });

    console.log("scenario: healthy (healthz, verbatim SSE, savings)");
    await withGateway({ toml: BASE_TOML + MODELS_TOML }, async () => {
      save("healthz-ok.json", normalize(await (await fetch(`${GW}/healthz`)).json(), "ok"));
      const res = await debugTurn(SIMPLE_PROMPT, { stream: true });
      save("sse-headers.json", normalize(pickWfHeaders(res.headers), "sse"));
      const raw = Buffer.from(await res.arrayBuffer()).toString("utf8");
      save("sse-transcript.txt", raw.replace(/("request_id"\s*:\s*")[0-9a-f]+(")/g, "$1fx-sse$2"));
      await debugTurn(COMPLEX_PROMPT); // a cloud-routed turn so savings has both routes
      save("savings.json", normalize(await (await fetch(`${GW}/v1/savings?period=all`)).json(), "savings"));
      // The route-split feed after one local + one cloud turn — drives the tray meter + tile.
      save("recent.json", normalize(await (await fetch(`${GW}/router/recent`)).json(), "recent"));
    });

    console.log("scenario: offline ([gateway] offline = true)");
    await withGateway({ toml: BASE_TOML + "\n[gateway]\noffline = true\n" + MODELS_TOML }, async () => {
      save("healthz-offline.json", normalize(await (await fetch(`${GW}/healthz`)).json(), "offline"));
      const res = await debugTurn(COMPLEX_PROMPT); // scores cloud, served local under offline
      save(
        "decision-offline.json",
        normalize({ headers: pickWfHeaders(res.headers), wayfinder: (await res.json()).wayfinder }, "offline"),
      );
    });

    console.log("scenario: no-models (decision_only — needs the gateway from PR #68)");
    await withGateway({ toml: BASE_TOML }, async () => {
      const res = await debugTurn(SIMPLE_PROMPT);
      if (res.status === 200) {
        const body = await res.json();
        if (body.wayfinder && body.wayfinder.decision_only) {
          save(
            "decision-only.json",
            normalize({ headers: pickWfHeaders(res.headers), wayfinder: body.wayfinder }, "decision-only"),
          );
          return;
        }
      }
      skipped.push("decision-only.json (gateway returned " + res.status + " — re-run once PR #68 lands)");
    });
  } finally {
    upstream.close();
  }
  for (const s of skipped) console.log(`  SKIPPED ${s}`);
  console.log("done.");
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
