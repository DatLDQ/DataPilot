import clsx from "clsx";
import { AlertTriangle, BarChart3, CheckCircle2, ChevronRight, Database, LineChart, PencilLine } from "lucide-react";
import { useState } from "react";
import { Bar, BarChart, ResponsiveContainer, Tooltip, XAxis } from "recharts";
import type { Cell, ChartUI, ProfileUI, SqlResultUI } from "../api/types";
import { downloadCSV, formatCell, formatCompact, formatMs } from "../lib/format";
import { CHART, SERIES } from "../lib/palette";
import type { Block } from "../state/runReducer";
import { ChartCard } from "./ChartCard";
import { DataTable } from "./DataTable";
import { CopyButton, IconButton, Spinner } from "./ui";

type ToolBlock = Extract<Block, { kind: "tool" }>;

const META: Record<string, { icon: typeof Database; label: string }> = {
  run_sql: { icon: Database, label: "SQL query" },
  profile_column: { icon: BarChart3, label: "Column profile" },
  create_chart: { icon: LineChart, label: "Chart" },
};

function title(block: ToolBlock): string {
  const input = block.input ?? {};
  if (block.name === "run_sql" && typeof input.purpose === "string" && input.purpose) return input.purpose;
  if (block.name === "profile_column" && input.table) return `Profile ${input.table}.${input.column}`;
  if (block.name === "create_chart" && input.title) return String(input.title);
  return META[block.name]?.label ?? block.name;
}

function StatusIcon({ status }: { status: ToolBlock["status"] }) {
  if (status === "drafting") return <PencilLine className="size-3.5 animate-pulse text-muted" aria-label="Writing" />;
  if (status === "running") return <Spinner className="size-3.5 text-accent" />;
  if (status === "error") return <AlertTriangle className="size-3.5 text-critical" aria-label="Failed" />;
  return <CheckCircle2 className="size-3.5 text-good" aria-label="Done" />;
}

function SqlBlock({ sql }: { sql: string }) {
  return (
    <div className="relative">
      <pre className="overflow-x-auto rounded-lg border border-line bg-page/60 p-3 pr-10 font-mono text-xs leading-relaxed whitespace-pre-wrap text-ink-2">
        {sql}
      </pre>
      <div className="absolute top-1.5 right-1.5">
        <CopyButton text={sql} label="Copy SQL" />
      </div>
    </div>
  );
}

function ProfileView({ ui }: { ui: ProfileUI }) {
  const stats = Object.entries(ui.stats);
  const [labelCol, countCol] = ui.distribution.columns;
  const data = ui.distribution.rows.map((r) => ({ label: r[0] as Cell, count: Number(r[1] ?? 0) }));
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2">
        {stats.map(([k, v]) => (
          <div key={k} className="rounded-md border border-line bg-surface-2 px-2.5 py-1.5">
            <div className="text-[11px] text-muted">{k.replace(/_/g, " ")}</div>
            <div className="tabular text-sm text-ink">{formatCell(v)}</div>
          </div>
        ))}
      </div>
      {data.length > 0 && (
        <div>
          <div className="mb-1 text-xs text-muted">
            {countCol} by {labelCol}
          </div>
          <div className="h-36">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={data} margin={{ top: 4, right: 4, left: 4, bottom: 0 }} barCategoryGap={2}>
                <XAxis
                  dataKey="label"
                  tick={{ fill: CHART.muted, fontSize: 10 }}
                  tickLine={false}
                  axisLine={{ stroke: CHART.axis }}
                  tickFormatter={(v) => (typeof v === "number" ? formatCompact(v) : String(v).slice(0, 12))}
                  minTickGap={12}
                />
                <Tooltip
                  cursor={{ fill: "rgba(255,255,255,0.04)" }}
                  isAnimationActive={false}
                  contentStyle={{ background: "#222221", border: "1px solid #383835", borderRadius: 8, fontSize: 12 }}
                  labelStyle={{ color: "#f0efec" }}
                  itemStyle={{ color: "#c3c2b7" }}
                  formatter={(v) => [formatCell(v as Cell), countCol]}
                />
                <Bar dataKey="count" fill={SERIES[0]} radius={[4, 4, 0, 0]} maxBarSize={24} isAnimationActive={false} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}
    </div>
  );
}

export function ToolCard({ block }: { block: ToolBlock }) {
  const [open, setOpen] = useState(false);
  const Icon = META[block.name]?.icon ?? Database;
  const sql = typeof block.input?.sql === "string" ? block.input.sql : null;

  // Successful charts render as first-class content rather than inside a collapsed card.
  if (block.name === "create_chart" && block.status === "done" && block.ui) {
    return <ChartCard ui={block.ui as unknown as ChartUI} />;
  }

  const sqlUi = block.name === "run_sql" && block.status === "done" ? (block.ui as unknown as SqlResultUI) : null;
  return (
    <div
      className={clsx(
        "overflow-hidden rounded-xl border bg-surface",
        block.status === "error" ? "border-critical/40" : "border-line",
      )}
    >
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2.5 px-3.5 py-2.5 text-left hover:bg-surface-2/60"
        aria-expanded={open}
      >
        <ChevronRight className={clsx("size-4 shrink-0 text-muted transition-transform", open && "rotate-90")} />
        <Icon className="size-4 shrink-0 text-ink-2" />
        <span className="min-w-0 flex-1 truncate text-sm text-ink">{title(block)}</span>
        {block.summary && (
          <span
            className={clsx(
              "max-w-[45%] truncate text-xs",
              block.status === "error" ? "text-critical" : "text-muted",
            )}
          >
            {block.summary}
          </span>
        )}
        <StatusIcon status={block.status} />
      </button>
      {open && (
        <div className="space-y-3 border-t border-line px-3.5 py-3">
          {sql && <SqlBlock sql={sql} />}
          {block.name === "profile_column" && block.status === "done" && block.ui && (
            <ProfileView ui={block.ui as unknown as ProfileUI} />
          )}
          {sqlUi && (
            <div>
              <div className="mb-1.5 flex items-center justify-between">
                <span className="text-xs text-muted">
                  {sqlUi.rows.length}
                  {sqlUi.truncated ? "+" : ""} rows · {formatMs(sqlUi.elapsed_ms)}
                </span>
                <IconButton
                  label="Download CSV"
                  onClick={() => downloadCSV(title(block), sqlUi.columns, sqlUi.rows)}
                >
                  <span className="text-xs">CSV</span>
                </IconButton>
              </div>
              <DataTable columns={sqlUi.columns} rows={sqlUi.rows} />
            </div>
          )}
          {block.status === "error" && (
            <p className="rounded-lg bg-critical/10 px-3 py-2 font-mono text-xs text-ink-2">{block.summary}</p>
          )}
          {block.status === "drafting" && <p className="text-xs text-muted">Claude is writing this tool call…</p>}
        </div>
      )}
    </div>
  );
}
