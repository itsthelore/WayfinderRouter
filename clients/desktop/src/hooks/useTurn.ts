// The turn owner (WF-DESIGN-0012): drives the pure turn machine over the shared
// routeTurnStream. The headers decision paints before the first token (onDecision #1), the
// trailing wayfinder event enriches it (onDecision #2), a reply error keeps the decision —
// and Stop aborts through the AbortSignal. The hook never scores (WF-ADR-0001).
import { useCallback, useReducer, useRef } from "react";
import { routeTurnStream } from "@wayfinder/shared/gateway";
import { initialTurnState, turnReducer, type TurnState } from "@/lib/appState";
import { GATEWAY_BASE } from "@/lib/gateway";

export interface UseTurnOptions {
  baseUrl?: string;
  /** models[0] from useCheapestModel — colours the early headers decision. */
  cheapest?: string | null;
  /** The OfflineToggle's client preference: adds X-Wayfinder-Offline per turn (WF-ADR-0039). */
  offline?: boolean;
}

export interface UseTurn extends TurnState {
  send: (prompt: string, history?: Array<{ role: string; content: string }>) => Promise<void>;
  stop: () => void;
  reset: () => void;
}

export function useTurn({ baseUrl = GATEWAY_BASE, cheapest = null, offline = false }: UseTurnOptions = {}): UseTurn {
  const [state, dispatch] = useReducer(turnReducer, initialTurnState);
  const abortRef = useRef<AbortController | null>(null);

  const send = useCallback(
    async (prompt: string, history: Array<{ role: string; content: string }> = []) => {
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      dispatch({ type: "SUBMIT", prompt });
      try {
        const reply = await routeTurnStream([...history, { role: "user", content: prompt }], {
          baseUrl,
          cheapest,
          signal: controller.signal,
          headers: offline ? { "X-Wayfinder-Offline": "1" } : {},
          onDecision: (decision) => dispatch({ type: "DECISION", decision }),
          onToken: (delta, reply) => dispatch({ type: "TOKEN", delta, reply }),
        });
        dispatch({ type: "DONE", reply });
      } catch (err) {
        dispatch({
          type: "ERROR",
          message: controller.signal.aborted
            ? "stopped"
            : err instanceof Error
              ? err.message
              : String(err),
        });
      }
    },
    [baseUrl, cheapest, offline],
  );

  const stop = useCallback(() => abortRef.current?.abort(), []);
  const reset = useCallback(() => dispatch({ type: "RESET" }), []);

  return { ...state, send, stop, reset };
}
