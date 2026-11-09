import { describe, expect, it } from "vitest";

import { DASH, date, dateTime, money, plural, text, yesNo } from "@/lib/format";

describe("money", () => {
  it.each([
    ["200.72", "$200.72"],
    ["1234.5", "$1,234.50"],
    ["1234567.00", "$1,234,567.00"],
    ["0", "$0.00"],
    ["0.125", "$0.13"],
    ["0.124", "$0.12"],
    ["9.995", "$10.00"],
    ["-20", "-$20.00"],
  ])("%s → %s", (input, expected) => {
    expect(money(input)).toBe(expected);
  });

  it("shows a dash for missing values and passes through non-decimals", () => {
    expect(money(null)).toBe(DASH);
    expect(money(undefined)).toBe(DASH);
    expect(money("")).toBe(DASH);
    expect(money("n/a")).toBe("n/a");
  });

  it("never goes through floating point", () => {
    expect(money("90071992547409930.99")).toBe("$90,071,992,547,409,930.99");
  });
});

describe("dates", () => {
  it("formats ISO dates without a timezone shift", () => {
    expect(date("2026-11-04")).toBe("4 Nov 2026");
    expect(date("2026-01-31")).toBe("31 Jan 2026");
    expect(date(null)).toBe(DASH);
  });

  it("formats timestamps in UTC", () => {
    expect(dateTime("2026-11-09T14:00:00Z")).toBe("9 Nov 2026 14:00 UTC");
    expect(dateTime("2026-11-09T09:30:00-05:00")).toBe("9 Nov 2026 14:30 UTC");
    expect(dateTime("not a date")).toBe("not a date");
    expect(dateTime(undefined)).toBe(DASH);
  });
});

describe("small helpers", () => {
  it("yesNo, text and plural", () => {
    expect(yesNo(true)).toBe("yes");
    expect(yesNo(false)).toBe("no");
    expect(yesNo(null)).toBe(DASH);
    expect(text("")).toBe(DASH);
    expect(text(0)).toBe("0");
    expect(plural(1, "loan")).toBe("1 loan");
    expect(plural(1200, "loan")).toBe("1,200 loans");
  });
});
