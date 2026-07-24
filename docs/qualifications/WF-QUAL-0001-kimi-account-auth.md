---
schema_version: 1
id: WF-QUAL-0001
type: provider-qualification
status: blocked-pending-provider-approval
date: 2026-07-24
tags: [kimi, oauth, device-code, account, ios, provider]
---

# Kimi account authentication qualification

## Decision

Kimi account authentication is **blocked pending provider approval**.

Wayfinder v0.2.0 may implement Moonshot/Kimi Platform API-key support. It must
not display or ship “Sign in with Kimi” until the unresolved gates below are
closed with provider-owned documentation or written approval.

## Verified public evidence

As checked on 2026-07-24:

- Kimi Code documents `kimi login` as an RFC 8628 device-code flow.
- The command requests a device authorization, presents a verification URL and
  user code, polls until completion, and persists the resulting token for the
  Kimi Code client.
- Kimi documents Kimi Code membership separately from the Kimi Platform API.
- The official Kimi Code repository is publicly available.

This proves that Kimi operates an account-authenticated first-party CLI flow. It
does not prove that:

- third-party clients may use the same client identifier;
- Kimi Code membership authorizes Wayfinder model execution;
- the token audience/scopes authorize a documented general model endpoint;
- the endpoint, required headers, catalog, refresh, revocation, and usage-limit
  contracts are available for a third-party App Store client.

## Required approval gates

| Gate | Current result |
| --- | --- |
| Provider-approved Wayfinder client ID or explicit public-client permission | Blocked |
| Documented token audience and scopes for intended model execution | Blocked |
| Documented/approved model and discovery endpoints | Blocked |
| Kimi-specific required headers and client identity contract | Blocked |
| Refresh, revocation, expiry, usage-limit, and account-state contract | Blocked |
| Regional and App Review implications | Blocked |
| Product/legal confirmation that membership permits this client | Blocked |

## Implementation rule

No engineer may copy client identifiers, headers, tokens, device identifiers,
or private endpoints from Kimi Code. Wayfinder must identify itself truthfully.
Reverse engineering is not a qualification artifact.

If approval arrives, implementation still lands after:

- the generic account framework and fake OAuth server harness;
- device-code pending, slow-down, expiry, denial, cancel, and success tests;
- Keychain and secret-leak tests;
- model catalog and usage-limit fixtures;
- physical-device authorization evidence;
- product copy that distinguishes subscription from API-metered usage.

## Sources

- Kimi Code command reference:
  https://www.kimi.com/code/docs/en/kimi-code-cli/reference/kimi-command
- Kimi Code membership guide:
  https://www.kimi.com/help/kimi-code/membership-guide
- Official Kimi Code repository:
  https://github.com/MoonshotAI/kimi-code
