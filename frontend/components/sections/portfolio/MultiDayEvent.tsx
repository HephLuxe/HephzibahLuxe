import Image from "next/image";
import Link from "next/link";
import { formatLocationYear, type PortfolioEvent } from "@/lib/portfolio";

export default function MultiDayEvent({ event }: { event: PortfolioEvent }) {
  return (
    <section className="bg-background text-primary">
      <div className="px-4 sm:px-6 md:px-8 lg:px-12 xl:px-16 2xl:px-20 py-10 sm:py-12 md:py-12 lg:py-14 xl:py-20 2xl:py-24">
        {/* Back link */}
        <Link
          href="/portfolio"
          className="inline-flex items-center gap-2 font-sans font-light text-primary hover:opacity-70 transition-opacity text-[13px] sm:text-[13px] md:text-[14px] lg:text-[14px] xl:text-[15px] 2xl:text-[16px]"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
            <path d="M15 18l-6-6 6-6" />
          </svg>
          <span className="underline underline-offset-4">View all Events</span>
        </Link>

        {/* Location + year */}
        <p className="mt-10 sm:mt-11 md:mt-12 lg:mt-12 xl:mt-14 font-sans font-light leading-[184%] tracking-[-0.03em] uppercase text-primary text-[12px] sm:text-[13px] md:text-[14px] lg:text-[14px] xl:text-[15px] 2xl:text-[16px]">
          {formatLocationYear(event)}
        </p>

        {/* Title */}
        <h1 className="mt-2 font-display font-thin italic tracking-[0.02em] text-primary text-[26px] leading-[36px] sm:text-[24px] sm:leading-[40px] md:text-[26px] md:leading-[48px] lg:text-[28px] lg:leading-[52px] xl:text-[42px] xl:leading-[62px] 2xl:text-[60px] 2xl:leading-[70px]">
          {event.title}
        </h1>

        {/* Description paragraphs */}
        {event.description && (
          <div className="mt-4 sm:mt-4 md:mt-5 max-w-[1450px] space-y-3 sm:space-y-4">
            {event.description.map((para, idx) => (
              <p
                key={idx}
                className="font-body font-light text-primary text-[15px] leading-[24px] sm:text-[15px] sm:leading-[26px] md:text-[16px] md:leading-[28px] lg:text-[15px] lg:leading-[25px] xl:text-[18px] xl:leading-[28px] 2xl:text-[18px] 2xl:leading-[30px]"
              >
                {para}
              </p>
            ))}
          </div>
        )}

        {/* Sub-events grid — 2 cols mobile/landscape/iPad, 3 cols laptop+ */}
        {event.subEvents && event.subEvents.length > 0 && (
          <div className="mt-14 sm:mt-16 md:mt-16 lg:mt-20 xl:mt-24 grid grid-cols-2 md:grid-cols-3 gap-x-4 sm:gap-x-5 md:gap-x-6 lg:gap-x-8 xl:gap-x-10 2xl:gap-x-12 gap-y-12 sm:gap-y-14 md:gap-y-14 lg:gap-y-16 xl:gap-y-20">
            {event.subEvents.map((sub) => (
              <Link
                key={sub.slug}
                href={`/portfolio/${event.slug}/${sub.slug}`}
                className="group block"
              >
                <div className="relative w-full aspect-[3/4] overflow-hidden">
                  {sub.image && (
                    <Image
                      src={sub.image}
                      alt={sub.title}
                      fill
                      className="object-cover transition-transform duration-500 group-hover:scale-105"
                      sizes="(max-width: 1024px) 50vw, 33vw"
                    />
                  )}
                </div>

                <p className="mt-5 font-sans font-light leading-[184%] tracking-[-0.03em] uppercase text-primary text-[13px] sm:text-[14px] md:text-[14px] lg:text-[15px] xl:text-[16px] 2xl:text-[17px]">
                  {sub.subtitle}
                </p>
                <h3 className="mt-1 font-display font-light tracking-[0.01em] text-primary text-[15px] leading-[28px] sm:text-[17px] sm:leading-[30px] md:text-[20px] md:leading-[30px] lg:text-[24px] lg:leading-[32px] xl:text-[28px] xl:leading-[36px] 2xl:text-[32px] 2xl:leading-[40px]">
                  {sub.title}
                </h3>
              </Link>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}