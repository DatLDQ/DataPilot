import clsx from "clsx";
import type { Cell } from "../api/types";
import { formatCell } from "../lib/format";

interface Props {
  columns: string[];
  rows: Cell[][];
  maxHeight?: number;
  footer?: string;
}

export function DataTable({ columns, rows, maxHeight = 320, footer }: Props) {
  const numeric = columns.map((_, i) => rows.length > 0 && rows.every((r) => r[i] === null || typeof r[i] === "number"));
  return (
    <div className="overflow-hidden rounded-lg border border-line">
      <div className="overflow-auto" style={{ maxHeight }}>
        <table className="tabular w-full border-collapse text-[13px]">
          <thead className="sticky top-0 z-10 bg-surface-2">
            <tr>
              {columns.map((c, i) => (
                <th
                  key={`${c}-${i}`}
                  className={clsx(
                    "whitespace-nowrap border-b border-line px-3 py-2 font-medium text-ink-2",
                    numeric[i] ? "text-right" : "text-left",
                  )}
                >
                  {c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, r) => (
              <tr key={r} className="hover:bg-surface-2/60">
                {row.map((v, i) => (
                  <td
                    key={i}
                    className={clsx(
                      "max-w-[320px] truncate whitespace-nowrap border-b border-line/60 px-3 py-1.5",
                      numeric[i] ? "text-right" : "text-left",
                      v === null ? "italic text-muted" : "text-ink",
                    )}
                    title={formatCell(v)}
                  >
                    {formatCell(v)}
                  </td>
                ))}
              </tr>
            ))}
            {rows.length === 0 && (
              <tr>
                <td colSpan={columns.length || 1} className="px-3 py-4 text-center text-muted">
                  No rows
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      {footer && <div className="border-t border-line bg-surface-2 px-3 py-1.5 text-xs text-muted">{footer}</div>}
    </div>
  );
}
