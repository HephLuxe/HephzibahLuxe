import Image from "next/image";
import Link from "next/link";

export default function ReadyToBegin() {
  return (
    <section className="relative z-40 bg-background text-primary">
      <div className="flex flex-col items-center text-center px-6 py-20 sm:px-8 sm:py-24 md:px-10 md:py-28 lg:px-12 lg:py-32 xl:px-16 xl:py-36 2xl:px-20 2xl:py-40">
        <h2 className="font-display font-thin tracking-[-0.01em] text-primary text-[30px] leading-[100%] sm:text-[34px] sm:leading-[110%] md:text-[40px] md:leading-[110%] lg:text-[45px] lg:leading-[100%] xl:text-[54px] xl:leading-[110%] 2xl:text-[62px] 2xl:leading-[110%] max-w-[1000px] xl:max-w-[1180px] 2xl:max-w-[1320px]">
          Ready to Begin Your Celebration with Intention?
        </h2>

        <p className="mt-8 sm:mt-10 md:mt-10 lg:mt-10 xl:mt-12 2xl:mt-14 font-body font-light tracking-[0] text-primary text-[15px] leading-[24px] sm:text-[16px] sm:leading-[26px] md:text-[18px] md:leading-[28px] lg:text-[18px] lg:leading-[29px] xl:text-[20px] xl:leading-[32px] 2xl:text-[22px] 2xl:leading-[34px] max-w-[860px] xl:max-w-[1000px] 2xl:max-w-[1120px]">
          Every celebration we plan is thoughtfully tailored to reflect your vision, priorities, and scale. With care, clarity, and flexibility, we guide you through each stage of the planning journey, creating an experience that feels seamless from beginning to end. If you&apos;re ready to begin the conversation, we&apos;d love to hear from you.
        </p>

        <Link
          href="/inquiry"
          className="group mt-10 sm:mt-11 md:mt-12 lg:mt-12 xl:mt-14 inline-flex items-center justify-center gap-3 border border-primary px-6 py-3 md:px-7 md:py-3.5 xl:px-8 xl:py-4 transition-colors hover:bg-primary hover:text-background"
        >
          <span className="font-body font-light italic tracking-[0.01em] text-[18px] sm:text-[19px] md:text-[20px] lg:text-[20px] xl:text-[22px] 2xl:text-[24px] leading-[35px]">
            Share Your Vision
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