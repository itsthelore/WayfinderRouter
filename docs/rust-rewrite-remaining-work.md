# Rust rewrite closeout

The rewrite is complete. Rust is the sole router and gateway implementation;
Wayfinder Desktop embeds the native helper and has no Python runtime or fallback
dependency.

The accepted cutover contract is WF-ADR-0046. The completed migration record is
WF-ROADMAP-0014.

Remaining work belongs to the normal product and release backlogs:

1. finish signed and notarized Desktop release evidence on Apple Silicon;
2. continue native Swift Chat, setup, and connection UX;
3. extend Rust-owned commands only when a supported user journey needs them;
4. add any standalone package manager distribution as a separately supported
   native-binary channel;
5. keep CI, containers, fixtures, and release tooling free of Python execution.

The old coexistence commands are intentionally not fallback entry points. An
unsupported command returns a usage error from the Rust binary.
