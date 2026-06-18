# Integration examples

Recipes for putting a chat UI in front of the Wayfinder gateway. Both rely on the
v0.1.2 per-request override (WF-ADR-0011): the OpenAI `model` field is read as a
routing directive (`auto`, `prefer-local`, `prefer-cloud`, or a configured endpoint
name), so a UI's ordinary model dropdown becomes a per-conversation routing-mode
picker — no fork, no custom code.

First run the gateway (see the repository README) with a `wayfinder-router.toml`
whose `[gateway.models]` keys are the endpoints you want to pin to (e.g. `local`,
`cloud`).

## LibreChat

Files in this directory:

- `librechat.yaml` — a custom OpenAI-compatible endpoint named "Wayfinder", with
  the routing directives listed as selectable models.
- `docker-compose.override.yml` — runs the gateway as a sidecar in LibreChat's own
  Compose stack.

Drop both into your LibreChat checkout (as `./librechat.yaml` and
`./docker-compose.override.yml`), put your routing config in
`./wayfinder-data/wayfinder-router.toml`, then `docker compose up`. Pick "Wayfinder"
as the endpoint; the model dropdown (`auto` / `prefer-local` / `prefer-cloud` / a
pinned endpoint) sets the routing mode for that conversation.

## Open WebUI

No file needed — it's all connection config:

1. Settings → **Connections** → add an **OpenAI API** connection.
2. **Base URL**: your gateway's `…:8088/v1`. **API Key**: any placeholder (the
   gateway ignores it).
3. The gateway exposes no `/v1/models`, so Open WebUI's auto-fetch finds nothing —
   use the connection's **Model IDs** field to list the directives manually:
   `auto`, `prefer-local`, `prefer-cloud`, and any configured endpoint names.

Those ids then appear in the model selector and route exactly as in LibreChat.

## What still needs the fork

Both UIs give you a per-conversation routing-mode *picker* for free. What neither
gives you is a live **threshold slider** per conversation — injecting a changing
`X-Wayfinder-Threshold` header from the UI needs custom code. That control is the
job of the `wayfinder-chat` fork (WF-ADR-0010); these recipes are the no-fork path
that proves the routing out end to end first.
