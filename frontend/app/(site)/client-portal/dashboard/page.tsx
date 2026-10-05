import type { Metadata } from "next";
import ClientPortalDashboard from "@/components/sections/client-portal/ClientPortalDashboard";

export const metadata: Metadata = {
    robots: { index: false, follow: false },
};

export default function ClientPortalDashboardPage() {
    return (
        <main>
            <ClientPortalDashboard />
        </main>
    );
}
