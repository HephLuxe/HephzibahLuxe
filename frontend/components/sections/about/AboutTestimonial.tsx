"use client";

import Image from "next/image";
import Link from "next/link";

export default function AboutTestimonial() {
    return (
        <section className="relative bg-secondary text-background py-16 sm:py-20 md:py-24 lg:py-32 xl:py-36 2xl:py-40 overflow-hidden">
            {/* Top-left edge image */}
            <div className="absolute -left-30 top-10 w-[160px] h-[180px] sm:-left-28 sm:top-12 sm:w-[180px] sm:h-[220px] md:-left-12 md:top-16 md:w-[220px] md:h-[280px] lg:left-0 lg:top-16 lg:w-[130px] lg:h-[320px] xl:w-[180px] xl:h-[420px] 2xl:w-[220px] 2xl:h-[500px]">
                <Image
                    src="/images/aboutpage/aboutfours.jpg"
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
                    src="/images/aboutpage/aboutfives.jpg"
                    alt=""
                    fill
                    className="object-cover"
                    sizes="220px"
                />
            </div>

            {/* Content */}
            <div className="relative z-10 max-w-7xl mx-auto px-6 sm:px-8 md:px-12 lg:pl-[180px] lg:pr-[60px] xl:pl-[110px] xl:pr-[130px] 2xl:pl-[70px] 2xl:pr-[70px]">
                <div className="pl-10 pr-4 sm:pl-16 sm:pr-16 md:pl-24 md:pr-10 lg:pl-0 lg:pr-0">
                    <p className="font-display font-thin text-background text-[24px] leading-[38px] sm:text-[28px] sm:leading-[44px] md:text-[34px] md:leading-[50px] lg:text-[45px] lg:leading-[55px] xl:text-[52px] xl:leading-[64px] 2xl:text-[60px] 2xl:leading-[72px] mb-8 sm:mb-10 lg:mb-10 xl:mb-12 2xl:mb-14">
                        &ldquo;This moment meant so much more to me than I can fully express. To be surrounded by people who have walked this journey with me, lifting me up in prayer and sharing in this time of thanksgiving… it was truly special. It reminded me of how blessed I am, not just for the years, but for the love, the support, and the grace that has carried me through them.&rdquo;
                    </p>

                    <p className="font-sans font-light tracking-[0.2em] text-background text-[13px] leading-[24px] sm:text-[14px] sm:leading-[26px] md:text-[16px] md:leading-[28px] lg:text-[20px] lg:leading-[35px] xl:text-[22px] 2xl:text-[24px] mb-8 sm:mb-10 md:mb-12 lg:mb-14 xl:mb-16">
                        — WINNIE, THE CELEBRANT
                    </p>

                    <div className="flex flex-col lg:flex-row gap-4 lg:gap-6 items-start">
                        <Link
                            href="/portfolio"
                            onClick={(e) => {
                                if (window.location.pathname === "/portfolio") {
                                    e.preventDefault();
                                    window.scrollTo({ top: 0, behavior: "smooth" });
                                }
                            }}
                            className="group inline-flex items-center gap-6 lg:gap-8 border border-background px-6 lg:px-8 py-2.5 lg:py-3 transition-colors hover:bg-background"
                        >
                            <span className="font-body font-light italic text-background group-hover:text-secondary transition-colors text-[16px] leading-[28px] md:text-[18px] md:leading-[30px] lg:text-[20px] lg:leading-[35px] xl:text-[22px] 2xl:text-[24px]">
                                View Our Portfolio
                            </span>
                            <span className="relative inline-block w-[18px] h-[18px] lg:w-[20px] lg:h-[20px] xl:w-[22px] xl:h-[22px] 2xl:w-[24px] 2xl:h-[24px] flex-shrink-0">
                                <Image src="/icons/whitebuttonarrow.svg" alt="" fill className="object-contain transition-opacity group-hover:opacity-0" />
                                <Image src="/icons/buttonarrow.svg" alt="" fill className="object-contain opacity-0 transition-opacity group-hover:opacity-100" />
                            </span>
                        </Link>

                        <Link
                            href="/services"
                            className="group inline-flex items-center gap-6 lg:gap-8 border border-background px-6 lg:px-8 py-2.5 lg:py-3 transition-colors hover:bg-background"
                        >
                            <span className="font-body font-light italic text-background group-hover:text-secondary transition-colors text-[16px] leading-[28px] md:text-[18px] md:leading-[30px] lg:text-[20px] lg:leading-[35px] xl:text-[22px] 2xl:text-[24px]">
                                Explore Our Services
                            </span>
                            <span className="relative inline-block w-[18px] h-[18px] lg:w-[20px] lg:h-[20px] xl:w-[22px] xl:h-[22px] 2xl:w-[24px] 2xl:h-[24px] flex-shrink-0">
                                <Image src="/icons/whitebuttonarrow.svg" alt="" fill className="object-contain transition-opacity group-hover:opacity-0" />
                                <Image src="/icons/buttonarrow.svg" alt="" fill className="object-contain opacity-0 transition-opacity group-hover:opacity-100" />
                            </span>
                        </Link>
                    </div>
                </div>
            </div>
        </section>
    );
}