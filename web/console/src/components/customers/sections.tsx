import { DataTable, type ColumnSpec, type Row } from "@/components/data-table";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { Schemas } from "@/lib/api/client";
import { dateTime, text, yesNo } from "@/lib/format";

// The customer page's tab sections (T-CON-11, CON-R8). Every figure is the console API's; these only
// choose columns and highlight rows. Identifiers arrive already masked.

type Detail = Schemas["CustomerDetail"];
type Loan = Schemas["LoanDetail"];

const rows = (items: readonly object[]) => items as unknown as Row[];

function Facts({ items }: { items: [string, string][] }) {
  return (
    <dl className="grid grid-cols-[max-content_1fr] gap-x-6 gap-y-2 text-sm">
      {items.map(([term, value]) => (
        <div key={term} className="contents">
          <dt className="text-muted-foreground">{term}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

export function ProfileSection({ detail }: { detail: Detail }) {
  const { profile: p, preferences: pref } = detail;
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card>
        <CardHeader>
          <CardTitle>Contact details</CardTitle>
        </CardHeader>
        <CardContent>
          <Facts
            items={[
              ["Customer id", String(p.customer_id)],
              ["Phone", p.phone],
              ["Email", p.email],
              ["Address", p.address],
              ["Time zone", p.timezone],
              ["Preferred language", p.preferred_language],
              ["Segment", p.segment],
              ["KYC", p.kyc_status],
            ]}
          />
        </CardContent>
      </Card>
      <Card data-testid="contact-preferences">
        <CardHeader>
          <CardTitle>Contact preferences</CardTitle>
        </CardHeader>
        <CardContent>
          <Facts
            items={[
              ["Consent to AI voice calls", yesNo(pref.consent_voice_ai)],
              ["Do not call", yesNo(pref.dnc)],
              ["Cease contact requested", yesNo(pref.cease_contact)],
              ["Open dispute", yesNo(pref.dispute_open)],
              ["Preferred window", text(pref.preferred_window)],
              ["Updated", dateTime(pref.updated_at)],
            ]}
          />
        </CardContent>
      </Card>
    </div>
  );
}

const LOAN_COLUMNS: ColumnSpec[] = [
  { key: "number", header: "Loan" },
  { key: "product_name", header: "Product" },
  { key: "status", header: "Status", kind: "badge" },
  { key: "outstanding_balance", header: "Outstanding", kind: "money", align: "right" },
  { key: "overdue_amount", header: "Overdue", kind: "money", align: "right" },
  { key: "oldest_unpaid_due_date", header: "Oldest unpaid due", kind: "date" },
  { key: "days_past_due", header: "Days past due", kind: "number", align: "right" },
  {
    key: "bucket",
    header: "Bucket",
    kind: "badge",
    tones: { current: "outline", "61-90": "destructive", "90+": "destructive" },
  },
  { key: "next_due_date", header: "Next due", kind: "date" },
  { key: "next_due_amount", header: "Next amount", kind: "money", align: "right" },
  { key: "fees_accrued", header: "Fees accrued", kind: "money", align: "right" },
  { key: "as_of", header: "As of", kind: "date" },
];

export function LoansSection({ loans }: { loans: Loan[] }) {
  return <DataTable rows={rows(loans)} columns={LOAN_COLUMNS} empty="No loans." caption="Loans" />;
}

const INSTALLMENT_COLUMNS: ColumnSpec[] = [
  { key: "seq", header: "#", kind: "number", align: "right" },
  { key: "due_date", header: "Due", kind: "date" },
  { key: "principal_due", header: "Principal", kind: "money", align: "right" },
  { key: "interest_due", header: "Interest", kind: "money", align: "right" },
  { key: "fee_due", header: "Fee", kind: "money", align: "right" },
  { key: "paid_amount", header: "Paid", kind: "money", align: "right" },
  { key: "paid_date", header: "Paid on", kind: "date" },
  {
    key: "status",
    header: "Status",
    kind: "badge",
    tones: { paid: "outline", scheduled: "secondary", partial: "default", missed: "destructive" },
  },
];

/** Unpaid instalments stand out: missed in red, partial in amber; paid ones are muted. */
export const INSTALLMENT_TONE = {
  key: "status",
  values: { missed: "danger", partial: "warning", paid: "muted" },
} as const;

function PerLoan({ loans, children }: { loans: Loan[]; children: (loan: Loan) => React.ReactNode }) {
  if (loans.length === 0) return <p className="text-muted-foreground text-sm">No loans.</p>;
  return (
    <div className="flex flex-col gap-6">
      {loans.map((loan) => (
        <section key={loan.loan_id} aria-label={`Loan ${loan.number}`} className="flex flex-col gap-2">
          <h2 className="text-base font-semibold">
            Loan {loan.number} · {loan.product_name}
          </h2>
          {children(loan)}
        </section>
      ))}
    </div>
  );
}

export function ScheduleSection({ loans }: { loans: Loan[] }) {
  return (
    <PerLoan loans={loans}>
      {(loan) => (
        <DataTable
          rows={rows(loan.installments)}
          columns={INSTALLMENT_COLUMNS}
          rowTone={INSTALLMENT_TONE}
          empty="No instalments."
          caption={`Instalment schedule for loan ${loan.number}`}
        />
      )}
    </PerLoan>
  );
}

const TRANSACTION_COLUMNS: ColumnSpec[] = [
  { key: "ts", header: "When", kind: "datetime" },
  { key: "amount", header: "Amount", kind: "money", align: "right" },
  { key: "channel", header: "Channel" },
  { key: "status", header: "Status", kind: "badge", tones: { pending: "default", posted: "outline" } },
  { key: "reference", header: "Reference", kind: "code" },
];

/** Pending payments are not posted yet: flagged with a badge and an amber row. */
export const TRANSACTION_TONE = { key: "status", values: { pending: "warning" } } as const;

export function TransactionsSection({ loans }: { loans: Loan[] }) {
  return (
    <PerLoan loans={loans}>
      {(loan) => (
        <>
          <p className="text-muted-foreground text-sm">
            Newest first, up to 20. Pending payments are not posted to the loan yet.
          </p>
          <DataTable
            rows={rows(loan.transactions)}
            columns={TRANSACTION_COLUMNS}
            rowTone={TRANSACTION_TONE}
            empty="No transactions."
            caption={`Transactions for loan ${loan.number}`}
          />
        </>
      )}
    </PerLoan>
  );
}

const INTERACTION_COLUMNS: ColumnSpec[] = [
  { key: "started_at", header: "Started", kind: "datetime" },
  { key: "call_id", header: "Call id", kind: "code", href: "/audit?call_id={call_id}" },
  { key: "direction", header: "Direction" },
  { key: "campaign", header: "Campaign" },
  { key: "contact_result", header: "Result", kind: "badge" },
  { key: "verified", header: "Verified", kind: "yesno" },
  { key: "reason_code", header: "Reason" },
  { key: "summary", header: "Summary" },
];

const PROMISE_COLUMNS: ColumnSpec[] = [
  { key: "id", header: "Promise", kind: "number" },
  { key: "promised_date", header: "Date", kind: "date" },
  { key: "amount", header: "Amount", kind: "money", align: "right" },
  { key: "status", header: "Status", kind: "badge", tones: { broken: "destructive", kept: "outline" } },
  { key: "call_id", header: "Call id", kind: "code", href: "/audit?call_id={call_id}" },
];

const TASK_COLUMNS: ColumnSpec[] = [
  { key: "id", header: "Task", kind: "number" },
  { key: "type", header: "Type", kind: "badge" },
  { key: "reason", header: "Reason" },
  { key: "due_at", header: "Due", kind: "datetime" },
  { key: "owner", header: "Owner" },
  { key: "status", header: "Status", kind: "badge" },
  { key: "summary", header: "Summary" },
];

export function HistorySection({ detail }: { detail: Detail }) {
  return (
    <div className="flex flex-col gap-6">
      <section aria-label="Calls" className="flex flex-col gap-2">
        <h2 className="text-base font-semibold">Calls</h2>
        <DataTable rows={rows(detail.interactions)} columns={INTERACTION_COLUMNS} empty="No calls yet." />
      </section>
      <section aria-label="Open promises" className="flex flex-col gap-2">
        <h2 className="text-base font-semibold">Open promises</h2>
        <DataTable rows={rows(detail.open_promises)} columns={PROMISE_COLUMNS} empty="No open promises." />
      </section>
      <section aria-label="Open tasks" className="flex flex-col gap-2">
        <h2 className="text-base font-semibold">Open tasks</h2>
        <DataTable rows={rows(detail.open_tasks)} columns={TASK_COLUMNS} empty="No open tasks." />
      </section>
    </div>
  );
}
