import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { BUCKETS } from "@/lib/constants";

import { SEGMENTS, type SearchState } from "./model";

const SELECT =
  "border-input bg-background h-8 rounded-lg border px-2.5 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50";

/** A plain GET form: the search state lives in the URL (`/customers?q=…&segment=…&bucket=…`). */
export function SearchForm({ state }: { state: SearchState }) {
  return (
    <form
      method="get"
      action="/customers"
      role="search"
      aria-label="Search customers"
      className="flex flex-wrap items-end gap-3"
    >
      <label className="flex min-w-64 flex-1 flex-col gap-1 text-sm">
        Search
        <Input name="q" defaultValue={state.q} placeholder="Name, phone, customer id or loan last 4" />
      </label>
      <label className="flex flex-col gap-1 text-sm">
        Segment
        <select name="segment" defaultValue={state.segment} className={SELECT}>
          <option value="">any</option>
          {SEGMENTS.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      </label>
      <label className="flex flex-col gap-1 text-sm">
        Bucket
        <select name="bucket" defaultValue={state.bucket} className={SELECT}>
          <option value="">any</option>
          {BUCKETS.map((b) => (
            <option key={b} value={b}>
              {b}
            </option>
          ))}
        </select>
      </label>
      <Button type="submit">Search</Button>
    </form>
  );
}
