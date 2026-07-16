# Apple Foundation Models live integration gate

This gate exercises the real signed Rust-helper-to-XPC-to-Foundation-Models path. It is deliberately
disabled during ordinary builds and CI. It never prints generated content: the JSON report contains
only stable availability categories, completion booleans, event counts, and byte counts.

## Requirements

- Apple Silicon running macOS 26 or newer;
- Apple Intelligence enabled and `SystemLanguageModel.default` available;
- a Developer ID or Apple Development signing identity with a Team ID;
- a signed `Wayfinder.app` containing `Contents/Helpers/WayfinderGateway.app`, whose main
  executable is the genuine Rust gateway and whose `Contents/XPCServices` contains both brokers;
- the Wayfinder menu-bar process closed for app-closed inference evidence.

Ad-hoc signing is insufficient because the XPC service authenticates the helper identifier, Apple
code-signing anchor, and matching Team ID.

## Build and run

Build the release bundle with a real identity:

```sh
CODESIGN_IDENTITY="Apple Development: Example (TEAMID)" \
CODESIGN_TIMESTAMP_OPTION=--timestamp=none \
WAYFINDER_RELEASE_ARCHS=arm64 \
  macos/WayfinderMac/script/build_release_bundle.sh
```

Create a private test copy of the production gateway helper app:

```sh
CODESIGN_IDENTITY="Apple Development: Example (TEAMID)" \
CODESIGN_TIMESTAMP_OPTION=--timestamp=none \
WAYFINDER_APP_BUNDLE="$PWD/macos/WayfinderMac/dist-release/Wayfinder.app" \
  macos/WayfinderMac/script/build_apple_foundation_live_harness.sh
```

Then explicitly enable the live gate and point it at the harness:

```sh
WAYFINDER_RUN_APPLE_FOUNDATION_LIVE=1 \
WAYFINDER_APP_BUNDLE="/private/tmp/WayfinderFoundationLiveHarness.app" \
  macos/WayfinderMac/script/run_apple_foundation_live.sh
```

The harness is a byte-for-byte bundle copy of the production containing gateway helper app, then
re-signed with the supplied identity. The release bundle now uses the same containing-app topology;
the copy merely isolates gated live testing under `/private/tmp`. Signed clean-machine proof
remains part of Step 10.

The wrapper fails before inference when the gate is absent, the machine is not Apple Silicon, the
OS is older than macOS 26, the menu-bar app is open, the bundle is incomplete, or signature checks
fail. The hidden helper command independently requires the same exact environment gate.

## Successful evidence shape

Values vary by model response and are intentionally not golden-tested:

```json
{
  "availability": "available",
  "buffered": {"completed": true, "response_bytes": 42},
  "cancellation": {"observed": true, "requested": true},
  "completed": true,
  "provider": "apple-foundation-models",
  "schema_version": "1",
  "streaming": {"completed": true, "events": 2, "response_bytes": 42}
}
```

Success proves availability, one bounded buffered generation, one ordered terminal-complete stream,
and cancellation observed by the native session while the UI process is closed. A non-available
device exits nonzero with only a stable category such as `model-not-ready` or
`apple-intelligence-not-enabled`.

Do not paste prompts, responses, unified logs containing content, or raw XPC payloads into the
capability matrix or pull request. Record only the command, machine/OS class, signing topology,
timestamp, exit status, and sanitized JSON report.

## Recorded evidence

2026-07-15, Apple Silicon, macOS 27.0, Apple Development signed helper harness, menu-bar process
closed. The gated command exited `0` with this content-free report:

```json
{"schema_version":"1","provider":"apple-foundation-models","completed":true,"availability":"available","buffered":{"completed":true,"response_bytes":23},"streaming":{"completed":true,"events":2,"response_bytes":23},"cancellation":{"requested":true,"observed":true}}
```

The run also exposed and fixed two cancellation races before the successful evidence was recorded:
early idempotent cancellation is retained until task insertion, and all authenticated XPC
connections share one broker task registry. This evidence predates adoption of the same containing
helper-app topology in the production bundle, so a new signed clean-machine run is still required.
