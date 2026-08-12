"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import Image from "next/image";
import { usePathname } from "next/navigation";

const navLinks = [
    { label: "Home", href: "/" },
    { label: "About", href: "/about" },
    { label: "Services", href: "/services" },
    { label: "Portfolio", href: "/portfolio" },
    { label: "Before & After", href: "/before-and-after" },
    { label: "Inquiries", href: "/inquiry" },
];

export default function Navbar() {
    const [isOpen, setIsOpen] = useState(false);
    const pathname = usePathname();

    useEffect(() => {
        document.body.style.overflow = isOpen ? "hidden" : "";
        return () => {
            document.body.style.overflow = "";
        };
    }, [isOpen]);

    const isActive = (href: string) => {
        if (href === "/") return pathname === "/";
        return pathname === href || pathname.startsWith(href + "/");
    };

    return (
        <header className="bg-primary relative z-50">
            <nav className="flex items-center justify-between px-3 sm:px-4 md:px-6 lg:px-8 xl:px-12 2xl:px-16 py-2 sm:py-2 md:py-1 lg:py-1">
                {/* Logo + Wordmark */}
                <Link href="/" className="flex items-center" onClick={() => setIsOpen(false)}>
                    <Image
                        src="/brand/logos.svg"
                        alt="Hephzibah Luxe"
                        width={80}
                        height={80}
                        priority
                        className="w-16 h-16 sm:w-18 sm:h-18 md:w-20 md:h-20 lg:w-20 lg:h-20 xl:w-20 xl:h-20 2xl:w-28 2xl:h-28"
                    />
                    <div className="flex flex-col">
                        <span className="font-display font-thin text-background tracking-[-0.01em] leading-[100%] text-[26px] sm:text-[30px] md:text-[34px] lg:text-[38px] xl:text-[40px] 2xl:text-[44px]">
                            HEPHZIBAH LUXE
                        </span>
                        <span className="font-body font-light italic text-background/80 mt-1 text-[12px] sm:text-[12px] md:text-[14px] lg:text-[12px] xl:text-[13px] 2xl:text-[14px]">
                            Event Planning & Design Studio
                        </span>
                    </div>
                </Link>

                {/* Desktop nav */}
                <div className="hidden lg:flex items-center gap-8 xl:gap-10 2xl:gap-12">
                    <ul className="flex items-center gap-8 xl:gap-8 2xl:gap-10">
                        {navLinks
                            .filter((link) => link.label !== "Home")
                            .map((link) => (
                                <li key={link.href}>
                                    <Link
                                        href={link.href}
                                        className={`font-body italic text-background transition-colors hover:text-background/70 pb-1 border-b text-[17px] xl:text-[17px] 2xl:text-[19px] ${
                                            isActive(link.href)
                                                ? "border-background"
                                                : "border-transparent"
                                        }`}
                                    >
                                        {link.label}
                                    </Link>
                                </li>
                            ))}
                    </ul>
                    <div className="h-6 xl:h-7 2xl:h-8 w-px bg-background/30" />
                    <Link
                        href="/client-portal"
                        aria-label="Client portal"
                        className="transition-opacity hover:opacity-70"
                    >
                        <Image
                            src="/icons/profile.svg"
                            alt=""
                            width={24}
                            height={24}
                            className="w-[20px] h-[20px] xl:w-[22px] xl:h-[22px] 2xl:w-[24px] 2xl:h-[24px]"
                        />
                    </Link>
                </div>

                {/* Mobile menu button */}
                <button
                    onClick={() => setIsOpen(true)}
                    aria-label="Open menu"
                    className="lg:hidden transition-opacity hover:opacity-70"
                >
                    <Image
                        src="/icons/menu.svg"
                        alt=""
                        width={40}
                        height={40}
                        className="w-[32px] h-[32px] sm:w-[36px] sm:h-[36px] md:w-[40px] md:h-[40px]"
                    />
                </button>
            </nav>

            {/* Mobile menu overlay */}
            <div
                className={`fixed inset-0 z-50 bg-primary transition-opacity duration-300 lg:hidden ${
                    isOpen ? "opacity-100 pointer-events-auto" : "opacity-0 pointer-events-none"
                }`}
            >
                <div className="flex flex-col h-full px-6 sm:px-8 md:px-10 py-6 sm:py-7 md:py-8">
                    <div className="flex items-start justify-between">
                        <Image
                            src="/brand/logos.svg"
                            alt="Hephzibah Luxe"
                            width={240}
                            height={240}
                            className="w-[220px] h-[220px] sm:w-[200px] sm:h-[200px] md:w-[300px] md:h-[300px]"
                        />
                        <button
                            onClick={() => setIsOpen(false)}
                            aria-label="Close menu"
                            className="transition-opacity hover:opacity-70 mt-2"
                        >
                            <Image
                                src="/icons/close.svg"
                                alt=""
                                width={32}
                                height={32}
                                className="w-[24px] h-[24px] sm:w-[28px] sm:h-[28px] md:w-[32px] md:h-[32px]"
                            />
                        </button>
                    </div>

                    <ul className="flex flex-col items-end justify-center flex-1 gap-3 sm:gap-2 md:gap-3 pr-2 sm:pr-4 md:pr-6 -mt-32">
                        {navLinks.map((link) => (
                            <li key={link.href}>
                                <Link
                                    href={link.href}
                                    onClick={() => setIsOpen(false)}
                                    className={`font-display font-thin text-background tracking-[0.05em] uppercase transition-opacity hover:opacity-70 pb-1 border-b text-[36px] sm:text-[48px] md:text-[60px] ${
                                        isActive(link.href)
                                            ? "border-background"
                                            : "border-transparent"
                                    }`}
                                >
                                    {link.label}
                                </Link>
                            </li>
                        ))}
                    </ul>

                    <div className="flex justify-end pb-8 md:pb-10">
                        <Link
                            href="/client-portal"
                            onClick={() => setIsOpen(false)}
                            className="font-sans text-background uppercase tracking-[0.1em] underline underline-offset-4 text-sm sm:text-base md:text-lg"
                        >
                            Client Login
                        </Link>
                    </div>
                </div>
            </div>
        </header>
    );
}