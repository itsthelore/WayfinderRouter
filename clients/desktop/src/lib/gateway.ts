// The one place the popover knows where the gateway lives (WF-ADR-0042: loopback only —
// the CSP pins connect-src to this origin). Everything else takes baseUrl as a parameter
// so tests can point at a mock.
export const GATEWAY_BASE = "http://127.0.0.1:8088";

/** localStorage key: this machine has seen a live gateway (drives first-run vs unreachable). */
export const SEEN_GATEWAY_KEY = "wf.seenGateway";
