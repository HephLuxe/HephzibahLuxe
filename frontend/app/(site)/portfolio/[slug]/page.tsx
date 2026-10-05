import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { getPortfolioEvent, getPortfolioEvents } from "@/lib/api";
import { toPortfolioEvent } from "@/lib/portfolio";
import MultiDayEvent from "@/components/sections/portfolio/MultiDayEvent";
import SingleDayEvent from "@/components/sections/portfolio/SingleDayEvent";
import Instagram from "@/components/sections/Instagram";

export const revalidate = 300;
export const dynamicParams = true;

export async function generateStaticParams() {
  try {
    const events = await getPortfolioEvents();
    return events.map((event) => ({ slug: event.slug }));
  } catch (err) {
    // API unreachable at build time: render every slug on demand instead.
    console.warn("[portfolio] generateStaticParams: API unreachable, skipping prerender:", err);
    return [];
  }
}

interface PageProps {
  params: Promise<{ slug: string }>;
}

export async function generateMetadata({ params }: PageProps): Promise<Metadata> {
  const { slug } = await params;
  let api: Awaited<ReturnType<typeof getPortfolioEvent>>;
  try {
    api = await getPortfolioEvent(slug);
  } catch {
    // Never fail on metadata: fall back to the root layout's generic metadata and
    // let the page itself surface the API error through error.tsx.
    return {};
  }
  if (!api) return {};

  const event = toPortfolioEvent(api);
  const title = `${event.title} | Hephzibah-Luxe`;
  const description = event.description?.[0];
  return {
    title,
    description,
    openGraph: {
      title,
      description,
      type: "article",
      images: api.cover_image
        ? [{ url: api.cover_image.image, alt: api.cover_image.alt_text || event.title }]
        : undefined,
    },
  };
}

export default async function PortfolioDetailsPage({ params }: PageProps) {
  const { slug } = await params;
  const api = await getPortfolioEvent(slug);

  if (!api) notFound();
  const event = toPortfolioEvent(api);

  return (
    <main>
      {event.type === "multi-day" ? (
        <MultiDayEvent event={event} />
      ) : (
        <SingleDayEvent event={event} />
      )}

      <Instagram />
    </main>
  );
}
