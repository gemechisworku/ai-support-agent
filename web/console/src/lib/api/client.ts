import "server-only";

import { connection } from "next/server";
import createClient from "openapi-fetch";

import { ApiError } from "./errors";
import type { components, paths } from "./schema";

// The only module that talks to the console API (specs/11-console, CON-R11). It runs on the Next.js
// server: the URL and token come from the server environment and never reach the browser.

export type Schemas = components["schemas"];

export function consoleApiUrl(): string {
  return (process.env.CONSOLE_API_URL ?? "http://localhost:8004").replace(/\/+$/, "");
}

function client() {
  const token = process.env.CONSOLE_API_TOKEN;
  if (!token) throw new ApiError("CONSOLE_API_TOKEN is not set on the console server.", null);
  return createClient<paths>({
    baseUrl: consoleApiUrl(),
    headers: { Authorization: `Bearer ${token}` },
    cache: "no-store",
  });
}

type Reply<D> = { data?: D; error?: unknown; response: Response };

/** Unwrap an openapi-fetch reply: data on 2xx, otherwise an `ApiError` with the console API's detail. */
export async function unwrap<D>(request: () => Promise<Reply<D>>): Promise<D> {
  // Console data is always per request: never prerender it at build time (when no API is running).
  await connection();
  let reply: Reply<D>;
  try {
    reply = await request();
  } catch (cause) {
    if (cause instanceof ApiError) throw cause;
    throw new ApiError(`The console API is not reachable at ${consoleApiUrl()}.`, null);
  }
  const { data, error, response } = reply;
  if (!response.ok || data === undefined) {
    const detail = (error as { detail?: unknown } | undefined)?.detail;
    const message =
      typeof detail === "string" ? detail : `HTTP ${response.status} ${response.statusText}`.trim();
    throw new ApiError(`The console API returned an error: ${message}`, response.status);
  }
  return data;
}

// ------------------------------------------------------------------ data explorer (M2)

export function getOverview() {
  return unwrap(() => client().GET("/api/overview"));
}

export type CustomerSearch = {
  q?: string;
  segment?: string;
  bucket?: string;
  limit?: number;
  offset?: number;
};

export function searchCustomers(query: CustomerSearch) {
  return unwrap(() => client().GET("/api/customers", { params: { query } }));
}

export function getCustomer(customerId: number) {
  return unwrap(() =>
    client().GET("/api/customers/{customer_id}", { params: { path: { customer_id: customerId } } }),
  );
}

export type LoanSearch = { bucket?: string; product?: string; limit?: number; offset?: number };

export function listLoans(query: LoanSearch) {
  return unwrap(() => client().GET("/api/loans", { params: { query } }));
}

export function listPolicies() {
  return unwrap(() => client().GET("/api/policies"));
}

export function getPolicy(docId: string, version?: string) {
  return unwrap(() =>
    client().GET("/api/policies/{doc_id}", { params: { path: { doc_id: docId }, query: { version } } }),
  );
}

export function searchPolicies(body: Schemas["PolicyQuery"]) {
  return unwrap(() => client().POST("/api/policies/search", { body }));
}

export function listPersonas() {
  return unwrap(() => client().GET("/api/personas"));
}

export function getAudit(callId: string) {
  return unwrap(() => client().GET("/api/audit/{call_id}", { params: { path: { call_id: callId } } }));
}
