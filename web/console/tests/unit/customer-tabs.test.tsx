import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  pageCount,
  parsePage,
  parseTab,
  searchHref,
  searchQuery,
  searchState,
  tabHref,
} from "@/components/customers/model";
import { HistorySection, ScheduleSection, TransactionsSection } from "@/components/customers/sections";
import { TabLinks } from "@/components/customers/tab-links";
import type { Schemas } from "@/lib/api/client";

const loan = {
  loan_id: 9002,
  customer_id: 9002,
  number: "•••• 4417",
  product_code: "PERSONAL",
  product_name: "Personal Loan",
  status: "active",
  as_of: "2026-11-09",
  outstanding_balance: "3813.79",
  overdue_amount: "200.72",
  oldest_unpaid_due_date: "2026-11-04",
  next_due_date: "2026-12-04",
  next_due_amount: "200.72",
  days_past_due: 5,
  bucket: "1-30",
  fees_accrued: "0.00",
  headline: "•••• 4417 · Personal Loan · 5 DPD · $200.72 overdue since 4 November 2026",
  installments: [
    {
      seq: 17,
      due_date: "2026-10-04",
      principal_due: "180.00",
      interest_due: "20.72",
      fee_due: "0.00",
      paid_amount: "200.72",
      paid_date: "2026-10-04",
      status: "paid",
    },
    {
      seq: 18,
      due_date: "2026-11-04",
      principal_due: "181.00",
      interest_due: "19.72",
      fee_due: "0.00",
      paid_amount: "0.00",
      paid_date: null,
      status: "missed",
    },
    {
      seq: 19,
      due_date: "2026-12-04",
      principal_due: "182.00",
      interest_due: "18.72",
      fee_due: "0.00",
      paid_amount: "50.00",
      paid_date: null,
      status: "partial",
    },
  ],
  transactions: [
    {
      id: 2,
      ts: "2026-11-08T10:00:00Z",
      amount: "200.72",
      channel: "mobile",
      status: "pending",
      reference: "PMT-••••4417-018",
    },
    {
      id: 1,
      ts: "2026-10-04T18:32:00Z",
      amount: "200.72",
      channel: "transfer",
      status: "posted",
      reference: "PMT-••••4417-017",
    },
  ],
} as unknown as Schemas["LoanDetail"];

describe("URL state", () => {
  it("parses tabs, pages and filters, dropping unknown values", () => {
    expect(parseTab("schedule")).toBe("schedule");
    expect(parseTab(["history", "loans"])).toBe("history");
    expect(parseTab("bogus")).toBe("profile");
    expect(parseTab(undefined)).toBe("profile");
    expect(parsePage({ page: "3" })).toBe(3);
    expect(parsePage({ page: "-1" })).toBe(1);
    expect(searchState({ q: "  frank ", segment: "vip", bucket: "1-30" })).toEqual({
      q: "frank",
      segment: "",
      bucket: "1-30",
      page: 1,
    });
  });

  it("omits empty params and turns the page into an offset", () => {
    expect(searchQuery({ q: "", segment: "", bucket: "", page: 1 })).toEqual({ limit: 25, offset: 0 });
    expect(searchQuery({ q: "frank", segment: "retail", bucket: "", page: 3 })).toEqual({
      q: "frank",
      segment: "retail",
      limit: 25,
      offset: 50,
    });
    expect(searchHref({ q: "frank", segment: "", bucket: "1-30", page: 2 })).toBe(
      "/customers?q=frank&bucket=1-30&page=2",
    );
    expect(searchHref({ q: "", segment: "", bucket: "", page: 1 })).toBe("/customers");
    expect(pageCount(0)).toBe(1);
    expect(pageCount(51)).toBe(3);
    expect(tabHref(9002, "profile")).toBe("/customers/9002");
    expect(tabHref(9002, "schedule")).toBe("/customers/9002?tab=schedule");
  });
});

describe("TabLinks", () => {
  it("links every tab and marks the active one", () => {
    render(<TabLinks customerId={9002} active="transactions" />);
    const nav = screen.getByRole("navigation", { name: "Customer sections" });
    expect(within(nav).getAllByRole("link")).toHaveLength(5);
    expect(within(nav).getByRole("link", { name: "Transactions" })).toHaveAttribute("aria-current", "page");
    expect(within(nav).getByRole("link", { name: "Schedule" })).toHaveAttribute(
      "href",
      "/customers/9002?tab=schedule",
    );
  });
});

describe("sections", () => {
  it("highlights missed and partial instalments and mutes paid ones", () => {
    render(<ScheduleSection loans={[loan]} />);
    expect(screen.getByRole("heading", { name: "Loan •••• 4417 · Personal Loan" })).toBeInTheDocument();
    const tone = (text: string) => screen.getByText(text).closest("tr")?.getAttribute("data-tone");
    expect(tone("missed")).toBe("danger");
    expect(tone("partial")).toBe("warning");
    expect(tone("paid")).toBe("muted");
  });

  it("flags pending transactions", () => {
    render(<TransactionsSection loans={[loan]} />);
    expect(screen.getByText("pending").closest("tr")).toHaveAttribute("data-tone", "warning");
    expect(screen.getByText("posted").closest("tr")).not.toHaveAttribute("data-tone");
    expect(screen.getByText("PMT-••••4417-018")).toBeInTheDocument();
  });

  it("shows calls with audit links and empty states", () => {
    const detail = {
      interactions: [
        {
          call_id: "call-1",
          direction: "outbound",
          campaign: "missed_payment",
          started_at: "2026-11-09T14:00:00Z",
          ended_at: null,
          contact_result: "connected",
          verified: true,
          reason_code: null,
          summary: "Promised to pay",
        },
      ],
      open_promises: [],
      open_tasks: [],
    } as unknown as Schemas["CustomerDetail"];
    render(<HistorySection detail={detail} />);
    expect(screen.getByRole("link", { name: "call-1" })).toHaveAttribute("href", "/audit?call_id=call-1");
    expect(screen.getByText("No open promises.")).toBeInTheDocument();
    expect(screen.getByText("No open tasks.")).toBeInTheDocument();
  });
});
