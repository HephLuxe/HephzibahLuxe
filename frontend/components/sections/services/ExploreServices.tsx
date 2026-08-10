"use client";

import { useRef, useCallback } from "react";
import Image from "next/image";
import Link from "next/link";

const services = [
    {
        image: "/images/portfoliopage/portfoliotwo.jpg",
        title: "Weddings",
        description:
            "Thoughtfully planned weddings that honour your story, your culture, and your vision—guided with care from the first conversation to the final farewell.",
    },
    {
        image: "/images/servicespage/birthdays.jpg",
        title: "Birthdays & Milestones",
        description:
            "From birthdays and anniversaries to life's meaningful milestones, we create celebrations that feel personal, refined, and beautifully considered—honouring the occasion and the people at its heart.",
    },
    {
        image: "/images/intro/introfives.jpg",
        title: "Corporate & Brand Events",
        description:
            "Polished, intentional events designed to reflect your brand, engage your guests, and deliver a seamless experience from arrival to farewell.",
    },
    {
        image: "/images/hero/heroones.jpg",
        title: "Private Events & Social Gatherings",
        description:
            "From proposals and naming ceremonies to private dinners and intimate gatherings, every celebration is thoughtfully planned with the same care, intention, and attention to detail as our largest events.",
    },
];

export default function ExploreServices() {
    const scrollRef = useRef<HTMLDivElement>(null);
    const isDraggingRef = useRef(false);
    const startXRef = useRef(0);
    const startScrollLeftRef = useRef(0);
    const movedRef = useRef(false);

    const handlePointerDown = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
        // Let native scrolling handle touch input (preserves momentum / elastic feel)
        if (e.pointerType === "touch") return;

        // Don't initiate a drag on interactive elements
        const target = e.target as HTMLElement;
        if (target.closest("a, button")) return;

        const container = scrollRef.current;
        if (!container) return;

        isDraggingRef.current = true;
        movedRef.current = false;
        startXRef.current = e.clientX;
        startScrollLeftRef.current = container.scrollLeft;
        container.setPointerCapture(e.pointerId);
    }, []);

    const handlePointerMove = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
        if (!isDraggingRef.current) return;
        const container = scrollRef.current;
        if (!container) return;

        const delta = e.clientX - startXRef.current;
        if (Math.abs(delta) > 4) movedRef.current = true;
        container.scrollLeft = startScrollLeftRef.current - delta;
    }, []);

    const handlePointerUp = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
        if (!isDraggingRef.current) return;
        const container = scrollRef.current;
        if (container) {
            try {
                container.releasePointerCapture(e.pointerId);
            } catch {
                // capture may have been released already
            }
        }
        isDraggingRef.current = false;
    }, []);

    // Suppress accidental clicks at the end of a drag
    const handleClickCapture = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
        if (movedRef.current) {
            e.preventDefault();
            e.stopPropagation();
            movedRef.current = false;
        }
    }, []);

    const scrollBy = useCallback((amount: number) => {
    const container = scrollRef.current;
    if (!container) return;
    container.scrollBy({ left: amount, behavior: "smooth" });
}, []);

const scrollLeft = useCallback(() => scrollBy(-200), [scrollBy]);
const scrollRight = useCallback(() => scrollBy(200), [scrollBy]);

    return (
        <section className="bg-background py-16 sm:py-20 md:py-24 lg:py-18 xl:py-16 2xl:py-32 overflow-hidden">
            <div className="px-6 sm:px-8 md:px-10 lg:px-10 xl:px-14 2xl:px-20">
                <div className="flex flex-col lg:flex-row gap-12 lg:gap-16 xl:gap-20 2xl:gap-24">
                    {/* Left: sticky text content */}
                    <div className="lg:w-[55%] lg:sticky lg:top-32 h-fit flex-shrink-0">
                        <h2 className="font-display font-thin text-primary mb-8 sm:mb-10 md:mb-10 tracking-[0] text-[50px] leading-[1] sm:text-[60px] sm:leading-[1] md:text-[80px] md:leading-[1] lg:text-[120px] lg:leading-[1.05] xl:text-[100px] xl:leading-[1.05] 2xl:text-[140px] 2xl:leading-[1.05]">
                            <span className="block">EXPLORE</span>
                            <span className="block">
                                <em className="italic font-thin">our</em> SERVICES
                            </span>
                        </h2>

                        <p className="font-body font-light text-primary mb-10 xl:mb-12 max-w-[660px] xl:max-w-[640px] 2xl:max-w-[720px] text-[15px] leading-[26px] sm:text-[16px] sm:leading-[28px] md:text-[18px] md:leading-[30px] lg:text-[19px] lg:leading-[28px] xl:text-[21px] xl:leading-[32px] 2xl:text-[23px] 2xl:leading-[34px]">
                            We design celebrations of every kind—weddings, milestones, corporate events, and private gatherings—each approached with the same level of intention and care. From intimate moments to larger-scale occasions, our focus remains on refined planning, seamless execution, and an experience that feels beautifully considered from beginning to end.
                        </p>

                        <Link
                            href="/inquiry"
                            className="group inline-flex items-center justify-center gap-6 border border-primary px-6 py-3 xl:px-7 xl:py-3.5 2xl:px-8 2xl:py-4 transition-colors hover:bg-primary"
                        >
                            <span className="font-body font-light italic tracking-[0.01em] text-primary group-hover:text-background transition-colors text-[18px] sm:text-[19px] md:text-[20px] lg:text-[20px] xl:text-[22px] 2xl:text-[24px]">
                                Begin Your Journey
                            </span>
                            <span className="relative inline-block w-[20px] h-[20px] xl:w-[22px] xl:h-[22px] 2xl:w-[24px] 2xl:h-[24px]">
                                <Image
                                    src="/icons/buttonarrow.svg"
                                    alt=""
                                    fill
                                    className="object-contain transition-opacity group-hover:opacity-0"
                                />
                                <Image
                                    src="/icons/whitebuttonarrow.svg"
                                    alt=""
                                    fill
                                    className="object-contain opacity-0 transition-opacity group-hover:opacity-100"
                                />
                            </span>
                        </Link>
                    </div>

                    {/* Right: horizontal scroll cards + arrow controls */}
<div className="lg:w-[45%] min-w-0 flex flex-col">
    <div
        ref={scrollRef}
        className="flex gap-8 xl:gap-10 2xl:gap-12 overflow-x-auto pb-2 scrollbar-hide select-none cursor-grab active:cursor-grabbing"
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        onPointerCancel={handlePointerUp}
        onClickCapture={handleClickCapture}
    >
        {services.map((service, i) => (
            <div
                key={i}
                className={`flex-none w-[300px] sm:w-[320px] md:w-[360px] lg:w-[340px] xl:w-[380px] 2xl:w-[420px] group ${
                    i % 2 === 0 ? "mt-12 lg:mt-20 xl:mt-24" : ""
                }`}
            >
                <div className="relative w-full aspect-[3/4] overflow-hidden mb-6 xl:mb-8 pointer-events-none">
                    <Image
                        src={service.image}
                        alt={service.title}
                        fill
                        className="object-cover transition-transform duration-700 group-hover:scale-105"
                        sizes="420px"
                        draggable={false}
                    />
                </div>
                <h3 className="font-display font-thin text-primary mb-3 leading-tight tracking-[0] text-[26px] sm:text-[28px] md:text-[32px] lg:text-[30px] xl:text-[36px] 2xl:text-[40px]">
                    {service.title}
                </h3>
                <p className="font-body font-light text-primary text-[15px] leading-[24px] sm:text-[16px] sm:leading-[26px] md:text-[18px] md:leading-[28px] lg:text-[19px] lg:leading-[28px] xl:text-[20px] xl:leading-[30px] 2xl:text-[22px] 2xl:leading-[32px]">
                    {service.description}
                </p>
            </div>
        ))}
    </div>

    {/* Arrow controls — bottom right on all screens */}
<div className="mt-8 sm:mt-10 md:mt-12 flex justify-end gap-3 sm:gap-4">
    <button
        type="button"
        onClick={scrollLeft}
        aria-label="Scroll cards left"
        className="group w-12 h-12 sm:w-14 sm:h-14 md:w-16 md:h-16 rounded-full border border-primary flex items-center justify-center transition-colors hover:bg-primary"
    >
        <Image
            src="/icons/leftarrow.svg"
            alt=""
            width={24}
            height={24}
            className="w-4 h-4 sm:w-5 sm:h-5 md:w-6 md:h-6 transition-[filter] group-hover:[filter:invert(1)_brightness(2)]"
        />
    </button>

    <button
        type="button"
        onClick={scrollRight}
        aria-label="Scroll cards right"
        className="group w-12 h-12 sm:w-14 sm:h-14 md:w-16 md:h-16 rounded-full border border-primary flex items-center justify-center transition-colors hover:bg-primary"
    >
        <Image
            src="/icons/rightarrow.svg"
            alt=""
            width={24}
            height={24}
            className="w-4 h-4 sm:w-5 sm:h-5 md:w-6 md:h-6 transition-[filter] group-hover:[filter:invert(1)_brightness(2)]"
        />
    </button>
</div>
</div>
                </div>
            </div>
        </section>
    );
}