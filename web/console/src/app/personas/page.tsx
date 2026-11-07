import type { Metadata } from "next";
import { Suspense } from "react";

import { ErrorPanel } from "@/components/error-panel";
import { LoadingBlock, PageHeader } from "@/components/page";
import { PersonaCard } from "@/components/personas/persona-card";
import { listPersonas } from "@/lib/api/client";
import { load } from "@/lib/api/errors";

export const metadata: Metadata = { title: "Test personas" };

export default function PersonasPage() {
  return (
    <>
      <PageHeader
        title="Test personas"
        description="The 12 scripted personas: who to call, what to expect, and the synthetic details to role-play each one."
      />
      <Suspense fallback={<LoadingBlock rows={6} />}>
        <PersonaList />
      </Suspense>
    </>
  );
}

async function PersonaList() {
  const result = await load(listPersonas());
  if (!result.ok) return <ErrorPanel error={result.error} />;
  if (result.data.length === 0) {
    return <p className="text-muted-foreground text-sm">No personas: seed the bank first.</p>;
  }
  return (
    <div className="grid gap-4 lg:grid-cols-2" data-testid="persona-list">
      {result.data.map((persona) => (
        <PersonaCard key={persona.customer_id} persona={persona} />
      ))}
    </div>
  );
}
