import Image from "next/image";
import Link from "next/link";

const portfolioImages = [
  "/images/portfoliopage/portfoliosix.jpg",
  "/images/hero/herofours.jpg",
  "/images/portfoliopage/portfoliofive.jpg",
  "/images/portfoliopage/portfoliotwo.jpg",
  "/images/portfoliopage/portfolioeight.jpg",
  "/images/portfoliopage/portfolionine.jpg",

];

export default function Portfolio() {
  const loopedImages = [...portfolioImages, ...portfolioImages];

  return (
    <section className="relative overflow-hidden">
      {/* Two-tone background */}
      <div className="absolute inset-0 flex flex-col">
        <div className="bg-background h-[180px] sm:h-[190px] md:h-[200px] lg:h-[200px] xl:h-[220px] 2xl:h-[240px] flex-shrink-0" />
        <div className="bg-secondary flex-1" />
      </div>

      <div className="relative grid grid-cols-[1.6fr_1fr] lg:grid-cols-[1fr_2fr] items-start">
        {/* Left column */}
        <div className="flex flex-col">
          {/* Tagline (white area) */}
          <div className="px-6 pt-8 pb-6 sm:px-7 sm:pt-12 sm:pb-12 md:px-8 md:pt-10 md:pb-8 lg:px-12 lg:pt-10 lg:pb-10 xl:px-14 xl:pt-12 xl:pb-8 2xl:px-16 2xl:pt-14 2xl:pb-14">
            <p className="font-body font-extralight italic text-primary tracking-[0.01em] text-[16px] leading-[26px] sm:text-[20px] sm:leading-[28px] md:text-[24px] md:leading-[32px] lg:text-[24px] lg:leading-[40px] xl:text-[24px] xl:leading-[44px] 2xl:text-[36px] 2xl:leading-[48px]">
              Designed with care, guided by experience, and brought together with presence
            </p>
          </div>

          {/* Heading + paragraph + button (dark area) */}
          <div className="flex flex-col px-6 pt-16 pb-12 sm:px-7 sm:pt-16 sm:pb-14 md:px-8 md:pt-20 md:pb-20 lg:px-12 lg:pt-20 lg:pb-28 xl:px-14 xl:pt-24 xl:pb-40 2xl:px-16 2xl:pt-28 2xl:pb-48">
            <h3 className="font-display font-thin tracking-[0.03em] text-background text-[32px] leading-[38px] sm:text-[36px] sm:leading-[42px] md:text-[40px] md:leading-[48px] lg:text-[45px] lg:leading-[50px] xl:text-[52px] xl:leading-[58px] 2xl:text-[60px] 2xl:leading-[68px]">
              Our <em className="italic">Portfolio</em>
            </h3>

            <p className="mt-6 sm:mt-7 md:mt-8 font-body font-extralight tracking-[0.03em] text-background text-[14px] leading-[24px] sm:text-[16px] sm:leading-[26px] md:text-[18px] md:leading-[28px] lg:text-[18px] lg:leading-[30px] xl:text-[18px] xl:leading-[32px] 2xl:text-[22px] 2xl:leading-[34px] max-w-[400px] xl:max-w-[460px] 2xl:max-w-[520px]">
              Explore the moments we&apos;ve brought to life through intentional planning and thoughtful design. Each celebration reflects a story of care, collaboration, and craftsmanship.
            </p>

            <Link
              href="/portfolio"
              className="group mt-8 sm:mt-9 md:mt-10 lg:mt-10 xl:mt-12 inline-flex items-center justify-center gap-2 sm:gap-2.5 md:gap-3 self-start border border-background px-4 py-2.5 sm:px-5 sm:py-2.5 md:px-6 md:py-3 lg:px-6 lg:py-3 xl:px-7 xl:py-3.5 2xl:px-8 2xl:py-4 transition-colors hover:bg-background"
            >
              <span className="font-body font-light italic tracking-[0.01em] text-background group-hover:text-secondary transition-colors text-[14px] leading-[22px] sm:text-[15px] sm:leading-[24px] md:text-[17px] md:leading-[28px] lg:text-[20px] lg:leading-[35px] xl:text-[22px] 2xl:text-[24px]">
                View Our Past Projects
              </span>
              <span className="relative inline-block w-[16px] h-[16px] sm:w-[17px] sm:h-[17px] md:w-[18px] md:h-[18px] lg:w-[20px] lg:h-[20px] xl:w-[22px] xl:h-[22px] 2xl:w-[24px] 2xl:h-[24px]">
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

        {/* Right column: carousel */}
        <div className="relative overflow-hidden bg-background h-[400px] sm:h-[440px] md:h-[480px] lg:h-[520px] xl:h-[580px] 2xl:h-[640px] mt-12 sm:mt-16 md:mt-20 lg:mt-[100px] xl:mt-[120px] 2xl:mt-[140px] mb-8 sm:mb-10 md:mb-12 lg:mb-16 xl:mb-20 2xl:mb-24">
          <div className="absolute inset-0 flex gap-2 animate-scroll-x">
            {loopedImages.map((src, i) => (
              <div
                key={i}
                className="relative flex-shrink-0 w-[200px] lg:w-[360px] h-full"
              >
                <Image
                  src={src}
                  alt=""
                  fill
                  className="object-cover"
                  sizes="(max-width: 1024px) 200px, 360px"
                />
              </div>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}