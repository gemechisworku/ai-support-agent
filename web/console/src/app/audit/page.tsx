import type { Metadata } from "next";
import { Suspense } from "react";

import { AuditTable, CallIdForm } from "@/components/audit/audit-trail";
import { ErrorPanel } from "@/components/error-panel";
import { LoadingBlock, PageHeader } from "@/components/page";
import { getAudit } from "@/lib/api/client";
import { load } from "@/lib/api/errors";

export const metadata: Metadata = { title: "Audit trail" };

function firstValue(value: string | string[] | undefined): string {
  return (Array.isArray(value) ? value[0] : value)?.trim() ?? "";
}

export default function AuditPage(props: PageProps<"/audit">) {
  return (
    <>
      <PageHeader
        title="Audit trail"
        description="Every gateway tool call of one call, in order: what the agent asked for and whether the gateway allowed it."
      />
      <Suspense fallback={<LoadingBlock />}>
        <AuditView searchParams={props.searchParams} />
      </Suspense>
    </>
  );
}

async function AuditView({ searchParams }: { searchParams: PageProps<"/audit">["searchParams"] }) {
  const callId = firstValue((await searchParams).call_id);
  return (
    <div className="flex flex-col gap-4">
      <CallIdForm callId={callId} />
      {callId ? (
        <AuditRows callId={callId} />
      ) : (
        <p className="text-muted-foreground text-sm" data-testid="audit-hint">
          Enter a call id to see its tool calls. A list of recent calls to pick from arrives with the Calls
          page (milestone M4).
        </p>
      )}
    </div>
  );
}

async function AuditRows({ callId }: { callId: string }) {
  const result = await load(getAudit(callId));
  if (!result.ok) return <ErrorPanel error={result.error} />;
  return <AuditTable callId={callId} rows={result.data} />;
}
