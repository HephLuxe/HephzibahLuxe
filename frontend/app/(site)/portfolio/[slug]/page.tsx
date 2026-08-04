import { notFound } from "next/navigation";
import { getEventBySlug, portfolioEvents } from "@/data/portfolio";
import MultiDayEvent from "@/components/sections/portfolio/MultiDayEvent";
import SingleDayEvent from "@/components/sections/portfolio/SingleDayEvent";
import Instagram from "@/components/sections/Instagram";

export function generateStaticParams() {
  return portfolioEvents.map((event) => ({ slug: event.slug }));
}

interface PageProps {
  params: Promise<{ slug: string }>;
}

export default async function PortfolioDetailsPage({ params }: PageProps) {
  const { slug } = await params;
  const event = getEventBySlug(slug);

  if (!event) notFound();

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