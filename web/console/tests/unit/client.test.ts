import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { getAudit, getCustomer, getOverview, searchCustomers, searchPolicies } from "@/lib/api/client";
import { ApiError, load } from "@/lib/api/errors";

vi.mock("next/server", () => ({ connection: vi.fn(async () => undefined) }));

const fetchMock = vi.fn<typeof fetch>();

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function lastRequest(): Request {
  const [input] = fetchMock.mock.calls.at(-1)!;
  return input as Request;
}

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  vi.stubEnv("CONSOLE_API_URL", "http://console-api.test:8004/");
  vi.stubEnv("CONSOLE_API_TOKEN", "unit-token");
});

afterEach(() => {
  fetchMock.mockReset();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("console API client", () => {
  it("sends the bearer token to the configured URL and returns the data", async () => {
    fetchMock.mockResolvedValue(json({ customers: 200 }));
    await expect(getOverview()).resolves.toEqual({ customers: 200 });
    const request = lastRequest();
    expect(request.url).toBe("http://console-api.test:8004/api/overview");
    expect(request.headers.get("authorization")).toBe("Bearer unit-token");
  });

  it("encodes query, path and body parameters", async () => {
    fetchMock.mockResolvedValue(json({ items: [], total: 0, limit: 25, offset: 0 }));
    await searchCustomers({ q: "frank", bucket: "1-30" });
    expect(lastRequest().url).toBe("http://console-api.test:8004/api/customers?q=frank&bucket=1-30");

    fetchMock.mockResolvedValue(json([]));
    await getAudit("call/with space");
    expect(lastRequest().url).toBe("http://console-api.test:8004/api/audit/call%2Fwith%20space");

    fetchMock.mockResolvedValue(json({ chunks: [], confident: false, best_similarity: 0 }));
    await searchPolicies({ query: "late fee" });
    const request = lastRequest();
    expect(request.method).toBe("POST");
    expect(await request.json()).toEqual({ query: "late fee" });
  });

  it("maps a console API error to an ApiError with its detail and status", async () => {
    fetchMock.mockResolvedValue(json({ detail: "GET /customers/1 failed: HTTP 404" }, 404));
    const error = await getCustomer(1).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(404);
    expect((error as ApiError).notFound).toBe(true);
    expect((error as ApiError).message).toContain("GET /customers/1 failed");
  });

  it("names the console API when it cannot be reached", async () => {
    fetchMock.mockRejectedValue(new TypeError("fetch failed"));
    const result = await load(getOverview());
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error.status).toBeNull();
      expect(result.error.message).toBe("The console API is not reachable at http://console-api.test:8004.");
    }
  });

  it("refuses to call without a token", async () => {
    vi.stubEnv("CONSOLE_API_TOKEN", "");
    await expect(getOverview()).rejects.toThrow("CONSOLE_API_TOKEN is not set");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("load() rethrows unexpected errors", async () => {
    await expect(load(Promise.reject(new Error("bug")))).rejects.toThrow("bug");
  });
});
