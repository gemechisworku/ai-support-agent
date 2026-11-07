/** A console API request that failed; `message` is safe to show (the console API never leaks internals). */
export class ApiError extends Error {
  readonly service = "console API";

  constructor(
    message: string,
    /** HTTP status from the console API, or null when it could not be reached at all. */
    readonly status: number | null,
  ) {
    super(message);
    this.name = "ApiError";
  }

  get notFound(): boolean {
    return this.status === 404;
  }
}

export type Result<T> = { ok: true; data: T } | { ok: false; error: ApiError };

/** Await an API call and turn an `ApiError` into a value pages can render (expected errors). */
export async function load<T>(promise: Promise<T>): Promise<Result<T>> {
  try {
    return { ok: true, data: await promise };
  } catch (error) {
    if (error instanceof ApiError) return { ok: false, error };
    throw error;
  }
}
