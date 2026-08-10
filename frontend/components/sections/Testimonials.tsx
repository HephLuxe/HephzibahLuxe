"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import Image from "next/image";
import Link from "next/link";

const testimonials = [
    {
        image: "/images/testimonial/testimonialone.jpg",
        name: "Abimbola, Manager",
        subheader: "Lagos State Government (OPL & CE) MSME Engagement Forum",
        quote:
            "“Working with Hephzibah Luxe was a wonderful experience. Despite the short notice and tight budget, the team ensured the event was well organised and executed seamlessly from start to finish. Their coordination and attention to detail created a welcoming and professional atmosphere for our guests. We truly appreciated their dedication, professionalism, and calm approach throughout the entire process. ”",
        cta: "View Their Forum",
        href: "/portfolio/msme-forum",
    },
    {
        image: "/images/testimonial/testimonialtwo.jpg",
        name: "Winnie, Celebrant",
        subheader: "A Golden 50th: An Intimate Two-Day Celebration of Family, Faith & Joy",
        quote:
            "“I wanted something simple but meaningful—and that’s exactly what this was. Every detail felt intentional, and the photos captured me in such a beautiful, authentic way. There was a quiet attention to detail that made me feel completely at ease, and it truly showed in the final images. What I loved most was that the focus never shifted away from me. It felt like the perfect way to step into fifty. ”",
        cta: "View Her Birthday",
        href: "/portfolio/golden-50th",
    },
    {
        image: "/images/testimonial/testimonialthreee.jpg",
        name: "Olamipe, Family",
        subheader: "An Intimate 85th: A Celebration of Grace, Family & Legacy",
        quote:
            "“With very little time to plan, we were unsure how everything would come together, but Hephzibah Luxe handled every detail with such care and calmness. Watching our mother celebrate alongside her church community, family, and close friends was incredibly special, and the atmosphere felt warm, graceful, and a true reflection of her life and faith.”",
        cta: "View The Birthday",
        href: "/portfolio/intimate-85th",
    },
];

const SWIPE_THRESHOLD_RATIO = 0.15; // 15% of track width to trigger slide change

export default function Testimonials() {
    const [activeIndex, setActiveIndex] = useState(0);
    const [isDragging, setIsDragging] = useState(false);
    const [dragOffset, setDragOffset] = useState(0);
    const trackRef = useRef<HTMLDivElement>(null);
    const startXRef = useRef(0);
    const isDraggingRef = useRef(false);
    const dragOffsetRef = useRef(0);

    // Autoplay — paused while dragging
    useEffect(() => {
        if (isDragging) return;
        const interval = setInterval(() => {
            setActiveIndex((prev) => (prev + 1) % testimonials.length);
        }, 10000);
        return () => clearInterval(interval);
    }, [isDragging]);

    const handlePointerDown = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
        const target = e.target as HTMLElement;
        if (target.closest("a, button")) return;

        startXRef.current = e.clientX;
        dragOffsetRef.current = 0;
        isDraggingRef.current = true;
        setIsDragging(true);
        (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    }, []);

    const handlePointerMove = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
        if (!isDraggingRef.current) return;
        const delta = e.clientX - startXRef.current;
        dragOffsetRef.current = delta;
        setDragOffset(delta);
    }, []);

    const finishDrag = useCallback(() => {
        if (!isDraggingRef.current) return;

        const trackWidth = trackRef.current?.clientWidth ?? 0;
        const threshold = trackWidth * SWIPE_THRESHOLD_RATIO;
        const offset = dragOffsetRef.current;

        if (offset < -threshold) {
            setActiveIndex((prev) => Math.min(prev + 1, testimonials.length - 1));
        } else if (offset > threshold) {
            setActiveIndex((prev) => Math.max(prev - 1, 0));
        }

        dragOffsetRef.current = 0;
        setDragOffset(0);
        isDraggingRef.current = false;
        setIsDragging(false);
    }, []);

    const handlePointerUp = useCallback(
        (e: React.PointerEvent<HTMLDivElement>) => {
            try {
                (e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId);
            } catch {
                // ignore — capture may have been released already
            }
            finishDrag();
        },
        [finishDrag]
    );

    // Compute transform: combine slide index (in %) with drag offset (in px)
    const transform = `translateX(calc(-${activeIndex * 100}% + ${dragOffset}px))`;

    return (
        <section className="relative z-40 bg-secondary text-background py-16 sm:py-20 md:py-24 lg:py-16 xl:py-20 2xl:py-24 overflow-hidden">
            <div className="px-6 sm:px-8 md:px-10 lg:px-12 xl:px-16 2xl:px-20">
                <div
                    ref={trackRef}
                    className="relative overflow-hidden touch-pan-y select-none cursor-grab active:cursor-grabbing"
                    onPointerDown={handlePointerDown}
                    onPointerMove={handlePointerMove}
                    onPointerUp={handlePointerUp}
                    onPointerCancel={handlePointerUp}
                >
                    <div
                        className={`flex ${isDragging ? "" : "transition-transform duration-700 ease-in-out"}`}
                        style={{ transform }}
                    >
                        {testimonials.map((t, i) => (
                            <div key={i} className="w-full flex-shrink-0">
                                {/* Stacked / float layout (< lg) */}
                                <div className="lg:hidden">
                                    <div className="float-left w-[140px] sm:w-[160px] md:w-[180px] mr-5 sm:mr-6 md:mr-7 mb-2">
                                        <div className="relative w-full aspect-[3/4] overflow-hidden pointer-events-none">
                                            <Image
                                                src={t.image}
                                                alt={t.name}
                                                fill
                                                className="object-cover"
                                                sizes="(max-width: 768px) 160px, 180px"
                                                priority={i === 0}
                                                draggable={false}
                                            />
                                        </div>
                                    </div>

                                    <h2 className="font-display font-thin tracking-[0] text-background text-[24px] sm:text-[28px] md:text-[36px] leading-[100%] pt-3 md:pt-4">
                                        {t.name}
                                    </h2>

                                    <p className="mt-7 sm:mt-8 md:mt-10 font-body font-medium text-background text-[14px] leading-[20px] sm:text-[15px] sm:leading-[22px] md:text-[17px] md:leading-[24px]">
                                        {t.subheader}
                                    </p>

                                    <p className="mt-4 sm:mt-5 md:mt-6 font-body font-light text-background text-[14px] leading-[22px] sm:text-[15px] sm:leading-[24px] md:text-[18px] md:leading-[28px]">
                                        {t.quote}
                                    </p>

                                    <div className="clear-both" />

                                    <Link
                                        href={t.href}
                                        className="group mt-8 sm:mt-9 md:mt-10 inline-flex items-center justify-center gap-3 border border-background px-6 py-3 md:px-7 md:py-3.5 transition-colors hover:bg-background"
                                    >
                                        <span className="font-body font-light italic tracking-[0.01em] text-background group-hover:text-secondary transition-colors text-[16px] sm:text-[17px] md:text-[19px]">
                                            {t.cta}
                                        </span>
                                        <span className="relative inline-block w-[18px] h-[18px] md:w-[19px] md:h-[19px]">
                                            <Image
                                                src="/icons/whitebuttonarrow.svg"
                                                alt=""
                                                fill
                                                className="object-contain transition-opacity group-hover:opacity-0"
                                            />
                                            <Image
                                                src="/icons/buttonarrow.svg"
                                                alt=""
                                                fill
                                                className="object-contain opacity-0 transition-opacity group-hover:opacity-100"
                                            />
                                        </span>
                                    </Link>
                                </div>

                                {/* Two-column layout (lg+) */}
                                <div className="hidden lg:grid grid-cols-2 gap-16 xl:gap-20 2xl:gap-24 items-stretch">
                                    <div className="relative w-full min-h-[560px] xl:min-h-[640px] 2xl:min-h-[720px] overflow-hidden pointer-events-none">
                                        <Image
                                            src={t.image}
                                            alt={t.name}
                                            fill
                                            className="object-cover"
                                            sizes="50vw"
                                            priority={i === 0}
                                            draggable={false}
                                        />
                                    </div>

                                    <div className="flex flex-col">
                                        <h2 className="font-display font-thin tracking-[0] text-background text-[44px] xl:text-[52px] 2xl:text-[60px] leading-[100%]">
                                            {t.name}
                                        </h2>

                                        <p className="mt-20 xl:mt-30 2xl:mt-30 font-body font-medium text-background text-[20px] leading-[25px] xl:text-[22px] xl:leading-[28px] 2xl:text-[24px] 2xl:leading-[30px]">
                                            {t.subheader}
                                        </p>

                                        <p className="mt-6 xl:mt-7 2xl:mt-8 font-body font-light text-background text-[20px] leading-[25px] xl:text-[22px] xl:leading-[32px] 2xl:text-[24px] 2xl:leading-[36px] max-w-[560px] xl:max-w-[640px] 2xl:max-w-[720px]">
                                            {t.quote}
                                        </p>

                                        <Link
                                            href={t.href}
                                            className="group mt-10 xl:mt-12 inline-flex items-center justify-center gap-3 self-start border border-background px-6 py-3 xl:px-7 xl:py-3.5 2xl:px-8 2xl:py-4 transition-colors hover:bg-background"
                                        >
                                            <span className="font-body font-light italic tracking-[0.01em] text-background group-hover:text-secondary transition-colors text-[20px] leading-[35px] xl:text-[22px] 2xl:text-[24px]">
                                                {t.cta}
                                            </span>
                                            <span className="relative inline-block w-[20px] h-[20px] xl:w-[22px] xl:h-[22px] 2xl:w-[24px] 2xl:h-[24px]">
                                                <Image
                                                    src="/icons/whitebuttonarrow.svg"
                                                    alt=""
                                                    fill
                                                    className="object-contain transition-opacity group-hover:opacity-0"
                                                />
                                                <Image
                                                    src="/icons/buttonarrow.svg"
                                                    alt=""
                                                    fill
                                                    className="object-contain opacity-0 transition-opacity group-hover:opacity-100"
                                                />
                                            </span>
                                        </Link>
                                    </div>
                                </div>
                            </div>
                        ))}
                    </div>
                </div>

                {/* Pagination dots */}
                <div className="flex items-center justify-center gap-3 mt-6 sm:mt-8 md:mt-10 lg:mt-12 xl:mt-14">
                    {testimonials.map((_, i) => (
                        <button
                            key={i}
                            onClick={() => setActiveIndex(i)}
                            aria-label={`Go to testimonial ${i + 1}`}
                            className={`h-2.5 w-2.5 md:h-3 md:w-3 xl:h-3.5 xl:w-3.5 rounded-full border border-background transition-colors ${i === activeIndex ? "bg-background" : "bg-[#C4C4C480]"
                                }`}
                        />
                    ))}
                </div>
            </div>
        </section>
    );
}