// Client-portal session handling (browser only).
//
// Token storage: access + refresh JWTs live in localStorage. That keeps this
// change frontend-only, but it means any script running on the page can read
// them. PLANNED HARDENING: move both tokens into httpOnly cookies set by Next.js
// route handlers that proxy auth/token/, auth/token/refresh/ and
// auth/token/logout/, so page JavaScript never sees a token.
//
// Backend contract (apps/accounts):
//   POST auth/token/          {email, password}  -> {access, refresh, user}
//   POST auth/token/refresh/  {refresh}          -> {access, refresh}  (rotates; old refresh is blacklisted)
//   POST auth/token/logout/   {refresh} + Bearer -> blacklists the refresh token
//   POST auth/force-password-change/ {new_password, confirm_password} + Bearer
//                             -> {detail, access, refresh}  (all prior refresh tokens are blacklisted)
//   Access lives 1h, refresh 7d.

import { useEffect, useState } from "react";
import { ApiError, apiFetch, type ApiFetchInit } from "./client-api";

const ACCESS_KEY = "hl.portal.access";
const REFRESH_KEY = "hl.portal.refresh";

// Refresh a little before the access token actually expires, so a request is
// not sent with a token that dies in flight.
const EXPIRY_SKEW_MS = 30_000;

export interface LoginUser {
  id: number;
  email: string;
  first_name: string;
  last_name: string;
  force_password_change: boolean;
}

export interface CurrentUser {
  id: number;
  email: string;
  first_name: string;
  last_name: string;
  date_joined: string;
  timezone: string;
}

interface TokenPair {
  access: string;
  refresh: string;
}

// ── Storage ──────────────────────────────────────────────────

function readToken(key: string): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function storeTokens({ access, refresh }: TokenPair): void {
  window.localStorage.setItem(ACCESS_KEY, access);
  window.localStorage.setItem(REFRESH_KEY, refresh);
}

export function clearTokens(): void {
  if (typeof window === "undefined") return;
  window.localStorage.removeItem(ACCESS_KEY);
  window.localStorage.removeItem(REFRESH_KEY);
}

/** True when a refresh token is stored. Says nothing about whether it is still valid. */
export function hasSession(): boolean {
  return readToken(REFRESH_KEY) !== null;
}

function isExpiring(token: string): boolean {
  try {
    const payload = JSON.parse(atob(token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/")));
    return typeof payload.exp !== "number" || payload.exp * 1000 - EXPIRY_SKEW_MS <= Date.now();
  } catch {
    // Unreadable token: treat as expired and let the refresh decide.
    return true;
  }
}

// ── Login / refresh / logout ────────────────────────────────

export async function login(email: string, password: string): Promise<LoginUser> {
  const data = await apiFetch<TokenPair & { user: LoginUser }>("/api/v1/auth/token/", {
    method: "POST",
    json: { email: email.trim(), password },
  });
  storeTokens(data);
  return data.user;
}

// Single-flight: concurrent callers share one refresh request. Refresh tokens
// rotate and the old one is blacklisted, so two parallel refreshes with the same
// token would make the second fail and log the user out.
//
// Within a tab, refreshInFlight dedupes. Across tabs (which share localStorage),
// the Web Locks API serialises refreshes where available, and the post-failure
// re-read below covers browsers without it.
let refreshInFlight: Promise<string> | null = null;

const REFRESH_LOCK = "hl.portal.refresh";

// How long a rejected refresh waits before re-reading storage. The tab that won
// the race may not have stored its new pair yet, and localStorage writes reach
// other tabs asynchronously; re-reading at once could clear that new pair.
const REJECTED_REFRESH_SETTLE_MS = 1_000;

function withRefreshLock<T>(fn: () => Promise<T>): Promise<T> {
  if (typeof navigator !== "undefined" && "locks" in navigator && navigator.locks) {
    // request() resolves with fn's resolved value; lib.dom types it one Promise deep.
    return navigator.locks.request(REFRESH_LOCK, fn) as Promise<T>;
  }
  return fn();
}

/** Exchange the stored refresh token for a new pair. Returns the new access token. */
export function refresh(): Promise<string> {
  if (refreshInFlight) return refreshInFlight;

  // The refresh token this tab saw when it decided to refresh.
  const seen = readToken(REFRESH_KEY);

  refreshInFlight = withRefreshLock(async () => {
    const refreshToken = readToken(REFRESH_KEY);
    if (!refreshToken) {
      clearTokens();
      throw new ApiError(401, "Your session has ended. Please log in again.", "session_missing");
    }
    // Another tab rotated the pair while this one waited for the lock: use it.
    const storedAccess = readToken(ACCESS_KEY);
    if (refreshToken !== seen && storedAccess) return storedAccess;

    try {
      const data = await apiFetch<TokenPair>("/api/v1/auth/token/refresh/", {
        method: "POST",
        json: { refresh: refreshToken },
      });
      storeTokens(data);
      return data.access;
    } catch (err) {
      // The server rejected the refresh token (expired, blacklisted, garbage).
      // A network failure or 429 is not a rejection, so the tokens are kept for
      // the next attempt.
      if (err instanceof ApiError && err.status >= 400 && err.status < 500 && err.status !== 429) {
        // If the stored refresh token is no longer the one that failed, another
        // tab rotated it first (ours was blacklisted by that rotation). Adopt its
        // pair; the caller retries once with it. Clear only when the stored
        // token is still the rejected one, i.e. the session really is over.
        await new Promise((resolve) => setTimeout(resolve, REJECTED_REFRESH_SETTLE_MS));
        const nowStored = readToken(REFRESH_KEY);
        const nowAccess = readToken(ACCESS_KEY);
        if (nowStored && nowStored !== refreshToken && nowAccess) return nowAccess;
        clearTokens();
      }
      throw err;
    }
  }).finally(() => {
    refreshInFlight = null;
  });

  return refreshInFlight;
}

/** A usable access token, refreshing first if the stored one is missing or about to expire. */
export async function getAccessToken(): Promise<string> {
  const access = readToken(ACCESS_KEY);
  if (access && !isExpiring(access)) return access;
  return refresh();
}

/**
 * apiFetch with a Bearer token. On a 401 it refreshes once (single-flight) and
 * retries. Pass `init` as a function when the request body depends on stored
 * tokens, so the retry is built from the post-refresh values.
 */
export async function authFetch<T = unknown>(
  path: string,
  init: ApiFetchInit | (() => ApiFetchInit) = {}
): Promise<T> {
  const build = typeof init === "function" ? init : () => init;

  const send = (token: string) => {
    const req = build();
    const headers = new Headers(req.headers);
    headers.set("Authorization", `Bearer ${token}`);
    return apiFetch<T>(path, { ...req, headers });
  };

  try {
    return await send(await getAccessToken());
  } catch (err) {
    if (!(err instanceof ApiError) || err.status !== 401) throw err;
    return send(await refresh());
  }
}

/** Blacklist the refresh token on the server (best effort) and clear local tokens. */
export async function logout(): Promise<void> {
  try {
    if (hasSession()) {
      await authFetch("/api/v1/auth/token/logout/", () => ({
        method: "POST",
        json: { refresh: readToken(REFRESH_KEY) },
      }));
    }
  } catch {
    // Still log out locally. The refresh token may stay valid server-side until
    // it expires if this call failed.
  } finally {
    clearTokens();
  }
}

/**
 * POST auth/force-password-change/ for a user logged in with a temporary password.
 * The backend revokes every refresh token the user holds (this session's too)
 * and returns a fresh {access, refresh} pair, which replaces the stored one.
 */
export async function forcePasswordChange(newPassword: string, confirmPassword: string): Promise<void> {
  const data = await authFetch<TokenPair & { detail: string }>("/api/v1/auth/force-password-change/", {
    method: "POST",
    json: { new_password: newPassword, confirm_password: confirmPassword },
  });
  storeTokens(data);
}

export function fetchCurrentUser(): Promise<CurrentUser> {
  return authFetch<CurrentUser>("/api/v1/users/me/");
}

// ── Hook ─────────────────────────────────────────────────────

export type SessionState =
  | { status: "loading" }
  | { status: "authenticated"; user: CurrentUser }
  | { status: "unauthenticated" }
  | { status: "error"; message: string };

/**
 * Loads the signed-in user from users/me/. Resolves to "unauthenticated" when
 * there is no session, the session cannot be refreshed, or the account still has
 * to replace its temporary password (the backend answers 403 for every other
 * path until it does, so the user has to go back through login).
 */
export function useCurrentUser(): SessionState {
  const [state, setState] = useState<SessionState>({ status: "loading" });

  useEffect(() => {
    let cancelled = false;

    async function load() {
      if (!hasSession()) {
        if (!cancelled) setState({ status: "unauthenticated" });
        return;
      }
      try {
        const user = await fetchCurrentUser();
        if (!cancelled) setState({ status: "authenticated", user });
      } catch (err) {
        if (cancelled) return;
        const forceChange =
          err instanceof ApiError &&
          err.status === 403 &&
          typeof err.body === "object" &&
          err.body !== null &&
          (err.body as { force_password_change?: unknown }).force_password_change === true;
        if (err instanceof ApiError && (err.status === 401 || forceChange)) {
          clearTokens();
          setState({ status: "unauthenticated" });
        } else {
          setState({ status: "error", message: err instanceof ApiError ? err.detail : "Something went wrong." });
        }
      }
    }

    load();

    // Other tabs share localStorage. A removed refresh token means another tab
    // logged out (or its session ended): follow it. A new one means another tab
    // refreshed or logged in: reload the user so this tab picks it up.
    // `storage` events only fire in the tabs that did NOT make the change.
    function onStorage(e: StorageEvent) {
      if (e.storageArea !== window.localStorage) return;
      if (e.key !== REFRESH_KEY && e.key !== null) return; // null: storage.clear()
      if (cancelled) return;
      if (!hasSession()) setState({ status: "unauthenticated" });
      else if (e.newValue !== e.oldValue) load();
    }
    window.addEventListener("storage", onStorage);

    return () => {
      cancelled = true;
      window.removeEventListener("storage", onStorage);
    };
  }, []);

  return state;
}
