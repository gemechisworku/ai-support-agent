import { CircleAlert } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import type { ApiError } from "@/lib/api/errors";

/** The readable error state every page shows when the console API fails (CON-R11): never a stack trace. */
export function ErrorPanel({
  error,
  title,
}: {
  error: Pick<ApiError, "message" | "status">;
  title?: string;
}) {
  const unreachable = error.status === null;
  return (
    <Alert variant="destructive" role="alert" data-testid="error-panel">
      <CircleAlert />
      <AlertTitle>{title ?? "Could not load data from the console API"}</AlertTitle>
      <AlertDescription>
        <p>{error.message}</p>
        {unreachable ? (
          <p className="text-muted-foreground mt-2">
            Start it with <code className="font-mono">docker compose up -d --wait console-api</code> (or{" "}
            <code className="font-mono">make console</code>), then reload.
          </p>
        ) : null}
      </AlertDescription>
    </Alert>
  );
}
