# wayfinder-routing-core

The one authoritative, deterministic Wayfinder routing implementation.

This crate may depend only on portable parsing, serialization, and error
libraries. Production code here must not perform filesystem, network, process,
async-runtime, Keychain, provider, UI, or Apple-framework work.

The gateway and generated Apple bindings consume this crate and the same golden
fixtures. Platform hosts supply typed values from
`wayfinder-runtime-contracts`; they do not reimplement scoring or tier
selection.
