"use client";

import { useState } from "react";
import Image from "next/image";
import Link from "next/link";
import { portfolioEvents, type EventCategory, type PortfolioEvent } from "@/data/portfolio";

type Filter = "All Events" | EventCategory;

const filters: Filter[] = ["All Events", "Weddings", "Birthdays", "Corporate", "Social Events"];

export default function PortfolioEvents() {
  const [activeFilter, setActiveFilter] = useState<Filter>("All Events");

  const visibleEvents =
    activeFilter === "All Events"
      ? portfolioEvents
      : portfolioEvents.filter((e) => e.category === activeFilter);

  // Desktop row pattern: 3, 2, 3, 2…
  const rows: PortfolioEvent[][] = [];
  let i = 0;
  let pattern = 3;
  while (i < visibleEvents.length) {
    rows.push(visibleEvents.slice(i, i + pattern));
    i += pattern;
    pattern = pattern === 3 ? 2 : 3;
  }

  return (
    <section className="bg-background text-primary">
      <div className="px-4 sm:px-6 md:px-8 lg:px-12 xl:px-16 2xl:px-20 py-8 sm:py-10 md:py-12 lg:py-12 xl:py-14 2xl:py-16">
        {/* Filter tabs */}
        <div className="flex items-center justify-center flex-nowrap gap-x-4 sm:gap-x-6 md:gap-x-8 lg:gap-x-12 xl:gap-x-14 2xl:gap-x-16 mb-8 sm:mb-10 md:mb-10 lg:mb-12 xl:mb-12 overflow-x-auto scrollbar-hide">
          {filters.map((filter) => {
            const isActive = activeFilter === filter;
            return (
              <button
                key={filter}
                type="button"
                onClick={() => setActiveFilter(filter)}
                className={`font-sans font-light leading-[184%] tracking-[-0.03em] text-center whitespace-nowrap pb-1 transition-colors text-[13px] sm:text-[14px] md:text-[18px] lg:text-[20px] xl:text-[22px] 2xl:text-[24px] ${isActive
                    ? "text-primary border-b-2 border-primary"
                    : "text-primary/70 hover:text-primary"
                  }`}
              >
                {filter}
              </button>
            );
          })}
        </div>

        {/* Empty state */}
        {visibleEvents.length === 0 ? (
          <div className="flex flex-col items-center text-center py-12 sm:py-16 md:py-18 lg:py-20 xl:py-24 mx-auto">
            <h3 className="font-display font-thin tracking-[0.01em] text-primary lg:whitespace-nowrap text-[28px] leading-[120%] sm:text-[32px] sm:leading-[120%] md:text-[36px] md:leading-[115%] lg:text-[42px] lg:leading-[110%] xl:text-[48px] xl:leading-[110%] 2xl:text-[54px] 2xl:leading-[110%]">
              Your Celebration Could Be the Next Story We Tell
            </h3>
            <p className="mt-6 sm:mt-7 md:mt-8 font-body font-light italic text-primary text-[15px] leading-[26px] sm:text-[16px] sm:leading-[28px] md:text-[18px] md:leading-[30px] lg:text-[17px] lg:leading-[28px] xl:text-[19px] xl:leading-[32px] 2xl:text-[21px] 2xl:leading-[34px] max-w-[560px] xl:max-w-[640px] 2xl:max-w-[720px]">
              We design {activeFilter === "Social Events" ? "social events" : activeFilter.toLowerCase()} {" "} with intention, warmth, and refined detail — always centred around you. Ready to Begin? Let&apos;s create something amazing
            </p>

            <Link
              href="/inquiry"
              className="group mt-10 sm:mt-11 md:mt-12 xl:mt-14 inline-flex items-center gap-4 border border-primary px-6 py-3 md:px-7 md:py-3.5 xl:px-8 xl:py-4 transition-colors hover:bg-primary"
            >
              <span className="font-body font-light italic text-primary group-hover:text-background transition-colors text-[16px] leading-[26px] sm:text-[17px] md:text-[18px] lg:text-[18px] xl:text-[20px] 2xl:text-[22px]">
                Share Your Vision
              </span>
              <Image
                src="/icons/buttonarrow.svg"
                alt=""
                width={22}
                height={22}
                className="w-[18px] h-[18px] xl:w-[20px] xl:h-[20px] 2xl:w-[22px] 2xl:h-[22px] transition-[filter,transform] group-hover:[filter:invert(1)] group-hover:translate-x-1"
              />
            </Link>
          </div>
        ) : (
          <>
            {/* Mobile / landscape / iPad: 2-column grid */}
            <div className="lg:hidden grid grid-cols-2 gap-x-4 sm:gap-x-5 md:gap-x-6 gap-y-10 sm:gap-y-12 md:gap-y-14">
              {visibleEvents.map((event) => (
                <EventCard key={event.slug} event={event} />
              ))}
            </div>

            {/* Laptop+: 3-2-3-2 row pattern */}
            <div className="hidden lg:flex flex-col gap-y-14 xl:gap-y-16 2xl:gap-y-20">
              {rows.map((rowEvents, rowIdx) => {
                // 1 event OR 2 events → render inside a 3-col grid so cards stay at 3-col width
                if (rowEvents.length === 1 || rowEvents.length === 2) {
                  return (
                    <div
                      key={rowIdx}
                      className="grid grid-cols-3 gap-8 xl:gap-10 2xl:gap-12"
                    >
                      {rowEvents.map((event) => (
                        <EventCard key={event.slug} event={event} />
                      ))}
                    </div>
                  );
                }
                // 3 events → full 3-column row
                return (
                  <div
                    key={rowIdx}
                    className="grid grid-cols-3 gap-8 xl:gap-10 2xl:gap-12"
                  >
                    {rowEvents.map((event) => (
                      <EventCard key={event.slug} event={event} />
                    ))}
                  </div>
                );
              })}
            </div>
          </>
        )}
      </div>
    </section>
  );
}

function EventCard({ event, compact = false }: { event: PortfolioEvent; compact?: boolean }) {
  return (
    <Link href={`/portfolio/${event.slug}`} className="group block">
      <div
        className={`relative w-full overflow-hidden ${compact ? "aspect-[3/4] lg:aspect-[5/4]" : "aspect-[3/4]"
          }`}
      >
        <Image
          src={event.coverImage}
          alt={event.title}
          fill
          className="object-cover transition-transform duration-500 group-hover:scale-105"
          sizes="(max-width: 1024px) 50vw, 33vw"
        />
      </div>

      <p className="mt-4 sm:mt-5 md:mt-5 font-sans font-light leading-[184%] tracking-[-0.03em] uppercase text-primary text-[12px] sm:text-[13px] md:text-[14px] lg:text-[15px] xl:text-[16px] 2xl:text-[17px]">
        {event.location} — {event.year}
      </p>
      <h3 className="mt-1 sm:mt-1.5 font-display font-light tracking-[0.01em] text-primary text-[15px] leading-[24px] sm:text-[17px] sm:leading-[26px] md:text-[19px] md:leading-[28px] lg:text-[24px] lg:leading-[32px] xl:text-[28px] xl:leading-[36px] 2xl:text-[32px] 2xl:leading-[40px]">
        {event.title}
      </h3>
    </Link>
  );
}