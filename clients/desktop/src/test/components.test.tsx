// Component tests against the RECORDED gateway fixtures (WF-DESIGN-0012 "Testing the contract").
// jsdom cannot see vibrancy/motion — those live in docs/desktop-fidelity.md's manual list.

import { beforeEach, describe, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { decisionFromDebug } from "@wayfinder/shared/gateway";
import { featureRows, whyLine } from "@wayfinder/shared/decision";
import { ExternalLink } from "lucide-react";
import { Bar } from "@/components/menu/Bar";
import { SplitBar } from "@/components/menu/SplitBar";
import { MetricRow } from "@/components/menu/MetricRow";
import { ActionRow } from "@/components/menu/ActionRow";
import { FooterMenuItem } from "@/components/menu/FooterMenuItem";
import { DecisionSummary } from "@/components/DecisionSummary";
import { StreamingMessage } from "@/components/StreamingMessage";
import { formatSaved, type SavingsReport } from "@/lib/format";
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

describe("Bar — a plain fill meter for true 0..1 scalars (no knob — see WF-DESIGN-0014's deviation note)", () => {
  it("clamps 0..1 and exposes the percent to a11y", () => {
    render(<Bar fraction={0.62} label="local share" />);
    const meter = screen.getByRole("meter", { name: "local share" });
    expect(meter).toHaveAttribute("aria-valuenow", "62");
  });
  it("out-of-range fractions clamp instead of overflowing", () => {
    render(<Bar fraction={1.7} label="over" />);
    expect(screen.getByRole("meter", { name: "over" })).toHaveAttribute("aria-valuenow", "100");
  });
});

describe("SplitBar — the route split as a composition, not a quota fill", () => {
  it("renders proportional segments with an a11y summary", () => {
    const { container } = render(
      <SplitBar
        segments={[
          { label: "local", count: 1, color: "teal" },
          { label: "cloud", count: 3, color: "orange" },
        ]}
      />,
    );
    const bar = screen.getByRole("img", { name: "route split — local: 1 (25%), cloud: 3 (75%)" });
    expect(bar).toBeInTheDocument();
    const segments = container.querySelectorAll('[role="img"] > div');
    expect(segments.length).toBe(2);
    expect((segments[0] as HTMLElement).style.width).toBe("25%");
    expect((segments[1] as HTMLElement).style.width).toBe("75%");
    // A nonzero share never renders below a 12px pill (the reference's 2% bar is visible).
    expect((segments[0] as HTMLElement).style.minWidth).toBe("12px");
  });
  it("an empty split is just the track — no segments", () => {
    const { container } = render(
      <SplitBar
        segments={[
          { label: "local", count: 0, color: "teal" },
          { label: "cloud", count: 0, color: "orange" },
        ]}
      />,
    );
    expect(container.querySelectorAll('[role="img"] > div').length).toBe(0);
  });
  it("zero-count segments are dropped rather than rendered as slivers", () => {
    const { container } = render(
      <SplitBar
        segments={[
          { label: "local", count: 2, color: "teal" },
          { label: "cloud", count: 0, color: "orange" },
        ]}
      />,
    );
    expect(container.querySelectorAll('[role="img"] > div').length).toBe(1);
  });
});

describe("MetricRow — bold label, optional bar, left/right values, optional insight", () => {
  it("renders the label, a supplied bar, values, and insight line", () => {
    render(
      <MetricRow
        label="Routing"
        bar={<SplitBar segments={[{ label: "local", count: 1, color: "teal" }, { label: "cloud", count: 1, color: "orange" }]} />}
        left="50% routed locally"
        right="2 turns"
        insight="Routed: local: 1 · cloud: 1"
      />,
    );
    expect(screen.getByText("Routing")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /route split/ })).toBeInTheDocument();
    expect(screen.getByText("50% routed locally")).toBeInTheDocument();
    expect(screen.getByText("2 turns")).toBeInTheDocument();
    expect(screen.getByText("Routed: local: 1 · cloud: 1")).toBeInTheDocument();
  });
  it("bar, right, and insight are all optional — a bar-less section is just label + line", () => {
    render(<MetricRow label="Saved" left="Not yet available" />);
    expect(screen.getByText("Not yet available")).toBeInTheDocument();
    expect(screen.queryByRole("meter")).not.toBeInTheDocument();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
  });
  it("lines variant stacks dark body lines — the reference's own Cost form", () => {
    render(<MetricRow label="Saved" lines={["Today: $0.04 · 15K tokens", "Last 30 days: $254.24"]} />);
    expect(screen.getByText("Today: $0.04 · 15K tokens")).toBeInTheDocument();
    expect(screen.getByText("Last 30 days: $254.24")).toBeInTheDocument();
  });
  it("help renders a visible (?) beside the label; clicking it opens the panel", async () => {
    const user = userEvent.setup();
    render(<MetricRow label="Routing" help="Where your recent turns went." left="50%" />);
    // Help only appears when explicitly asked for — the panel is absent until the click.
    expect(screen.queryByText("Where your recent turns went.")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "about routing" }));
    expect(await screen.findByRole("dialog")).toHaveTextContent("Where your recent turns went.");
  });
  it("no help -> no (?) button", () => {
    render(<MetricRow label="Saved" left="Not yet available" />);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});

describe("ActionRow — icon+label, checkable, or a chevron push (CodexBar's own row shapes)", () => {
  it("plain row shows its icon and calls onClick", async () => {
    const user = userEvent.setup();
    const onClick = vi.fn();
    const { container } = render(<ActionRow icon={ExternalLink} label="Open Dashboard" onClick={onClick} />);
    expect(container.querySelector("svg.lucide-external-link")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Open Dashboard" }));
    expect(onClick).toHaveBeenCalled();
  });
  it("checkable rows show a checkmark when on, and a blank slot when off (never the icon)", () => {
    const { container: off } = render(<ActionRow label="Offline mode" checked={false} onClick={() => {}} />);
    expect(off.querySelector("svg.lucide-check")).not.toBeInTheDocument();
    const { container: on } = render(<ActionRow label="Offline mode" checked onClick={() => {}} />);
    expect(on.querySelector("svg.lucide-check")).toBeInTheDocument();
  });
  it("disabled rows (offline by config) have no click handler", () => {
    render(<ActionRow label="Offline mode (by config)" checked disabled />);
    expect(screen.getByRole("button", { name: "Offline mode (by config)" })).toBeDisabled();
  });
  it("chevron rows push a sub-screen (Chat)", async () => {
    const user = userEvent.setup();
    const onOpenChat = vi.fn();
    render(<ActionRow label="Chat" chevron onClick={onOpenChat} />);
    const row = screen.getByRole("button", { name: "Chat" });
    expect(row).toHaveTextContent("›");
    await user.click(row);
    expect(onOpenChat).toHaveBeenCalled();
  });
});

describe("FooterMenuItem — exact NSMenu style, real shortcuts (never decorative)", () => {
  it("shows the label and the right-aligned shortcut", async () => {
    const user = userEvent.setup();
    const onClick = vi.fn();
    render(<FooterMenuItem label="Settings…" shortcut="⌘," onClick={onClick} />);
    expect(screen.getByText("⌘,")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Settings…/ }));
    expect(onClick).toHaveBeenCalled();
  });
});

describe("DecisionSummary — the live turn's prompt-analysis card (WF-DESIGN-0014)", () => {
  it("shows the score numeral, route pill, and deterministic caption", () => {
    const { container } = render(<DecisionSummary decision={cloud} enriched offline />);
    expect(container.querySelector('[role="meter"]')).toBeInTheDocument();
    expect(screen.getByText("Route: Cloud")).toBeInTheDocument();
    expect(screen.getByText(cloud.score.toFixed(2))).toBeInTheDocument();
    expect(screen.getByText("Deterministic · No model call · offline")).toBeInTheDocument();
  });
  it("renders the five feature rows and the why line once enriched", () => {
    render(<DecisionSummary decision={cloud} enriched />);
    const list = screen.getByRole("list", { name: "prompt features" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(5);
    expect(screen.getByRole("listitem", { name: "Code blocks: yes" })).toBeInTheDocument();
    expect(screen.getByText(/code detected/)).toBeInTheDocument();
  });
  it("skeletons the feature rows before enrichment lands (no contributions yet)", () => {
    const { container } = render(<DecisionSummary decision={cloud} enriched={false} />);
    expect(screen.queryByRole("list", { name: "prompt features" })).not.toBeInTheDocument();
    expect(container.querySelectorAll("[aria-hidden] > *").length).toBeGreaterThan(0);
  });
  it("local decisions render the local route pill", () => {
    render(<DecisionSummary decision={local} enriched />);
    expect(screen.getByText("Route: Local")).toBeInTheDocument();
  });
  it("the caption surfaces the cache-hit and decision-only delivery states", () => {
    const { rerender } = render(<DecisionSummary decision={cloud} enriched cache />);
    expect(screen.getByText("Deterministic · No model call · cache hit")).toBeInTheDocument();
    rerender(<DecisionSummary decision={{ ...cloud, decisionOnly: true }} enriched />);
    expect(screen.getByText("Deterministic · No model call · decision only")).toBeInTheDocument();
  });
});

describe("featureRows / whyLine — pure display helpers over the gateway's contributions", () => {
  it("summarises a simple prompt as the mockup does (short, no code, no sections)", () => {
    const rows = Object.fromEntries(featureRows(local).map((r) => [r.key, r.value]));
    expect(rows).toMatchObject({ words: "2", lists: "none", code: "no", sections: "no", lexical: "low" });
    expect(whyLine(local)).toBe("short prompt, no code, no structured sections.");
  });
  it("summarises a rich prompt (code, structured sections, high lexical signal)", () => {
    const rows = Object.fromEntries(featureRows(cloud).map((r) => [r.key, r.value]));
    expect(rows).toMatchObject({ words: "420", code: "yes", sections: "yes", lexical: "high" });
    expect(whyLine(cloud)).toContain("code detected");
    expect(whyLine(cloud)).toContain("technical terms");
  });
  it("is total over a decision with no contributions (header-only)", () => {
    const bare = { model: "local", score: 0, isLocal: true, contributions: [] } as unknown as typeof local;
    expect(featureRows(bare).map((r) => r.value)).toEqual(["0", "none", "no", "no", "low"]);
    expect(whyLine(bare)).toBe("short prompt, no code, no structured sections.");
  });
  it("covers the medium buckets — mid word count, a list count, medium lexical signal", () => {
    const mid = {
      model: "cloud",
      score: 0.4,
      isLocal: false,
      contributions: [
        { name: "word_count", value: 80, share: 0.5 },
        { name: "list_item_count", value: 3, share: 0.1 },
        { name: "reasoning_term_count", value: 5, share: 0.2 },
      ],
    } as unknown as typeof cloud;
    const rows = Object.fromEntries(featureRows(mid).map((r) => [r.key, r.value]));
    expect(rows).toMatchObject({ words: "80", lists: "3", lexical: "medium" });
    expect(whyLine(mid)).toContain("medium-length prompt");
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

describe("formatSaved — never '$0.00'", () => {
  it("0.42 -> $0.42, 0.0072 -> <$0.01", () => {
    expect(formatSaved(0.42)).toBe("$0.42");
    expect(formatSaved(0.0072)).toBe("<$0.01");
  });
  it("the recorded fixture is sub-cent", () => {
    expect(formatSaved(savings.saved)).toBe("<$0.01");
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

  describe("slash commands — the '/' menu, filtered and keyboard-navigable like Claude's", () => {
    const commands = [
      { name: "clear", description: "Clear this conversation", run: vi.fn() },
      { name: "clock", description: "unused in these tests", run: vi.fn() },
      { name: "settings", description: "Open Settings…", run: vi.fn() },
    ];
    beforeEach(() => commands.forEach((c) => (c.run as ReturnType<typeof vi.fn>).mockClear()));

    it("bare '/' lists every command; a mid-message '/' does not", async () => {
      const user = userEvent.setup();
      render(<Composer streaming={false} commands={commands} onSend={() => {}} onStop={() => {}} />);
      const box = screen.getByRole("textbox", { name: "message" });
      await user.type(box, "/");
      expect(screen.getByRole("listbox", { name: "slash commands" })).toBeInTheDocument();
      expect(screen.getAllByRole("option")).toHaveLength(3);
      await user.clear(box);
      await user.type(box, "not / a command");
      expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    });

    it("filters by prefix as you type, and closes once the command word is followed by a space", async () => {
      const user = userEvent.setup();
      render(<Composer streaming={false} commands={commands} onSend={() => {}} onStop={() => {}} />);
      const box = screen.getByRole("textbox", { name: "message" });
      await user.type(box, "/cl");
      expect(screen.getAllByRole("option").map((o) => o.textContent)).toEqual([
        expect.stringContaining("/clear"),
        expect.stringContaining("/clock"),
      ]);
      await user.type(box, "ear ");
      expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    });

    it("ArrowDown/ArrowUp move the highlight, Enter runs the highlighted command and clears the box", async () => {
      const user = userEvent.setup();
      render(<Composer streaming={false} commands={commands} onSend={() => {}} onStop={() => {}} />);
      const box = screen.getByRole("textbox", { name: "message" });
      await user.type(box, "/");
      await user.keyboard("{ArrowDown}{ArrowDown}"); // clear -> clock -> settings
      expect(screen.getByRole("option", { name: /settings/ })).toHaveAttribute("aria-selected", "true");
      await user.keyboard("{ArrowUp}"); // back to clock
      await user.keyboard("{Enter}");
      expect(commands[1]!.run).toHaveBeenCalled();
      expect(commands[0]!.run).not.toHaveBeenCalled();
      expect(box).toHaveValue("");
    });

    it("clicking a command runs it without losing textarea focus", async () => {
      const user = userEvent.setup();
      render(<Composer streaming={false} commands={commands} onSend={() => {}} onStop={() => {}} />);
      const box = screen.getByRole("textbox", { name: "message" });
      await user.type(box, "/settings");
      await user.click(screen.getByRole("option", { name: /settings/ }));
      expect(commands[2]!.run).toHaveBeenCalled();
    });

    it("Escape dismisses the menu and clears the composer", async () => {
      const user = userEvent.setup();
      render(<Composer streaming={false} commands={commands} onSend={() => {}} onStop={() => {}} />);
      const box = screen.getByRole("textbox", { name: "message" });
      await user.type(box, "/cl");
      await user.keyboard("{Escape}");
      expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
      expect(box).toHaveValue("");
    });

    it("no matching command -> no menu, and Enter sends the literal text", async () => {
      const user = userEvent.setup();
      const onSend = vi.fn();
      render(<Composer streaming={false} commands={commands} onSend={onSend} onStop={() => {}} />);
      const box = screen.getByRole("textbox", { name: "message" });
      await user.type(box, "/nope{Enter}");
      expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
      expect(onSend).toHaveBeenCalledWith("/nope");
    });
  });
});
