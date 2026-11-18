import Link from "next/link";
import { Suspense } from "react";

import { ErrorPanel } from "@/components/error-panel";
import { LoadingBlock, PageHeader } from "@/components/page";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { getOverview } from "@/lib/api/client";
import { load } from "@/lib/api/errors";
import { BUCKETS } from "@/lib/constants";

export default function OverviewPage() {
  return (
    <>
      <PageHeader
        title="Overview"
        description="The context the voice agent works with: read-only, from the same services the agent uses."
      />
      <Suspense fallback={<LoadingBlock rows={3} />}>
        <OverviewData />
      </Suspense>
      <Card>
        <CardHeader>
          <CardTitle>What is here</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-2 text-sm">
          <p>
            <Link className="font-medium underline-offset-4 hover:underline" href="/customers">
              Customers
            </Link>{" "}
            (bank API): search, then a profile with contact preferences, loans with their computed status, the
            instalment schedule, transactions and call history.
          </p>
          <p>
            <Link className="font-medium underline-offset-4 hover:underline" href="/policies">
              Policies
            </Link>{" "}
            (gateway): knowledge-base documents, versions and chunks, plus a search playground that runs the
            agent&apos;s own <code className="font-mono">search_policy</code> retrieval.
          </p>
          <p>
            <Link className="font-medium underline-offset-4 hover:underline" href="/personas">
              Test personas
            </Link>
            : the 12 scripted personas and the synthetic details to role-play them.{" "}
            <Link className="font-medium underline-offset-4 hover:underline" href="/audit">
              Audit trail
            </Link>
            : every gateway tool call of one call, in order.
          </p>
          <p className="text-muted-foreground">
            Numbers are masked to the last 4 digits everywhere except the persona sheet&apos;s test
            identities.
          </p>
        </CardContent>
      </Card>
    </>
  );
}

async function OverviewData() {
  const result = await load(getOverview());
  if (!result.ok) return <ErrorPanel error={result.error} />;
  const o = result.data;
  const stats = [
    { label: "Customers", value: o.customers, href: "/customers" },
    { label: "Loans", value: o.loans, href: "/customers" },
    { label: "Policy documents", value: o.kb_documents, href: "/policies" },
    { label: "Test personas", value: o.personas, href: "/personas" },
  ];
  return (
    <section aria-label="Counts" className="flex flex-col gap-4">
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        {stats.map((s) => (
          <Link key={s.label} href={s.href} data-testid={`stat-${s.label}`}>
            <Card className="hover:bg-muted/50 transition-colors">
              <CardHeader>
                <CardDescription>{s.label}</CardDescription>
                <CardTitle className="text-3xl tabular-nums">{s.value.toLocaleString("en-US")}</CardTitle>
              </CardHeader>
            </Card>
          </Link>
        ))}
      </div>
      <Card>
        <CardHeader>
          <CardTitle>Loans by delinquency bucket</CardTitle>
          <CardDescription>Days past due at the bank&apos;s as-of date.</CardDescription>
        </CardHeader>
        <CardContent>
          <dl className="grid grid-cols-5 gap-2 text-sm">
            {BUCKETS.map((bucket) => (
              <div key={bucket} className="rounded-md border p-3" data-testid={`bucket-${bucket}`}>
                <dt className="text-muted-foreground">{bucket}</dt>
                <dd className="text-xl font-semibold tabular-nums">
                  {(o.loans_by_bucket[bucket] ?? 0).toLocaleString("en-US")}
                </dd>
              </div>
            ))}
          </dl>
        </CardContent>
      </Card>
    </section>
  );
}
