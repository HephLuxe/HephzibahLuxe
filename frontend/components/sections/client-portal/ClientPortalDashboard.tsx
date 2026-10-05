"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { logout, useCurrentUser } from "@/lib/auth";

const LOGIN_PATH = "/client-portal";

export default function ClientPortalDashboard() {
    const router = useRouter();
    const session = useCurrentUser();
    const [loggingOut, setLoggingOut] = useState(false);

    useEffect(() => {
        if (session.status === "unauthenticated") router.replace(LOGIN_PATH);
    }, [session.status, router]);

    async function handleLogout() {
        setLoggingOut(true);
        await logout();
        router.replace(LOGIN_PATH);
    }

    const bodyCls = "font-body font-light text-primary text-[15px] leading-[24px] sm:text-[16px] sm:leading-[26px] md:text-[18px] md:leading-[28px] lg:text-[16px] lg:leading-[25px] xl:text-[18px] xl:leading-[28px] 2xl:text-[20px] 2xl:leading-[30px]";

    return (
        <section className="bg-background text-primary min-h-[60vh]">
            <div className="max-w-3xl mx-auto px-4 sm:px-6 md:px-10 py-16 sm:py-20 md:py-24">
                {session.status === "authenticated" ? (
                    <>
                        <h1 className="font-display font-thin tracking-[0.03em] leading-[110%] text-primary text-[40px] sm:text-[44px] md:text-[52px] lg:text-[64px]">
                            Welcome, <span className="italic">{session.user.first_name}</span>
                        </h1>
                        <p className={`mt-6 ${bodyCls}`}>
                            Signed in as <span className="font-normal">{session.user.email}</span>
                        </p>
                        <p className={`mt-4 ${bodyCls}`}>
                            Your full Client Portal, with your planning documents, timelines and shared updates, is coming soon.
                        </p>
                        <div className="mt-10">
                            <button
                                type="button"
                                onClick={handleLogout}
                                disabled={loggingOut}
                                className="inline-flex items-center px-10 py-4 md:px-11 md:py-[18px] bg-secondary border border-secondary hover:bg-background hover:border-primary transition-colors group"
                            >
                                <span className="font-sans font-normal tracking-[0.15em] uppercase text-background group-hover:text-primary transition-colors text-[14px] sm:text-[15px] md:text-[16px] lg:text-[14px] xl:text-[16px]">
                                    {loggingOut ? "Logging Out…" : "Log Out"}
                                </span>
                            </button>
                        </div>
                    </>
                ) : session.status === "error" ? (
                    <p role="alert" className={bodyCls}>
                        {session.message}
                    </p>
                ) : (
                    <p className={bodyCls} aria-live="polite">
                        Loading…
                    </p>
                )}
            </div>
        </section>
    );
}
