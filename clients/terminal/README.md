# wayfinder-terminal

A terminal client for the [Wayfinder router](https://github.com/itsthelore/wayfinder-router)
gateway, built with [Ink](https://github.com/vadimdemedes/ink) (React for the terminal).

Wayfinder's architecture is **one backend, many thin clients**: the gateway makes the routing
decision (offline, deterministic, no model call — [WF-ADR-0001]) and serves replies; clients just
render what it returns. `wayfinder-terminal` is one such client. It **never scores** — every routing
decision shown is the gateway's.

## What you get

- A decision-first chat: each turn is routed inline as `● LOCAL` / `◆ CLOUD` with the score, and
  the reply **streams** in token-by-token.
- `tab` expands the numeric **"why"** breakdown behind a decision.
- Slash-command panels pulling **live** gateway data: `/models`, `/keys`, `/cost`, `/settings`,
  `/threads`, `/help`, and `/init` (scaffold a `wayfinder-router.toml`).
- Routing directives from the composer: `/local`, `/cloud`, `/auto`, `/threshold`, `/scope`, …

## Requirements

A running Wayfinder gateway. Start one from the Python package:

```sh
pipx install "wayfinder-router[gateway]"
wayfinder-router serve            # defaults to http://127.0.0.1:8088
```

## Run

No install — try it straight from npm:

```sh
npx wayfinder-terminal --base-url http://127.0.0.1:8088
```

Or install it:

```sh
npm install -g wayfinder-terminal
wayfinder-terminal --base-url http://127.0.0.1:8088
```

Options: `--base-url URL` (or `$WAYFINDER_BASE_URL`, default `http://127.0.0.1:8088`),
`--rows N`, `--cols N`, `--help`.

## Develop

```sh
npm install
npm run build      # esbuild: src/app.jsx → dist/app.js (react/ink stay external)
npm run smoke      # headless mount check (no gateway needed)
npm start          # build + launch against the default gateway
```

Layout: `src/app.jsx` (the whole UI), `src/gateway.js` (the wire contract —
`POST /v1/chat/completions` with `X-Wayfinder-Debug`, plus `/router/models`), `src/theme.js`
(palette), `bin/wayfinder.js` (CLI entry).

Apache-2.0.

[WF-ADR-0001]: https://github.com/itsthelore/wayfinder-router/blob/main/decisions/WF-ADR-0001-standalone-deterministic-router.md
