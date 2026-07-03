// Component tests against the RECORDED gateway fixtures (WF-DESIGN-0012 "Testing the
// contract"): decisions flow through the real decisionFromDebug into the real components.
// jsdom cannot see vibrancy/motion — those live in docs/desktop-fidelity.md's manual list.

import { describe, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { decisionFromDebug } from "@wayfinder/shared/gateway";
import { DecisionPill } from "@/components/DecisionPill";
import { ScoreReadout } from "@/components/ScoreReadout";
import { WhyBars } from "@/components/WhyBars";
import { DecisionCard } from "@/components/DecisionCard";
import { StreamingMessage } from "@/components/StreamingMessage";
import { StatusDot } from "@/components/StatusDot";
import { SavingsGlance, formatSaved, type SavingsReport } from "@/components/SavingsGlance";
import { OnboardingCard } from "@/components/OnboardingCard";
import { Composer } from "@/components/Composer";

function fixture<T = Record<string, unknown>>(name: string): T {
  const path = join(process.cwd(), "src", "test", "fixtures", name);
  return JSON.parse(readFileSync(path, "utf8")) as T;
}

type DecisionFixture = { headers: Record<string, string>; wayfinder: Record<string, unknown> };
const local = decisionFromDebug(fixture<DecisionFixture>("decision-local.json").wayfinder);
const cloud = decisionFromDebug(fixture<DecisionFixture>("decision-cloud.json").wayfinder);
const savings = fixture<SavingsReport>("savings.json");

describe("DecisionPill — route, glyph, accent, sr text (fixture table)", () => {
  const table = [
    { d: local, label: "LOCAL", glyph: "●", route: "local", sr: "routed locally" },
    { d: cloud, label: "CLOUD", glyph: "◆", route: "cloud", sr: "routed to cloud" },
  ];
  for (const row of table) {
    it(`${row.route}: ${row.glyph} ${row.label} + model in mono`, () => {
      const { container } = render(<DecisionPill decision={row.d} />);
      const pill = container.firstElementChild!;
      expect(pill).toHaveAttribute("data-route", row.route);
      expect(pill.textContent).toContain(row.label);
      expect(pill.textContent).toContain(row.glyph);
      expect(pill.textContent).toContain(row.d.model);
      expect(screen.getByText(row.sr)).toBeInTheDocument();
    });
  }
});

describe("ScoreReadout — 2dp tabular score over a rail", () => {
  it("renders the fixture score at 2dp with a meter", () => {
    render(<ScoreReadout decision={cloud} />);
    expect(screen.getByText(cloud.score.toFixed(2))).toBeInTheDocument();
    const meter = screen.getByRole("meter", { name: "complexity score" });
    expect(meter).toHaveAttribute("aria-valuenow", String(cloud.score));
  });
});

describe("WhyBars — skeletons until enriched, then top-4 by share", () => {
  it("shows skeleton rows before the trailing wayfinder event", () => {
    const { container } = render(<WhyBars decision={cloud} enriched={false} />);
    expect(screen.queryByRole("list")).not.toBeInTheDocument();
    expect(container.querySelectorAll("[aria-hidden] > *").length).toBe(4);
  });
  it("renders top-4 contributions with VoiceOver-readable rows", () => {
    render(<WhyBars decision={cloud} enriched />);
    const list = screen.getByRole("list", { name: "top scoring factors" });
    const items = list.querySelectorAll("li");
    expect(items.length).toBe(4);
    // rows read "name, N% of score" and are ordered by share, largest first
    const labels = [...items].map((li) => li.getAttribute("aria-label")!);
    expect(labels[0]).toMatch(/, \d+% of score$/);
    const shares = [...cloud.contributions].sort((a, b) => b.share - a.share).slice(0, 4);
    expect(labels[0].startsWith(shares[0].name.replace(/_/g, " "))).toBe(true);
  });
});

describe("DecisionCard — hero with badge sub-line and why disclosure", () => {
  it("carries the route on the card and the routing badge text", () => {
    const { container } = render(<DecisionCard decision={cloud} enriched offline />);
    expect(container.querySelector('[data-route="cloud"]')).toBeInTheDocument();
    expect(screen.getByText(/cloud · score 0\.\d{2} · offline/)).toBeInTheDocument();
  });
  it("why is collapsed by default and toggles open", async () => {
    const user = userEvent.setup();
    render(<DecisionCard decision={cloud} enriched />);
    expect(screen.queryByRole("list", { name: "top scoring factors" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /why/ }));
    expect(screen.getByRole("list", { name: "top scoring factors" })).toBeInTheDocument();
  });
});

describe("StreamingMessage — busy + caret while streaming, selectable text", () => {
  it("streaming: aria-busy with a caret", () => {
    const { container } = render(<StreamingMessage reply="Routing" streaming />);
    expect(container.firstElementChild).toHaveAttribute("aria-busy", "true");
    expect(container.querySelector(".animate-pulse")).toBeInTheDocument();
  });
  it("done: no caret, text intact", () => {
    const { container } = render(<StreamingMessage reply="Routing decisions stay local." streaming={false} />);
    expect(container.firstElementChild).toHaveAttribute("aria-busy", "false");
    expect(container.querySelector(".animate-pulse")).not.toBeInTheDocument();
    expect(screen.getByText("Routing decisions stay local.")).toBeInTheDocument();
  });
});

describe("StatusDot — role=status with health labels", () => {
  it.each([
    ["ok", "gateway running"],
    ["degraded", "gateway degraded"],
    ["unreachable", "gateway unreachable"],
  ] as const)("%s -> %s", (status, label) => {
    render(<StatusDot status={status} missingKeys={status === "degraded" ? ["ANTHROPIC_API_KEY"] : []} />);
    expect(screen.getByRole("status", { name: label })).toBeInTheDocument();
  });
});

describe("SavingsGlance — never '0 relative units'", () => {
  it("renders the recorded savings (sub-cent shows <$0.01)", () => {
    render(<SavingsGlance report={savings} />);
    expect(screen.getByText("<$0.01")).toBeInTheDocument();
    expect(screen.getByText(/today/)).toBeInTheDocument();
  });
  const hiddenCases: Array<[SavingsReport | null, string]> = [
    [{ ...savings, priced: false }, "unpriced"],
    [{ ...savings, requests: 0 }, "no traffic"],
    [{ ...savings, saved: 0 }, "zero saved"],
    [null, "no report"],
  ];
  it.each(hiddenCases)("hidden when %#: %s", (report) => {
    const { container } = render(<SavingsGlance report={report} />);
    expect(container).toBeEmptyDOMElement();
  });
  it("formats: 0.42 -> $0.42, 0.0072 -> <$0.01", () => {
    expect(formatSaved(0.42)).toBe("$0.42");
    expect(formatSaved(0.0072)).toBe("<$0.01");
  });
});

describe("OnboardingCard — the connect-a-model nudge", () => {
  it("shows the copyable init snippet", async () => {
    const user = userEvent.setup();
    render(<OnboardingCard />);
    expect(screen.getByText("wayfinder-router init")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "copy" }));
    expect(screen.getByRole("button", { name: "copied" })).toBeInTheDocument();
  });
});

describe("Composer — Enter sends, Shift+Enter newlines, Stop aborts", () => {
  it("Enter sends the trimmed prompt and clears", async () => {
    const user = userEvent.setup();
    const onSend = vi.fn();
    render(<Composer streaming={false} onSend={onSend} onStop={() => {}} />);
    const box = screen.getByRole("textbox", { name: "message" });
    await user.type(box, "  route me  {Enter}");
    expect(onSend).toHaveBeenCalledWith("route me");
    expect(box).toHaveValue("");
  });
  it("Shift+Enter inserts a newline instead of sending", async () => {
    const user = userEvent.setup();
    const onSend = vi.fn();
    render(<Composer streaming={false} onSend={onSend} onStop={() => {}} />);
    const box = screen.getByRole("textbox", { name: "message" });
    await user.type(box, "line one{Shift>}{Enter}{/Shift}line two");
    expect(onSend).not.toHaveBeenCalled();
    expect(box).toHaveValue("line one\nline two");
  });
  it("streaming swaps send for Stop, which fires onStop", async () => {
    const user = userEvent.setup();
    const onStop = vi.fn();
    render(<Composer streaming onSend={() => {}} onStop={onStop} />);
    expect(screen.queryByRole("button", { name: "send" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "stop streaming" }));
    expect(onStop).toHaveBeenCalled();
  });
});
