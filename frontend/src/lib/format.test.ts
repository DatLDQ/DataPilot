import { describe, expect, it } from "vitest";
import { dateLabeler, formatCell } from "./format";

describe("dateLabeler", () => {
  it("labels monthly series as month and year", () => {
    const fmt = dateLabeler(["2024-03-01T00:00:00", "2024-04-01T00:00:00"])!;
    expect(fmt("2024-03-01T00:00:00")).toBe("Mar 2024");
  });

  it("labels yearly series as years and daily series as dates", () => {
    expect(dateLabeler(["2024-01-01", "2025-01-01"])!("2025-01-01")).toBe("2025");
    expect(dateLabeler(["2024-03-05T00:00:00", "2024-03-06T00:00:00"])!("2024-03-05T00:00:00")).toBe("2024-03-05");
  });

  it("leaves non-date and timed columns alone", () => {
    expect(dateLabeler(["HCM", "Hà Nội"])).toBeNull();
    expect(dateLabeler(["2024-03-01T10:30:00"])).toBeNull();
  });
});

describe("formatCell", () => {
  it("drops a midnight time suffix but keeps real times", () => {
    expect(formatCell("2024-03-01T00:00:00")).toBe("2024-03-01");
    expect(formatCell("2024-03-01T10:30:00")).toBe("2024-03-01T10:30:00");
  });
});
