"use client";

import { useState, useEffect } from "react";
import Image from "next/image";
import Link from "next/link";

const introImages = [
  "/images/portfoliopage/portfoliotwo.jpg",
  "/images/intro/introtwo.jpg",
  "/images/intro/introfour.jpg",
  "/images/intro/introfives.jpg",
];

export default function Intro() {
  const [activeIndex, setActiveIndex] = useState(0);

  useEffect(() => {
    const interval = setInterval(() => {
      setActiveIndex((prev) => (prev + 1) % introImages.length);
    }, 4000);
    return () => clearInterval(interval);
  }, []);

  return (
    <section className="bg-background text-primary">
      <div className="px-6 py-10 sm:px-8 sm:py-12 md:px-10 md:py-14 lg:px-12 lg:py-16 xl:px-16 xl:py-20 2xl:px-20 2xl:py-24">
        <div className="grid grid-cols-1 lg:grid-cols-[1.3fr_1fr] gap-8 lg:gap-6 xl:gap-8 2xl:gap-10 items-stretch">
          {/* Left column */}
          <div className="flex flex-col">
            {/* Logo — scales across breakpoints */}
            <div className="mb-4 sm:mb-5 md:mb-6 lg:mb-6">
              <Image
                src="/brand/bluelogos.svg"
                alt=""
                width={220}
                height={220}
                className="w-[110px] sm:w-[130px] md:w-[160px] lg:w-[180px] xl:w-[200px] 2xl:w-[220px] h-auto"
              />
            </div>

            <h2 className="font-display font-thin tracking-[0.03em] text-primary text-[28px] leading-[34px] sm:text-[32px] sm:leading-[40px] md:text-[40px] md:leading-[48px] lg:text-[45px] lg:leading-[50px] xl:text-[52px] xl:leading-[58px] 2xl:text-[60px] 2xl:leading-[68px] lg:max-w-[560px] xl:max-w-[620px] 2xl:max-w-[700px]">
              Behind every seamless celebration is a team defined by care, professionalism, and genuine connection.
            </h2>

            <div className="mt-8 sm:mt-10 lg:mt-10 space-y-5 sm:space-y-6 lg:max-w-[520px] xl:max-w-[580px] 2xl:max-w-[640px]">
              <p className="font-body font-extralight tracking-[0.03em] text-primary text-[15px] leading-[26px] sm:text-[16px] sm:leading-[28px] md:text-[18px] md:leading-[29px] lg:text-[19px] lg:leading-[30px] xl:text-[20px] xl:leading-[32px] 2xl:text-[22px] 2xl:leading-[34px]">
                Hephzibah Luxe is a design-led wedding and event planning studio guided by intention, care, and refined execution. Our work is shaped by years of experience, a deep respect for meaningful moments, and the belief that celebrations should feel personal, considered, and beautifully brought to life.
              </p>
              <p className="font-body font-extralight tracking-[0.03em] text-primary text-[15px] leading-[26px] sm:text-[16px] sm:leading-[28px] md:text-[18px] md:leading-[29px] lg:text-[19px] lg:leading-[30px] xl:text-[20px] xl:leading-[32px] 2xl:text-[22px] 2xl:leading-[34px]">
                From intimate gatherings to milestone celebrations, we approach every event with presence, creativity, and thoughtful collaboration — curating experiences that feel effortless, warm, and memorable.
              </p>
              <p className="font-body font-extralight italic tracking-[0.03em] text-primary text-[15px] leading-[26px] sm:text-[16px] sm:leading-[28px] md:text-[18px] md:leading-[29px] lg:text-[19px] lg:leading-[30px] xl:text-[20px] xl:leading-[32px] 2xl:text-[22px] 2xl:leading-[34px]">
                Our story is best discovered through the journey that shaped our work.
              </p>
            </div>

            <Link
              href="/about"
              className="group mt-10 sm:mt-12 lg:mt-12 inline-flex items-center justify-center gap-3 self-start border border-primary px-6 py-3 lg:px-7 lg:py-3.5 xl:px-8 xl:py-4 transition-colors hover:bg-primary hover:text-background"
            >
              <span className="font-body font-light italic tracking-[0.01em] text-[18px] sm:text-[19px] md:text-[20px] lg:text-[20px] xl:text-[22px] 2xl:text-[24px] leading-[35px]">
                About The Brand
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

          {/* Right column: image carousel */}
          <div className="relative w-full aspect-[4/5] sm:aspect-[16/10] md:aspect-[3/2] lg:aspect-auto lg:h-auto lg:self-stretch lg:mt-[112px] xl:mt-[120px] 2xl:mt-[140px] overflow-hidden">
            {introImages.map((src, i) => (
              <Image
                key={src}
                src={src}
                alt="Hephzibah Luxe event"
                fill
                priority={i === 0}
                className={`object-cover transition-opacity duration-1000 ease-in-out ${
                  i === activeIndex ? "opacity-100" : "opacity-0"
                }`}
                sizes="(max-width: 1024px) 100vw, 45vw"
              />
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}