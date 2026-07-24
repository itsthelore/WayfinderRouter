---
schema_version: 1
id: WF-ADR-0049
type: decision
status: accepted
date: 2026-07-24
tags: [ios, ipados, swiftdata, persistence, conversations, privacy]
---

# Use SwiftData behind a conversation-store boundary

## Context

Wayfinder mobile must own threads and drafts without a Mac, gateway, or cloud
service. Phase 2 requires restoration, retention, deletion, export, and a
migration seam before provider execution is added.

The open implementation choice was SwiftData versus a directly managed SQLite
store. The app also needs deterministic domain tests and must not allow storage
types to spread through SwiftUI or provider code.

## Decision

Use SwiftData as the v0.2 native storage engine behind an asynchronous
`ConversationStore` protocol.

SwiftUI and `AppModel` consume Codable, Sendable snapshots. They do not import
SwiftData, fetch persistence models, or own a model context. A dedicated model
actor serializes all SwiftData access.

The first schema stores:

- one atomic, versioned payload per conversation;
- bounded thread metadata required for chronological lists;
- one workspace record containing the active thread and composer draft.

Messages and compact route receipts are encoded inside the conversation
payload. This avoids a prematurely complex object graph while provider and
streaming contracts are still evolving. It also makes export deterministic and
keeps a future SQLite or CloudKit implementation behind the same protocol.

The SwiftData schema is declared through `VersionedSchema` and
`SchemaMigrationPlan` from its first release. Future schema changes add a new
version and explicit migration stage rather than editing the first schema in
place.

## Stored data

Allowed:

- thread and message identifiers;
- user-visible message content according to retention settings;
- timestamps, roles, and terminal status;
- compact route receipts;
- title, active-thread identity, and composer draft.

Forbidden:

- API keys, access or refresh tokens, OAuth codes, cookies, or authorization
  headers;
- raw provider request/response envelopes;
- hidden reasoning or private chain-of-thought;
- complete provider logs or credential paths.

Credential persistence remains a separate Keychain-only Phase 3 boundary.

## Failure behavior

Production startup attempts to create the durable store. If initialization
fails, Wayfinder uses an in-memory store for the current session and exposes a
sanitized persistence warning. It must not crash, claim data was saved, or
write secrets into the warning.

Individual load/save/delete/export failures preserve the current in-memory UI
state and surface a bounded recovery message.

## Retention, deletion, and export

- retention deletes threads whose `updatedAt` precedes a supplied cutoff;
- deleting a thread also clears the active workspace identity when required;
- deleting all threads clears both conversations and the workspace draft;
- export produces a versioned, sorted JSON envelope containing only allowed
  conversation snapshots;
- import and CloudKit sync remain separate review boundaries.

## Consequences

### Positive

- native storage and migration support with no third-party dependency;
- actor-isolated persistence and straightforward in-memory test containers;
- UI, routing, and provider layers remain storage-engine independent;
- deterministic portable export shape;
- later CloudKit or SQLite implementations can conform to the same protocol.

### Negative

- atomic JSON payloads are not optimized for full-text message queries;
- SwiftData remains an Apple-platform implementation detail;
- a later high-volume search requirement may justify normalized records or a
  SQLite-backed store.

Those costs are acceptable for the first standalone mobile release and can be
changed behind the protocol.

## Rejected alternatives

### SwiftData models directly in SwiftUI

Rejected because it couples views, previews, tests, and navigation state to one
storage engine.

### Direct SQLite now

Rejected because the current scale does not justify custom migrations,
statement management, and concurrency plumbing. SQLite remains a valid future
implementation if search or cross-platform storage requirements demand it.

### UserDefaults or one unversioned JSON file

Rejected because conversation history needs transactional writes, explicit
migration, bounded fetch/delete behavior, and testable failure handling.

## Related

- WF-ADR-0047 — native mobile independence
- WF-ADR-0048 — shared routing core and Apple embedding
- WF-DESIGN-0020 — mobile Chat shell
- WF-ROADMAP-0016 — native mobile v0.2 delivery
