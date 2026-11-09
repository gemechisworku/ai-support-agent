import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AuditTable, CallIdForm, type AuditRow } from "@/components/audit/audit-trail";
import { PersonaCard, TEST_IDENTITY_LABEL, type Persona } from "@/components/personas/persona-card";

const FRANK: Persona = {
  customer_id: 9002,
  full_name: "Frank Fischer",
  phone: "+15550100302",
  extension: "302",
  dob: "1985-03-14",
  loan_id: 9002,
  loan_last4: "4417",
  product_code: "PERSONAL",
  situation: "5 days past due, first ever miss",
  expected_outcome: "Reason 'forgot', promise to pay in 3 days",
};

describe("PersonaCard", () => {
  it("links to the customer and keeps the DOB inside the labelled test identity", () => {
    render(<PersonaCard persona={FRANK} />);
    expect(screen.getByRole("link", { name: "Frank Fischer" })).toHaveAttribute("href", "/customers/9002");
    expect(screen.getByText("ext. 302")).toBeInTheDocument();
    const identity = screen.getByRole("region", { name: `${TEST_IDENTITY_LABEL}: Frank Fischer` });
    expect(identity).toHaveTextContent(TEST_IDENTITY_LABEL);
    expect(within(identity).getByTestId("persona-dob")).toHaveTextContent("14 Mar 1985");
    expect(within(identity).getByText("4417")).toBeInTheDocument();
    expect(screen.getByText("Reason 'forgot', promise to pay in 3 days")).toBeInTheDocument();
  });
});

const ROWS: AuditRow[] = [
  {
    id: 1,
    ts: "2026-11-09T14:00:00Z",
    tool: "verify_identity",
    decision: "allow",
    latency_ms: 5,
    customer_id: null,
  },
  {
    id: 2,
    ts: "2026-11-09T14:00:01Z",
    tool: "record_promise_to_pay",
    decision: "deny",
    latency_ms: 8,
    customer_id: 9002,
  },
  {
    id: 3,
    ts: "2026-11-09T14:00:02Z",
    tool: "get_loan_status",
    decision: "error",
    latency_ms: 1200,
    customer_id: 9002,
  },
];

describe("AuditTable", () => {
  it("lists rows in the given order and highlights denials and errors", () => {
    render(<AuditTable callId="c-1" rows={ROWS} />);
    const body = screen.getAllByRole("rowgroup")[1];
    const rows = within(body).getAllByRole("row");
    expect(rows.map((r) => within(r).getAllByRole("cell")[1].textContent)).toEqual([
      "verify_identity",
      "record_promise_to_pay",
      "get_loan_status",
    ]);
    expect(rows[0]).not.toHaveAttribute("data-tone");
    expect(rows[1]).toHaveAttribute("data-tone", "danger");
    expect(rows[2]).toHaveAttribute("data-tone", "warning");
    expect(within(rows[0]).getAllByRole("cell")[0]).toHaveTextContent("9 Nov 2026 14:00 UTC");
    expect(within(rows[2]).getByText("1,200")).toBeInTheDocument();
    expect(within(rows[1]).getByRole("link", { name: "9002" })).toHaveAttribute("href", "/customers/9002");
  });

  it("shows an empty state for a call without tool calls", () => {
    render(<AuditTable callId="no-such-call" rows={[]} />);
    expect(screen.getByText("No gateway tool calls recorded for call no-such-call.")).toBeInTheDocument();
  });
});

describe("CallIdForm", () => {
  it("is a GET form to /audit prefilled with the call id", () => {
    render(<CallIdForm callId="abc" />);
    const form = screen.getByRole("search");
    expect(form).toHaveAttribute("method", "get");
    expect(form).toHaveAttribute("action", "/audit");
    expect(screen.getByLabelText("Call id")).toHaveValue("abc");
  });
});
