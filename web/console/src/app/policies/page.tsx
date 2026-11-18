import type { Metadata } from "next";
import Link from "next/link";
import { Suspense } from "react";

import { DataTable } from "@/components/data-table";
import { ErrorPanel } from "@/components/error-panel";
import { LoadingBlock, PageHeader } from "@/components/page";
import { DOCUMENT_COLUMNS, documentRows } from "@/components/policies/rows";
import { listPolicies } from "@/lib/api/client";
import { load } from "@/lib/api/errors";
import { plural } from "@/lib/format";

export const metadata: Metadata = { title: "Policies" };

export default function PoliciesPage() {
  return (
    <>
      <PageHeader
        title="Policies"
        description="The policy knowledge base the agent answers from (gateway service API): documents, versions and chunks."
      />
      <p className="text-sm">
        <Link className="text-primary font-medium underline-offset-4 hover:underline" href="/policies/search">
          Open the search playground
        </Link>{" "}
        to run the agent&apos;s own <code className="font-mono">search_policy</code> retrieval.
      </p>
      <Suspense fallback={<LoadingBlock rows={5} />}>
        <Documents />
      </Suspense>
    </>
  );
}

async function Documents() {
  const result = await load(listPolicies());
  if (!result.ok) return <ErrorPanel error={result.error} />;
  return (
    <section aria-label="Documents" className="flex flex-col gap-2">
      <p className="text-muted-foreground text-sm" data-testid="document-count">
        {plural(result.data.length, "document")}
      </p>
      <DataTable
        rows={documentRows(result.data)}
        columns={DOCUMENT_COLUMNS}
        empty="No policy documents are ingested yet (run make kb-ingest)."
        caption="Policy documents"
      />
    </section>
  );
}
