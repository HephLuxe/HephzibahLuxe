import Link from "next/link";
import { flattenGallery, type PortfolioEvent, type SubEvent } from "@/lib/portfolio";
import GalleryRowRenderer from "./GalleryRowRenderer";
import { GalleryProvider } from "./GalleryLightbox";

export default function SubEventDetail({ event, subEvent }: { event: PortfolioEvent; subEvent: SubEvent }) {
  const flatImages = flattenGallery(subEvent.gallery);

  return (
    <section className="bg-background text-primary">
      <div className="px-4 sm:px-6 md:px-8 lg:px-12 xl:px-16 2xl:px-20 py-10 sm:py-12 md:py-12 lg:py-14 xl:py-20 2xl:py-24">
        <Link
          href={`/portfolio/${event.slug}`}
          className="inline-flex items-center gap-2 font-sans font-light text-primary hover:opacity-70 transition-opacity text-[13px] sm:text-[13px] md:text-[14px] lg:text-[14px] xl:text-[15px] 2xl:text-[16px]"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
            <path d="M15 18l-6-6 6-6" />
          </svg>
          <span className="underline underline-offset-4">Back to the Event Overview</span>
        </Link>

        <p className="mt-10 sm:mt-11 md:mt-12 lg:mt-12 xl:mt-14 font-sans font-light leading-[184%] tracking-[-0.03em] uppercase text-primary text-[12px] sm:text-[13px] md:text-[14px] lg:text-[14px] xl:text-[15px] 2xl:text-[16px]">
          {subEvent.subtitle}
        </p>

        <h1 className="mt-2 font-display font-thin italic tracking-[0.02em] text-primary text-[26px] leading-[36px] sm:text-[24px] sm:leading-[40px] md:text-[26px] md:leading-[48px] lg:text-[28px] lg:leading-[52px] xl:text-[42px] xl:leading-[62px] 2xl:text-[60px] 2xl:leading-[70px]">
          {subEvent.title}
        </h1>

        {subEvent.description && (
          <div className="mt-4 sm:mt-4 md:mt-5 max-w-[1400px] space-y-3 sm:space-y-4">
            {subEvent.description.map((para, idx) => (
              <p
                key={idx}
                className="font-body font-light text-primary text-[15px] leading-[24px] sm:text-[15px] sm:leading-[26px] md:text-[18px] md:leading-[28px] lg:text-[15px] lg:leading-[25px] xl:text-[18px] xl:leading-[28px] 2xl:text-[20px] 2xl:leading-[30px]"
              >
                {para}
              </p>
            ))}
          </div>
        )}

        {subEvent.gallery && (
          <GalleryProvider images={flatImages}>
            <div className="mt-12 sm:mt-14 md:mt-14 lg:mt-16 xl:mt-20 flex flex-col gap-2 lg:gap-3">
              {subEvent.gallery.map((row, idx) => (
                <GalleryRowRenderer key={idx} row={row} />
              ))}
            </div>
          </GalleryProvider>
        )}
      </div>
    </section>
  );
}