"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

type Item = { href: string; label: string; milestone?: string };

// Data explorer pages exist from M2; the operations pages arrive with T-CON-02..05 (M4).
export const SECTIONS: { title: string; items: Item[] }[] = [
  {
    title: "Data explorer",
    items: [
      { href: "/", label: "Overview" },
      { href: "/customers", label: "Customers" },
      { href: "/policies", label: "Policies" },
      { href: "/personas", label: "Test personas" },
      { href: "/audit", label: "Audit trail" },
    ],
  },
  {
    title: "Operations",
    items: [
      { href: "/campaigns", label: "Campaigns", milestone: "M4" },
      { href: "/calls", label: "Calls", milestone: "M4" },
      { href: "/followups", label: "Follow-ups", milestone: "M4" },
      { href: "/compliance", label: "Compliance", milestone: "M4" },
    ],
  },
];

export function isActive(pathname: string, href: string): boolean {
  return href === "/" ? pathname === "/" : pathname === href || pathname.startsWith(`${href}/`);
}

export function Nav() {
  const pathname = usePathname();
  return (
    <nav aria-label="Console" className="flex flex-col gap-6">
      {SECTIONS.map((section) => (
        <div key={section.title} className="flex flex-col gap-1">
          <p className="text-muted-foreground px-2 text-xs font-medium tracking-wide uppercase">
            {section.title}
          </p>
          {section.items.map((item) =>
            item.milestone ? (
              <span
                key={item.href}
                aria-disabled="true"
                className="text-muted-foreground flex items-center justify-between rounded-md px-2 py-1.5 text-sm"
              >
                {item.label}
                <Badge variant="outline">coming in {item.milestone}</Badge>
              </span>
            ) : (
              <Link
                key={item.href}
                href={item.href}
                aria-current={isActive(pathname, item.href) ? "page" : undefined}
                className={cn(
                  "hover:bg-muted rounded-md px-2 py-1.5 text-sm",
                  isActive(pathname, item.href) && "bg-muted font-medium",
                )}
              >
                {item.label}
              </Link>
            ),
          )}
        </div>
      ))}
    </nav>
  );
}
