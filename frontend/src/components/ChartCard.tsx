import { BarChart3, Code2, Download, Table2 } from "lucide-react";
import { useMemo, useState } from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell as PieCell,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
  type TooltipContentProps,
  type TooltipValueType,
} from "recharts";
import type { Cell, ChartUI } from "../api/types";
import { dateLabeler, downloadCSV, formatCell, formatCompact } from "../lib/format";
import { CHART, OTHER, SERIES, seriesColor } from "../lib/palette";
import { DataTable } from "./DataTable";
import { IconButton } from "./ui";

const AXIS_TICK = { fill: CHART.muted, fontSize: 11 };
const MAX_PIE_SLICES = 8;

function ChartTooltip({ active, payload, label }: TooltipContentProps<TooltipValueType, string | number>) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-lg border border-line-strong bg-surface-2 px-3 py-2 text-xs shadow-xl">
      {label !== undefined && <div className="mb-1 font-medium text-ink">{String(label)}</div>}
      {payload.map((p) => (
        <div key={String(p.dataKey ?? p.name)} className="flex items-center gap-2 py-0.5">
          <span className="size-2 rounded-full" style={{ background: (p.color ?? p.payload?.fill) as string }} />
          <span className="text-ink-2">{p.name}</span>
          <span className="tabular ml-auto pl-4 font-medium text-ink">{formatCell(p.value as Cell)}</span>
        </div>
      ))}
    </div>
  );
}

function Legend({ keys }: { keys: string[] }) {
  if (keys.length < 2) return null;
  return (
    <div className="mb-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-2">
      {keys.map((k, i) => (
        <span key={k} className="flex items-center gap-1.5">
          <span className="size-2.5 rounded-sm" style={{ background: seriesColor(k, i) }} />
          {k}
        </span>
      ))}
    </div>
  );
}

function pieData(data: Record<string, Cell>[], x: string, y: string) {
  const sorted = [...data].sort((a, b) => Number(b[y] ?? 0) - Number(a[y] ?? 0));
  if (sorted.length <= MAX_PIE_SLICES) return sorted.map((d) => ({ name: String(d[x]), value: Number(d[y] ?? 0) }));
  const head = sorted.slice(0, MAX_PIE_SLICES - 1).map((d) => ({ name: String(d[x]), value: Number(d[y] ?? 0) }));
  const rest = sorted.slice(MAX_PIE_SLICES - 1).reduce((s, d) => s + Number(d[y] ?? 0), 0);
  return [...head, { name: "Other", value: rest }];
}

function ChartBody({ ui }: { ui: ChartUI }) {
  const { spec } = ui;
  const labelX = dateLabeler(ui.data.map((d) => d[spec.x]));
  const data = labelX ? ui.data.map((d) => ({ ...d, [spec.x]: labelX(d[spec.x]) })) : ui.data;
  const keys = spec.y_keys;
  const grid = <CartesianGrid stroke={CHART.grid} vertical={false} />;
  const xAxis = (
    <XAxis dataKey={spec.x} tick={AXIS_TICK} tickLine={false} axisLine={{ stroke: CHART.axis }} minTickGap={16} />
  );
  const yAxis = <YAxis tick={AXIS_TICK} tickLine={false} axisLine={false} tickFormatter={formatCompact} width={48} />;
  const tooltip = (cursor: object | boolean = { stroke: CHART.axis }) => (
    <Tooltip content={ChartTooltip} cursor={cursor} isAnimationActive={false} />
  );
  const margin = { top: 8, right: 12, left: 0, bottom: 0 };

  switch (spec.chart_type) {
    case "bar":
      return (
        <BarChart data={data} margin={margin} barGap={2} barCategoryGap="20%">
          {grid}
          {xAxis}
          {yAxis}
          {tooltip({ fill: "rgba(255,255,255,0.04)" })}
          {keys.map((k, i) => (
            <Bar key={k} dataKey={k} name={k} fill={seriesColor(k, i)} maxBarSize={24} radius={[4, 4, 0, 0]} />
          ))}
        </BarChart>
      );
    case "horizontal_bar":
      return (
        <BarChart data={data} layout="vertical" margin={margin} barGap={2} barCategoryGap="20%">
          <CartesianGrid stroke={CHART.grid} horizontal={false} />
          <XAxis type="number" tick={AXIS_TICK} tickLine={false} axisLine={false} tickFormatter={formatCompact} />
          <YAxis
            type="category"
            dataKey={spec.x}
            tick={AXIS_TICK}
            tickLine={false}
            axisLine={{ stroke: CHART.axis }}
            width={120}
          />
          {tooltip({ fill: "rgba(255,255,255,0.04)" })}
          {keys.map((k, i) => (
            <Bar key={k} dataKey={k} name={k} fill={seriesColor(k, i)} maxBarSize={24} radius={[0, 4, 4, 0]} />
          ))}
        </BarChart>
      );
    case "line":
      return (
        <LineChart data={data} margin={margin}>
          {grid}
          {xAxis}
          {yAxis}
          {tooltip()}
          {keys.map((k, i) => (
            <Line
              key={k}
              dataKey={k}
              name={k}
              stroke={seriesColor(k, i)}
              strokeWidth={2}
              strokeLinecap="round"
              strokeLinejoin="round"
              dot={data.length <= 24 ? { r: 4, strokeWidth: 2, stroke: CHART.surface, fill: seriesColor(k, i) } : false}
              activeDot={{ r: 5, strokeWidth: 2, stroke: CHART.surface }}
              connectNulls
              isAnimationActive={false}
            />
          ))}
        </LineChart>
      );
    case "area":
      return (
        <AreaChart data={data} margin={margin}>
          {grid}
          {xAxis}
          {yAxis}
          {tooltip()}
          {keys.map((k, i) => (
            <Area
              key={k}
              dataKey={k}
              name={k}
              stroke={seriesColor(k, i)}
              strokeWidth={2}
              fill={seriesColor(k, i)}
              fillOpacity={0.1}
              activeDot={{ r: 5, strokeWidth: 2, stroke: CHART.surface }}
              connectNulls
              isAnimationActive={false}
            />
          ))}
        </AreaChart>
      );
    case "scatter":
      return (
        <ScatterChart margin={margin}>
          <CartesianGrid stroke={CHART.grid} />
          <XAxis
            dataKey={spec.x}
            type="number"
            name={spec.x}
            tick={AXIS_TICK}
            tickLine={false}
            axisLine={{ stroke: CHART.axis }}
            tickFormatter={formatCompact}
            domain={["auto", "auto"]}
          />
          <YAxis
            dataKey={keys[0]}
            type="number"
            name={keys[0]}
            tick={AXIS_TICK}
            tickLine={false}
            axisLine={false}
            tickFormatter={formatCompact}
            width={48}
            domain={["auto", "auto"]}
          />
          {tooltip({ strokeDasharray: "0", stroke: CHART.axis })}
          <Scatter data={data} name={keys[0]} fill={SERIES[0]} stroke={CHART.surface} strokeWidth={2} />
        </ScatterChart>
      );
    case "pie": {
      const slices = pieData(data, spec.x, keys[0]);
      return (
        <PieChart>
          <Tooltip content={ChartTooltip} isAnimationActive={false} />
          <Pie
            data={slices}
            dataKey="value"
            nameKey="name"
            innerRadius="55%"
            outerRadius="85%"
            stroke={CHART.surface}
            strokeWidth={2}
            isAnimationActive={false}
          >
            {slices.map((s, i) => (
              <PieCell key={s.name} fill={s.name === "Other" ? OTHER : SERIES[i % SERIES.length]} />
            ))}
          </Pie>
        </PieChart>
      );
    }
  }
}

export function ChartCard({ ui }: { ui: ChartUI }) {
  const [view, setView] = useState<"chart" | "table" | "sql">("chart");
  const { spec, data } = ui;
  const columns = useMemo(() => [spec.x, ...spec.y_keys], [spec]);
  const rows = useMemo(() => data.map((d) => columns.map((c) => d[c] ?? null)), [data, columns]);
  const legendKeys =
    spec.chart_type === "pie" ? pieData(data, spec.x, spec.y_keys[0]).map((s) => s.name) : spec.y_keys;

  return (
    <figure className="overflow-hidden rounded-xl border border-line bg-surface">
      <div className="flex items-start gap-3 px-4 pt-3.5">
        <div className="min-w-0 flex-1">
          <figcaption className="font-medium text-ink">{spec.title}</figcaption>
          {spec.description && <p className="mt-0.5 text-sm text-ink-2">{spec.description}</p>}
        </div>
        <div className="flex shrink-0 items-center gap-0.5">
          <IconButton label="Chart" active={view === "chart"} onClick={() => setView("chart")}>
            <BarChart3 className="size-4" />
          </IconButton>
          <IconButton label="Table view" active={view === "table"} onClick={() => setView("table")}>
            <Table2 className="size-4" />
          </IconButton>
          <IconButton label="SQL" active={view === "sql"} onClick={() => setView("sql")}>
            <Code2 className="size-4" />
          </IconButton>
          <IconButton label="Download CSV" onClick={() => downloadCSV(spec.title, columns, rows)}>
            <Download className="size-4" />
          </IconButton>
        </div>
      </div>
      <div className="px-4 pt-3 pb-4">
        {view === "chart" && (
          <>
            <Legend keys={legendKeys} />
            <div className="h-72 w-full">
              <ResponsiveContainer width="100%" height="100%">
                {ChartBody({ ui })}
              </ResponsiveContainer>
            </div>
          </>
        )}
        {view === "table" && <DataTable columns={columns} rows={rows} maxHeight={288} />}
        {view === "sql" && (
          <pre className="max-h-72 overflow-auto rounded-lg border border-line bg-surface-2 p-3 font-mono text-xs leading-relaxed whitespace-pre-wrap text-ink-2">
            {spec.sql}
          </pre>
        )}
        {ui.truncated && <p className="mt-2 text-xs text-muted">Showing the first {data.length} points.</p>}
      </div>
    </figure>
  );
}
