import { DataTable, type ColumnSpec, type RowTone } from "@/components/data-table";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { Schemas } from "@/lib/api/client";

export type AuditRow = Schemas["AuditView"];

/** Columns of the audit trail, in time order: time, tool, decision, latency, customer (CON-R10). */
export const AUDIT_COLUMNS: ColumnSpec[] = [
  { key: "ts", header: "Time", kind: "datetime" },
  { key: "tool", header: "Tool", kind: "code" },
  {
    key: "decision",
    header: "Decision",
    kind: "badge",
    tones: { allow: "secondary", deny: "destructive", error: "outline" },
  },
  { key: "latency_ms", header: "Latency (ms)", kind: "number", align: "right" },
  { key: "customer_id", header: "Customer", href: "/customers/{customer_id}" },
];

/** Denied calls stand out as danger, errors as warnings. */
export const AUDIT_TONE: RowTone = { key: "decision", values: { deny: "danger", error: "warning" } };

/** GET form: submitting navigates to `/audit?call_id=…`, so every audit view is linkable. */
export function CallIdForm({ callId }: { callId?: string }) {
  return (
    <form method="get" action="/audit" className="flex max-w-2xl gap-2" role="search">
      <label htmlFor="call_id" className="sr-only">
        Call id
      </label>
      <Input
        id="call_id"
        name="call_id"
        defaultValue={callId ?? ""}
        placeholder="Call id, e.g. fbde0dde-804f-4a79-ba80-2795d29b3079"
        className="font-mono"
        required
      />
      <Button type="submit">Show audit trail</Button>
    </form>
  );
}

export function AuditTable({ callId, rows }: { callId: string; rows: AuditRow[] }) {
  return (
    <DataTable
      rows={rows}
      columns={AUDIT_COLUMNS}
      rowTone={AUDIT_TONE}
      caption={`Gateway tool calls of call ${callId}`}
      empty={`No gateway tool calls recorded for call ${callId}.`}
    />
  );
}
