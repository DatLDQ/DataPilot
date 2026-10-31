import type { ChartUI } from "../api/types";

export type AnswerPart = { kind: "text"; text: string } | { kind: "chart"; id: number; ui: ChartUI };

// The agent places a chart by writing [[chart:N]] in its answer (sometimes wrapped in backticks).
const PLACEHOLDER = /`?\[\[chart:(\d+)\]\]`?/g;
// While the answer streams, a placeholder can arrive half-written: hide it until it is complete.
const PARTIAL = /`?\[(\[(c(h(a(r(t(:\d*)?)?)?)?)?)?)?$/;

/**
 * Split the answer into text and the charts it places. Charts it does not place go at the end,
 * so a chart is never lost, and a chart placed twice is shown once.
 */
export function layoutAnswer(text: string, charts: Map<number, ChartUI>, streaming: boolean): AnswerPart[] {
  const source = streaming ? text.replace(PARTIAL, "") : text;
  const parts: AnswerPart[] = [];
  const placed = new Set<number>();
  let last = 0;
  const pushText = (chunk: string) => {
    if (chunk.trim()) parts.push({ kind: "text", text: chunk });
  };
  for (const match of source.matchAll(PLACEHOLDER)) {
    pushText(source.slice(last, match.index));
    last = match.index + match[0].length;
    const id = Number(match[1]);
    const ui = charts.get(id);
    if (ui && !placed.has(id)) {
      placed.add(id);
      parts.push({ kind: "chart", id, ui });
    }
  }
  pushText(source.slice(last));
  if (!streaming) {
    for (const [id, ui] of charts) if (!placed.has(id)) parts.push({ kind: "chart", id, ui });
  }
  return parts;
}
