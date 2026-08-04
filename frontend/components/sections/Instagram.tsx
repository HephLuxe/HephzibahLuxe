import Image from "next/image";
import Link from "next/link";

const instagramImages = [
  "/images/testimonial/testimonialone.jpg",
  "/images/insta/instagramtwo.jpg",
  "/images/insta/instagramthree.jpg",
  "/images/portfoliopage/portfolioeight.jpg",
];

export default function Instagram() {
  return (
    <section className="relative z-40 bg-background text-primary py-16 md:py-20 lg:py-20 xl:py-20 2xl:py-32">
      <div className="px-6 md:px-10 lg:px-12 xl:px-12 2xl:px-20">
        {/* Top row */}
        <div className="flex flex-col lg:flex-row lg:items-center lg:justify-between gap-6 mb-10 md:mb-14 lg:mb-16 xl:mb-20">
          <h2 className="font-display font-thin tracking-[0] text-primary text-[36px] leading-[44px] md:text-[44px] md:leading-[52px] lg:text-[55px] lg:leading-[60px] xl:text-[64px] xl:leading-[70px] 2xl:text-[72px] 2xl:leading-[80px] max-w-[500px] md:max-w-[640px] xl:max-w-[760px] 2xl:max-w-[860px]">
            Follow <em className="italic">Hephzibah Luxe</em>
            <span className="block pl-12 md:pl-16 lg:pl-20 xl:pl-24 2xl:pl-28">On Instagram</span>
          </h2>

          {/* Desktop button */}
          <Link
            href="https://www.instagram.com/hephzibahluxe"
            target="_blank"
            rel="noopener noreferrer"
            className="hidden lg:inline-flex group items-center justify-between gap-6 self-start border border-primary px-6 py-4 xl:px-7 xl:py-5 2xl:px-8 2xl:py-6 transition-colors hover:bg-primary hover:text-background min-w-[340px] xl:min-w-[380px] 2xl:min-w-[420px]"
          >
            <span className="font-sans tracking-[0.15em] uppercase text-primary group-hover:text-background transition-colors text-[14px] xl:text-[16px] 2xl:text-[18px]">
              Follow Hephzibah Luxe
            </span>
            <Image
              src="/icons/instagram.svg"
              alt=""
              width={26}
              height={26}
              className="w-[22px] h-[22px] xl:w-[24px] xl:h-[24px] 2xl:w-[26px] 2xl:h-[26px] group-hover:[filter:invert(1)] transition-[filter]"
            />
          </Link>
        </div>

        {/* Image grid */}
        <div className="grid grid-cols-4 gap-3 md:gap-4 lg:gap-6 xl:gap-7 2xl:gap-8">
          {instagramImages.map((src, i) => (
            <div key={i} className="relative aspect-square overflow-hidden">
              <Image
                src={src}
                alt=""
                fill
                className="object-cover transition-transform duration-500 hover:scale-105"
                sizes="(max-width: 1024px) 25vw, 20vw"
              />
            </div>
          ))}
        </div>

        {/* Mobile button */}
        <Link
          href="https://www.instagram.com/hephzibahluxe"
          target="_blank"
          rel="noopener noreferrer"
          className="lg:hidden mt-8 md:mt-10 group flex items-center justify-center gap-4 border border-primary px-6 py-4 md:px-7 md:py-5 transition-colors hover:bg-primary"
        >
          <span className="font-sans tracking-[0.15em] uppercase text-primary group-hover:text-background transition-colors text-[13px] md:text-[15px]">
            Follow Hephzibah Luxe
          </span>
          <Image
            src="/icons/instagram.svg"
            alt=""
            width={26}
            height={26}
            className="w-[22px] h-[22px] md:w-[24px] md:h-[24px] group-hover:[filter:invert(1)] transition-[filter]"
          />
        </Link>
      </div>
    </section>
  );
}