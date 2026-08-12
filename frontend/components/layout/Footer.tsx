"use client";

import Image from "next/image";
import Link from "next/link";
import { usePathname } from "next/navigation";

const navLinks = [
    [
        { label: "Home", href: "/" },
        { label: "About", href: "/about" },
        { label: "Portfolio", href: "/portfolio" },
    ],
    [
        { label: "Services", href: "/services" },
        { label: "Before & After", href: "/before-and-after" },
        { label: "Inquiries", href: "/inquiry" },
    ],
];

export default function Footer() {
    const pathname = usePathname();

    const scrollToTop = () => {
        window.scrollTo({ top: 0, behavior: "smooth" });
    };

    const handleNavClick = (href: string) => (e: React.MouseEvent<HTMLAnchorElement>) => {
        if (pathname === href) {
            // Already on this page — prevent navigation, just scroll up
            e.preventDefault();
            window.scrollTo({ top: 0, behavior: "smooth" });
        } else {
            // Navigating to a new page — let Next.js route, then force scroll to top after navigation
            // (Next.js sometimes preserves scroll on client transitions)
            setTimeout(() => window.scrollTo({ top: 0, behavior: "instant" as ScrollBehavior }), 0);
        }
    };

    return (
        <footer className="bg-primary text-background">
            <div className="px-4 sm:px-6 md:px-8 lg:px-8 xl:px-10 2xl:px-20 pt-10 sm:pt-12 md:pt-14 lg:pt-16 xl:pt-20 2xl:pt-24 pb-4 sm:pb-5 md:pb-6 lg:pb-6 xl:pb-8 2xl:pb-10">
                {/* Block 1: logo + nav links */}
                <div className="flex flex-row items-start justify-between gap-6 sm:gap-8 md:gap-10 lg:gap-10 xl:gap-14 2xl:gap-20">
                    <div className="flex-shrink-0">
                        <Image
                            src="/brand/logos.svg"
                            alt="Hephzibah Luxe"
                            width={320}
                            height={320}
                            className="w-[100px] h-[100px] sm:w-[130px] sm:h-[130px] md:w-[170px] md:h-[170px] lg:w-[280px] lg:h-[280px] xl:w-[320px] xl:h-[320px] 2xl:w-[360px] 2xl:h-[360px]"
                        />
                    </div>

                    <nav className="flex gap-8 sm:gap-10 md:gap-14 lg:gap-20 xl:gap-24 2xl:gap-28">
                        {navLinks.map((column, ci) => (
                            <ul key={ci} className="flex flex-col gap-3 sm:gap-4 md:gap-5 lg:gap-8 xl:gap-9 2xl:gap-10">
                                {column.map((link) => (
                                    <li key={link.href}>
                                        <Link
                                            href={link.href}
                                            onClick={handleNavClick(link.href)}
                                            className="font-display font-thin leading-[100%] text-background transition-opacity hover:opacity-70 text-[20px] sm:text-[24px] md:text-[30px] lg:text-[43px] xl:text-[50px] 2xl:text-[58px]"
                                        >
                                            {link.label}
                                        </Link>
                                    </li>
                                ))}
                            </ul>
                        ))}
                    </nav>
                </div>

                {/* Rest of footer unchanged */}
                <div className="mt-8 sm:mt-10 md:mt-10 lg:mt-12 xl:mt-14 flex flex-col lg:flex-row lg:justify-between gap-6 sm:gap-8 md:gap-10 lg:gap-16 xl:gap-20 2xl:gap-24">
                    <div className="font-body font-normal tracking-[-0.01em] text-background max-w-[480px] xl:max-w-[560px] 2xl:max-w-[640px] text-[13px] leading-[16px] sm:text-[14px] sm:leading-[18px] md:text-[18px] md:leading-[22px] lg:text-[18px] lg:leading-[27px] xl:text-[18px] xl:leading-[29px] 2xl:text-[20px] 2xl:leading-[31px]">
                        <p>
                            Crafting Refined, Beautiful, and Meaningful Celebrations — from Intimate Moments to Grand Occasions. Designed with Intention, Executed with Elegance.
                        </p>
                        <p className="mt-3 sm:mt-3.5 md:mt-4 lg:mt-4">Nigerian-Rooted | Globally Connected</p>
                    </div>

                    <div className="font-body font-normal tracking-[-0.01em] text-background space-y-1 sm:space-y-1.5 md:space-y-1.5 lg:space-y-2 text-[13px] leading-[16px] sm:text-[14px] sm:leading-[18px] md:text-[18px] md:leading-[22px] lg:text-[18px] lg:leading-[27px] xl:text-[18px] xl:leading-[29px] 2xl:text-[20px] 2xl:leading-[31px]">
                        <p>29 Adeniran Ogunsanya, Surulere, Lagos, Nigeria</p>
                        <p>Monday – Friday: 9:00 a.m. – 5:00 p.m.</p>
                        <p>Closed: After December 18 and on Public Holidays</p>
                        <p>0802 320 3870</p>
                    </div>
                </div>

                <div className="mt-8 sm:mt-9 md:mt-10 lg:mt-10 xl:mt-12 flex items-end justify-between gap-4">
                    <p className="font-body font-normal text-background text-[9px] leading-[16px] sm:text-[11px] sm:leading-[18px] md:text-[12px] md:leading-[20px] lg:text-[14px] lg:leading-[24px] xl:text-[15px] xl:leading-[26px] 2xl:text-[16px] 2xl:leading-[28px]">
                        © 2025 Hephzibah Luxe. All rights reserved.
                    </p>

                    <button
                        onClick={scrollToTop}
                        aria-label="Back to top"
                        className="lg:hidden flex flex-col items-center gap-1 group"
                    >
                        <Image
                            src="/icons/back.svg"
                            alt=""
                            width={40}
                            height={40}
                            className="w-[32px] h-[32px] sm:w-[36px] sm:h-[36px] md:w-[40px] md:h-[40px] transition-transform group-hover:-translate-y-1"
                        />
                        <span className="font-body font-normal text-background text-[9px] leading-[14px] sm:text-[10px] sm:leading-[15px] md:text-[11px] md:leading-[16px]">
                            Back to Top
                        </span>
                    </button>
                </div>
            </div>
        </footer>
    );
}