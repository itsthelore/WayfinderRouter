---
schema_version: 1
id: WF-DESIGN-0013
type: design
tags: [governance, audit, policy, identity, detectors, on-disk, single-node, deterministic, store, mmap, sqlite, movement-a, movement-b]
---

# WF-DESIGN-0013: The Single-Node Governance Spine — Store, Audit, Policy, Detectors, Identity

## Status

Accepted — built, gated, and shipped (WF-ADR-0045); measured outcomes and the residual list
live in WF-ROADMAP-0012 §Measured outcomes.

> The net-new machinery WF-ROADMAP-0012 requires on one node, on disk, behind WF-ADR-0001's
> frozen constitution: a persistent append-only audit/decision log with partitioned indexes, a
> compiled-once policy engine with a deterministic total order over rules, a productized detector
> set that reproduces the committed benchmark P/R, an identity model that finally gives
> `VirtualKey.tags` a consumer, and Movement A's rehousing of the gateway's stateful surfaces onto
> on-disk bounded structures behind unchanged contracts. Every API below is specified concretely so
> a failing contract test can be written from it before a line of implementation is built.

## Context

WF-ROADMAP-0011 names the destination (Wayfinder as the policy enforcement point an organization's
AI traffic flows through); WF-ROADMAP-0012 is the single-node exercise that builds the load-bearing
spine, in two movements. **Movement A** rehouses the gateway's scale-fragile in-RAM surfaces
(`cache.py`, `feedback.py`, `ratelimit.py`, `reliability.py`, `pricing.py`'s `SavingsLedger`, and
`gateway.py`'s `Budget`) onto on-disk bounded structures *behind unchanged contracts*. **Movement
B** builds the governance spine proper: audit log, policy engine, detectors, identity.

The hard constraints (WF-ADR-0001, WF-ADR-0043, and the WF-ROADMAP-0012 constraint ledger) are:
offline, deterministic, sub-millisecond hot path, no model call, no data egress; stdlib only for
the storage engine; `import wayfinder_router` pulls no rich/textual/fastapi/httpx; audit records
never store raw prompt content or detector match text (digests, spans, counts, metadata only —
consistent with `cache.py`'s key-is-a-hash posture and `Metrics`' metadata-only stance); one node,
no sharding, no external services; and every currently observable behavior is frozen.

The perf budget drives the whole design: policy evaluation (route score + compiled-once detectors +
policy verbs + identity attribution + audit-append) p99 < 1 ms in-process; audit queries p99 < 100
ms flat 1M→10M; incremental re-eval of a ~1,000-request changeset < 5 s log-size-independent; cold
rebuild ≤ ~2 min per 1M records parallel across 4 cores; RSS ≤ ~10 GB with log+indexes on disk.

The gateway's real decision surface (read from `gateway.py:1804–2260`) is what the audit record must
faithfully capture. The enforcement pipeline, in order, is: gateway-wide rate-limit admission
(`ratelimit`, 429) → virtual-key auth (`vkeys.match`, 401) → per-key rate-limit (429) → override
parse → **score once** (`score_complexity`, `decision_seconds` is the decision-latency metric) →
route selection producing `(chosen, mode)` where `mode ∈ {scored, pinned, slash-pinned,
threshold-override, sticky, budget-degraded, key-scoped}` → offline degrade (WF-ADR-0039) → budget
enforcement (`Budget`, degrade-to-cheapest or 402 block) → per-key model-allowlist clamp (the
"final word on the route", `mode="key-scoped"`) → `wf_headers` (`x-wayfinder-router-model/score/
mode/request-id`, plus `-budget`/`-offline`) → `recent` ring + `_record_turn` (cost metadata into
`SavingsLedger`). The governance spine inserts **one new stage** (identity + detectors + policy +
audit-append) between the clamp and `wf_headers`, and is a pure no-op when its config tables are
absent. `VirtualKey.tags` is parsed (`gateway.py:638`) and round-tripped (`gateway.py:858`) but
consumed by no line today; the identity model is where it acquires its consumer.

## Design

New modules, all stdlib-only and lazily reachable (none imported by `wayfinder_router/__init__.py`,
preserving the import contract; the gateway imports them locally, as it already does for
`bootstrap`):

| Module | Responsibility | Stdlib deps |
|---|---|---|
| `wayfinder_router/store.py` | Append-only segmented log + partitioned index; the durable substrate | `mmap`, `sqlite3`, `struct`, `zlib`, `os`, `multiprocessing`, `threading` |
| `wayfinder_router/audit.py` | Decision-record schema + append/query/replay/re-eval over `store` | `json`, `hashlib`, `time` |
| `wayfinder_router/policy.py` | Compiled-once rules, match conditions, verbs, deterministic total order | `hashlib`, `tomllib` |
| `wayfinder_router/detectors.py` | Productized detector set + compile-once registry + hit summaries | `re` |
| `wayfinder_router/identity.py` | Principal schema + attribution from virtual keys | `tomllib` |

### 1. Storage engine — `wayfinder_router/store.py`

**Chosen primary design: an append-only segmented log as the durable source of truth, paired with
one memory-managed SQLite (WAL) index shard per log segment.** The log carries the record bytes;
the SQLite shards carry only the small indexable projection (seq, ts, the declared index fields,
segment id, offset, length). Both are stdlib (`mmap`, `struct`, `zlib`, `sqlite3`).

**Rationale (why this over the alternatives).** The two gates no single-file design satisfies
cleanly are *sub-ms append* and *4-core parallel rebuild*. The segmented log wins both: (a) an
append is a buffered `write()` of a framed record plus one prepared-statement `INSERT` into the
live shard — no B-tree page-splitting write amplification on the record bytes, and fsync is deferred
(see durability contract), so p99 append is microseconds; (b) sealed segments are independent and
immutable, so rebuild fans out across cores with **no shared writer** — each worker rebuilds the
shards for a disjoint set of segments. SQLite supplies the *query* indexes (B-tree secondaries)
without hand-rolling and crash-testing a bespoke on-disk B-tree, which is where a pure-mmap index
would burn its risk budget. The partition-by-time shard layout means an identity/policy/route query
is O(shards) cheap probes returning a paginated window, not an O(N) scan.

**Fallback (noted, not chosen): pure sqlite3-WAL single-table store** — records inline as a `BLOB`
column in one `records` table with covering secondary indexes. Simpler and less code, but it
serializes all writes through one writer and makes rebuild serial (mitigable only by building N
per-shard DBs and `ATTACH`-merging — i.e. re-inventing the segment partitioning anyway). Adopt the
fallback only if the segmented-log framing proves to carry more crash-recovery surface than the
schedule allows; the audit/policy/identity layers above `store` are written against the `RecordStore`
interface and are backend-agnostic, so the swap is contained.

**On-disk layout** (rooted at a configured directory, default `<start_dir>/wayfinder-governance/`):

```
root/
  MANIFEST.json          # {schema_version, index_fields, segments:[{id,seq_lo,seq_hi,ts_lo,ts_hi,records,bytes,sealed}], high_water_seq}
  segments/
    000000.log           # append-only framed records (sealed, immutable once full)
    000001.log           # the live segment (open for append)
  index/
    000000.db            # sqlite WAL shard for segment 000000 (read-only once sealed)
    000001.db            # live shard for the open segment
```

**Record frame on disk** (little-endian, fixed 24-byte header + payload):
`struct.pack("<IQdI", length, seq, ts_wall, crc32) + payload`, where `length = len(payload)`,
`crc32 = zlib.crc32(payload)`. A torn tail (short read or crc mismatch) marks end-of-valid-log.

**Segment sealing bounds:** `SEGMENT_MAX_RECORDS = 1_000_000`, `SEGMENT_MAX_BYTES = 512 * 1024 *
1024`. Reaching either seals the current segment (fsync + mark `sealed` in MANIFEST, set the shard
read-only) and opens the next. One segment ≈ one time partition (contiguous seq and ts ranges).

**Partitioned-index scheme — by time.** The partition key is the log segment (contiguous in both
`seq` and `ts_wall` by construction of an append-only log). Each shard DB holds one table:
`records(seq INTEGER PRIMARY KEY, ts_wall REAL, seg INTEGER, off INTEGER, len INTEGER, <field>
TEXT ...)` with a secondary B-tree index on `ts_wall` and on **each declared index field**. A
range/identity/policy/route query fans out only to the shards whose `[ts_lo, ts_hi]` overlaps the
requested window (all shards when the filter has no time bound), runs one indexed lookup per shard,
merges by `seq`, and applies `after_seq`/`limit` pagination. Time is chosen as the partition
dimension because it is the dominant audit filter, it makes sealing/pruning trivial, and it is the
only dimension monotone with append order.

**Write path & honest sub-ms append.** `append` frames the payload, `write()`s it to the live
segment's buffered file object (no per-record `fsync`), and executes one prepared `INSERT` into the
live shard (WAL, `PRAGMA synchronous=NORMAL` so the INSERT touches the WAL page in memory, not the
disk platter). Durability is deferred to a barrier. The append returns a `Location` in
microseconds; the p99 < 1 ms budget is met because no syscall in the path blocks on the platter.

**Durability contract (explicit).** A record is *durable* once (a) `flush()` returns, or (b)
`fsync_bytes` (default 4 MiB) of un-fsynced payload has accumulated and triggered an automatic
barrier, or (c) `close()` completes. A barrier = flush the log file buffer + `os.fsync` the log fd +
`PRAGMA wal_checkpoint(PASSIVE)` on the live shard. On crash, records appended since the last
barrier may be lost, but the log is **never left torn**: recovery truncates at the first
short/crc-bad frame. `durability="strict"` (opt-in) fsyncs every append (drops sub-ms; for
compliance deployments that need per-record durability). Default `durability="buffered"`.

**Read path.** `read(seq)`/`read_at(loc)` `mmap` the target segment (segments are cached mmaps,
bounded LRU) and slice `payload = mm[off+24 : off+24+len]`, verifying crc. `query(...)` uses the
shards as above and returns `Location`s; the audit layer materializes records from them.

**Crash-recovery contract.** On `RecordStore(root)` open: read MANIFEST; for the live segment, scan
frames from `seq_lo` verifying crc, truncate the file at the first torn frame, and for any frame
whose `seq` exceeds the live shard's `MAX(seq)` re-`INSERT` it into the shard (log is truth, shard
is derived). If a shard DB is missing or `PRAGMA integrity_check` fails, drop and rebuild it from its
segment. Sealed segments are verified by MANIFEST record count + a cheap tail-crc check, not a full
re-scan.

**Rebuild (parallel).** `RecordStore.rebuild(root, *, workers=4)` partitions the segment list across
`workers` **processes** (`multiprocessing`, not threads — sidesteps the GIL); each worker
independently rebuilds the shard DBs for its segments (no shared writer), then the parent rewrites
MANIFEST. Linear scan + index build, embarrassingly parallel → ≤ ~2 min / 1M records / core.

**Public API (all cross-cutting options keyword-only):**

```python
SCHEMA_VERSION: int = 1
SEGMENT_MAX_RECORDS: int = 1_000_000
SEGMENT_MAX_BYTES: int = 512 * 1024 * 1024
DEFAULT_FSYNC_BYTES: int = 4 * 1024 * 1024

class StoreError(Exception): ...
class SegmentCorruptError(StoreError): ...

@dataclass(frozen=True)
class Location:
    segment_id: int
    offset: int      # byte offset of the frame header within the segment file
    length: int      # payload length (excludes the 24-byte header)
    seq: int
    ts_wall: float

class RecordStore:
    def __init__(self, root: str, *, index_fields: tuple[str, ...] = (),
                 durability: str = "buffered", fsync_bytes: int = DEFAULT_FSYNC_BYTES,
                 clock: Callable[[], float] = time.time) -> None: ...
    def append(self, payload: bytes, *, keys: Mapping[str, str | int | None]) -> Location: ...
    def read(self, seq: int) -> bytes | None: ...            # None if seq unknown
    def read_at(self, loc: Location) -> bytes: ...
    def query(self, *, start_ts: float | None = None, end_ts: float | None = None,
              equals: Mapping[str, str] = {}, after_seq: int = 0,
              limit: int = 1000) -> list[Location]: ...       # seq-ascending, len ≤ limit
    def scan(self, *, start_seq: int = 0,
             end_seq: int | None = None) -> Iterator[tuple[int, bytes]]: ...
    def high_water_seq(self) -> int: ...
    def flush(self) -> None: ...                             # durability barrier
    def close(self) -> None: ...
    @classmethod
    def rebuild(cls, root: str, *, index_fields: tuple[str, ...],
                workers: int = 4) -> "RebuildReport": ...

@dataclass(frozen=True)
class RebuildReport:
    segments: int
    records: int
    seconds: float
    workers: int
```

`equals` keys must be a subset of `index_fields` (else `StoreError`). `keys` in `append` may include
`None` values (stored as SQL NULL, never matched by an `equals` filter).

### 2. Audit / decision log — `wayfinder_router/audit.py`

The audit record is the full route-decision surface plus the governance signals, **metadata only** —
no raw prompt, no matched text (WF-ROADMAP-0012 constraint; consistent with `cache.py`).

```python
AUDIT_SCHEMA_VERSION: int = 1
AUDIT_INDEX_FIELDS: tuple[str, ...] = ("identity_id", "vkey_id", "policy_id", "route")

@dataclass(frozen=True)
class DetectorHit:                 # shared shape with detectors.DetectorHit (re-exported)
    name: str
    count: int
    spans: tuple[tuple[int, int], ...]   # (start, end) char offsets — NEVER the matched text

@dataclass(frozen=True)
class AuditRecord:
    schema_version: int            # == AUDIT_SCHEMA_VERSION
    seq: int                       # assigned by the store on append (0 before append)
    ts_wall: float                 # time.time() at decision
    ts_mono: float                 # time.perf_counter() at decision (intra-process ordering)
    request_id: str                # gateway's 12-hex request id
    identity_id: str               # resolved principal id (never "" — "anonymous" if none)
    identity_kind: str             # principal kind at decision time (human|agent|service|anonymous)
    team: str | None               # principal team at decision time
    tags: tuple[str, ...]          # principal residual tags at decision time
    vkey_id: str | None            # virtual-key id, or None when keys are unconfigured
    route: str                     # final chosen model/endpoint (post-policy)
    route_pre_policy: str          # chosen route as the gateway computed it before the policy stage
    score: float                   # decision.score
    mode: str                      # scored|pinned|slash-pinned|threshold-override|sticky|budget-degraded|key-scoped
    offline: bool
    budget_state: str | None       # None | "degraded" | "blocked"
    policy_id: str | None          # None when no policy configured
    policy_hash: str | None        # CompiledPolicy.policy_hash (12-hex) or None
    rule: str | None               # deciding (terminal) rule id, or None
    verbs: tuple[str, ...]         # all applied verb names, in application order
    detector_hits: tuple[DetectorHit, ...]
    prompt_tokens: int
    completion_tokens: int
    estimated: bool
    realized: float
    baseline: float
    saved: float
    unit: str                      # "usd" | "relative"
    request_digest: str            # sha256 hex of the canonicalized request body (no raw text)
    def to_json(self) -> dict: ...
    @classmethod
    def from_json(cls, data: Mapping) -> "AuditRecord": ...   # raises AuditSchemaError on version mismatch

class AuditError(Exception): ...
class AuditSchemaError(AuditError): ...

@dataclass(frozen=True)
class AuditPage:
    records: tuple[AuditRecord, ...]
    next_after_seq: int | None     # None when the page is the last

@dataclass(frozen=True)
class ReevalResult:
    seq: int
    before: "PolicyDecision"       # decision recorded at the time (reconstructed from the record)
    after: "PolicyDecision"        # decision the supplied policy/detectors produce now
    changed: bool                  # before.route != after.route or before.verb != after.verb

class AuditLog:
    def __init__(self, root: str, *, store: "RecordStore | None" = None,
                 durability: str = "buffered") -> None: ...
    def append(self, record: AuditRecord) -> int: ...           # returns assigned seq
    def get(self, seq: int) -> AuditRecord | None: ...
    def query(self, *, start_ts: float | None = None, end_ts: float | None = None,
              identity_id: str | None = None, vkey_id: str | None = None,
              policy_id: str | None = None, route: str | None = None,
              after_seq: int = 0, limit: int = 1000) -> AuditPage: ...
    def replay(self, seq: int, *, policy: "CompiledPolicy") -> "PolicyDecision": ...
    def reeval(self, *, policy: "CompiledPolicy", changeset: Iterable[int] | None = None,
               match: Mapping[str, str] = {}) -> Iterator[ReevalResult]: ...
    def flush(self) -> None: ...
    def close(self) -> None: ...
```

**Append API.** `append(record)` canonicalizes `record.to_json()` (sorted keys, compact
separators, `ensure_ascii=False`) to bytes, calls `store.append(payload, keys={...
AUDIT_INDEX_FIELDS ...})`, stamps the returned `seq`/`ts_wall`, and returns the seq. `ts_mono` is set
by the caller (the gateway, at decision time) so it reflects hot-path timing, not append time.

**Query API.** Filters map directly to `store.query(equals=...)`; time bounds map to `start_ts/
end_ts`; `after_seq`+`limit` page. `next_after_seq` is the last returned record's seq when
`len(records) == limit`, else `None`. Ordering is always seq-ascending (== append/wall-clock order).

**Replay API.** `replay(seq, policy=...)` loads the record and re-derives the `PolicyDecision` by
feeding the record's **stored signals** (identity_id, identity_kind, team, tags, route_pre_policy,
score, detector_hits — the principal's attribution is stored at decision time precisely so replay
never consults the current identity registry, which may have changed) into `policy.evaluate`. Because policy evaluation is
pure and deterministic (WF-ADR-0001), same record + same policy version → byte-identical decision.
**Honest scope:** replay reconstructs the *policy decision given the recorded detector/identity/
route signals*; it cannot re-run detectors (no prompt text is stored, by constraint), so a replay is
faithful to the decision, not a re-detection. This is stated so the compliance claim (WF-ROADMAP-0011
§5) is not over-sold.

**Incremental re-eval API — log-size-independent.** `reeval` re-scores a bounded record set against
a new/edited policy:
- `changeset=[seq, ...]` → re-evaluates exactly those records (the ~1,000-request changeset case):
  bounded reads by primary key, O(changeset), independent of total N.
- `match={field: value}` (e.g. `{"policy_id": "p-finance"}` or `{"route": "cloud"}`) → uses the
  partitioned index to fetch only records that the changed rules could apply to, bounded by the
  index slice, not a full scan. A policy edit re-evaluates only records whose stored signals index
  under the edited rules' match dimensions.
Either way the work is bounded by the matched set, giving < 5 s at both 1M and 10M.

### 3. Policy engine — `wayfinder_router/policy.py`

```python
POLICY_SCHEMA_VERSION: int = 1

# The gateway's existing verb vocabulary (gateway.py) plus the content verbs (WF-ROADMAP-0011 §1).
VERBS: tuple[str, ...] = ("route", "pin", "degrade", "throttle",
                          "clamp", "redact", "warn", "log", "block", "deny")
# Terminal-verb precedence, HIGH → LOW. The terminal decision is the applied rule minimizing
# (VERB_RANK[verb], order_key); order_key is unique so the argmin is unique — ties impossible.
VERB_PRECEDENCE: tuple[str, ...] = ("deny", "block", "clamp", "degrade",
                                    "pin", "throttle", "redact", "warn", "log", "route")

class PolicyError(Exception): ...

@dataclass(frozen=True)
class MatchCondition:
    # Every field defaults to the empty set / None = wildcard. A present clause must match (AND
    # across clauses). Sets match by membership; score_min/max are inclusive bounds.
    identity_ids: frozenset[str] = frozenset()
    identity_kinds: frozenset[str] = frozenset()      # human|agent|service|anonymous
    teams: frozenset[str] = frozenset()
    tags_any: frozenset[str] = frozenset()            # matches if the principal has ANY of these
    tags_all: frozenset[str] = frozenset()            # matches if the principal has ALL of these
    models: frozenset[str] = frozenset()              # inbound/requested model
    routes: frozenset[str] = frozenset()              # route_pre_policy
    detectors_any: frozenset[str] = frozenset()       # a hit for ANY of these detector names
    detectors_all: frozenset[str] = frozenset()
    score_min: float | None = None
    score_max: float | None = None
    def matches(self, ctx: "PolicyContext") -> bool: ...

@dataclass(frozen=True)
class Rule:
    id: str                       # unique within a policy (enforced at compile)
    priority: int                 # lower = evaluated earlier (ascending)
    enabled: bool
    match: MatchCondition
    verb: str                     # in VERBS
    args: Mapping[str, str]       # verb params: {"target": ...} pin/clamp/degrade; {"message": ...} block/deny
    def order_key(self) -> tuple[int, str]:            # (priority, id) — total order, ties impossible

@dataclass(frozen=True)
class PolicyContext:
    identity_id: str
    identity_kind: str
    team: str | None
    tags: frozenset[str]
    vkey_id: str | None
    model: str                    # inbound requested model
    route: str                    # route_pre_policy (the gateway's pre-policy choice)
    score: float
    detector_names: frozenset[str]   # names present in detector_hits

@dataclass(frozen=True)
class BlockOutcome:
    status: int                   # 403 for deny, 403 for block (structured); distinct verb recorded
    message: str
    verb: str                     # "block" | "deny"

@dataclass(frozen=True)
class PolicyDecision:
    policy_hash: str
    rule: str | None              # terminal rule id (None when no rule matched)
    verb: str                     # terminal verb ("route" when no rule matched)
    route: str                    # final route after route-mutating verbs
    verbs: tuple[str, ...]        # all applied verb names, in total-order sequence
    applied_rules: tuple[str, ...]
    block: BlockOutcome | None    # set iff terminal verb ∈ {block, deny}
    redactions: tuple[str, ...]   # detector names to redact from the forwarded body (sorted)
    throttle: bool                # a throttle verb applied
    def to_headers(self) -> dict[str, str]: ...   # x-wayfinder-policy, x-wayfinder-policy-rule, x-wayfinder-policy-verb

@dataclass(frozen=True)
class CompiledPolicy:
    policy_id: str
    policy_hash: str              # sha256(canonical(sorted enabled rules))[:12]
    rules: tuple[Rule, ...]       # pre-sorted ascending by order_key; disabled rules excluded
    def evaluate(self, ctx: PolicyContext) -> PolicyDecision: ...

def compile_policy(rules: Iterable[Rule], *, policy_id: str = "default") -> CompiledPolicy: ...
def policy_from_toml(text: str) -> CompiledPolicy: ...   # raises PolicyError on schema/verb errors
```

**Deterministic precedence — total order, ties impossible by construction.** At compile, rules are
sorted ascending by `order_key = (priority, id)`. Because `id` is unique within a policy (enforced —
duplicate id → `PolicyError`), `order_key` is a strict total order; no two rules ever compare equal.
`evaluate` walks the sorted enabled rules once, collects every rule whose `match.matches(ctx)` is
true (these are `applied_rules`, in order), and picks the **terminal rule** as the argmin over
applied rules of `(VERB_PRECEDENCE.index(verb), order_key)`. The argmin is unique. Route-mutating
verbs (`pin`/`clamp`/`degrade` → `args["target"]`; `block`/`deny` → forwarded route irrelevant) set
`route`; content verbs (`redact`/`warn`/`log`) accumulate independently of the terminal choice
(`redactions` gathers every applied `redact` rule's target detector names). `throttle` sets the
throttle flag. When no rule matches, the decision is `verb="route", rule=None, route=ctx.route`.
This is the WF-ADR-0035 allowlist-clamp's successor: the clamp folds in as a generated `clamp` rule
and remains "the final word on the route" via its precedence.

**Compile-once.** `compile_policy`/`policy_from_toml` run at gateway load and on hot-reload only;
`evaluate` allocates nothing per-call beyond the decision object and never recompiles a pattern or
re-sorts. `policy_hash` is stamped on `x-wayfinder-policy` and into every `AuditRecord`.

**TOML representation** (config.py idiom — `[gateway.keys.<id>.budget]`-style nested tables):

```toml
[policy]
enabled = true                     # DEFAULT false — absent/false ⇒ policy stage skipped entirely
id = "org-baseline"

[policy.rules.block-secrets]
priority = 10
enabled = true
verb = "block"
message = "Requests containing credentials are not permitted."
detectors_any = ["aws_access_key", "github_pat", "slack_token", "private_key"]

[policy.rules.redact-pii]
priority = 20
verb = "redact"
detectors_any = ["email", "us_ssn", "credit_card"]

[policy.rules.finance-pin]
priority = 30
verb = "pin"
target = "cloud-approved"
teams = ["finance"]
```

`priority` defaults to 100, `enabled` to true. Verb args (`target`, `message`) are read from the
rule table's flat keys; match fields are the `MatchCondition` field names.

### 4. Product detectors — `wayfinder_router/detectors.py`

Productizes `benchmarks/detectors.py` with **byte-identical patterns and validators** so the frozen
benchmark oracle (micro P 0.812 / R 0.867, `benchmarks/detector-validation-results.md`) is reproduced
by construction. Same `Detector` shape (name, compiled pattern, optional validator).

```python
@dataclass(frozen=True)
class DetectorHit:
    name: str
    count: int
    spans: tuple[tuple[int, int], ...]   # (start, end) char offsets — NEVER matched text

@dataclass(frozen=True)
class Detector:
    name: str
    pattern: re.Pattern[str]
    validator: Callable[[str], bool] | None = None
    def detects(self, text: str) -> bool: ...            # identical semantics to benchmarks.Detector
    def scan(self, text: str) -> DetectorHit | None: ... # None when no validated match; else count+spans

class DetectorRegistry:
    def __init__(self, detectors: Iterable[Detector]) -> None: ...   # patterns compiled once here
    def scan(self, text: str) -> tuple[DetectorHit, ...]: ...        # sorted by name; only firing detectors
    def names(self) -> tuple[str, ...]: ...
    @classmethod
    def default(cls) -> "DetectorRegistry": ...                       # the frozen product set

# Byte-identical to benchmarks/detectors.py: email, us_ssn, aws_access_key, github_pat,
# slack_token, private_key, credit_card (validator=_is_card), high_entropy_hex.
DETECTORS: tuple[Detector, ...] = (...)
DETECTORS_BY_NAME: dict[str, Detector] = {d.name: d for d in DETECTORS}
```

**`scan` builds spans from `match.start()`/`match.end()` only** — the matched substring is never
retained, so a `DetectorHit` cannot carry secret text (enforced by the type: `spans` are `int`
pairs, and there is no text field). `count` is the number of validated matches.

**Relationship to the benchmark oracle (frozen quality gate).** The product `DETECTORS` must, for
every name in `benchmarks/detectors.DETECTORS`, contain a `Detector` with an identical
`pattern.pattern` string and the same validator behavior. A contract test imports
`benchmarks.detector_validation.evaluate` and `benchmarks.detector_validation.full_corpus`, runs
them once with `benchmarks` detectors and once with `wayfinder_router.detectors.DETECTORS`, and
asserts the product set's micro precision ≥ 0.812 and micro recall ≥ 0.867 and per-detector P/R ≥
the committed table — zero regression. `_luhn_ok`/`_is_card` are ported verbatim (private helpers).

### 5. Identity model — `wayfinder_router/identity.py`

Gives `VirtualKey.tags` its consumer at last: virtual keys are the v1 identity source; every request
resolves to exactly one principal or the anonymous principal.

```python
ANONYMOUS_ID: str = "anonymous"
IDENTITY_KINDS: tuple[str, ...] = ("human", "agent", "service", "anonymous")

class IdentityError(Exception): ...

@dataclass(frozen=True)
class Principal:
    id: str
    kind: str                      # in IDENTITY_KINDS
    team: str | None = None
    tags: tuple[str, ...] = ()     # residual tags (after team:/kind: are consumed)

ANONYMOUS: Principal = Principal(id=ANONYMOUS_ID, kind="anonymous")

class IdentityRegistry:
    def __init__(self, principals: Iterable[Principal]) -> None: ...     # by-id map; duplicate id → IdentityError
    def resolve(self, *, vkey_id: str | None,
                vkey_tags: tuple[str, ...] = ()) -> Principal: ...
    def get(self, principal_id: str) -> Principal | None: ...
    @classmethod
    def from_toml(cls, text: str) -> "IdentityRegistry": ...

# Tag convention consumed here (and nowhere else today): "team:<x>" → team, "kind:<x>" → kind,
# everything else → residual tags. This is the sole consumer of VirtualKey.tags.
def principal_from_vkey(vkey_id: str, tags: tuple[str, ...]) -> Principal: ...
```

**Attribution rule (exactly one principal, always).** `resolve`:
1. `vkey_id is None` (keys unconfigured, or unauthenticated open gateway) → `ANONYMOUS`.
2. `vkey_id` maps to a configured `[identity.principals.<id>]` → that `Principal`.
3. `vkey_id` present but unmapped → `principal_from_vkey(vkey_id, vkey_tags)`: id = `vkey_id`,
   `kind` from a `kind:<x>` tag (default `"human"`), `team` from a `team:<x>` tag, residual tags
   kept. This is where `VirtualKey.tags` (parsed at `gateway.py:638`, unused until now) becomes
   load-bearing — feeding both policy match conditions and audit attribution.

**TOML representation:**

```toml
[identity]
enabled = true                     # DEFAULT false

[identity.principals.alice]
kind = "human"
team = "finance"
tags = ["role:analyst"]

[identity.principals.nightly-agent]
kind = "agent"
team = "platform"
```

A principal id maps to a virtual-key id (v1: the key id *is* the principal id when a
`[identity.principals.<key-id>]` table exists; otherwise the synthesized path applies).

### 6. Gateway integration points

**The single new stage.** In `chat_completions`, immediately after the per-key allowlist clamp (the
current "final word on the route", `gateway.py:2023–2027`) and before `wf_headers` is built
(`gateway.py:2029`), insert the governance stage — gated so it is a literal no-op when unconfigured:

```
... clamp → chosen/mode final ...
if governance_active:                        # gw.policy is not None and gw.policy.enabled
    principal = identity_registry.resolve(vkey_id=key_id, vkey_tags=key_cfg.tags if key_cfg else ())
    hits = detector_registry.scan(prompt_all)          # in-process only; prompt_all never stored
    ctx = PolicyContext(identity_id=principal.id, identity_kind=principal.kind, team=principal.team,
                        tags=frozenset(principal.tags), vkey_id=key_id, model=body.get("model"),
                        route=chosen, score=decision.score, detector_names={h.name for h in hits})
    pdecision = compiled_policy.evaluate(ctx)
    # apply verbs: deny/block → structured 403; clamp/degrade/pin → chosen = target; throttle → 429;
    #              redact → rewrite forwarded body copy; warn/log → headers + audit only
    chosen, mode = _apply_policy_verbs(pdecision, chosen, mode)
    wf_headers.update(pdecision.to_headers())
if audit_active:                             # gw.audit is not None and gw.audit.enabled
    audit_log.append(AuditRecord(... route=chosen, route_pre_policy=route_pre_policy,
                                 detector_hits=hits, policy_id=..., policy_hash=..., rule=...,
                                 verbs=pdecision.verbs, request_digest=cache.cache_key(chosen, body), ...))
```

The audit-append is inside the p99 < 1 ms budget because `store.append` is buffered (durability
barrier is deferred). Detector scan over `prompt_all` reuses the already-extracted prompt; the
registry is compiled once at startup (like `breaker`/`response_cache`/`rate_limiter`, instantiated
in the `create_app` body near `gateway.py:1550`).

**Config tables that switch it on (default OFF ⇒ byte-identical to today):**
- `[policy]` (`enabled=false` default) + `[policy.rules.<name>]`
- `[identity]` (`enabled=false` default) + `[identity.principals.<id>]`
- `[audit]` (`enabled=false` default, `dir=<path>`, `durability="buffered"`)
- `[gateway.store]` (`backend="memory"` default | `"disk"`, `dir=<path>`) — **the single knob**
  that flips Movement A surfaces onto disk backends (§7). Absent ⇒ current in-RAM behavior.

When `[policy]`/`[audit]` are absent or disabled, `governance_active`/`audit_active` are false and
the entire block is skipped — the WF-ROADMAP-0012 zero-regression covenant.

**Metrics endpoint gains (additive only — new counters, existing ones untouched):**
`wayfinder_router_policy_evaluations_total`, `wayfinder_router_policy_verb_total{verb=...}`,
`wayfinder_router_policy_blocks_total`, `wayfinder_router_policy_redactions_total`,
`wayfinder_router_detector_hits_total{detector=...}`,
`wayfinder_router_audit_appends_total`, `wayfinder_router_audit_append_latency_seconds` (histogram),
`wayfinder_router_identity_attributions_total{kind=...}`. All rendered only when governance is
active, mirroring `key_requests` being emitted only when non-empty.

### 7. Movement A rehousing (behind unchanged contracts)

All four are selected by `[gateway.store].backend="disk"`; `"memory"` (default) is the current code
path, so nothing observable changes by default. Each preserves its exact class/function contract.

**(a) Disk-backed `ResponseCache`.** *Unchanged contract:* `get(key)`, `put(key, entry)`, `clear()`,
`reconfigure(*, enabled, max_entries, max_bytes, ttl)`, `stats()`, and the `CachedResponse` shape
(`cache.py:112–219`). *File format:* an append-only body segment file (`cache/bodies.log`, the raw
response bytes framed like `store`) plus a SQLite index (`cache/index.db`:
`entries(key TEXT PRIMARY KEY, off, len, status, content_type, prompt_tokens, completion_tokens,
estimated, stored_at REAL, mru INTEGER)`). `get` reads the body by offset + verifies TTL; `put`
appends the body and upserts the index row. *Bound:* `max_entries` (LRU by `mru`) **and**
`max_bytes` (sum of `len`) — eviction deletes LRU index rows; the body log compacts when dead bytes
exceed 50%. `reconfigure(enabled=False)` truncates both files (the privacy purge is preserved).

**(b) Indexed/bounded feedback store.** *Unchanged contract:* module functions
`record_label(log_path, text, label)` and `read_labels(log_path) -> list[dict]` (`feedback.py`).
*File format:* the JSONL log is kept verbatim (calibration still reads `{"text","label"}` lines),
plus a sidecar `<log_path>.idx` of `struct`-packed `(offset, length)` per line. `record_label`
appends the line and one index entry; `read_labels()` with no args reads wholesale as today (same
return), while a new keyword-only `read_labels(log_path, *, offset=0, limit=None)` pages via the
sidecar without reading the whole file — additive, so existing callers are byte-identical. *Bound:*
the sidecar makes reads O(page) instead of O(file); the log itself stays append-only and unbounded
(calibration is a full replay by design, WF-ADR-0006).

**(c) Persistent rate-limiter / breaker state.** *Unchanged contracts:* `RateLimiter.admit`,
`add_tokens`, `reconfigure`, `stats`, `snapshot` (`ratelimit.py`); `CircuitBreaker.allow`,
`is_open`, `record` (`reliability.py`). *File format:* a single best-effort `state.db` SQLite
(`ratelimit(scope TEXT PRIMARY KEY, window_id, requests, tokens)`, `breaker(target TEXT PRIMARY KEY,
fails, opened_at)`), written on each transition inside the existing lock and reloaded on
construction — the same best-effort, never-raise-into-the-request-path posture as
`SavingsLedger.save/load`. *Bound:* O(#scopes + #targets); rows for expired windows are lazily
overwritten. Purpose is survival across restart; the observable admission/breaker behavior is
unchanged.

**(d) On-disk `SavingsLedger` / `Budget` day buckets.** *Unchanged contract:* `record`, `period`,
`totals`, `spent`, `save`, `load`, `to_dict`, `from_dict` and their exact return dicts
(`pricing.py:180–340`). *File format:* the in-RAM `days` dict is backed by SQLite
(`buckets(day TEXT, scope TEXT, route TEXT, n, realized, baseline, savings, tokens, estimated_n,
PRIMARY KEY(day, scope, route))`, where `scope ∈ {"", vkey}`); `record` upserts, `spent`/`period`
aggregate by SQL. *Bound:* `max_days` retained (old days pruned as today), rows ≤ max_days × routes ×
keys. `Budget` enforcement is unchanged — it still reads `ledger.spent(window, vkey=...)`, now
answered from disk. Default `"memory"` keeps the current dict + JSON snapshot.

### 8. Scaling story and honest risks

**Why queries stay flat 1M→10M.** Records live in time-partitioned segments; each segment's SQLite
shard has B-tree secondaries on `ts_wall` and every index field. A filtered, paginated query fans
out only to the shards whose time range overlaps the window (all shards for a pure identity/policy/
route filter), does one indexed probe per shard, merges by seq, and returns a `limit`-bounded page.
Cost is O(overlapping-shards × log(shard-size) + limit) — dominated by the fixed page size and a
shard count that grows ~linearly but contributes only microsecond probes, so wall time is flat
within noise against the 100 ms budget as N goes 1M→10M.

**Why re-eval is changeset-local.** `reeval` reads only the changeset seqs (primary-key reads) or
the index slice a changed rule can match — never a full scan — so its cost tracks the changeset
size, not N. A 1,000-record changeset is 1,000 keyed reads + 1,000 pure `evaluate` calls ⇒ well
under 5 s at both 1M and 10M.

**Why rebuild parallelizes.** Sealed segments are immutable and independent; `rebuild` assigns
disjoint segment sets to `workers` processes that build their shards with no shared writer and no
lock, so throughput scales ~linearly to 4 cores. It is the one path allowed to grow with N.

**Risks and mitigations.**
- *fsync tail latency* — the sub-ms append depends on deferring durability. Mitigation: buffered
  append + `synchronous=NORMAL` + a size/interval barrier; `durability="strict"` is available where
  per-record durability is required (and the p99 gate is then measured separately). The honest
  statement: buffered mode can lose the last <4 MiB on a hard crash; the log is never torn.
- *SQLite writer contention* — only the **live** shard is ever written, and only by the single
  append path; all sealed shards are read-only, so query fan-out never contends with append. WAL
  lets readers run concurrently with the one writer.
- *GIL* — rebuild uses `multiprocessing` (separate interpreters), not threads. On the hot path, the
  work that matters (`sqlite3` INSERT, file `write`, `re` scanning) executes in C and releases the
  GIL; detectors and policy are compiled once, so no per-request Python recompilation competes for
  it.
- *RSS* — log + shards + bodies live on disk; RAM holds only bounded mmap LRU pages, bounded SQLite
  page caches (`PRAGMA cache_size` capped per shard), the small `recent` ring, and compiled
  detectors/policy — keeping working-set RSS ≤ ~10 GB with the data on disk.

## Contracts

The invariants a spec-first contract test asserts (each is a failing test before implementation):

1. **Import contract.** `import wayfinder_router` imports none of `rich`, `textual`, `fastapi`,
   `httpx`, and none of `store`/`audit`/`policy`/`detectors`/`identity` (they are lazily reachable
   only). A test asserts the modules are absent from `sys.modules` after `import wayfinder_router`.
2. **Store durability & recovery.** After `append` + `flush` + simulated crash (truncate mid-frame),
   re-open recovers all flushed records, drops the torn tail, and rebuilds the shard; `query`
   returns the recovered records. Records past a bad crc are never returned.
3. **Store query flatness.** `query(equals={...}, limit=L)` returns ≤ L seq-ascending `Location`s and
   its result set is identical whether the store holds 1M or 10M records (correctness invariant that
   underwrites the perf gate).
4. **Audit metadata-only.** No `AuditRecord` field, and no bytes written by `AuditLog.append`,
   contain prompt text or matched detector text; `DetectorHit` carries only `name/count/spans:int`.
   A test scans the on-disk payload for planted secret text and asserts absence.
5. **Audit replay determinism.** `replay(seq, policy=p)` == the `PolicyDecision` reconstructed from
   the record's stored signals, byte-for-byte, across repeated calls and process restarts.
6. **Re-eval boundedness.** `reeval(changeset=[...])` reads exactly the changeset records (asserted
   via a counting store shim); result count == changeset size.
7. **Policy total order.** `compile_policy` rejects duplicate rule ids (`PolicyError`); `evaluate`'s
   terminal rule is the unique argmin of `(VERB_PRECEDENCE.index(verb), order_key)`; two rules never
   tie. `policy_hash` is stable across reorderings of the input rule list (canonicalized).
8. **Policy verb outcomes.** Each verb maps to its documented outcome (deny/block → `BlockOutcome`
   403; pin/clamp/degrade → `route` mutation to `args["target"]`; redact → `redactions`; throttle →
   `throttle=True`; warn/log → headers/audit only; no match → `verb="route"`, `route=ctx.route`).
9. **Detector oracle reproduction.** `wayfinder_router.detectors.DETECTORS` reproduces
   `benchmarks/detector-validation-results.md` micro P ≥ 0.812, R ≥ 0.867, and per-detector P/R ≥ the
   committed table, on `benchmarks.detector_validation.full_corpus`.
10. **Identity totality.** `resolve` returns exactly one `Principal` for every input; `vkey_id=None`
    → `ANONYMOUS`; unmapped `vkey_id` with `["team:x","kind:agent"]` tags → principal with
    `team="x", kind="agent"`.
11. **Zero-regression covenant.** With `[policy]`, `[identity]`, `[audit]` absent and
    `[gateway.store]` absent/`memory`, the existing gateway test suite is byte-identical (the new
    stage is skipped; Movement A uses the in-RAM path).
12. **Movement A contract preservation.** Each disk backend passes the *existing* contract tests of
    `ResponseCache`/feedback/`RateLimiter`/`CircuitBreaker`/`SavingsLedger` unmodified, plus a
    parametrized backend-equivalence test (memory vs disk produce identical observable results).

## Consequences

**Positive.** The governance plane's central claim — "it can prove what it did, without the data
leaving the building" (WF-ROADMAP-0011) — becomes literally true: a deterministic, offline,
model-call-free decision path with a metadata-only, replayable audit trail. `VirtualKey.tags` gains
its long-deferred consumer. The storage substrate is one design (`RecordStore`) reused by audit and
(optionally) the Movement A surfaces, so there is one crash-recovery story to test. Everything is
stdlib and lazily reachable, so the import contract and WF-ADR-0001 guard stay green.

**Negative / costs.** The segmented-log + SQLite-shard substrate is genuinely more code than a
single SQLite table (the fallback), and its crash-recovery/rebuild paths carry real test surface.
Buffered durability trades a bounded worst-case data loss (< `fsync_bytes`) for the sub-ms append —
an explicit, documented contract, not a silent one. Replay is faithful to the *decision given
recorded signals*, not a re-detection (no text is stored, by constraint) — stated so §5's compliance
claim is not over-sold.

**Neutral.** Movement A's disk backends default off; adopting them is an operational choice
(`[gateway.store].backend="disk"`) that trades a little latency for durability and a bounded RSS.

## Non-goals

- **No sharding, no external datastore, no multi-node.** One node, on disk, stdlib only
  (WF-ROADMAP-0012; WF-ADR-0001). SQLite is embedded, not a service.
- **No model call anywhere on the decision or policy path**, and no egress (WF-ADR-0001,
  WF-ADR-0043). Detectors are deterministic regex + cheap validators; any future model-backed
  scanner is local, off-path, opt-in.
- **No raw prompt or matched-text storage**, ever, in the audit log (WF-ROADMAP-0012).
- **No change to any observable existing behavior** with the governance tables absent and
  `[gateway.store]` on `memory` (the zero-regression covenant).
- **No SCIM / OIDC / fleet enrollment / hash-chain / chargeback here** — those are WF-ROADMAP-0011's
  later initiatives, designed when they are reached; this design is the single-node spine they
  build on.
- **No wall-clock speedup claim for the scoring path** (WF-ROADMAP-0010; the scan-bound profile is
  settled). The perf claims here are for the *added* governance work.

## Related

- WF-ROADMAP-0012 — the single-node governance-spine mission (constraint ledger + target table)
- WF-ROADMAP-0011 — the governance plane this spine serves (policy verbs, identity, audit, replay)
- WF-ROADMAP-0010 — the evidence standard (rerunnable claims, held-out folds)
- WF-ADR-0001 — the frozen constitution (offline, deterministic, sub-ms, no egress)
- WF-ADR-0043 — Wayfinder's own model use is local/in-container (bounds any future scanner)
- WF-ADR-0006/0031/0032/0033/0034/0035 — the stateful surfaces Movement A rehouses and the
  per-identity gate precedents the policy engine generalizes
- WF-DESIGN-0007 (savings ledger), WF-DESIGN-0008 (metrics/observability), WF-DESIGN-0010
  (reliability primitives) — the invocation-layer machinery the audit record faithfully captures
- `benchmarks/detectors.py`, `benchmarks/detector_validation.py`,
  `benchmarks/detector-validation-results.md` — the frozen detector quality oracle

## Appendix: spec-first contract-test file manifest (checkpoint approval list)

One line per proposed **new** test file (additive-only, per the Examiner extension protocol); the
count is the approximate number of tests. These are written from this design and approved as a unit
before any builder builds to them.

- `tests/test_store_append_read.py` — RecordStore append/read/read_at/scan/high_water round-trips,
  frame crc, `Location` correctness (~14 tests).
- `tests/test_store_durability_recovery.py` — buffered vs strict durability, flush barrier, torn-tail
  truncation, missing/corrupt-shard rebuild, crash-recovery contract (~12 tests).
- `tests/test_store_query_partitioned.py` — time-range + `equals` fan-out, pagination
  (`after_seq`/`limit`), seq-ascending order, size-invariant result identity (1M-vs-10M correctness
  shim) (~12 tests).
- `tests/test_store_rebuild_parallel.py` — `rebuild(workers=N)` reproduces indexes byte-for-byte vs
  serial, `RebuildReport` fields, multiprocessing determinism (~8 tests).
- `tests/test_audit_record_schema.py` — `AuditRecord.to_json/from_json`, `schema_version` gate,
  metadata-only invariant (planted-secret scan of payload), `DetectorHit` shape (~14 tests).
- `tests/test_audit_query_replay.py` — `AuditLog.append/get/query` filters + pagination, `replay`
  determinism across restart, replay-scope honesty (~14 tests).
- `tests/test_audit_reeval.py` — `reeval(changeset=...)` and `reeval(match=...)` boundedness (counting
  shim), `ReevalResult.changed` semantics, log-size independence (~10 tests).
- `tests/test_policy_compile_order.py` — duplicate-id rejection, `order_key` total order,
  `policy_hash` stability/canonicalization, compile-once (no per-eval recompile) (~12 tests).
- `tests/test_policy_evaluate_verbs.py` — `MatchCondition` AND/wildcard/any/all semantics, terminal
  argmin precedence, every verb's outcome, `to_headers`, no-match default (~20 tests).
- `tests/test_policy_toml.py` — `policy_from_toml` parsing, defaults (priority/enabled), verb-arg
  extraction, error types on bad schema/verb (~12 tests).
- `tests/test_detectors_product.py` — `Detector.scan` spans/count (no text), `DetectorRegistry`
  compile-once + sorted output + `default()`, pattern parity with `benchmarks` (~14 tests).
- `tests/test_detectors_oracle.py` — product set reproduces committed micro + per-detector P/R ≥
  baseline on `full_corpus`, zero regression (~6 tests).
- `tests/test_identity_resolve.py` — `resolve` totality (one principal always), anonymous fallback,
  configured-principal lookup, `team:`/`kind:` tag consumption, duplicate-id error (~12 tests).
- `tests/test_gateway_governance_integration.py` — new stage placement (after clamp, before
  headers), block/deny 403, pin/clamp/degrade route mutation, throttle 429, redact forwarded-body
  rewrite, audit-append on the path, additive metrics, **zero-regression when tables absent**
  (~20 tests).
- `tests/test_movementA_cache_disk.py` — disk `ResponseCache` passes existing get/put/clear/
  reconfigure/stats contract + memory-vs-disk equivalence + purge-on-disable (~12 tests).
- `tests/test_movementA_feedback_indexed.py` — indexed `read_labels`/`record_label` parity + paged
  read via sidecar + wholesale-read equivalence (~8 tests).
- `tests/test_movementA_ratelimit_breaker_persist.py` — persisted RateLimiter/CircuitBreaker survive
  restart, contract unchanged, best-effort never-raise (~10 tests).
- `tests/test_movementA_savings_disk.py` — disk `SavingsLedger` record/period/totals/spent/save/load
  parity with in-RAM + `Budget` enforcement unchanged (~12 tests).

Total: ~18 new test files, ~236 tests, spanning store / audit / policy / detectors / identity /
gateway-integration / Movement-A rehousing.
