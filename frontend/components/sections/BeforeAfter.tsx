import Image from "next/image";
import Link from "next/link";

export default function BeforeAfter() {
  return (
    <section className="bg-background text-primary">
      <div className="flex flex-col items-center px-6 py-20 sm:px-8 sm:py-24 md:px-10 md:py-28 lg:px-12 lg:py-32 xl:px-16 xl:py-36 2xl:px-20 2xl:py-40">
        <p className="font-display font-thin text-center tracking-[0] text-primary text-[22px] leading-[37px] sm:text-[26px] sm:leading-[42px] md:text-[32px] md:leading-[48px] lg:text-[40px] lg:leading-[55px] xl:text-[46px] xl:leading-[62px] 2xl:text-[52px] 2xl:leading-[70px] max-w-[1000px] xl:max-w-[1200px] 2xl:max-w-[1400px]">
          Our work goes beyond planning — it&apos;s about reimagining spaces with intention and care. Explore how thoughtful design transforms familiar settings into meaningful, elevated celebrations. These before-and-after moments reveal how vision and design can completely transform a space.
        </p>

        <Link
          href="/before-and-after"
          className="group mt-12 sm:mt-14 md:mt-16 lg:mt-16 xl:mt-20 inline-flex items-center justify-center gap-3 border border-primary px-6 py-3 md:px-7 md:py-3.5 xl:px-8 xl:py-4 transition-colors hover:bg-primary hover:text-background"
        >
          <span className="font-body font-light italic tracking-[0.01em] text-[18px] sm:text-[19px] md:text-[20px] lg:text-[20px] xl:text-[22px] 2xl:text-[24px] leading-[35px]">
            View Our Before &amp; After
          </span>
          <Image
            src="/icons/buttonarrow.svg"
            alt=""
            width={24}
            height={24}
            className="w-[20px] h-[20px] xl:w-[22px] xl:h-[22px] 2xl:w-[24px] 2xl:h-[24px] transition-transform group-hover:translate-x-1 group-hover:[filter:invert(1)]"
          />
        </Link>
      </div>
    </section>
  );
}