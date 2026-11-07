import { CircleAlert, CircleCheck } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import type { Schemas } from "@/lib/api/client";

type Chunk = Pick<
  Schemas["PolicyChunk"],
  "doc_id" | "version" | "section" | "title" | "text" | "rrf_score" | "similarity" | "product"
>;
type Result = { chunks: Chunk[]; confident: boolean; best_similarity: number };

/** The citation the agent logs for a chunk: `doc_id@version#section` (CON-R9, constitution IV). */
export function citation(chunk: Pick<Chunk, "doc_id" | "version" | "section">): string {
  return `${chunk.doc_id}@${chunk.version}#${chunk.section}`;
}

export function score(value: number | null | undefined, digits: number): string {
  return value === null || value === undefined ? "—" : value.toFixed(digits);
}

/** What `search_policy` returns for a query: cited chunks, scores and whether the agent may answer. */
export function SearchResults({ result }: { result: Result }) {
  return (
    <section aria-label="Search results" className="flex flex-col gap-4">
      {result.confident ? (
        <Alert data-testid="confidence" data-confident="true">
          <CircleCheck />
          <AlertTitle>Confident: the agent may answer from these chunks</AlertTitle>
          <AlertDescription>
            Best similarity {score(result.best_similarity, 3)} is at or above the threshold.
          </AlertDescription>
        </Alert>
      ) : (
        <Alert variant="destructive" data-testid="confidence" data-confident="false">
          <CircleAlert />
          <AlertTitle>Not confident: the agent would offer a follow-up instead of answering</AlertTitle>
          <AlertDescription>
            Best similarity {score(result.best_similarity, 3)} is below the threshold, so the agent must not
            state policy from these results.
          </AlertDescription>
        </Alert>
      )}
      {result.chunks.length === 0 ? (
        <p className="text-muted-foreground rounded-lg border border-dashed p-6 text-sm">
          No chunks returned.
        </p>
      ) : (
        <ol className="flex flex-col gap-3">
          {result.chunks.map((chunk, i) => (
            <li key={`${citation(chunk)}-${i}`}>
              <Card data-testid="search-chunk">
                <CardHeader>
                  <CardTitle className="text-base">
                    {i + 1}. {chunk.title}
                  </CardTitle>
                  <CardDescription className="flex flex-wrap items-center gap-2">
                    <code className="font-mono text-xs" data-testid="citation">
                      {citation(chunk)}
                    </code>
                    <Badge variant="outline">{chunk.product}</Badge>
                    <span>RRF {score(chunk.rrf_score, 4)}</span>
                    <span>similarity {score(chunk.similarity, 3)}</span>
                  </CardDescription>
                </CardHeader>
                <CardContent>
                  <pre className="bg-muted/50 rounded-md p-3 font-mono text-xs whitespace-pre-wrap">
                    {chunk.text}
                  </pre>
                </CardContent>
              </Card>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
