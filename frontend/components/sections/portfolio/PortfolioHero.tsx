"use client";

import { useState, useEffect } from "react";
import Image from "next/image";

const portfolioImages = [
  "/images/portfoliopage/portfolioonee.jpg",
  "/images/portfoliopage/portfoliotwo.jpg",
  "/images/intro/introfives.jpg",
  "/images/portfoliopage/newportfoliofourr.jpg",
  "/images/portfoliopage/portfoliofive.jpg",
  "/images/portfoliopage/portfoliosix.jpg",
  "/images/portfoliopage/newportfoliotwo.jpg",
  "/images/portfoliopage/newportfolioone.jpg",
  "/images/portfoliopage/newportfoliothree.jpg",
];

export default function PortfolioHero() {
  const [activeIndex, setActiveIndex] = useState(0);

  useEffect(() => {
    const interval = setInterval(() => {
      setActiveIndex((prev) => (prev + 1) % portfolioImages.length);
    }, 7000);
    return () => clearInterval(interval);
  }, []);

  return (
    <section className="relative w-full bg-primary overflow-hidden">
      <div className="relative z-10 pt-20 sm:pt-20 md:pt-18 lg:pt-16 xl:pt-20 2xl:pt-24 pb-20 sm:pb-24 md:pb-28 lg:pb-32 xl:pb-36 2xl:pb-40 flex flex-col items-center">
        <h1 className="font-display font-thin text-background text-center relative z-20 leading-[100%] tracking-[0.03em] text-[72px] sm:text-[92px] md:text-[116px] lg:text-[150px] xl:text-[172px] 2xl:text-[196px]">
          PORTFOLIO
        </h1>

        {/* Image overlaps the heading by ~50% of its height */}
        <div className="relative -mt-[36px] sm:-mt-[46px] md:-mt-[58px] lg:-mt-[72px] xl:-mt-[86px] 2xl:-mt-[98px] w-[300px] h-[330px] sm:w-[340px] sm:h-[370px] md:w-[420px] md:h-[460px] lg:w-[500px] lg:h-[550px] xl:w-[580px] xl:h-[640px] 2xl:w-[660px] 2xl:h-[720px] overflow-hidden z-10">
          {portfolioImages.map((src, i) => (
            <Image
              key={src}
              src={src}
              alt=""
              fill
              priority={i === 0}
              className={`object-cover transition-opacity duration-1000 ease-in-out ${
                i === activeIndex ? "opacity-100" : "opacity-0"
              }`}
              sizes="(max-width: 640px) 300px, (max-width: 768px) 340px, (max-width: 1024px) 420px, (max-width: 1280px) 500px, (max-width: 1536px) 580px, 660px"
            />
          ))}
        </div>
      </div>
    </section>
  );
}