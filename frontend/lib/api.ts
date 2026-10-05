// Server-side client for the Django public portfolio API.
// Only import this from Server Components / route segment functions.

export const PORTFOLIO_REVALIDATE_SECONDS = 300;

export type ApiEventType = "Birthday" | "Wedding" | "Corporate" | "Social Events" | "Others";

export interface ApiImage {
  image: string; // absolute URL
  alt_text: string;
  sort_order: number;
}

export interface ApiPortfolioEventSummary {
  slug: string;
  headline: string;
  event_type: ApiEventType;
  country: string;
  state: string;
  year: number;
  cover_image: ApiImage | null;
}

export interface ApiEventDay {
  slug: string | null; // curated public sub-event slug; null for odd data
  event_day_title: string;
  headline: string;
  content: string;
  date: string;
  images: ApiImage[];
}

export interface ApiPortfolioEventDetail extends ApiPortfolioEventSummary {
  description: string; // paragraphs separated by blank lines
  images: ApiImage[]; // event-level gallery, excludes the cover
  event_days: ApiEventDay[];
}

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status?: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

function apiBaseUrl(): string {
  const url =
    process.env.API_BASE_URL ||
    process.env.NEXT_PUBLIC_API_BASE_URL ||
    (process.env.NODE_ENV === "development" ? "http://localhost:8000" : "");
  if (!url) {
    throw new ApiError("API_BASE_URL (or NEXT_PUBLIC_API_BASE_URL) is not set");
  }
  return url.replace(/\/+$/, "");
}

/** True while `next build` is prerendering (used to degrade instead of failing the build). */
export function isBuildPhase(): boolean {
  return process.env.NEXT_PHASE === "phase-production-build";
}

async function apiGet(path: string): Promise<Response> {
  const url = `${apiBaseUrl()}${path}`;
  try {
    return await fetch(url, {
      headers: { Accept: "application/json" },
      next: { revalidate: PORTFOLIO_REVALIDATE_SECONDS },
    });
  } catch (err) {
    throw new ApiError(`Network error fetching ${url}: ${(err as Error).message}`);
  }
}

export async function getHomeStrip(): Promise<ApiImage[]> {
  const res = await apiGet("/api/v1/public/home-strip/");
  if (!res.ok) {
    throw new ApiError(`GET home strip failed with ${res.status}`, res.status);
  }
  return (await res.json()) as ApiImage[];
}

export async function getPortfolioEvents(): Promise<ApiPortfolioEventSummary[]> {
  const res = await apiGet("/api/v1/portfolio/events/");
  if (!res.ok) {
    throw new ApiError(`GET portfolio events failed with ${res.status}`, res.status);
  }
  return (await res.json()) as ApiPortfolioEventSummary[];
}

/** Returns null when the event does not exist or is unpublished (404). */
export async function getPortfolioEvent(slug: string): Promise<ApiPortfolioEventDetail | null> {
  const res = await apiGet(`/api/v1/portfolio/events/${encodeURIComponent(slug)}/`);
  if (res.status === 404) return null;
  if (!res.ok) {
    throw new ApiError(`GET portfolio event "${slug}" failed with ${res.status}`, res.status);
  }
  return (await res.json()) as ApiPortfolioEventDetail;
}
