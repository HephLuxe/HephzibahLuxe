"use client"; // Error boundaries must be Client Components

import { useEffect } from "react";
import Image from "next/image";
import Link from "next/link";

export default function PortfolioError({
  error,
  unstable_retry,
}: {
  error: Error & { digest?: string };
  unstable_retry: () => void;
}) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <main>
      <section className="bg-background text-primary">
        <div className="flex flex-col items-center text-center px-6 py-20 sm:py-24 md:py-28 lg:py-32 xl:py-36 mx-auto">
          <h1 className="font-display font-thin tracking-[0.01em] text-primary text-[28px] leading-[120%] sm:text-[32px] md:text-[36px] md:leading-[115%] lg:text-[42px] lg:leading-[110%] xl:text-[48px] 2xl:text-[54px]">
            Our Portfolio Is Taking a Moment
          </h1>
          <p className="mt-6 sm:mt-7 md:mt-8 font-body font-light italic text-primary text-[15px] leading-[26px] sm:text-[16px] sm:leading-[28px] md:text-[18px] md:leading-[30px] lg:text-[17px] lg:leading-[28px] xl:text-[19px] xl:leading-[32px] 2xl:text-[21px] 2xl:leading-[34px] max-w-[560px] xl:max-w-[640px] 2xl:max-w-[720px]">
            We couldn&apos;t load our past celebrations just now. Please try again in a moment.
          </p>

          <div className="mt-10 sm:mt-11 md:mt-12 xl:mt-14 flex flex-col sm:flex-row items-stretch sm:items-center justify-center gap-4 sm:gap-5">
            <button
              type="button"
              onClick={() => unstable_retry()}
              className="group inline-flex items-center justify-center gap-4 border border-primary bg-primary px-6 py-3 md:px-7 md:py-3.5 xl:px-8 xl:py-4 transition-colors hover:bg-background"
            >
              <span className="font-body font-light italic text-background group-hover:text-primary transition-colors text-[16px] leading-[26px] sm:text-[17px] md:text-[18px] xl:text-[20px] 2xl:text-[22px]">
                Try Again
              </span>
            </button>

            <Link
              href="/"
              className="group inline-flex items-center justify-center gap-4 border border-primary px-6 py-3 md:px-7 md:py-3.5 xl:px-8 xl:py-4 transition-colors hover:bg-primary"
            >
              <span className="font-body font-light italic text-primary group-hover:text-background transition-colors text-[16px] leading-[26px] sm:text-[17px] md:text-[18px] xl:text-[20px] 2xl:text-[22px]">
                Back to Homepage
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
        </div>
      </section>
    </main>
  );
}
