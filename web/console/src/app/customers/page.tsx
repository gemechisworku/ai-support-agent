import Link from "next/link";
import { Suspense } from "react";

import { pageCount, searchHref, searchQuery, searchState } from "@/components/customers/model";
import { SearchForm } from "@/components/customers/search-form";
import { DataTable, type ColumnSpec, type Row } from "@/components/data-table";
import { ErrorPanel } from "@/components/error-panel";
import { LoadingBlock, PageHeader } from "@/components/page";
import { searchCustomers } from "@/lib/api/client";
import { load } from "@/lib/api/errors";
import { plural } from "@/lib/format";

const COLUMNS: ColumnSpec[] = [
  { key: "customer_id", header: "Customer id", kind: "number", href: "/customers/{customer_id}" },
  { key: "full_name", header: "Name", href: "/customers/{customer_id}" },
  { key: "phone", header: "Phone" },
  { key: "segment", header: "Segment" },
  { key: "loan_count", header: "Loans", kind: "number", align: "right" },
  {
    key: "worst_bucket",
    header: "Worst bucket",
    kind: "badge",
    tones: { current: "outline", "61-90": "destructive", "90+": "destructive" },
  },
];

export default function CustomersPage(props: PageProps<"/customers">) {
  return (
    <>
      <PageHeader
        title="Customers"
        description="Search by name, phone, customer id or loan last 4. Data from the mock bank, via the console API."
      />
      <Suspense fallback={<LoadingBlock rows={6} />}>
        <CustomerSearch searchParams={props.searchParams} />
      </Suspense>
    </>
  );
}

async function CustomerSearch({ searchParams }: { searchParams: PageProps<"/customers">["searchParams"] }) {
  const state = searchState(await searchParams);
  const result = await load(searchCustomers(searchQuery(state)));
  return (
    <div className="flex flex-col gap-4">
      <SearchForm state={state} />
      {result.ok ? <Results state={state} page={result.data} /> : <ErrorPanel error={result.error} />}
    </div>
  );
}

function Results({
  state,
  page,
}: {
  state: ReturnType<typeof searchState>;
  page: Awaited<ReturnType<typeof searchCustomers>>;
}) {
  const pages = pageCount(page.total);
  return (
    <section aria-label="Results" className="flex flex-col gap-3">
      <p className="text-muted-foreground text-sm" data-testid="result-count">
        {plural(page.total, "customer")} · page {state.page} of {pages}
      </p>
      <DataTable
        rows={page.items as unknown as Row[]}
        columns={COLUMNS}
        empty="No customers match."
        caption="Customers"
      />
      {pages > 1 ? (
        <nav aria-label="Pages" className="flex gap-4 text-sm">
          {state.page > 1 ? (
            <Link
              className="underline-offset-4 hover:underline"
              href={searchHref({ ...state, page: state.page - 1 })}
            >
              ← Previous
            </Link>
          ) : null}
          {state.page < pages ? (
            <Link
              className="underline-offset-4 hover:underline"
              href={searchHref({ ...state, page: state.page + 1 })}
            >
              Next →
            </Link>
          ) : null}
        </nav>
      ) : null}
    </section>
  );
}
