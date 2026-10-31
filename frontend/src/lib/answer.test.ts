import { describe, expect, it } from "vitest";
import type { ChartUI } from "../api/types";
import { layoutAnswer } from "./answer";

const chart = (title: string) => ({ spec: { title } }) as unknown as ChartUI;
const charts = new Map([
  [1, chart("Revenue")],
  [2, chart("Margin")],
  [3, chart("Orders")],
]);

const shape = (parts: ReturnType<typeof layoutAnswer>) =>
  parts.map((p) => (p.kind === "chart" ? `chart ${p.id}` : p.text.trim()));

describe("layoutAnswer", () => {
  it("places charts where the answer puts them and appends the rest", () => {
    const text = "Revenue grew.\n\n[[chart:1]]\n\nMargins fell. `[[chart:2]]`\n\nDone.";
    expect(shape(layoutAnswer(text, charts, false))).toEqual([
      "Revenue grew.",
      "chart 1",
      "Margins fell.",
      "chart 2",
      "Done.",
      "chart 3",
    ]);
  });

  it("shows a chart once and drops placeholders for charts that do not exist", () => {
    const text = "[[chart:1]] again [[chart:1]] and [[chart:9]]";
    expect(shape(layoutAnswer(text, new Map([[1, chart("Revenue")]]), false))).toEqual([
      "chart 1",
      "again",
      "and",
    ]);
  });

  it("hides a half-written placeholder and unplaced charts while streaming", () => {
    expect(shape(layoutAnswer("Revenue grew.\n\n[[cha", charts, true))).toEqual(["Revenue grew."]);
    expect(shape(layoutAnswer("Revenue grew.\n\n[[chart:1", charts, true))).toEqual(["Revenue grew."]);
  });
});
