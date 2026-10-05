import PortfolioHero from "@/components/sections/portfolio/PortfolioHero";
import PortfolioEvents from "@/components/sections/portfolio/PortfolioEvents";
import ClientPhilosophy from "@/components/sections/portfolio/ClientPhilosophy";
import Instagram from "@/components/sections/Instagram";
import { getPortfolioEvents, isBuildPhase, type ApiPortfolioEventSummary } from "@/lib/api";
import { toPortfolioEventSummary } from "@/lib/portfolio";

export const revalidate = 300;

export default async function PortfolioPage() {
    let apiEvents: ApiPortfolioEventSummary[];
    try {
        apiEvents = await getPortfolioEvents();
    } catch (err) {
        // Don't fail `next build` when the API is unreachable: prerender the empty
        // state and let ISR replace it on the next successful revalidation.
        // At request time, rethrow so the segment's error.tsx is shown.
        if (!isBuildPhase()) throw err;
        console.warn("[portfolio] API unreachable during build, prerendering empty list:", err);
        apiEvents = [];
    }
    // The API lists newest first; the portfolio page shows events oldest first.
    const events = [...apiEvents].reverse().map(toPortfolioEventSummary);

    return (
        <main>
            <PortfolioHero />
            <PortfolioEvents events={events} />
            <ClientPhilosophy />
            <Instagram />
        </main>
    );
}
