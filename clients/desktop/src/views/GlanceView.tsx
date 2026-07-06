// The glance surface (glance pivot; information design inspired by CodexBar): dense tiles on
// L2 cards answering "is routing on, where did turns go, what did it save" without a scroll or
// a click. Chat is one tab away. Everything here renders gateway truth — nothing is computed
// beyond shares of the gateway's own counts (WF-ADR-0001).
import type { GatewayState } from "@/lib/appState";
import type { RecentReport } from "@/hooks/useRecent";
import { formatSaved, type SavingsReport } from "@/components/SavingsGlance";
import { StatusDot, type DotStatus } from "@/components/StatusDot";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

function Tile({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <Card className="rounded-lg">
      <CardContent className="flex flex-col gap-2 p-3">
        <div className="text-[11px] font-medium tracking-wide uppercase text-muted-foreground">
          {label}
        </div>
        {children}
      </CardContent>
    </Card>
  );
}

function ShareBar({ kind, count, share }: { kind: "local" | "cloud"; count: number; share: number }) {
  const pct = Math.round(share * 100);
  return (
    <div
      data-route={kind}
      aria-label={`${kind}: ${count} turns, ${pct}%`}
      className="grid grid-cols-[3.5rem_1fr_auto] items-center gap-2"
    >
      <span aria-hidden className="text-[11px] font-medium tracking-wide uppercase" style={{ color: "var(--route-accent)" }}>
        {kind}
      </span>
      <span aria-hidden className="h-1 rounded-full bg-track">
        <span
          className="block h-1 rounded-full transition-[width] duration-[var(--dur-slow)] ease-[var(--ease-standard)]"
          style={{ width: `${pct}%`, background: "var(--route-accent)" }}
        />
      </span>
      <span aria-hidden className="font-mono text-[11px] tabular-nums text-muted-foreground">
        {count}
      </span>
    </div>
  );
}

export function GlanceView({
  gw,
  status,
  recent,
  savings,
  cheapest,
}: {
  gw: GatewayState;
  status: DotStatus;
  recent: RecentReport | null;
  savings: SavingsReport | null;
  cheapest: string | null;
}) {
  const total = recent?.total ?? 0;
  const localCount = cheapest ? (recent?.byModel[cheapest] ?? 0) : 0;
  const cloudCount = total - localCount;

  return (
    <div className="flex flex-col gap-2.5 p-3.5" data-testid="glance">
      <Tile label="routing">
        {recent == null ? (
          <div className="flex flex-col gap-2" aria-hidden>
            <Skeleton className="h-3 w-full" />
            <Skeleton className="h-3 w-full" />
          </div>
        ) : total === 0 ? (
          <p className="text-[13px] leading-[1.45] text-muted-foreground">
            No turns yet — routing shows here as you use it.
          </p>
        ) : (
          <div className="flex flex-col gap-2">
            <ShareBar kind="local" count={localCount} share={localCount / total} />
            <ShareBar kind="cloud" count={cloudCount} share={cloudCount / total} />
          </div>
        )}
      </Tile>

      <Tile label="saved">
        {savings == null ? (
          <Skeleton className="h-6 w-24" aria-hidden />
        ) : savings.priced && savings.saved > 0 ? (
          <div className="flex items-baseline gap-2">
            <span className="font-mono text-[22px] font-semibold tabular-nums leading-none">
              {formatSaved(savings.saved)}
            </span>
            {savings.saved_pct > 0 && (
              <span className="text-[11px] text-muted-foreground">
                {Math.round(savings.saved_pct)}% vs always-frontier
              </span>
            )}
          </div>
        ) : (
          <p className="text-[13px] leading-[1.45] text-muted-foreground">
            Savings show once priced turns land.
          </p>
        )}
      </Tile>

      <Tile label="gateway">
        <div className="flex items-center gap-2">
          <StatusDot status={status} missingKeys={gw.missingKeys} />
          <span className="font-mono text-[11px] text-muted-foreground">127.0.0.1:8088</span>
        </div>
        {gw.missingKeys.length > 0 && (
          <p className="text-[11px] leading-[1.45]" style={{ color: "var(--route-cloud)" }}>
            missing <span className="font-mono">{gw.missingKeys.join(", ")}</span>
          </p>
        )}
      </Tile>
    </div>
  );
}
