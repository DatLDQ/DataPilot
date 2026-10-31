import { describe, expect, it } from "vitest";
import { niceScale } from "./axis";

const steps = (ticks: number[]) => ticks.slice(1).map((t, i) => Number((t - ticks[i]).toPrecision(12)));

describe("niceScale", () => {
  it("hugs the data with round, evenly spaced ticks", () => {
    const s = niceScale([35_420, 35_400, 36_010, 36_780, 37_100, 35_260, 36_900, 37_520, 38_150])!;
    expect(s.ticks).toEqual([35_000, 36_000, 37_000, 38_000, 39_000]);
    expect(s.domain).toEqual([35_000, 39_000]);
  });

  it("covers the data and keeps one step everywhere", () => {
    for (const values of [[0.12, 0.47], [3, 97], [-42, 18], [24_500, 38_300], [1.2e9, 7.9e9]]) {
      const s = niceScale(values)!;
      expect(s.domain[0]).toBeLessThanOrEqual(Math.min(...values));
      expect(s.domain[1]).toBeGreaterThanOrEqual(Math.max(...values));
      expect(new Set(steps(s.ticks)).size).toBe(1);
      expect(s.ticks.length).toBeLessThanOrEqual(7);
    }
  });

  it("reaches zero when asked", () => {
    expect(niceScale([120, 340, 610], { zero: true })!.ticks).toEqual([0, 200, 400, 600, 800]);
  });

  it("handles a flat series and no numbers", () => {
    expect(niceScale([5, 5])!.domain[0]).toBeLessThan(5);
    expect(niceScale(["a", null])).toBeNull();
  });
});
