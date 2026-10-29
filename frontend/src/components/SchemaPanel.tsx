import clsx from "clsx";
import { Calendar, Eye, Hash, PanelRightClose, Table2, ToggleLeft, Type, X } from "lucide-react";
import { useState } from "react";
import { api } from "../api/client";
import type { ColumnProfile, DatasetDetail, SqlResultUI } from "../api/types";
import { formatCell, isNumericType } from "../lib/format";
import { DataTable } from "./DataTable";
import { IconButton, Spinner } from "./ui";

function TypeIcon({ type }: { type: string }) {
  const cls = "size-3.5 shrink-0 text-muted";
  if (isNumericType(type)) return <Hash className={cls} />;
  if (/DATE|TIME/i.test(type)) return <Calendar className={cls} />;
  if (/BOOL/i.test(type)) return <ToggleLeft className={cls} />;
  return <Type className={cls} />;
}

function ColumnRow({ col }: { col: ColumnProfile }) {
  const nullPct = Number(col.null_pct ?? 0);
  return (
    <li className="rounded-md px-2 py-1.5 hover:bg-surface-2">
      <div className="flex items-center gap-2">
        <TypeIcon type={col.type} />
        <span className="min-w-0 flex-1 truncate font-mono text-[13px] text-ink">{col.name}</span>
        <span className="shrink-0 text-[11px] text-muted">{col.type.toLowerCase()}</span>
      </div>
      <div className="tabular mt-0.5 flex gap-3 pl-5.5 text-[11px] text-muted">
        {col.approx_unique !== null && <span>~{col.approx_unique.toLocaleString()} distinct</span>}
        {nullPct > 0 && <span className={clsx(nullPct >= 10 && "text-warning")}>{nullPct.toFixed(1)}% null</span>}
      </div>
      {col.min !== null && col.max !== null && (
        <div className="truncate pl-5.5 text-[11px] text-muted" title={`${formatCell(col.min)} – ${formatCell(col.max)}`}>
          {formatCell(col.min)} – {formatCell(col.max)}
        </div>
      )}
    </li>
  );
}

export function SchemaPanel({ dataset, onClose }: { dataset: DatasetDetail; onClose: () => void }) {
  const [preview, setPreview] = useState<{ table: string; data: SqlResultUI | null } | null>(null);

  const openPreview = async (table: string) => {
    setPreview({ table, data: null });
    try {
      setPreview({ table, data: await api.preview(dataset.id, table, 100) });
    } catch {
      setPreview(null);
    }
  };

  return (
    <aside className="hidden h-full w-80 shrink-0 flex-col border-l border-line bg-surface xl:flex">
      <div className="flex items-center justify-between px-4 pt-4 pb-2">
        <div className="min-w-0">
          <div className="truncate font-medium text-ink">{dataset.name}</div>
          <div className="truncate text-xs text-muted">{dataset.filename}</div>
        </div>
        <IconButton label="Hide schema" onClick={onClose}>
          <PanelRightClose className="size-4" />
        </IconButton>
      </div>
      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-2 pb-4">
        {dataset.profile.tables.map((t) => (
          <div key={t.name}>
            <div className="sticky top-0 z-10 flex items-center gap-2 bg-surface px-2 py-1.5">
              <Table2 className="size-4 text-accent" />
              <span className="min-w-0 flex-1 truncate font-mono text-sm text-ink">{t.name}</span>
              <span className="tabular text-[11px] text-muted">
                {t.row_count.toLocaleString()} × {t.columns.length}
              </span>
              <IconButton label={`Preview ${t.name}`} onClick={() => void openPreview(t.name)}>
                <Eye className="size-3.5" />
              </IconButton>
            </div>
            <ul>
              {t.columns.map((c) => (
                <ColumnRow key={c.name} col={c} />
              ))}
            </ul>
          </div>
        ))}
      </div>

      {preview && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-6"
          onClick={() => setPreview(null)}
          role="dialog"
          aria-modal
          aria-label={`Preview of ${preview.table}`}
        >
          <div
            className="flex max-h-full w-full max-w-5xl flex-col rounded-xl border border-line bg-surface p-4"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="mb-3 flex items-center justify-between">
              <span className="font-mono text-sm text-ink">{preview.table} · first 100 rows</span>
              <IconButton label="Close" onClick={() => setPreview(null)}>
                <X className="size-4" />
              </IconButton>
            </div>
            {preview.data ? (
              <DataTable columns={preview.data.columns} rows={preview.data.rows} maxHeight={600} />
            ) : (
              <div className="flex justify-center py-10">
                <Spinner className="size-5 text-muted" />
              </div>
            )}
          </div>
        </div>
      )}
    </aside>
  );
}
