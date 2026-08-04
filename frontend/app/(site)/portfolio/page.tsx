import PortfolioHero from "@/components/sections/portfolio/PortfolioHero";
import PortfolioEvents from "@/components/sections/portfolio/PortfolioEvents";
import ClientPhilosophy from "@/components/sections/portfolio/ClientPhilosophy";
import Instagram from "@/components/sections/Instagram";

export default function PortfolioPage() {
    return (
        <main>
            <PortfolioHero />
            <PortfolioEvents />
            <ClientPhilosophy />
            <Instagram />
        </main>
    );
}