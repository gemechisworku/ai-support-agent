"use client";

import {
  createColumnHelper,
  createSortedRowModel,
  rowSortingFeature,
  sortFn_alphanumeric,
  tableFeatures,
  useTable,
} from "@tanstack/react-table";
import Link from "next/link";
import { useMemo } from "react";

import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { cn } from "@/lib/utils";
import { DASH, date, dateTime, money, text, yesNo } from "@/lib/format";

// A sortable table for console API rows. Server components pass plain, serialisable column specs
// (no functions cross the server/client boundary); formatting happens here with `lib/format`.

export type Row = Record<string, unknown>;

export type ColumnKind = "text" | "number" | "money" | "date" | "datetime" | "yesno" | "badge" | "code";

export type ColumnSpec = {
  key: string;
  header: string;
  kind?: ColumnKind;
  /** Link the cell, e.g. "/customers/{customer_id}" ({field} placeholders come from the row). */
  href?: string;
  /** Badge tone per value for `kind: "badge"`, e.g. { deny: "destructive" }. */
  tones?: Record<string, "default" | "secondary" | "destructive" | "outline">;
  align?: "left" | "right";
  sortable?: boolean;
};

export type RowTone = { key: string; values: Record<string, "danger" | "warning" | "muted"> };

type Sortable = { getValue: (columnId: string) => unknown };

/** Numbers and decimal strings (money) compare numerically; missing values sort first ascending. */
function numeric(a: Sortable, b: Sortable, columnId: string): number {
  const value = (r: Sortable) => {
    const v = r.getValue(columnId);
    return v === null || v === undefined || v === "" ? Number.NEGATIVE_INFINITY : Number(v);
  };
  return value(a) - value(b);
}

const features = tableFeatures({
  rowSortingFeature,
  sortedRowModel: createSortedRowModel(),
  sortFns: { alphanumeric: sortFn_alphanumeric, numeric },
});

const helper = createColumnHelper<typeof features, Row>();

const TONE_CLASSES = {
  danger: "bg-destructive/10 hover:bg-destructive/15",
  warning: "bg-amber-500/10 hover:bg-amber-500/15",
  muted: "text-muted-foreground",
} as const;

export function fillHref(template: string, row: Row): string {
  return template.replace(/\{(\w+)\}/g, (_, key: string) => encodeURIComponent(String(row[key] ?? "")));
}

export function formatCell(kind: ColumnKind, value: unknown): string {
  if (value === null || value === undefined || value === "") return DASH;
  switch (kind) {
    case "money":
      return money(String(value));
    case "date":
      return date(String(value));
    case "datetime":
      return dateTime(String(value));
    case "yesno":
      return yesNo(Boolean(value));
    case "number":
      return typeof value === "number" ? value.toLocaleString("en-US") : String(value);
    default:
      return text(value as string | number);
  }
}

function Cell({ spec, row }: { spec: ColumnSpec; row: Row }) {
  const kind = spec.kind ?? "text";
  const value = row[spec.key];
  const shown = formatCell(kind, value);
  let content: React.ReactNode = shown;
  if (kind === "badge" && shown !== DASH) {
    content = <Badge variant={spec.tones?.[String(value)] ?? "secondary"}>{shown}</Badge>;
  } else if (kind === "code") {
    content = <code className="font-mono text-xs">{shown}</code>;
  }
  if (spec.href && shown !== DASH) {
    return (
      <Link
        className="text-primary font-medium underline-offset-4 hover:underline"
        href={fillHref(spec.href, row)}
      >
        {content}
      </Link>
    );
  }
  return content;
}

export function DataTable({
  rows,
  columns,
  empty = "Nothing to show.",
  rowTone,
  caption,
}: {
  rows: Row[];
  columns: ColumnSpec[];
  empty?: string;
  rowTone?: RowTone;
  caption?: string;
}) {
  const defs = useMemo(
    () =>
      helper.columns(
        columns.map((spec) =>
          helper.accessor((row): unknown => row[spec.key], {
            id: spec.key,
            header: spec.header,
            enableSorting: spec.sortable ?? true,
            sortFn: ["money", "number"].includes(spec.kind ?? "text") ? "numeric" : "alphanumeric",
            sortUndefined: "last",
          }),
        ),
      ),
    [columns],
  );
  const table = useTable({ features, columns: defs, data: rows });
  const specs = new Map(columns.map((c) => [c.key, c]));

  if (rows.length === 0) {
    return <p className="text-muted-foreground rounded-lg border border-dashed p-6 text-sm">{empty}</p>;
  }

  return (
    <div className="overflow-x-auto rounded-lg border">
      <Table>
        {caption ? <caption className="sr-only">{caption}</caption> : null}
        <TableHeader>
          {table.getHeaderGroups().map((group) => (
            <TableRow key={group.id}>
              {group.headers.map((header) => {
                const spec = specs.get(header.column.id);
                const sorted = header.column.getIsSorted();
                return (
                  <TableHead
                    key={header.id}
                    className={cn(spec?.align === "right" && "text-right")}
                    aria-sort={sorted === "asc" ? "ascending" : sorted === "desc" ? "descending" : undefined}
                  >
                    {header.column.getCanSort() ? (
                      <button
                        type="button"
                        className="hover:text-foreground inline-flex items-center gap-1"
                        onClick={header.column.getToggleSortingHandler()}
                      >
                        {spec?.header}
                        <span aria-hidden className="text-xs">
                          {sorted === "asc" ? "▲" : sorted === "desc" ? "▼" : ""}
                        </span>
                      </button>
                    ) : (
                      spec?.header
                    )}
                  </TableHead>
                );
              })}
            </TableRow>
          ))}
        </TableHeader>
        <TableBody>
          {table.getRowModel().rows.map((row) => {
            const tone = rowTone ? rowTone.values[String(row.original[rowTone.key])] : undefined;
            return (
              <TableRow key={row.id} data-tone={tone} className={cn(tone && TONE_CLASSES[tone])}>
                {row.getAllCells().map((cell) => {
                  const spec = specs.get(cell.column.id)!;
                  return (
                    <TableCell
                      key={cell.id}
                      className={cn(
                        spec.align === "right" && "text-right tabular-nums",
                        spec.kind === "money" && "tabular-nums",
                      )}
                    >
                      <Cell spec={spec} row={row.original} />
                    </TableCell>
                  );
                })}
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}
