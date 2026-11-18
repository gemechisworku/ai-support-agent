import type { Metadata } from "next";
import Link from "next/link";
import { Suspense } from "react";

import { ErrorPanel } from "@/components/error-panel";
import { LoadingBlock, PageHeader } from "@/components/page";
import { SearchResults } from "@/components/policies/search-results";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { searchPolicies, type Schemas } from "@/lib/api/client";
import { load } from "@/lib/api/errors";

export const metadata: Metadata = { title: "Policy search" };

const PRODUCTS = ["PERSONAL", "AUTO", "MORTGAGE", "SME_WC", "SALARY_ADV"] as const;
type Product = (typeof PRODUCTS)[number];

type Params = PageProps<"/policies/search">["searchParams"];

function first(value: string | string[] | undefined): string {
  return (Array.isArray(value) ? value[0] : value)?.trim() ?? "";
}

export default function PolicySearchPage(props: PageProps<"/policies/search">) {
  return (
    <>
      <p className="text-sm">
        <Link className="text-primary underline-offset-4 hover:underline" href="/policies">
          ← All policies
        </Link>
      </p>
      <PageHeader
        title="Policy search playground"
        description="Runs the same hybrid retrieval as the agent's search_policy tool and shows what it would be allowed to say."
      />
      <Suspense fallback={<LoadingBlock rows={4} />}>
        <Search searchParams={props.searchParams} />
      </Suspense>
    </>
  );
}

async function Search({ searchParams }: { searchParams: Params }) {
  const params = await searchParams;
  const q = first(params.q);
  const productParam = first(params.product);
  const product = (PRODUCTS as readonly string[]).includes(productParam) ? (productParam as Product) : "";

  return (
    <>
      <form method="get" action="/policies/search" className="flex flex-wrap items-end gap-3" role="search">
        <label className="flex min-w-72 flex-1 flex-col gap-1 text-sm">
          Question
          <Input
            name="q"
            defaultValue={q}
            placeholder="e.g. late fee personal loan"
            required
            maxLength={500}
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          Product
          <select
            name="product"
            defaultValue={product}
            className="border-input bg-background h-8 rounded-lg border px-2 text-sm"
          >
            <option value="">any</option>
            {PRODUCTS.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
        </label>
        <Button type="submit">Search</Button>
      </form>
      {q ? <Results query={q} product={product || undefined} /> : null}
    </>
  );
}

async function Results({ query, product }: { query: string; product?: Product }) {
  const body: Schemas["PolicyQuery"] = product ? { query, product } : { query };
  const result = await load(searchPolicies(body));
  if (!result.ok) return <ErrorPanel error={result.error} title="The policy search failed" />;
  return <SearchResults result={result.data} />;
}
