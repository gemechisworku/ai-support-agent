import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { citation, score, SearchResults } from "@/components/policies/search-results";
import { documentRows } from "@/components/policies/rows";

const chunk = {
  doc_id: "fees-schedule",
  version: "1.0",
  title: "Schedule of Loan Fees",
  section: "Schedule of Loan Fees > Late fees",
  product: "all" as const,
  text: "## Late fees\n\n| Product | Late fee |",
  rrf_score: 0.0328,
  similarity: 0.71,
};

describe("SearchResults", () => {
  it("shows confident results with their citation and scores", () => {
    render(<SearchResults result={{ chunks: [chunk], confident: true, best_similarity: 0.71 }} />);
    expect(screen.getByTestId("confidence")).toHaveAttribute("data-confident", "true");
    expect(screen.getByTestId("citation")).toHaveTextContent(
      "fees-schedule@1.0#Schedule of Loan Fees > Late fees",
    );
    expect(screen.getByText("RRF 0.0328")).toBeInTheDocument();
    expect(screen.getByText("similarity 0.710")).toBeInTheDocument();
    expect(screen.queryByText(/offer a follow-up/)).toBeNull();
  });

  it("warns that the agent would offer a follow-up when not confident", () => {
    render(<SearchResults result={{ chunks: [], confident: false, best_similarity: 0.455 }} />);
    const note = screen.getByTestId("confidence");
    expect(note).toHaveAttribute("data-confident", "false");
    expect(note).toHaveTextContent("the agent would offer a follow-up instead of answering");
    expect(note).toHaveTextContent("0.455");
    expect(screen.getByText("No chunks returned.")).toBeInTheDocument();
  });

  it("keeps the Markdown source as text", () => {
    render(<SearchResults result={{ chunks: [chunk], confident: true, best_similarity: 0.71 }} />);
    expect(screen.getByText(/## Late fees/)).toBeInTheDocument();
  });
});

describe("helpers", () => {
  it("citation, score and document rows", () => {
    expect(citation({ doc_id: "a", version: "1.1", section: "S > T" })).toBe("a@1.1#S > T");
    expect(score(null, 3)).toBe("—");
    expect(score(0.5, 2)).toBe("0.50");
    const [row] = documentRows([
      {
        doc_id: "late-fees-penalty-interest",
        title: "Late fees",
        product: "all",
        audience: "customer",
        versions: [
          { version: "1.1", effective_date: "2027-01-01", chunk_count: 2 },
          { version: "1.0", effective_date: "2026-01-01", chunk_count: 3 },
        ],
      },
    ]);
    expect(row.versions).toBe("1.1 (1 Jan 2027) · 1.0 (1 Jan 2026)");
    expect(row.chunks).toBe("1.1: 2 · 1.0: 3");
  });
});
