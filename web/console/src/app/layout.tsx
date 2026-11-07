import type { Metadata } from "next";

import { Nav } from "@/components/nav";
import { BANNER } from "@/lib/constants";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "Support agent console", template: "%s · Support agent console" },
  description: "Operations console and data explorer for the loan-servicing voice agent (local POC).",
  robots: { index: false, follow: false },
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="flex min-h-full flex-col">
        <div
          role="note"
          data-testid="synthetic-banner"
          className="bg-amber-100 px-4 py-1.5 text-center text-sm font-medium text-amber-950 dark:bg-amber-900/40 dark:text-amber-100"
        >
          {BANNER}
        </div>
        <div className="flex flex-1">
          <aside className="hidden w-60 shrink-0 border-r p-4 md:block">
            <p className="mb-6 px-2 text-sm font-semibold">Support agent console</p>
            <Nav />
          </aside>
          <main className="flex min-w-0 flex-1 flex-col gap-6 p-6">{children}</main>
        </div>
      </body>
    </html>
  );
}
