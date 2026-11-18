import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import type { Schemas } from "@/lib/api/client";
import { date } from "@/lib/format";

export type Persona = Schemas["PersonaView"];

export const TEST_IDENTITY_LABEL = "Synthetic test identity";

/**
 * One scripted persona (CON-R10). The verification details a tester needs to role-play the persona
 * (full name, DOB, loan last 4) sit in a box labelled as a synthetic test identity: the persona sheet
 * is the only console page that shows a DOB (CON-R7).
 */
export function PersonaCard({ persona }: { persona: Persona }) {
  return (
    <Card data-testid={`persona-${persona.customer_id}`}>
      <CardHeader>
        <CardTitle className="flex items-center justify-between gap-2">
          <Link
            className="text-primary underline-offset-4 hover:underline"
            href={`/customers/${persona.customer_id}`}
          >
            {persona.full_name}
          </Link>
          <Badge variant="outline">ext. {persona.extension}</Badge>
        </CardTitle>
        <CardDescription>
          Customer {persona.customer_id} · {persona.product_code} loan •••• {persona.loan_last4}
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3 text-sm">
        <dl className="grid grid-cols-[8rem_1fr] gap-x-3 gap-y-1">
          <dt className="text-muted-foreground">Situation</dt>
          <dd>{persona.situation}</dd>
          <dt className="text-muted-foreground">Expected outcome</dt>
          <dd>{persona.expected_outcome}</dd>
        </dl>
        <section
          aria-label={`${TEST_IDENTITY_LABEL}: ${persona.full_name}`}
          className="rounded-md border border-dashed border-amber-500/60 bg-amber-500/5 p-3"
        >
          <p className="mb-2 text-xs font-medium tracking-wide text-amber-700 uppercase dark:text-amber-300">
            {TEST_IDENTITY_LABEL} — for role-play only
          </p>
          <dl className="grid grid-cols-[8rem_1fr] gap-x-3 gap-y-1">
            <dt className="text-muted-foreground">Full name</dt>
            <dd>{persona.full_name}</dd>
            <dt className="text-muted-foreground">Date of birth</dt>
            <dd data-testid="persona-dob">{date(persona.dob)}</dd>
            <dt className="text-muted-foreground">Loan last 4</dt>
            <dd className="font-mono">{persona.loan_last4}</dd>
            <dt className="text-muted-foreground">Phone</dt>
            <dd className="font-mono">{persona.phone}</dd>
          </dl>
        </section>
      </CardContent>
    </Card>
  );
}
