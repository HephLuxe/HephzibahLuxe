// Hand-made gallery row layouts for the portfolio, recovered from the original
// static frontend/data/portfolio.ts. These live on the frontend (not in the CMS)
// and are applied to the API's ordered images by lib/portfolio.ts.
//
// eventSlug / subEventSlug are the public slugs from the portfolio API. Leave
// subEventSlug out for single-day events. A layout only gives row sizes and
// optional fr ratios for 2-image rows; it never names an image file, so it fits
// whatever images the API returns in that order. Images beyond the layout fall
// back to the automatic row pattern; an event or day without a layout uses the
// automatic pattern throughout. Testimonial positions are in data/testimonials.ts.
//
// skipFirstImage: the first API image is the sub-event's card photo only, as on
// the static site, and is left out of the gallery.

export interface GalleryLayoutRow {
  count: number;
  ratios?: number[];
}

export interface GalleryLayout {
  eventSlug: string;
  subEventSlug?: string;
  skipFirstImage?: boolean;
  rows: GalleryLayoutRow[];
}

export const galleryLayouts: GalleryLayout[] = [
  {
    eventSlug: "golden-50th",
    subEventSlug: "pre-birthday-photoshoot",
    skipFirstImage: true,
    rows: [
      { count: 3 },
      { count: 1 },
      { count: 2 },
      { count: 1 },
      { count: 2, ratios: [3, 2] },
      { count: 2 },
      { count: 2, ratios: [3, 2] },
      { count: 1 },
    ],
  },
  {
    eventSlug: "golden-50th",
    subEventSlug: "thanksgiving-gathering",
    skipFirstImage: true,
    rows: [
      { count: 3 },
      { count: 1 },
      { count: 2 },
      { count: 1 },
      { count: 2, ratios: [3, 2] },
      { count: 3 },
      { count: 2, ratios: [3, 2] },
    ],
  },
  {
    eventSlug: "golden-50th",
    subEventSlug: "celebration-night",
    skipFirstImage: true,
    rows: [
      { count: 2 },
      { count: 3 },
      { count: 2 },
      { count: 2, ratios: [2, 3] },
      { count: 2 },
      { count: 1 },
      { count: 1 },
      { count: 2 },
      { count: 1 },
      { count: 2 },
      { count: 1 },
    ],
  },
  {
    eventSlug: "intimate-85th",
    rows: [
      { count: 1 },
      { count: 2 },
      { count: 2, ratios: [2, 3] },
      { count: 3 },
      { count: 2, ratios: [3, 2] },
      { count: 2 },
      { count: 3 },
      { count: 2, ratios: [3, 2] },
      { count: 1 },
    ],
  },
  {
    eventSlug: "msme-forum",
    rows: [
      { count: 3 },
      { count: 1 },
      { count: 2 },
      { count: 1 },
      { count: 1 },
      { count: 2 },
      { count: 2, ratios: [3, 2] },
      { count: 1 },
    ],
  },
];
