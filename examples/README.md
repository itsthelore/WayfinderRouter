# Integration examples

Recipes for putting a chat UI in front of the Wayfinder gateway. Both rely on the
per-request override (WF-ADR-0011): the OpenAI `model` field is read as a routing
directive (`auto`, `prefer-local`, `prefer-hosted`, or a configured endpoint name),
so a UI's ordinary model dropdown becomes a per-conversation routing-mode picker —
no fork, no custom code. The gateway also serves `GET /v1/models` (WF-ADR-0012), so
the UIs discover those options automatically.

First run the gateway (see the repository README) with a `wayfinder-router.toml`
whose `[gateway.models]` keys are the endpoints you want to pin to (e.g. `local`,
`cloud`).

## LibreChat

Files in this directory:

- `librechat.yaml` — a custom OpenAI-compatible endpoint named "Wayfinder" that
  fetches the routing options from the gateway's `/v1/models`.
- `docker-compose.override.yml` — runs the gateway as a sidecar in LibreChat's own
  Compose stack.

Drop both into your LibreChat checkout (as `./librechat.yaml` and
`./docker-compose.override.yml`), put your routing config in
`./wayfinder-data/wayfinder-router.toml`, then `docker compose up`. Pick "Wayfinder"
as the endpoint; the model dropdown (`auto` / `prefer-local` / `prefer-hosted` / a
pinned endpoint) sets the routing mode for that conversation.

## Open WebUI

No file needed — it's all connection config:

1. Settings → **Connections** → add an **OpenAI API** connection.
2. **Base URL**: your gateway's `…:8088/v1`. **API Key**: any placeholder (the
   gateway ignores it).
3. Open WebUI fetches `/v1/models` and populates the selector with the routing
   options (`auto`, `prefer-local`, `prefer-hosted`, and your configured endpoints).

Those ids then appear in the model selector and route exactly as in LibreChat.

## What still needs the fork

Both UIs give you a per-conversation routing-mode *picker* for free. What neither
gives you is a live **threshold slider** per conversation — injecting a changing
`X-Wayfinder-Threshold` header from the UI needs custom code. That control is the
job of the `wayfinder-chat` fork (WF-ADR-0010); these recipes are the no-fork path
that proves the routing out end to end first.
