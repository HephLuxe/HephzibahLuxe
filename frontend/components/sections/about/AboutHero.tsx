import Image from "next/image";

const W = 1300;
const H = 900;

const POS = {
  h1: { top: 64, left: 40 },
  h2: { top: 200, left: 280 },
  leftImg: { top: 270, left: 30, w: 380, h: 470 },
  topRightImg: { top: 70, left: 850, w: 340, h: 280 },
  paragraphs: { top: 400, left: 430, w: 700 },
  bottomRightImg: { top: 700, w: 260, h: 200 },
};

function HeadingBlock({ color }: { color: "primary" | "background" }) {
  const cls = color === "primary" ? "text-primary" : "text-background";
  return (
    <>
      <h1
        className={`absolute font-display font-thin ${cls} text-[110px] leading-[120px] tracking-[-0.005em] whitespace-nowrap`}
        style={{ top: POS.h1.top, left: POS.h1.left }}
      >
        ABOUT <span className="italic">the</span> BRAND
      </h1>
      <h2
        className={`absolute font-display font-thin italic ${cls} text-[110px] leading-[120px] whitespace-nowrap`}
        style={{ top: POS.h2.top, left: POS.h2.left }}
      >
        Hephzibah Luxe
      </h2>
    </>
  );
}

function MobileHeadings({ color }: { color: "primary" | "background" }) {
  const cls = color === "primary" ? "text-primary" : "text-background";
  return (
    <>
      <h1
        className={`font-display font-thin ${cls} text-[48px] leading-[46px] sm:text-[68px] sm:leading-[74px] md:text-[88px] md:leading-[96px] tracking-[-0.005em] whitespace-nowrap`}
      >
        ABOUT <span className="italic">the</span> BRAND
      </h1>
      <h2
        className={`mt-1 sm:mt-1.5 md:mt-2 font-display font-thin italic ${cls} text-[40px] leading-[46px] sm:text-[68px] sm:leading-[74px] md:text-[88px] md:leading-[96px] whitespace-nowrap`}
      >
        Hephzibah Luxe
      </h2>
    </>
  );
}

export default function AboutHero() {
  return (
    <section className="relative w-full overflow-hidden bg-background">
      {/* ─────────────── Desktop (unchanged) ─────────────── */}
      <div className="hidden lg:block relative">
        <div className="relative mx-auto" style={{ width: W, height: H }}>
          <HeadingBlock color="primary" />

          <div
            className="absolute"
            style={{ top: POS.paragraphs.top, left: POS.paragraphs.left, width: POS.paragraphs.w }}
          >
            <p className="font-body font-light text-primary text-[19px] leading-[31px] tracking-[-0.005em] mb-6">
              Hephzibah Luxe was born from years of experience long before it had a name. For over a decade, we planned organically—bringing together family celebrations, milestone moments, and meaningful gatherings with care, precision, and heart. What began as a natural calling evolved into a refined practice shaped by trust, relationships, and an understanding of what truly makes a celebration memorable.
            </p>
            <p className="font-body font-light text-primary text-[19px] leading-[31px] tracking-[-0.005em] mb-6">
              Rooted in faith and inspired by God, Hephzibah Luxe is guided by the belief that celebrations are sacred moments—worthy of intention, beauty, and excellence. Through long-standing relationships with a trusted network of vendors, we curate experiences that feel seamless, thoughtful, and elevated.
            </p>
            <p className="font-body font-light text-primary text-[19px] leading-[31px] tracking-[-0.005em]">
              Today, Hephzibah Luxe blends heritage with modern execution. Design sits at the heart of everything we do, combining creative direction with thoughtful planning systems to offer a more refined way to plan—one that prioritises clarity, collaboration, and ease while honouring each client&apos;s individuality.
            </p>
          </div>

          <div
            className="absolute overflow-hidden"
            style={{ top: POS.leftImg.top, left: POS.leftImg.left, width: POS.leftImg.w, height: POS.leftImg.h }}
          >
            <Image src="/images/aboutpage/aboutone.jpg" alt="" fill className="object-cover" priority sizes="380px" />
            <div className="absolute" style={{ top: -POS.leftImg.top, left: -POS.leftImg.left, width: W }}>
              <HeadingBlock color="background" />
            </div>
          </div>

          <div
            className="absolute overflow-hidden"
            style={{ top: POS.topRightImg.top, left: POS.topRightImg.left, width: POS.topRightImg.w, height: POS.topRightImg.h }}
          >
            <Image src="/images/aboutpage/abouttwo.jpg" alt="" fill className="object-cover" priority sizes="340px" />
            <div className="absolute" style={{ top: -POS.topRightImg.top, left: -POS.topRightImg.left, width: W }}>
              <HeadingBlock color="background" />
            </div>
          </div>

          <div
            className="absolute"
            style={{
              top: POS.bottomRightImg.top,
              right: -50,
              width: POS.bottomRightImg.w,
              height: POS.bottomRightImg.h,
            }}
          >
            <Image src="/images/aboutpage/aboutthree.jpg" alt="" fill className="object-contain" sizes="260px" />
          </div>
        </div>
      </div>

      {/* ─────────────── Mobile / Landscape / iPad ─────────────── */}
      <div className="lg:hidden relative overflow-hidden">
        {/* Heading row */}
        <div className="relative px-4 sm:px-6 md:px-8 pt-20 sm:pt-24 md:pt-28">
          <div className="text-center">
            <MobileHeadings color="primary" />
          </div>

          {/* Top-right image — overlay right-4 matches image -right-4 (cancels bleed) so white text centers on viewport same as dark text */}
          <div className="absolute top-6 sm:top-14 md:top-16 -right-4 sm:-right-6 md:-right-8 w-[36%] sm:w-[34%] md:w-[32%] aspect-[4/3] overflow-hidden z-20">
            <Image src="/images/aboutpage/abouttwo.jpg" alt="" fill className="object-cover" priority sizes="36vw" />
            <div className="absolute top-[56px] sm:top-[40px] md:top-[48px] right-4 sm:right-6 md:right-8 w-screen text-center">
              <MobileHeadings color="background" />
            </div>
          </div>
        </div>

        {/* Continuous content */}
        <div className="px-4 sm:px-6 md:px-8 mt-4 font-body font-light text-primary text-[15px] leading-[26px] sm:text-[16px] sm:leading-[28px] md:text-[18px] md:leading-[30px] tracking-[-0.005em]">
          {/* Left float image — overlay -top-[46px] (was 54), brings white h2 down to land on dark h2 */}
          <div className="float-left relative w-[42%] sm:w-[40%] md:w-[38%] aspect-[3/4] -ml-12 sm:-ml-14 md:-ml-16 -mt-[62px] sm:-mt-[90px] md:-mt-[112px] mr-4 sm:mr-5 md:mr-6 overflow-hidden z-10">
            <Image src="/images/aboutpage/aboutone.jpg" alt="" fill className="object-cover" priority sizes="42vw" />
            <div className="absolute -top-[50px] sm:-top-[80px] md:-top-[104px] left-[32px] w-screen text-center">
              <MobileHeadings color="background" />
            </div>
          </div>

          <p className="mb-3 sm:mb-3 md:mb-4">
            Hephzibah Luxe was born from years of experience long before it had a name. For over a decade, we planned organically—bringing together family celebrations, milestone moments, and meaningful gatherings with care, precision, and heart. What began as a natural calling evolved into a refined practice shaped by trust, relationships, and an understanding of what truly makes a celebration memorable.
          </p>
          <p className="mb-3 sm:mb-3 md:mb-4">
            Rooted in faith and inspired by God, Hephzibah Luxe is guided by the belief that celebrations are sacred moments—worthy of intention, beauty, and excellence. Through long-standing relationships with a trusted network of vendors, we curate experiences that feel seamless, thoughtful, and elevated.
          </p>

          {/* Paragraph 3 — mobile splits text into two parts so it wraps ABOVE and BESIDE the absolute bottom image (never under). sm+: hidden/visible flip restores the original single-paragraph + float behavior. */}

          {/* MOBILE ONLY: first part — full width, wraps above the image */}
          <p className="sm:hidden mb-0">
            Today, Hephzibah Luxe blends heritage with modern execution. Design sits at the heart of everything we do, combining creative direction with thoughtful planning systems to offer a more refined way to plan—one that prioritises clarity,{" "}
          </p>
          {/* MOBILE ONLY: second part — right column reserved so this text sits beside the image */}
          <p className="pb-12 sm:hidden pr-[44%]">
            collaboration, and ease while honouring each client&apos;s individuality.
          </p>

          {/* sm+ ONLY: original single paragraph with float-right span — iPad / iPad Pro / desktop behavior unchanged */}
          <p className="hidden sm:block">
            Today, Hephzibah Luxe blends heritage with modern execution.{" "}
            <span className="float-right relative sm:w-[38%] md:w-[30%] aspect-[4/3] sm:-mr-10 md:-mr-12 ml-4 mb-0 overflow-hidden block">
              <Image src="/images/aboutpage/aboutthree.jpg" alt="" fill className="object-contain" sizes="40vw" />
            </span>
            Design sits at the heart of everything we do, combining creative direction with thoughtful planning systems to offer a more refined way to plan—one that prioritises clarity, collaboration, and ease while honouring each client&apos;s individuality.
          </p>
        </div>

        {/* MOBILE ONLY — bottom-right image pinned to the lg:hidden wrapper's bottom-right corner. Section grows to contain text only; image overlays the bottom-right corner with no gap below. */}
        <div className="sm:hidden absolute bottom-0 -right-8 w-[40%] aspect-[4/3] overflow-hidden z-10">
          <Image src="/images/aboutpage/aboutthree.jpg" alt="" fill className="object-contain" sizes="40vw" />
        </div>
      </div>
    </section>
  );
}
