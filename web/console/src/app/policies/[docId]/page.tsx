import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { Suspense } from "react";

import { ErrorPanel } from "@/components/error-panel";
import { LoadingBlock, PageHeader } from "@/components/page";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { getPolicy, listPolicies } from "@/lib/api/client";
import { load } from "@/lib/api/errors";
import { cn } from "@/lib/utils";
import { date } from "@/lib/format";

export const metadata: Metadata = { title: "Policy document" };

export default function PolicyDocumentPage(props: PageProps<"/policies/[docId]">) {
  return (
    <>
      <p className="text-sm">
        <Link className="text-primary underline-offset-4 hover:underline" href="/policies">
          ← All policies
        </Link>
      </p>
      <Suspense fallback={<LoadingBlock rows={6} />}>
        <PolicyDocument params={props.params} searchParams={props.searchParams} />
      </Suspense>
    </>
  );
}

async function PolicyDocument({
  params,
  searchParams,
}: {
  params: PageProps<"/policies/[docId]">["params"];
  searchParams: PageProps<"/policies/[docId]">["searchParams"];
}) {
  const { docId } = await params;
  const query = await searchParams;
  const version = typeof query.version === "string" && query.version ? query.version : undefined;

  const result = await load(getPolicy(docId, version));
  if (!result.ok) {
    if (result.error.notFound) notFound();
    return (
      <>
        <PageHeader title={docId} description={version ? `Version ${version}` : undefined} />
        <ErrorPanel error={result.error} title="Could not load this policy document" />
      </>
    );
  }
  const doc = result.data;
  // The version switcher is best-effort: the document stays readable if the list fails.
  const listing = await load(listPolicies());
  const versions = listing.ok ? (listing.data.find((d) => d.doc_id === doc.doc_id)?.versions ?? []) : [];

  return (
    <>
      <PageHeader title={doc.title} description={`Effective from ${date(doc.effective_date)}`} />
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <code className="font-mono" data-testid="document-version">
          {doc.doc_id}@{doc.version}
        </code>
        <Badge variant="secondary">{doc.product}</Badge>
        <Badge variant={doc.audience === "internal" ? "outline" : "secondary"}>{doc.audience}</Badge>
      </div>
      {versions.length > 1 ? (
        <nav aria-label="Versions" className="flex flex-wrap items-center gap-2 text-sm">
          <span className="text-muted-foreground">Versions:</span>
          {versions.map((v) => (
            <Link
              key={v.version}
              href={`/policies/${encodeURIComponent(doc.doc_id)}?version=${encodeURIComponent(v.version)}`}
              aria-current={v.version === doc.version ? "page" : undefined}
              className={cn(
                "hover:bg-muted rounded-md border px-2 py-0.5",
                v.version === doc.version && "bg-muted font-medium",
              )}
            >
              {v.version} · from {date(v.effective_date)}
            </Link>
          ))}
        </nav>
      ) : null}
      <section aria-label="Chunks" className="flex flex-col gap-3">
        <p className="text-muted-foreground text-sm">
          {doc.chunks.length} chunk{doc.chunks.length === 1 ? "" : "s"}, shown as stored (Markdown source).
        </p>
        {doc.chunks.map((chunk) => (
          <Card key={chunk.ord} data-testid="document-chunk">
            <CardHeader>
              <CardTitle className="text-base">{chunk.section}</CardTitle>
              <CardDescription>
                Chunk {chunk.ord} · {chunk.token_count} tokens
              </CardDescription>
            </CardHeader>
            <CardContent>
              <pre className="bg-muted/50 rounded-md p-3 font-mono text-xs whitespace-pre-wrap">
                {chunk.text}
              </pre>
            </CardContent>
          </Card>
        ))}
      </section>
    </>
  );
}
