// Display formatting. The API sends money as exact decimal strings; we round those strings
// (half-up) instead of converting to floating point, so the page shows what the ledger holds.

export function usd(value: string | null | undefined, digits?: number): string {
  if (value === null || value === undefined) return "—";
  const negative = value.trim().startsWith("-");
  const [intPart, frac = ""] = value.trim().replace(/^[-+]/, "").split(".");
  // Sub-cent amounts keep 4 decimals so they do not read as $0.00.
  const places = digits ?? (intPart === "0" && /^00/.test(frac) && /[1-9]/.test(frac) ? 4 : 2);
  const rounded = roundDecimal(intPart, frac, places);
  const sign = negative && /[1-9]/.test(rounded) ? "−" : "";
  return `${sign}$${rounded}`;
}

export function count(value: string | number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  const [intPart] = String(value).split(".");
  return groupThousands(intPart);
}

export function percent(value: string | null | undefined): string {
  // The API already rounds percentages ("3.58", "-17.53").
  if (value === null || value === undefined) return "—";
  return `${value.replace(/^-/, "−")}%`;
}

export function ratioPercent(value: string | null | undefined): string {
  // A ratio as a whole percentage: "0.0312" -> "3%". Moves the decimal point two places.
  if (value === null || value === undefined) return "—";
  const [intPart, frac = ""] = value.split(".");
  const hundreds = (intPart + frac.padEnd(2, "0").slice(0, 2)).replace(/^0+(?=\d)/, "");
  return `${roundDecimal(hundreds, frac.slice(2), 0)}%`;
}

function roundDecimal(intPart: string, frac: string, places: number): string {
  const padded = frac.padEnd(places + 1, "0");
  let n = BigInt((intPart || "0") + padded.slice(0, places));
  if (Number(padded[places]) >= 5) n += 1n;
  const digits = n.toString().padStart(places + 1, "0");
  const whole = groupThousands(digits.slice(0, digits.length - places));
  return places ? `${whole}.${digits.slice(digits.length - places)}` : whole;
}

function groupThousands(digits: string): string {
  return digits.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

// Shares for the bars, in exact decimal arithmetic: 0–100, rounded half-up. Null when there is
// nothing to divide by.
export function sharePercent(part: string | null | undefined, whole: string | null | undefined): number | null {
  if (part === null || part === undefined || whole === null || whole === undefined) return null;
  const p = units(part);
  const w = units(whole);
  if (w <= 0n || p < 0n) return null;
  return Number((p * 200n + w) / (2n * w));
}

export function compareDecimal(a: string, b: string): number {
  const diff = units(a) - units(b);
  return diff > 0n ? 1 : diff < 0n ? -1 : 0;
}

// A decimal string as an integer number of 1e-10 units (the ledger's scale).
function units(value: string): bigint {
  const trimmed = value.trim();
  const [intPart, frac = ""] = trimmed.replace(/^[-+]/, "").split(".");
  const n = BigInt((intPart || "0") + frac.padEnd(10, "0").slice(0, 10));
  return trimmed.startsWith("-") ? -n : n;
}
