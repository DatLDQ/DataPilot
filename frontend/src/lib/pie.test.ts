import { describe, expect, it } from "vitest";
import { MAX_PIE_SLICES, pieRings } from "./pie";

describe("pieRings", () => {
  it("makes one donut per period, oldest first, with the same categories in each", () => {
    const data = [
      { source: "Coal", "2023": 44, "2010": 40 },
      { source: "Oil", "2023": 32, "2010": 36 },
      { source: "Gas", "2023": 24, "2010": 24 },
    ];
    const { categories, rings } = pieRings(data, "source", ["2023", "2010"]);
    expect(categories).toEqual(["Coal", "Oil", "Gas"]);
    expect(rings.map((r) => r.key)).toEqual(["2010", "2023"]);
    expect(rings[0].slices).toEqual([
      { name: "Coal", value: 40 },
      { name: "Oil", value: 36 },
      { name: "Gas", value: 24 },
    ]);
  });

  it("keeps a single donut for one value column", () => {
    const { rings } = pieRings([{ c: "a", v: 1 }, { c: "b", v: 3 }], "c", ["v"]);
    expect(rings).toEqual([{ key: "v", slices: [{ name: "b", value: 3 }, { name: "a", value: 1 }] }]);
  });

  it("folds the smallest categories into Other", () => {
    const data = Array.from({ length: 12 }, (_, i) => ({ c: `c${i}`, v: 12 - i }));
    const { categories, rings } = pieRings(data, "c", ["v"]);
    expect(categories).toHaveLength(MAX_PIE_SLICES);
    expect(categories.at(-1)).toBe("Other");
    const other = rings[0].slices.at(-1)!;
    expect(other.value).toBe([5, 4, 3, 2, 1].reduce((a, b) => a + b));
  });
});
