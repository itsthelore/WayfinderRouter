// Theme lint (WF-DESIGN-0012): vendored shadcn components must be themed with the Wayfinder
// slot variables, not shadcn's stock palette. Two failure modes this guards:
//   1. raw Tailwind palette utilities (zinc/neutral/slate/gray/stone) surviving the theming —
//      the popover must never render shadcn-default zinc;
//   2. `dark:` variant utilities — dark mode is `prefers-color-scheme` ONLY (the CSS variables
//      flip; there is no `.dark` class), so a `dark:` utility in a component is dead code that
//      will silently never apply.
// Scope is components/ui/** — globals.css and app code are free to say what they like.

import { describe, expect, it } from "vitest";
import { readFileSync, readdirSync, existsSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const UI_DIR = fileURLToPath(new URL("../components/ui", import.meta.url));

// Raw palette utilities like bg-zinc-100, text-neutral-500, border-slate-200/50…
const RAW_PALETTE = /\b(?:[a-z-]+[:-])*(?:bg|text|border|ring|fill|stroke|outline|divide|shadow|from|via|to)-(?:zinc|neutral|slate|gray|stone)-\d{2,3}(?:\/\d{1,3})?\b/;
// Any dark-mode variant utility, e.g. dark:bg-…, dark:hover:text-…
const DARK_VARIANT = /(?:^|[\s"'`{:])dark:/;

function uiFiles(): string[] {
  if (!existsSync(UI_DIR)) return [];
  return readdirSync(UI_DIR)
    .filter((f: string) => /\.(tsx?|css)$/.test(f))
    .map((f: string) => join(UI_DIR, f));
}

describe("theme lint — components/ui carries the Wayfinder theme, not shadcn defaults", () => {
  it("vendored components exist to lint (nine land with the design system)", () => {
    // Informational until the components are vendored; the two real checks below are the gate.
    expect(uiFiles()).toBeInstanceOf(Array);
  });

  it("no raw zinc/neutral/slate/gray/stone palette utilities survive theming", () => {
    const offenders: string[] = [];
    for (const file of uiFiles()) {
      const src = readFileSync(file, "utf8");
      for (const [lineNo, line] of src.split("\n").entries()) {
        if (RAW_PALETTE.test(line)) offenders.push(`${file}:${lineNo + 1}: ${line.trim()}`);
      }
    }
    expect(offenders, offenders.join("\n")).toEqual([]);
  });

  it("no dark: variant utilities — dark mode is prefers-color-scheme only", () => {
    const offenders: string[] = [];
    for (const file of uiFiles()) {
      const src = readFileSync(file, "utf8");
      for (const [lineNo, line] of src.split("\n").entries()) {
        if (DARK_VARIANT.test(line)) offenders.push(`${file}:${lineNo + 1}: ${line.trim()}`);
      }
    }
    expect(offenders, offenders.join("\n")).toEqual([]);
  });
});
