"use client";

import { Button } from "@/components/ui/button";
import { ErrorPanel } from "@/components/error-panel";

// Unexpected errors only. Expected console API failures are rendered by the pages themselves
// (`load()` + <ErrorPanel>), because production builds hide server error messages from this boundary.
export default function RouteError({ retry }: { error: unknown; retry: () => void }) {
  return (
    <div className="flex flex-col gap-4">
      <ErrorPanel
        title="Something went wrong on this page"
        error={{ message: "The page failed to render. Check the console server logs.", status: 500 }}
      />
      <div>
        <Button variant="outline" onClick={() => retry()}>
          Try again
        </Button>
      </div>
    </div>
  );
}
