import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { DataTable, fillHref, formatCell } from "@/components/data-table";
import { ErrorPanel } from "@/components/error-panel";
import { isActive, Nav } from "@/components/nav";

vi.mock("next/navigation", () => ({ usePathname: () => "/customers/9002" }));

describe("ErrorPanel", () => {
  it("shows the message and a start hint when the console API is unreachable", () => {
    render(<ErrorPanel error={{ message: "The console API is not reachable at http://x.", status: null }} />);
    const panel = screen.getByRole("alert");
    expect(panel).toHaveTextContent("Could not load data from the console API");
    expect(panel).toHaveTextContent("The console API is not reachable at http://x.");
    expect(panel).toHaveTextContent("docker compose up -d --wait console-api");
  });

  it("omits the start hint for an HTTP error", () => {
    render(<ErrorPanel error={{ message: "HTTP 502", status: 502 }} />);
    expect(screen.getByRole("alert")).not.toHaveTextContent("docker compose");
  });
});

describe("DataTable", () => {
  const rows = [
    { id: 1, name: "Frank Fischer", balance: "1000.00", decision: "allow" },
    { id: 2, name: "Sam Carter", balance: "200.72", decision: "deny" },
  ];
  const columns = [
    { key: "name", header: "Name", href: "/customers/{id}" },
    { key: "balance", header: "Balance", kind: "money" as const, align: "right" as const },
    { key: "decision", header: "Decision", kind: "badge" as const, tones: { deny: "destructive" as const } },
  ];

  it("formats cells, links rows and tones flagged rows", () => {
    render(
      <DataTable rows={rows} columns={columns} rowTone={{ key: "decision", values: { deny: "danger" } }} />,
    );
    expect(screen.getByRole("link", { name: "Frank Fischer" })).toHaveAttribute("href", "/customers/1");
    expect(screen.getByText("$1,000.00")).toBeInTheDocument();
    const sam = screen.getByText("Sam Carter").closest("tr")!;
    expect(sam).toHaveAttribute("data-tone", "danger");
  });

  it("sorts money numerically, not as text", async () => {
    render(<DataTable rows={rows} columns={columns} />);
    await userEvent.click(screen.getByRole("button", { name: /Balance/ }));
    const body = screen.getAllByRole("rowgroup")[1];
    const amounts = within(body)
      .getAllByRole("row")
      .map((r) => within(r).getAllByRole("cell")[1].textContent);
    expect(amounts).toEqual(["$200.72", "$1,000.00"]);
  });

  it("shows the empty message", () => {
    render(<DataTable rows={[]} columns={columns} empty="No customers match." />);
    expect(screen.getByText("No customers match.")).toBeInTheDocument();
  });

  it("helpers", () => {
    expect(fillHref("/audit?call_id={call_id}", { call_id: "a b" })).toBe("/audit?call_id=a%20b");
    expect(formatCell("date", "2026-11-04")).toBe("4 Nov 2026");
    expect(formatCell("yesno", false)).toBe("no");
    expect(formatCell("number", 1200)).toBe("1,200");
    expect(formatCell("text", null)).toBe("—");
  });
});

describe("Nav", () => {
  it("marks the current section and disables the M4 pages", () => {
    render(<Nav />);
    expect(screen.getByRole("link", { name: "Customers" })).toHaveAttribute("aria-current", "page");
    expect(screen.queryByRole("link", { name: /Campaigns/ })).toBeNull();
    expect(screen.getByText("Campaigns").closest("[aria-disabled]")).toHaveTextContent("coming in M4");
  });

  it("isActive", () => {
    expect(isActive("/", "/")).toBe(true);
    expect(isActive("/customers", "/")).toBe(false);
    expect(isActive("/policies/search", "/policies")).toBe(true);
    expect(isActive("/policiesx", "/policies")).toBe(false);
  });
});
