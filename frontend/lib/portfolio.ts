// View-model types for the portfolio UI, plus a pure adapter from the
// Django public portfolio API (see lib/api.ts) to those types.

import type {
  ApiEventType,
  ApiImage,
  ApiPortfolioEventDetail,
  ApiPortfolioEventSummary,
} from "./api";
import { portfolioTestimonials } from "@/data/testimonials";
import { galleryLayouts, type GalleryLayout } from "@/data/galleryLayouts";

export type EventCategory = "Weddings" | "Birthdays" | "Corporate" | "Social Events" | "Others";
export type EventType = "single-day" | "multi-day";

export interface GalleryImage {
  src: string;
  alt: string;
}

export type GalleryRow =
  | { type: "images"; images: GalleryImage[]; ratios?: number[] }
  | { type: "testimonial"; quote: string; attribution: string };

export interface SubEvent {
  subtitle: string;
  title: string;
  image: string;
  slug: string;
  description?: string[];
  gallery?: GalleryRow[];
}

export interface PortfolioEvent {
  slug: string;
  type: EventType;
  category: EventCategory;
  location: string;
  year: number;
  title: string;
  coverImage: string;
  description?: string[];
  subEvents?: SubEvent[];
  gallery?: GalleryRow[];
}

const CATEGORY_BY_EVENT_TYPE: Record<ApiEventType, EventCategory> = {
  Wedding: "Weddings",
  Birthday: "Birthdays",
  Corporate: "Corporate",
  "Social Events": "Social Events",
  Others: "Others",
};

function toCategory(eventType: string): EventCategory {
  return CATEGORY_BY_EVENT_TYPE[eventType as ApiEventType] ?? "Others";
}

function toLocation(state: string, country: string): string {
  return [state, country]
    .map((s) => s?.trim())
    .filter(Boolean)
    .join(", ");
}

function toParagraphs(text: string | null | undefined): string[] | undefined {
  const paras = (text ?? "")
    .split(/\r?\n\s*\r?\n/)
    .map((p) => p.trim())
    .filter(Boolean);
  return paras.length ? paras : undefined;
}

function slugify(text: string): string {
  return text
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

/** Blank API alt text falls back to "<title> — photo N" (N = 1-based position in the gallery). */
function toGalleryImages(images: ApiImage[], title: string): GalleryImage[] {
  return images.map((img, idx) => ({
    src: img.image,
    alt: img.alt_text?.trim() || `${title} — photo ${idx + 1}`,
  }));
}

// Row sizes (and 3fr/2fr split rows) distilled from the hand-made layouts that
// previously lived in data/portfolio.ts: 3, 1, 2, 1, 2 (3:2), 2, 2 (3:2), 1.
// The cycle repeats for longer galleries; the final row takes whatever is left.
const ROW_PATTERN: Array<{ size: number; ratios?: number[] }> = [
  { size: 3 },
  { size: 1 },
  { size: 2 },
  { size: 1 },
  { size: 2, ratios: [3, 2] },
  { size: 2 },
  { size: 2, ratios: [3, 2] },
  { size: 1 },
];

function findLayout(eventSlug: string, subEventSlug?: string): GalleryLayout | undefined {
  return galleryLayouts.find(
    (l) => l.eventSlug === eventSlug && l.subEventSlug === subEventSlug,
  );
}

/**
 * Splits the ordered images into rows: the hand-made layout's rows first (when
 * there is one), then the automatic pattern for any images left over.
 */
export function toGalleryRows(
  images: GalleryImage[],
  layout?: GalleryLayout,
): GalleryRow[] | undefined {
  const rows: GalleryRow[] = [];
  let i = 0;
  for (const { count, ratios } of layout?.rows ?? []) {
    if (i >= images.length) break;
    const chunk = images.slice(i, i + count);
    rows.push(
      chunk.length === count && ratios
        ? { type: "images", images: chunk, ratios }
        : { type: "images", images: chunk },
    );
    i += chunk.length;
  }
  const rest = toAutoGalleryRows(images.slice(i));
  if (rest) rows.push(...rest);
  return rows.length ? rows : undefined;
}

function toAutoGalleryRows(images: GalleryImage[]): GalleryRow[] | undefined {
  if (images.length === 0) return undefined;
  const rows: GalleryRow[] = [];
  let i = 0;
  let p = 0;
  while (i < images.length) {
    const { size, ratios } = ROW_PATTERN[p % ROW_PATTERN.length];
    const chunk = images.slice(i, i + size);
    rows.push(
      chunk.length === size && ratios
        ? { type: "images", images: chunk, ratios }
        : { type: "images", images: chunk },
    );
    i += chunk.length;
    p += 1;
  }
  return rows;
}

/**
 * Inserts the static testimonials (data/testimonials.ts) for this event or
 * sub-event into its gallery rows. A testimonial goes after `afterRow` rows,
 * clamped to the end of the gallery.
 */
function withTestimonials(
  rows: GalleryRow[] | undefined,
  eventSlug: string,
  subEventSlug?: string,
): GalleryRow[] | undefined {
  const matches = portfolioTestimonials.filter(
    (t) => t.eventSlug === eventSlug && t.subEventSlug === subEventSlug,
  );
  if (matches.length === 0) return rows;

  const base = rows ?? [];
  const merged: GalleryRow[] = [];
  for (let pos = 0; pos <= base.length; pos++) {
    for (const t of matches) {
      const at = Math.min(Math.max(t.afterRow, 0), base.length);
      if (at === pos) {
        merged.push({ type: "testimonial", quote: t.quote, attribution: t.attribution });
      }
    }
    if (pos < base.length) merged.push(base[pos]);
  }
  return merged;
}

/** "Lagos, Nigeria — 2021", or just "2021" when there is no location. */
export function formatLocationYear(event: Pick<PortfolioEvent, "location" | "year">): string {
  return event.location ? `${event.location} — ${event.year}` : String(event.year);
}

/** Flattened gallery images in display order (for the lightbox). */
export function flattenGallery(rows: GalleryRow[] | undefined): GalleryImage[] {
  return rows?.flatMap((row) => (row.type === "images" ? row.images : [])) ?? [];
}

/** List-endpoint item → card-level view model (no description/gallery). */
export function toPortfolioEventSummary(api: ApiPortfolioEventSummary): PortfolioEvent {
  return {
    slug: api.slug,
    type: "single-day",
    category: toCategory(api.event_type),
    location: toLocation(api.state, api.country),
    year: api.year,
    title: api.headline,
    coverImage: api.cover_image?.image ?? "",
  };
}

/** Detail-endpoint payload → full view model. */
export function toPortfolioEvent(api: ApiPortfolioEventDetail): PortfolioEvent {
  const base = toPortfolioEventSummary(api);
  const days = api.event_days ?? [];

  if (days.length > 1) {
    // Curated public slugs from the API win; a slug is only generated from the
    // title when the API has none for that day.
    const usedSlugs = new Set<string>(
      days.map((day) => day.slug?.trim() ?? "").filter(Boolean),
    );
    const subEvents: SubEvent[] = days.map((day, idx) => {
      const title = day.headline?.trim() || day.event_day_title?.trim() || `Day ${idx + 1}`;
      let slug = day.slug?.trim() ?? "";
      if (!slug) {
        slug = slugify(title) || `day-${idx + 1}`;
        if (usedSlugs.has(slug)) slug = `${slug}-${idx + 1}`;
        usedSlugs.add(slug);
      }

      // images[0] doubles as the overview card image. It stays in the day's
      // gallery (staff uploads have no separate card photo) unless the day's
      // layout marks it as card-only, as the static site had it.
      const dayImages = day.images ?? [];
      const layout = findLayout(base.slug, slug);
      const galleryImages = toGalleryImages(
        dayImages.slice(layout?.skipFirstImage ? 1 : 0),
        title,
      );

      return {
        subtitle: day.event_day_title?.trim() ?? "",
        title,
        image: dayImages[0]?.image ?? "",
        slug,
        description: toParagraphs(day.content),
        gallery: withTestimonials(toGalleryRows(galleryImages, layout), base.slug, slug),
      };
    });

    return {
      ...base,
      type: "multi-day",
      description: toParagraphs(api.description),
      subEvents,
    };
  }

  // Event images and the single day's images render as one gallery, numbered together.
  const galleryImages = toGalleryImages(
    [...(api.images ?? []), ...(days[0]?.images ?? [])],
    api.headline,
  );

  return {
    ...base,
    type: "single-day",
    description: toParagraphs(api.description),
    gallery: withTestimonials(
      toGalleryRows(galleryImages, findLayout(base.slug)),
      base.slug,
    ),
  };
}
