"use client";

import Image from "next/image";
import Link from "next/link";

export default function ClientPhilosophy() {
    return (
        <section className="relative bg-secondary text-background py-16 sm:py-20 md:py-22 lg:py-24 xl:py-26 2xl:py-40 overflow-hidden">
            {/* Top-left edge image */}
            <div className="absolute -left-30 top-10 w-[160px] h-[180px] sm:-left-28 sm:top-12 sm:w-[180px] sm:h-[220px] md:-left-12 md:top-16 md:w-[220px] md:h-[280px] lg:left-0 lg:top-16 lg:w-[130px] lg:h-[320px] xl:w-[180px] xl:h-[420px] 2xl:w-[220px] 2xl:h-[500px]">
                <Image
                    src="/images/portfoliopage/portfoliophilosophy.jpg"
                    alt=""
                    fill
                    className="object-cover"
                    sizes="220px"
                    priority
                />
            </div>

            {/* Bottom-right edge image */}
            <div className="absolute -right-30 bottom-10 w-[160px] h-[200px] sm:-right-28 sm:bottom-12 sm:w-[180px] sm:h-[240px] md:-right-12 md:bottom-16 md:w-[220px] md:h-[300px] lg:right-0 lg:bottom-16 lg:w-[130px] lg:h-[320px] xl:w-[180px] xl:h-[420px] 2xl:w-[220px] 2xl:h-[500px]">
                <Image
                    src="/images/portfoliopage/portfoliophilosophytwo.jpg"
                    alt=""
                    fill
                    className="object-cover"
                    sizes="220px"
                />
            </div>

            {/* Content */}
            <div className="relative z-10 max-w-7xl mx-auto px-6 sm:px-8 md:px-12 lg:pl-[180px] lg:pr-[60px] xl:pl-[110px] xl:pr-[130px] 2xl:pl-[70px] 2xl:pr-[70px]">
                <div className="pl-10 pr-4 sm:pl-16 sm:pr-16 md:pl-24 md:pr-10 lg:pl-0 lg:pr-0">
                    {/* Heading */}
                    <h2 className="font-display font-thin text-background text-[26px] leading-[36px] sm:text-[30px] sm:leading-[42px] md:text-[36px] md:leading-[48px] lg:text-[46px] lg:leading-[58px] xl:text-[54px] xl:leading-[68px] 2xl:text-[62px] 2xl:leading-[76px] mb-6 sm:mb-8 lg:mb-8 xl:mb-8 2xl:mb-10">
                        No two events are the same — each unfolds into something beautifully personal
                    </h2>

                    {/* Body */}
                    <p className="font-body font-light text-background text-[16px] leading-[28px] sm:text-[18px] sm:leading-[30px] md:text-[19px] md:leading-[32px] lg:text-[20px] lg:leading-[34px] xl:text-[22px] xl:leading-[36px] 2xl:text-[24px] 2xl:leading-[40px] mb-6 sm:mb-8 lg:mb-8 xl:mb-10 2xl:mb-12">
                        Our clients are gracious, discerning, and appreciative of thoughtful design, creating the perfect canvas for us to do our inspired work. Their trust allows us to dream boldly and craft celebrations that feel personal, beautifully refined, and unforgettable.
                    </p>

                    {/* Attribution */}
                    <p className="font-sans font-light tracking-[0.2em] text-background text-[13px] leading-[24px] sm:text-[14px] sm:leading-[26px] md:text-[16px] md:leading-[28px] lg:text-[18px] lg:leading-[32px] xl:text-[20px] 2xl:text-[22px] mb-8 sm:mb-10 md:mb-12 lg:mb-14 xl:mb-16">
                        — WINIFRED OJULARI, CEO, HEPHZIBAH LUXE
                    </p>

                    {/* Bottom row — italic prompt on left, message link on right */}
                    <div className="flex flex-col lg:flex-row lg:items-center lg:justify-between gap-6 lg:gap-10">
                        <p className="font-body font-light italic text-background text-[18px] leading-[28px] sm:text-[20px] sm:leading-[30px] md:text-[22px] md:leading-[32px] lg:text-[24px] lg:leading-[36px] xl:text-[26px] xl:leading-[38px] 2xl:text-[28px] 2xl:leading-[40px]">
                            Ready to Begin? Let&apos;s Create Something Beautiful.
                        </p>

                        <Link
                            href="/inquiry"
                            className="group inline-flex items-center gap-6 lg:gap-8 border border-background px-6 lg:px-8 py-2.5 lg:py-3 self-start lg:self-auto flex-shrink-0 transition-colors hover:bg-background"
                        >
                            <span className="font-body font-light italic text-background group-hover:text-secondary transition-colors text-[18px] leading-[28px] sm:text-[20px] sm:leading-[30px] md:text-[22px] md:leading-[32px] lg:text-[24px] lg:leading-[36px] xl:text-[26px] xl:leading-[38px] 2xl:text-[28px] 2xl:leading-[40px]">
                                Share Your Vision
                            </span>
                            <span className="relative inline-block w-[20px] h-[20px] lg:w-[22px] lg:h-[22px] xl:w-[24px] xl:h-[24px] 2xl:w-[26px] 2xl:h-[26px] flex-shrink-0">
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
        </section>
    );
}