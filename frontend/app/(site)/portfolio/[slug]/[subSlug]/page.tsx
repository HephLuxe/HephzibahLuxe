import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { getPortfolioEvent, getPortfolioEvents } from "@/lib/api";
import { toPortfolioEvent } from "@/lib/portfolio";
import SubEventDetail from "@/components/sections/portfolio/SubEventDetail";
import Instagram from "@/components/sections/Instagram";

export const revalidate = 300;
export const dynamicParams = true;

export async function generateStaticParams() {
  try {
    const summaries = await getPortfolioEvents();
    const details = await Promise.all(summaries.map((s) => getPortfolioEvent(s.slug)));
    const params: Array<{ slug: string; subSlug: string }> = [];
    for (const api of details) {
      if (!api) continue;
      const event = toPortfolioEvent(api);
      for (const sub of event.subEvents ?? []) {
        params.push({ slug: event.slug, subSlug: sub.slug });
      }
    }
    return params;
  } catch (err) {
    // API unreachable at build time: render every sub-event on demand instead.
    console.warn("[portfolio] generateStaticParams: API unreachable, skipping prerender:", err);
    return [];
  }
}

interface PageProps {
  params: Promise<{ slug: string; subSlug: string }>;
}

async function getSubEvent(slug: string, subSlug: string) {
  const api = await getPortfolioEvent(slug);
  if (!api) return null;
  const event = toPortfolioEvent(api);
  const subEvent = event.subEvents?.find((s) => s.slug === subSlug);
  return subEvent ? { event, subEvent } : null;
}

export async function generateMetadata({ params }: PageProps): Promise<Metadata> {
  const { slug, subSlug } = await params;
  let found: Awaited<ReturnType<typeof getSubEvent>>;
  try {
    found = await getSubEvent(slug, subSlug);
  } catch {
    // Never fail on metadata: fall back to the root layout's generic metadata and
    // let the page itself surface the API error through error.tsx.
    return {};
  }
  if (!found) return {};

  const { event, subEvent } = found;
  const title = `${subEvent.title} | ${event.title} | Hephzibah-Luxe`;
  const description = subEvent.description?.[0] ?? event.description?.[0];
  const ogImage = subEvent.image || event.coverImage;
  return {
    title,
    description,
    openGraph: {
      title,
      description,
      type: "article",
      images: ogImage ? [{ url: ogImage, alt: subEvent.title }] : undefined,
    },
  };
}

export default async function SubEventPage({ params }: PageProps) {
  const { slug, subSlug } = await params;
  const found = await getSubEvent(slug, subSlug);
  if (!found) notFound();

  return (
    <main>
      <SubEventDetail event={found.event} subEvent={found.subEvent} />
      <Instagram />
    </main>
  );
}
