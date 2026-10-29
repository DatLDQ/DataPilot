/**
 * Categorical series colors, dark-mode steps of the validated reference palette.
 * Assigned in fixed order (never cycled); the backend caps charts at 8 series and
 * folds the remainder into "Other", which gets the neutral muted gray.
 */
export const SERIES = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"];
export const OTHER = "#6b6a66";

export const CHART = {
  surface: "#1a1a19",
  grid: "#2c2c2a",
  axis: "#383835",
  muted: "#898781",
  ink2: "#c3c2b7",
};

export function seriesColor(key: string, index: number): string {
  return key === "Other" ? OTHER : SERIES[index % SERIES.length];
}
