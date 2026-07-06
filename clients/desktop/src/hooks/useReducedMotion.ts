// prefers-reduced-motion, live (WF-DESIGN-0012). The CSS duration variables are already
// zeroed centrally in globals.css; this hook is for the JS-driven bits (WhyBars' first-reveal
// stagger) that need to know as a boolean.
import { useEffect, useState } from "react";

const QUERY = "(prefers-reduced-motion: reduce)";

export function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(() => window.matchMedia(QUERY).matches);
  useEffect(() => {
    const mql = window.matchMedia(QUERY);
    const onChange = (e: MediaQueryListEvent) => setReduced(e.matches);
    mql.addEventListener("change", onChange);
    return () => mql.removeEventListener("change", onChange);
  }, []);
  return reduced;
}
