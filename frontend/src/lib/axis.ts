export interface NiceScale {
  domain: [number, number];
  ticks: number[];
}

/** Round a raw step up to 1, 2, 2.5 or 5 times a power of ten, so every tick is a round number. */
function niceStep(raw: number): number {
  const power = 10 ** Math.floor(Math.log10(raw));
  const f = raw / power;
  const nice = f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10;
  return nice * power;
}

// Drop floating-point noise such as 0.30000000000000004.
const clean = (v: number) => Number(v.toPrecision(12));

/**
 * An evenly spaced axis on round numbers that covers every value: 34.9K..38.3K becomes 34K..39K in steps
 * of 1K. With `zero`, the axis also reaches zero (bars and areas are read against it). Returns null when
 * there is nothing numeric to scale.
 */
export function niceScale(values: unknown[], { zero = false, count = 5 } = {}): NiceScale | null {
  const nums = values.filter((v): v is number => typeof v === "number" && Number.isFinite(v));
  if (!nums.length) return null;
  let lo = Math.min(...nums);
  let hi = Math.max(...nums);
  if (zero) {
    lo = Math.min(lo, 0);
    hi = Math.max(hi, 0);
  }
  if (lo === hi) {
    // A flat series: give it room around its one value.
    const pad = lo === 0 ? 1 : Math.abs(lo) * 0.1;
    lo = zero && lo >= 0 ? lo : lo - pad;
    hi = zero && hi <= 0 ? hi : hi + pad;
  }
  let step = niceStep((hi - lo) / (count - 1));
  let start = Math.floor(lo / step) * step;
  let end = Math.ceil(hi / step) * step;
  // Snapping both ends outwards can add a tick or two; widen the step when it adds too many.
  while ((end - start) / step > count + 1) {
    step = niceStep(step * 1.5);
    start = Math.floor(lo / step) * step;
    end = Math.ceil(hi / step) * step;
  }
  const ticks: number[] = [];
  for (let i = 0; start + i * step <= end + step / 1e6; i++) ticks.push(clean(start + i * step));
  return { domain: [clean(start), clean(end)], ticks };
}
