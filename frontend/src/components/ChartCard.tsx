import clsx from "clsx";
import { BarChart3, Download, Table2 } from "lucide-react";
import { useId, useMemo, useState } from "react";
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
  type PieLabelRenderProps,
  type TooltipContentProps,
  type TooltipValueType,
} from "recharts";
import type { Cell, ChartUI } from "../api/types";
import { dateLabeler, downloadCSV, formatCell, formatCompact } from "../lib/format";
import { CHART, OTHER, SERIES, seriesColor } from "../lib/palette";
import { niceScale, type NiceScale } from "../lib/axis";
import { pieRings, type Ring } from "../lib/pie";
import { DataTable } from "./DataTable";
import { IconButton } from "./ui";

const AXIS_TICK = { fill: CHART.muted, fontSize: 11 };

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

function Legend({ keys, color }: { keys: string[]; color: (key: string, i: number) => string }) {
  if (keys.length < 2) return null;
  return (
    <div className="mb-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-2">
      {keys.map((k, i) => (
        <span key={k} className="flex items-center gap-1.5">
          <span className="size-2.5 rounded-sm" style={{ background: color(k, i) }} />
          {k}
        </span>
      ))}
    </div>
  );
}

/** Arrowhead for the axis lines, so each axis reads as pointing towards larger values. */
function ArrowDefs({ id }: { id: string }) {
  return (
    <defs>
      <marker id={id} viewBox="0 0 10 10" refX="8" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse">
        <path d="M0,1 L10,5 L0,9 z" fill={CHART.axisStrong} />
      </marker>
    </defs>
  );
}

function ChartBody({ ui, arrowId }: { ui: ChartUI; arrowId: string }) {
  const { spec } = ui;
  const labelX = dateLabeler(ui.data.map((d) => d[spec.x]));
  const data = labelX ? ui.data.map((d) => ({ ...d, [spec.x]: labelX(d[spec.x]) })) : ui.data;
  const keys = spec.y_keys;
  const grid = <CartesianGrid stroke={CHART.grid} vertical={false} />;
  // Recharts draws the x axis left to right and the y axis top to bottom, so the arrows go on the
  // x line's end and the y line's start.
  const xLine = { stroke: CHART.axisStrong, markerEnd: `url(#${arrowId})` };
  const yLine = { stroke: CHART.axisStrong, markerStart: `url(#${arrowId})` };
  const defs = <ArrowDefs id={arrowId} />;
  const xAxis = <XAxis dataKey={spec.x} tick={AXIS_TICK} tickLine={false} axisLine={xLine} minTickGap={16} />;
  // Value axes run on round, evenly spaced ticks. Bars and areas are read against zero; a line is read by
  // its shape, so its axis hugs the data.
  const values = data.flatMap((d) => keys.map((k) => d[k]));
  const scale = (s: NiceScale | null) => (s ? { domain: s.domain, ticks: s.ticks, interval: 0 as const } : {});
  const valueScale = scale(niceScale(values, { zero: spec.chart_type !== "line" && spec.chart_type !== "scatter" }));
  const yAxis = <YAxis tick={AXIS_TICK} tickLine={false} axisLine={yLine} tickFormatter={formatCompact} width={52} {...valueScale} />;
  const tooltip = (cursor: object | boolean = { stroke: CHART.axis }) => (
    <Tooltip content={ChartTooltip} cursor={cursor} isAnimationActive={false} />
  );
  const margin = { top: 14, right: 18, left: 0, bottom: 0 };

  switch (spec.chart_type) {
    case "bar":
      return (
        <BarChart data={data} margin={margin} barGap={2} barCategoryGap="20%">
          {defs}
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
          {defs}
          <CartesianGrid stroke={CHART.grid} horizontal={false} />
          <XAxis type="number" tick={AXIS_TICK} tickLine={false} axisLine={xLine} tickFormatter={formatCompact} {...valueScale} />
          <YAxis type="category" dataKey={spec.x} tick={AXIS_TICK} tickLine={false} axisLine={yLine} width={120} />
          {tooltip({ fill: "rgba(255,255,255,0.04)" })}
          {keys.map((k, i) => (
            <Bar key={k} dataKey={k} name={k} fill={seriesColor(k, i)} maxBarSize={24} radius={[0, 4, 4, 0]} />
          ))}
        </BarChart>
      );
    case "line":
      return (
        <LineChart data={data} margin={margin}>
          {defs}
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
          {defs}
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
          {defs}
          <CartesianGrid stroke={CHART.grid} />
          <XAxis
            dataKey={spec.x}
            type="number"
            name={spec.x}
            tick={AXIS_TICK}
            tickLine={false}
            axisLine={xLine}
            tickFormatter={formatCompact}
            {...scale(niceScale(data.map((d) => d[spec.x])))}
          />
          <YAxis
            dataKey={keys[0]}
            type="number"
            name={keys[0]}
            tick={AXIS_TICK}
            tickLine={false}
            axisLine={yLine}
            tickFormatter={formatCompact}
            width={52}
            {...valueScale}
          />
          {tooltip({ strokeDasharray: "0", stroke: CHART.axis })}
          <Scatter data={data} name={keys[0]} fill={SERIES[0]} stroke={CHART.surface} strokeWidth={2} />
        </ScatterChart>
      );
    case "pie":
      return null; // drawn by PieRings
  }
}

const sliceColor = (name: string, i: number) => (name === "Other" ? OTHER : SERIES[i % SERIES.length]);

/** Percent labels inside the ring; slivers stay unlabeled and keep their tooltip. */
function percentLabel({ cx, cy, midAngle, innerRadius, outerRadius, percent }: PieLabelRenderProps) {
  if (!percent || percent < 0.04) return null;
  const r = (Number(innerRadius) + Number(outerRadius)) / 2;
  const a = (-Number(midAngle) * Math.PI) / 180;
  return (
    <text
      x={Number(cx) + r * Math.cos(a)}
      y={Number(cy) + r * Math.sin(a)}
      fill="#fff"
      fontSize={11}
      fontWeight={500}
      textAnchor="middle"
      dominantBaseline="central"
    >
      {`${Math.round(percent * 100)}%`}
    </text>
  );
}

function PieTooltip({ active, payload }: TooltipContentProps<TooltipValueType, string | number>, total: number) {
  if (!active || !payload?.length) return null;
  const p = payload[0];
  const value = Number(p.value ?? 0);
  return (
    <div className="rounded-lg border border-line-strong bg-surface-2 px-3 py-2 text-xs shadow-xl">
      <div className="flex items-center gap-2">
        <span className="size-2 rounded-full" style={{ background: p.payload?.fill as string }} />
        <span className="text-ink-2">{p.name}</span>
        <span className="tabular ml-auto pl-4 font-medium text-ink">{formatCell(value)}</span>
      </div>
      {total > 0 && <div className="mt-0.5 text-right text-muted tabular">{((value / total) * 100).toFixed(1)}%</div>}
    </div>
  );
}

/** One donut per period (or value column) side by side, so a change in the mix is easy to see. */
function PieRings({ rings, label }: { rings: Ring[]; label: string | null }) {
  const many = rings.length > 1;
  return (
    <div className={clsx("grid gap-4", many && (rings.length === 2 ? "sm:grid-cols-2" : "sm:grid-cols-3"))}>
      {rings.map((ring) => {
        const total = ring.slices.reduce((s, x) => s + x.value, 0);
        return (
          <div key={ring.key} className="relative h-64">
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Tooltip content={(props) => PieTooltip(props, total)} isAnimationActive={false} />
                <Pie
                  data={ring.slices}
                  dataKey="value"
                  nameKey="name"
                  innerRadius="52%"
                  outerRadius="88%"
                  stroke={CHART.surface}
                  strokeWidth={2}
                  label={percentLabel}
                  labelLine={false}
                  isAnimationActive={false}
                >
                  {ring.slices.map((s, i) => (
                    <PieCell key={s.name} fill={sliceColor(s.name, i)} />
                  ))}
                </Pie>
              </PieChart>
            </ResponsiveContainer>
            <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center text-center">
              {many && <span className="text-base font-semibold text-ink">{ring.key}</span>}
              {label && <span className="max-w-24 text-[11px] leading-tight text-muted">{label}</span>}
            </div>
          </div>
        );
      })}
    </div>
  );
}

export function ChartCard({ ui }: { ui: ChartUI }) {
  const [view, setView] = useState<"chart" | "table">("chart");
  const arrowId = `arrow${useId().replace(/[^a-zA-Z0-9]/g, "")}`;
  const { spec, data } = ui;
  const columns = useMemo(() => [spec.x, ...spec.y_keys], [spec]);
  const rows = useMemo(() => data.map((d) => columns.map((c) => d[c] ?? null)), [data, columns]);
  const pie = useMemo(() => (spec.chart_type === "pie" ? pieRings(data, spec.x, spec.y_keys) : null), [spec, data]);
  // Each axis is titled at its far end: the value axis above its arrow, the x axis after its arrow.
  const valueLabel = spec.y_label || null;
  const xLabel = spec.x_label || spec.x;
  const [topLabel, endLabel] = spec.chart_type === "horizontal_bar" ? [xLabel, valueLabel] : [valueLabel, xLabel];

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
          <IconButton label="Download CSV" onClick={() => downloadCSV(spec.title, columns, rows)}>
            <Download className="size-4" />
          </IconButton>
        </div>
      </div>
      <div className="px-4 pt-3 pb-4">
        {view === "chart" &&
          (pie ? (
            <>
              <Legend keys={pie.categories} color={sliceColor} />
              <PieRings rings={pie.rings} label={valueLabel} />
            </>
          ) : (
            <>
              <Legend keys={spec.y_keys} color={seriesColor} />
              {topLabel && <div className="mb-0.5 text-[11px] font-medium text-muted">{topLabel}</div>}
              <div className="h-72 w-full">
                <ResponsiveContainer width="100%" height="100%">
                  {ChartBody({ ui, arrowId })}
                </ResponsiveContainer>
              </div>
              {endLabel && <div className="mt-0.5 text-right text-[11px] font-medium text-muted">{endLabel}</div>}
            </>
          ))}
        {view === "table" && <DataTable columns={columns} rows={rows} maxHeight={288} />}
        {ui.truncated && <p className="mt-2 text-xs text-muted">Showing the first {data.length} points.</p>}
      </div>
    </figure>
  );
}
