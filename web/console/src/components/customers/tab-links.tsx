import Link from "next/link";

import { cn } from "@/lib/utils";

import { CUSTOMER_TABS, tabHref, type CustomerTab } from "./model";

/** Tabs as links, so the selected tab is URL state (`?tab=`) and survives a reload. */
export function TabLinks({ customerId, active }: { customerId: number; active: CustomerTab }) {
  return (
    <nav aria-label="Customer sections" className="bg-muted inline-flex w-fit rounded-lg p-[3px]">
      {CUSTOMER_TABS.map((tab) => (
        <Link
          key={tab.id}
          href={tabHref(customerId, tab.id)}
          aria-current={tab.id === active ? "page" : undefined}
          className={cn(
            "text-foreground/60 hover:text-foreground rounded-md px-3 py-1 text-sm font-medium",
            tab.id === active && "bg-background text-foreground shadow-sm",
          )}
        >
          {tab.label}
        </Link>
      ))}
    </nav>
  );
}
