import type { Cell } from "../api/types";

export const MAX_PIE_SLICES = 8;

export interface Slice {
  name: string;
  value: number;
}

export interface Ring {
  key: string;
  slices: Slice[];
}

const num = (v: Cell | undefined) => (typeof v === "number" && Number.isFinite(v) ? v : 0);

/**
 * One donut per value key (a single key, or one per period when the chart has a series). Every donut
 * lists the same categories in the same order, so a category keeps its color from one donut to the next.
 * Beyond MAX_PIE_SLICES the smallest categories fold into "Other".
 */
export function pieRings(data: Record<string, Cell>[], x: string, keys: string[]): { categories: string[]; rings: Ring[] } {
  const totals = new Map<string, number>();
  for (const row of data) {
    const name = String(row[x]);
    totals.set(name, (totals.get(name) ?? 0) + keys.reduce((s, k) => s + num(row[k]), 0));
  }
  const ranked = [...totals.keys()].filter((n) => n !== "Other").sort((a, b) => totals.get(b)! - totals.get(a)!);
  const folds = ranked.length + (totals.has("Other") ? 1 : 0) > MAX_PIE_SLICES;
  const kept = folds ? ranked.slice(0, MAX_PIE_SLICES - 1) : ranked;
  const categories = folds || totals.has("Other") ? [...kept, "Other"] : kept;
  const keep = new Set(kept);

  const rings = orderKeys(keys).map((key) => {
    const values = new Map<string, number>();
    for (const row of data) {
      const name = keep.has(String(row[x])) ? String(row[x]) : "Other";
      values.set(name, (values.get(name) ?? 0) + num(row[key]));
    }
    return { key, slices: categories.map((name) => ({ name, value: values.get(name) ?? 0 })) };
  });
  return { categories, rings };
}

/** Periods read left to right in time order; other keys keep the order they came in. */
function orderKeys(keys: string[]): string[] {
  const real = keys.filter((k) => k !== "Other");
  const sortable = real.length > 1 && real.every((k) => /^\d{4}(-\d{2}(-\d{2})?)?$|^-?\d+(\.\d+)?$/.test(k));
  if (!sortable) return keys;
  const sorted = [...real].sort((a, b) => (/^\d+(\.\d+)?$/.test(a) && /^\d+(\.\d+)?$/.test(b) ? Number(a) - Number(b) : a.localeCompare(b)));
  return keys.includes("Other") ? [...sorted, "Other"] : sorted;
}
