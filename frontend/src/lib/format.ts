import type { Cell } from "../api/types";

const compact = new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 });
const plain = new Intl.NumberFormat("en", { maximumFractionDigits: 2 });

export function formatCompact(v: unknown): string {
  if (typeof v !== "number" || !Number.isFinite(v)) return String(v ?? "");
  return Math.abs(v) >= 1000 ? compact.format(v) : plain.format(v);
}

export function formatCell(v: Cell | Cell[] | undefined): string {
  if (v === null || v === undefined) return "NULL";
  if (Array.isArray(v)) return `[${v.map((x) => formatCell(x)).join(", ")}]`;
  if (typeof v === "number") return plain.format(v);
  // Date-only values arrive as midnight timestamps; the time part is noise.
  if (typeof v === "string") return v.replace(/^(\d{4}-\d{2}-\d{2})[T ]00:00:00(?:\.0+)?$/, "$1");
  return String(v);
}

export function formatMs(ms: number): string {
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;
}

export function formatTokens(n: number): string {
  return n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n);
}

export function formatCost(usd: number | null | undefined): string {
  if (usd === null || usd === undefined) return "n/a";
  return usd < 0.01 ? `$${usd.toFixed(4)}` : `$${usd.toFixed(3)}`;
}

function csvEscape(v: unknown): string {
  if (v === null || v === undefined) return "";
  const s = String(v);
  return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

export function toCSV(columns: string[], rows: unknown[][]): string {
  return [columns, ...rows].map((r) => r.map(csvEscape).join(",")).join("\r\n");
}

export function downloadCSV(filename: string, columns: string[], rows: unknown[][]): void {
  const blob = new Blob(["﻿" + toCSV(columns, rows)], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${filename.replace(/[^\p{L}\p{N}_-]+/gu, "_").slice(0, 60) || "data"}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

export function isNumericType(type: string): boolean {
  return /INT|DECIMAL|DOUBLE|FLOAT|REAL|NUMERIC/i.test(type);
}

const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}):(\d{2})(?:\.\d+)?)?$/;
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

const isMidnight = (m: RegExpExecArray) => !m[4] || (m[4] === "00" && m[5] === "00" && m[6] === "00");

/**
 * Pick one label format for a whole column of date-like values, so a monthly series reads
 * "Mar 2024" on every tick instead of "2024-03-01T00:00:00". Returns null for non-date columns.
 */
export function dateLabeler(values: unknown[]): ((v: Cell) => Cell) | null {
  const present = values.filter((v) => v !== null && v !== undefined);
  if (!present.length || !present.every((v) => typeof v === "string" && ISO_DATE.test(v))) return null;
  const parts = present.map((v) => ISO_DATE.exec(v as string)!);
  if (!parts.every(isMidnight)) return null;
  const monthly = parts.every((m) => m[3] === "01");
  const yearly = monthly && present.length > 1 && parts.every((m) => m[2] === "01");
  return (v) => {
    const m = typeof v === "string" ? ISO_DATE.exec(v) : null;
    if (!m) return v;
    if (yearly) return m[1];
    if (monthly) return `${MONTHS[Number(m[2]) - 1]} ${m[1]}`;
    return `${m[1]}-${m[2]}-${m[3]}`;
  };
}
