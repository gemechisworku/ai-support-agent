// Display formatting for console API values (pure; unit-tested). Money arrives as an exact decimal
// string and is formatted without going through floating point. Dates are ISO strings.

export const DASH = "—";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "1234.5" → "$1,234.50"; "-20" → "-$20.00"; "0.125" → "$0.13"; null → "—". */
export function money(value: string | null | undefined): string {
  if (value === null || value === undefined || value === "") return DASH;
  const match = /^(-?)(\d+)(?:\.(\d+))?$/.exec(value.trim());
  if (!match) return value;
  const [, sign, whole, fraction = ""] = match;
  let cents = BigInt(whole + fraction.padEnd(2, "0").slice(0, 2));
  if (fraction.length > 2 && Number(fraction[2]) >= 5) cents += 1n; // round half up
  const digits = cents.toString().padStart(3, "0");
  const units = digits.slice(0, -2).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `${sign}$${units}.${digits.slice(-2)}`;
}

/** "2026-11-04" → "4 Nov 2026". */
export function date(value: string | null | undefined): string {
  if (!value) return DASH;
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(value);
  if (!match) return value;
  const [, y, m, d] = match;
  return `${Number(d)} ${MONTHS[Number(m) - 1]} ${y}`;
}

/** An ISO timestamp → "9 Nov 2026 14:00 UTC" (the console shows UTC). */
export function dateTime(value: string | null | undefined): string {
  if (!value) return DASH;
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  const iso = parsed.toISOString();
  return `${date(iso)} ${iso.slice(11, 16)} UTC`;
}

export function yesNo(value: boolean | null | undefined): string {
  if (value === null || value === undefined) return DASH;
  return value ? "yes" : "no";
}

export function text(value: string | number | null | undefined): string {
  return value === null || value === undefined || value === "" ? DASH : String(value);
}

export function plural(count: number, noun: string): string {
  return `${count.toLocaleString("en-US")} ${noun}${count === 1 ? "" : "s"}`;
}
