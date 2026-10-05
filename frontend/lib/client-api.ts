// Browser-side client for the Django API (/api/v1/...).
//
// Separate from lib/api.ts, which is the server-side portfolio client. This one
// runs in the browser, so it can only read NEXT_PUBLIC_* variables.
//
// Every error the backend returns uses one envelope:
//   { detail: string, code: string, errors?: { field: string[] } }
// apiFetch turns any non-2xx into an ApiError carrying those parts, so callers
// never parse response bodies themselves.

const API_BASE_URL = (process.env.NEXT_PUBLIC_API_BASE_URL ?? "").replace(/\/+$/, "");

export const NETWORK_ERROR_MESSAGE =
  "We couldn't reach our servers. Please check your connection and try again.";
export const RATE_LIMIT_MESSAGE =
  "Too many attempts. Please wait a moment and try again.";
export const GENERIC_ERROR_MESSAGE = "Something went wrong. Please try again.";

export type FieldErrors = Record<string, string[]>;

export class ApiError extends Error {
  /** HTTP status, or 0 when the request never got a response (network failure). */
  readonly status: number;
  /** Human-readable message: the backend's `detail`, or a friendly fallback. */
  readonly detail: string;
  /** Machine-readable backend `code` (e.g. "validation_error"), or a local one. */
  readonly code: string;
  /** Per-field messages from the envelope's `errors`. Empty when there are none. */
  readonly fieldErrors: FieldErrors;
  /** The parsed response body, for the rare caller that needs an extra key. */
  readonly body: unknown;

  constructor(status: number, detail: string, code: string, fieldErrors: FieldErrors = {}, body: unknown = null) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
    this.code = code;
    this.fieldErrors = fieldErrors;
    this.body = body;
  }
}

function toMessages(value: unknown): string[] {
  if (typeof value === "string") return [value];
  if (Array.isArray(value)) return value.flatMap(toMessages);
  if (value && typeof value === "object") return Object.values(value).flatMap(toMessages);
  return [];
}

function parseFieldErrors(body: Record<string, unknown>): FieldErrors {
  // Normal case: the envelope's `errors` object.
  // Fallback: a bare DRF validation body ({ email: ["..."], code: "..." }) that
  // never got a `detail`, e.g. a blank field on the login serializer.
  const source =
    body.errors && typeof body.errors === "object"
      ? (body.errors as Record<string, unknown>)
      : Object.fromEntries(
          Object.entries(body).filter(([key, value]) => key !== "detail" && key !== "code" && Array.isArray(value))
        );

  const out: FieldErrors = {};
  for (const [field, value] of Object.entries(source)) {
    const messages = toMessages(value);
    if (messages.length) out[field] = messages;
  }
  return out;
}

async function toApiError(response: Response): Promise<ApiError> {
  const body: unknown = await response.json().catch(() => null);
  const record = body && typeof body === "object" && !Array.isArray(body) ? (body as Record<string, unknown>) : {};

  if (response.status === 429) {
    return new ApiError(429, RATE_LIMIT_MESSAGE, typeof record.code === "string" ? record.code : "rate_limited", {}, body);
  }

  const fieldErrors = parseFieldErrors(record);
  const detail =
    typeof record.detail === "string" && record.detail
      ? record.detail
      : Object.values(fieldErrors)[0]?.[0] ?? GENERIC_ERROR_MESSAGE;
  const code = typeof record.code === "string" ? record.code : `http_${response.status}`;

  return new ApiError(response.status, detail, code, fieldErrors, body);
}

export function apiUrl(path: string): string {
  return `${API_BASE_URL}${path.startsWith("/") ? path : `/${path}`}`;
}

export interface ApiFetchInit extends Omit<RequestInit, "body"> {
  /** Serialised as the JSON request body. */
  json?: unknown;
}

/**
 * Fetch a JSON endpoint. Resolves with the parsed body on 2xx (or `undefined`
 * for an empty body); throws ApiError otherwise, including on network failure
 * (status 0, code "network_error").
 */
export async function apiFetch<T = unknown>(path: string, init: ApiFetchInit = {}): Promise<T> {
  const { json, headers, ...rest } = init;
  const finalHeaders = new Headers(headers);
  finalHeaders.set("Accept", "application/json");
  if (json !== undefined) finalHeaders.set("Content-Type", "application/json");

  let response: Response;
  try {
    response = await fetch(apiUrl(path), {
      ...rest,
      headers: finalHeaders,
      body: json !== undefined ? JSON.stringify(json) : undefined,
    });
  } catch {
    throw new ApiError(0, NETWORK_ERROR_MESSAGE, "network_error");
  }

  if (!response.ok) throw await toApiError(response);

  const text = await response.text();
  return (text ? JSON.parse(text) : undefined) as T;
}
