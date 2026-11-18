import type { ColumnSpec, Row } from "@/components/data-table";
import type { Schemas } from "@/lib/api/client";
import { date } from "@/lib/format";

/** Columns of the policy document table (serialisable, for the client DataTable). */
export const DOCUMENT_COLUMNS: ColumnSpec[] = [
  { key: "title", header: "Title", href: "/policies/{doc_id}" },
  { key: "doc_id", header: "Document id", kind: "code" },
  { key: "product", header: "Product", kind: "badge" },
  { key: "audience", header: "Audience", kind: "badge", tones: { internal: "outline" } },
  { key: "versions", header: "Versions (effective from)" },
  { key: "chunks", header: "Chunks per version" },
];

/** One row per document; versions newest first, as the gateway lists them. */
export function documentRows(docs: Schemas["KbDocumentSummary"][]): Row[] {
  return docs.map((d) => ({
    doc_id: d.doc_id,
    title: d.title,
    product: d.product,
    audience: d.audience,
    versions: d.versions.map((v) => `${v.version} (${date(v.effective_date)})`).join(" · "),
    chunks: d.versions.map((v) => `${v.version}: ${v.chunk_count}`).join(" · "),
  }));
}
