---
schema_version: 1
id: WF-ROADMAP-0014
type: roadmap
tags: [rust, migration, gateway, router, macos, packaging]
---

# Roadmap: migrate Wayfinder's router and gateway to Rust

## Status

Complete. Rust is the sole production router and gateway implementation
(WF-ADR-0046).

## Delivered

- deterministic routing and configuration crates;
- bounded OpenAI-compatible and Anthropic-compatible HTTP gateway;
- provider translation, streaming, retries, failover, budgets, rate limits,
  cache, metrics, and persistence;
- native service management and desktop-owned setup/configuration commands;
- authenticated macOS credential and Apple Foundation Models XPC clients;
- opt-in bounded ChatGPT account delivery;
- signed-helper capability and architecture verification;
- embedded Apple Silicon gateway packaging for Wayfinder Desktop;
- Rust-owned CI, container, fixtures, and release verification.

## Cutover

The coexistence period ended with WF-ADR-0046:

- the helper no longer delegates commands to another runtime;
- the legacy package, test suite, benchmark executables, PyPI workflow, and
  Python-based container were removed;
- capabilities report `implementation = "rust"` and `gateway_ready = true`;
- unsupported coexistence commands fail closed rather than launching a fallback;
- native Desktop Chat replaces the former terminal and web chat surfaces.

Historical compatibility fixtures remain checked in as migration evidence. They
are immutable input to Rust tests, not executable authority or a distribution
dependency.

## Remaining product work

Future work is ordinary Rust/Swift product development rather than migration:

- expand native configuration and diagnostics where user journeys require it;
- complete physical-device signing, notarization, and release evidence;
- add broader standalone binary distribution only with its own reviewed
  support and rollback contract;
- retain the offline, deterministic, keyless decision invariant.
