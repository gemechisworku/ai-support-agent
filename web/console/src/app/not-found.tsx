import Link from "next/link";

import { PageHeader } from "@/components/page";

export default function NotFound() {
  return (
    <div className="flex flex-col gap-4" data-testid="not-found">
      <PageHeader title="Not found" description="There is nothing at this address." />
      <p className="text-sm">
        <Link className="font-medium underline-offset-4 hover:underline" href="/">
          Back to the overview
        </Link>
      </p>
    </div>
  );
}
