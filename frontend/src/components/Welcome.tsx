import { BarChart3, Brain, Database, Lightbulb, ShieldCheck, Sparkles, Upload } from "lucide-react";
import type { DatasetDetail } from "../api/types";
import { Button, Spinner } from "./ui";

const FEATURES = [
  { icon: Brain, title: "Agentic analysis", text: "Claude plans, writes SQL, checks results and iterates until it can answer." },
  { icon: BarChart3, title: "Charts on demand", text: "Trends, rankings and distributions rendered from live query results." },
  { icon: ShieldCheck, title: "Safe by design", text: "Read-only DuckDB sandbox with SQL validation and query timeouts." },
];

export function NoDataset({ busy, onUpload, onSample }: { busy: boolean; onUpload: () => void; onSample: () => void }) {
  return (
    <div className="mx-auto flex max-w-2xl flex-col items-center px-6 py-16 text-center">
      <div className="mb-5 flex size-14 items-center justify-center rounded-2xl bg-accent/15">
        <Database className="size-7 text-accent" />
      </div>
      <h1 className="text-2xl font-semibold text-ink">Ask your data anything</h1>
      <p className="mt-2 max-w-lg text-ink-2">
        Upload a CSV, Excel, Parquet or JSON file and DataPilot will explore it, run SQL, build charts and explain
        what it finds, showing every step along the way.
      </p>
      <div className="mt-6 flex gap-3">
        <Button variant="primary" onClick={onUpload} disabled={busy}>
          {busy ? <Spinner className="size-4" /> : <Upload className="size-4" />} Upload a file
        </Button>
        <Button onClick={onSample} disabled={busy}>
          <Sparkles className="size-4" /> Try the sample dataset
        </Button>
      </div>
      <div className="mt-12 grid w-full gap-3 text-left sm:grid-cols-3">
        {FEATURES.map((f) => (
          <div key={f.title} className="rounded-xl border border-line bg-surface p-4">
            <f.icon className="mb-2 size-5 text-accent" />
            <div className="text-sm font-medium text-ink">{f.title}</div>
            <div className="mt-1 text-xs leading-relaxed text-muted">{f.text}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

export function Suggestions({
  dataset,
  suggestions,
  onPick,
}: {
  dataset: DatasetDetail;
  suggestions: string[] | null;
  onPick: (q: string) => void;
}) {
  const rows = dataset.profile.tables.reduce((s, t) => s + t.row_count, 0);
  const cols = dataset.profile.tables.reduce((s, t) => s + t.columns.length, 0);
  return (
    <div className="mx-auto max-w-2xl px-6 py-14">
      <h1 className="text-2xl font-semibold text-ink">{dataset.name}</h1>
      <p className="tabular mt-1 text-ink-2">
        {dataset.profile.tables.length} {dataset.profile.tables.length === 1 ? "table" : "tables"} ·{" "}
        {rows.toLocaleString()} rows · {cols} columns
      </p>
      <div className="mt-8 mb-3 flex items-center gap-2 text-sm text-muted">
        <Lightbulb className="size-4" /> Suggested questions
      </div>
      {suggestions === null ? (
        <div className="grid gap-2 sm:grid-cols-2">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="h-16 animate-pulse rounded-xl border border-line bg-surface" />
          ))}
        </div>
      ) : (
        <div className="grid gap-2 sm:grid-cols-2">
          {suggestions.map((q) => (
            <button
              key={q}
              type="button"
              onClick={() => onPick(q)}
              className="rounded-xl border border-line bg-surface px-4 py-3 text-left text-sm text-ink-2 transition-colors hover:border-accent/50 hover:bg-surface-2 hover:text-ink"
            >
              {q}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
