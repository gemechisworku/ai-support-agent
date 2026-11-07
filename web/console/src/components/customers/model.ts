// Pure helpers for the customer explorer (T-CON-11): URL state ↔ console API query, tabs, pagination.
import { BUCKETS } from "@/lib/constants";

export const PAGE_SIZE = 25;
export const SEGMENTS = ["retail", "premium", "sme"] as const;

export const CUSTOMER_TABS = [
  { id: "profile", label: "Profile" },
  { id: "loans", label: "Loans" },
  { id: "schedule", label: "Schedule" },
  { id: "transactions", label: "Transactions" },
  { id: "history", label: "History" },
] as const;

export type CustomerTab = (typeof CUSTOMER_TABS)[number]["id"];

type Params = Record<string, string | string[] | undefined>;

/** First value of a search param, trimmed; "" when absent. */
export function param(params: Params, key: string): string {
  const value = params[key];
  return (Array.isArray(value) ? value[0] : value)?.trim() ?? "";
}

export function parseTab(value: string | string[] | undefined): CustomerTab {
  const tab = Array.isArray(value) ? value[0] : value;
  return CUSTOMER_TABS.find((t) => t.id === tab)?.id ?? "profile";
}

/** 1-based page number from `?page=`; anything invalid is page 1. */
export function parsePage(params: Params): number {
  const page = Number.parseInt(param(params, "page"), 10);
  return Number.isFinite(page) && page > 0 ? page : 1;
}

export type SearchState = { q: string; segment: string; bucket: string; page: number };

/** The search form's state from the URL; unknown segment/bucket values are dropped. */
export function searchState(params: Params): SearchState {
  const segment = param(params, "segment");
  const bucket = param(params, "bucket");
  return {
    q: param(params, "q"),
    segment: (SEGMENTS as readonly string[]).includes(segment) ? segment : "",
    bucket: (BUCKETS as readonly string[]).includes(bucket) ? bucket : "",
    page: parsePage(params),
  };
}

/** The console API query for a search state: empty values omitted, page → offset. */
export function searchQuery(state: SearchState) {
  return {
    ...(state.q ? { q: state.q } : {}),
    ...(state.segment ? { segment: state.segment } : {}),
    ...(state.bucket ? { bucket: state.bucket } : {}),
    limit: PAGE_SIZE,
    offset: (state.page - 1) * PAGE_SIZE,
  };
}

/** `/customers?…` for a state, keeping only non-empty filters (page 1 is implied). */
export function searchHref(state: SearchState): string {
  const query = new URLSearchParams();
  if (state.q) query.set("q", state.q);
  if (state.segment) query.set("segment", state.segment);
  if (state.bucket) query.set("bucket", state.bucket);
  if (state.page > 1) query.set("page", String(state.page));
  const qs = query.toString();
  return qs ? `/customers?${qs}` : "/customers";
}

export function pageCount(total: number): number {
  return Math.max(1, Math.ceil(total / PAGE_SIZE));
}

export function tabHref(customerId: number, tab: CustomerTab): string {
  return tab === "profile" ? `/customers/${customerId}` : `/customers/${customerId}?tab=${tab}`;
}
