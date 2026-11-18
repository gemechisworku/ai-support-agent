import Link from "next/link";
import { notFound } from "next/navigation";
import { Suspense } from "react";

import { parseTab } from "@/components/customers/model";
import {
  HistorySection,
  LoansSection,
  ProfileSection,
  ScheduleSection,
  TransactionsSection,
} from "@/components/customers/sections";
import { TabLinks } from "@/components/customers/tab-links";
import { ErrorPanel } from "@/components/error-panel";
import { LoadingBlock, PageHeader } from "@/components/page";
import { getCustomer } from "@/lib/api/client";
import { load } from "@/lib/api/errors";

type Props = PageProps<"/customers/[id]">;

export default function CustomerPage(props: Props) {
  return (
    <>
      <Link className="text-muted-foreground text-sm underline-offset-4 hover:underline" href="/customers">
        ← Customers
      </Link>
      <Suspense fallback={<LoadingBlock rows={8} />}>
        <CustomerDetail params={props.params} searchParams={props.searchParams} />
      </Suspense>
    </>
  );
}

async function CustomerDetail({ params, searchParams }: Pick<Props, "params" | "searchParams">) {
  const { id } = await params;
  const tab = parseTab((await searchParams).tab);
  if (!/^\d+$/.test(id)) notFound();
  const customerId = Number(id);
  const result = await load(getCustomer(customerId));
  if (!result.ok) {
    if (result.error.notFound) notFound();
    return <ErrorPanel error={result.error} />;
  }
  const detail = result.data;
  const p = detail.profile;
  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title={p.full_name}
        description={`Customer ${p.customer_id} · phone ${p.phone} · ${p.segment} · ${p.timezone} · KYC ${p.kyc_status}`}
      />
      {detail.loans.length ? (
        <ul className="flex flex-col gap-1 text-sm" aria-label="Loans at a glance">
          {detail.loans.map((loan) => (
            <li key={loan.loan_id} data-testid="loan-headline">
              {loan.headline}
            </li>
          ))}
        </ul>
      ) : null}
      <TabLinks customerId={customerId} active={tab} />
      <div data-testid={`tab-${tab}`}>
        {tab === "profile" ? <ProfileSection detail={detail} /> : null}
        {tab === "loans" ? <LoansSection loans={detail.loans} /> : null}
        {tab === "schedule" ? <ScheduleSection loans={detail.loans} /> : null}
        {tab === "transactions" ? <TransactionsSection loans={detail.loans} /> : null}
        {tab === "history" ? <HistorySection detail={detail} /> : null}
      </div>
    </div>
  );
}
