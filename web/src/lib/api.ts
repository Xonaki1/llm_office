/**
 * API client.
 *
 * The access token lives in memory only — never localStorage, which an XSS
 * payload can read. The refresh token is an httpOnly cookie the browser
 * attaches automatically, so a 401 triggers a silent refresh and one retry.
 */

const BASE = process.env.NEXT_PUBLIC_API_URL ?? "/api";

let accessToken: string | null = null;
let refreshInFlight: Promise<string | null> | null = null;
const listeners = new Set<(token: string | null) => void>();

export function setAccessToken(token: string | null): void {
  accessToken = token;
  listeners.forEach((listener) => listener(token));
}

export function getAccessToken(): string | null {
  return accessToken;
}

export function onTokenChange(listener: (token: string | null) => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly requestId?: string,
  ) {
    super(message);
    this.name = "ApiError";
  }

  get isAuthError(): boolean {
    return this.status === 401;
  }
}

async function parseError(response: Response): Promise<ApiError> {
  let detail = response.statusText || `request failed (${response.status})`;
  let requestId: string | undefined;
  try {
    const body = await response.json();
    if (typeof body?.detail === "string") detail = body.detail;
    if (typeof body?.request_id === "string") requestId = body.request_id;
  } catch {
    // A non-JSON error body (a proxy error page, say) leaves the status text.
  }
  return new ApiError(response.status, detail, requestId);
}

/**
 * Refresh the access token. Concurrent callers share one in-flight request so
 * a burst of 401s cannot rotate the refresh token several times over — which
 * the server would treat as token reuse and revoke the whole session.
 */
export async function refreshAccessToken(): Promise<string | null> {
  if (refreshInFlight) return refreshInFlight;

  refreshInFlight = (async () => {
    try {
      const response = await fetch(`${BASE}/auth/refresh`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
      if (!response.ok) {
        setAccessToken(null);
        return null;
      }
      const body = (await response.json()) as { access_token: string };
      setAccessToken(body.access_token);
      return body.access_token;
    } catch {
      setAccessToken(null);
      return null;
    } finally {
      refreshInFlight = null;
    }
  })();

  return refreshInFlight;
}

interface RequestOptions extends Omit<RequestInit, "body"> {
  body?: unknown;
  /** Set internally to stop a refreshed request from looping. */
  retried?: boolean;
}

export async function apiFetch<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { body, retried, headers, ...rest } = options;

  const requestHeaders = new Headers(headers);
  if (body !== undefined) requestHeaders.set("Content-Type", "application/json");
  if (accessToken) requestHeaders.set("Authorization", `Bearer ${accessToken}`);

  const response = await fetch(`${BASE}${path}`, {
    ...rest,
    headers: requestHeaders,
    credentials: "include",
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  if (response.status === 401 && !retried) {
    const token = await refreshAccessToken();
    if (token) return apiFetch<T>(path, { ...options, retried: true });
  }

  if (!response.ok) throw await parseError(response);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const api = {
  get: <T>(path: string) => apiFetch<T>(path),
  post: <T>(path: string, body?: unknown) => apiFetch<T>(path, { method: "POST", body }),
  patch: <T>(path: string, body?: unknown) => apiFetch<T>(path, { method: "PATCH", body }),
  delete: <T>(path: string) => apiFetch<T>(path, { method: "DELETE" }),
};

/**
 * Open a run's SSE stream.
 *
 * `EventSource` cannot send an Authorization header, and putting a token in the
 * query string would leak it into proxy and server logs — so the stream is read
 * from a `fetch` body instead, which also lets us resume with `Last-Event-ID`.
 */
export async function openRunStream(
  orgId: string,
  runId: string,
  options: { signal: AbortSignal; lastEventId?: number; onEvent: (event: unknown) => void },
): Promise<void> {
  const headers = new Headers({ Accept: "text/event-stream" });
  if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);
  if (options.lastEventId) headers.set("Last-Event-ID", String(options.lastEventId));

  const response = await fetch(`${BASE}/orgs/${orgId}/runs/${runId}/stream`, {
    headers,
    credentials: "include",
    signal: options.signal,
  });

  if (response.status === 401) {
    const token = await refreshAccessToken();
    if (!token) throw new ApiError(401, "session expired");
    return openRunStream(orgId, runId, options);
  }
  if (!response.ok) throw await parseError(response);
  if (!response.body) throw new ApiError(500, "stream has no body");

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // SSE frames are separated by a blank line. Keep the trailing partial frame
    // in the buffer until its terminator arrives.
    let boundary = buffer.indexOf("\n\n");
    while (boundary !== -1) {
      const frame = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      const data = frame
        .split("\n")
        .filter((line) => line.startsWith("data:"))
        .map((line) => line.slice(5).trim())
        .join("\n");
      if (data) {
        try {
          options.onEvent(JSON.parse(data));
        } catch {
          // A malformed frame must not tear down the stream.
        }
      }
      boundary = buffer.indexOf("\n\n");
    }
  }
}
