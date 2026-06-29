#!/usr/bin/env node
// wayfinder-terminal — the terminal client entry point. Plain JS (no JSX) so it needs no transpile;
// it imports the built app from ../dist/app.js and renders it with Ink. The routing decision is
// the gateway's (WF-ADR-0001) — this client never scores.
import React from 'react';
import {render} from 'ink';
import App from '../dist/app.js';

const args = process.argv.slice(2);
const flag = (name) => args.includes(name);
const opt = (name, fallback) => {
  const i = args.indexOf(name);
  return i >= 0 && args[i + 1] !== undefined ? args[i + 1] : fallback;
};

if (flag('-h') || flag('--help')) {
  process.stdout.write(`wayfinder-terminal — terminal client for the Wayfinder gateway

Usage:
  wayfinder-terminal [--base-url URL] [--rows N] [--cols N]

Options:
  --base-url URL   gateway base URL (default: $WAYFINDER_BASE_URL or http://127.0.0.1:8088)
  --rows N         viewport rows (default: 30)
  --cols N         viewport columns (default: 96)
  -h, --help       show this help

The gateway is the backend; this is a thin client of it. Start a gateway first, e.g.:
  wayfinder-router serve
Then point the client at it:
  npx wayfinder-terminal --base-url http://127.0.0.1:8088
`);
  process.exit(0);
}

const baseUrl = opt('--base-url', process.env.WAYFINDER_BASE_URL || 'http://127.0.0.1:8088');
const rows = Number(opt('--rows', 30)) || 30;
const cols = Number(opt('--cols', 96)) || 96;

render(React.createElement(App, {baseUrl, rows, cols}));
