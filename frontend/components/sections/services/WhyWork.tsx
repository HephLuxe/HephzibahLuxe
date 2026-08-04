import Image from "next/image";

const points = [
  {
    title: "More Than Planning — We Create Meaningful Experiences",
    description:
      "Planning is only part of what we do. We shape how each celebration feels—from the first conversation to the final farewell—creating experiences that are seamless, deeply personal, and effortless to enjoy.",
  },
  {
    title: "Every Celebration Begins with Listening",
    description:
      "Every celebration begins with understanding your story, priorities, and vision. We take time to ask questions, explore the details that matter most, and build every decision around what feels authentic to you.",
  },
  {
    title: "Guided with Care. Executed with Precision",
    description:
      "Behind every effortless celebration is thoughtful planning, seamless coordination, and attention to detail. We manage every moving part so you can be fully present and celebrate with confidence.",
  },
  {
    title: "Thoughtfully Redefining Luxury",
    description:
      "We believe exceptional celebrations aren't defined by budget. Through creativity, intention, and thoughtful design, we create elevated experiences at every scale.",
  },
];

export default function WhyWork() {
  return (
    <section className="bg-secondary text-background py-16 sm:py-20 md:py-24 lg:py-24 xl:py-28 2xl:py-32 overflow-hidden">
      <div className="px-6 sm:px-8 md:px-10 lg:px-12 xl:px-16 2xl:px-20">
        <div className="grid grid-cols-1 lg:grid-cols-[40%_60%] gap-10 lg:gap-10 xl:gap-10 2xl:gap-24 items-start">
          {/* Image — second on mobile, first on desktop */}
          <div className="order-2 lg:order-1 relative w-full aspect-[4/5] sm:aspect-[3/4] md:aspect-[5/6] lg:aspect-auto lg:h-full lg:min-h-[700px] xl:min-h-[800px] 2xl:min-h-[900px] overflow-hidden">
            <Image
              src="/images/servicespage/work.jpg"
              alt="Elegant table setting"
              fill
              className="object-cover"
              sizes="(max-width: 1024px) 100vw, 40vw"
            />
          </div>

          {/* Text content */}
          <div className="order-1 lg:order-2 flex flex-col">
            <h2 className="font-display font-thin tracking-[0] text-background text-[36px] leading-[42px] sm:text-[44px] sm:leading-[50px] md:text-[52px] md:leading-[58px] lg:text-[60px] lg:leading-[65px] xl:text-[72px] xl:leading-[78px] 2xl:text-[84px] 2xl:leading-[90px]">
              WHY WORK
              <br />
              <em className="italic font-thin">with</em> HEPHZIBAH LUXE?
            </h2>

            <ul className="mt-10 sm:mt-12 md:mt-12 lg:mt-12 xl:mt-14 space-y-8 sm:space-y-9 md:space-y-10 lg:space-y-10 xl:space-y-12 2xl:space-y-14 lg:pl-12 xl:pl-16 2xl:pl-20">
              {points.map((point, i) => (
                <li key={i}>
                  <div className="flex items-center gap-3 md:gap-4">
                    <Image
                      src="/icons/checkmark.svg"
                      alt=""
                      width={26}
                      height={26}
                      className="w-[20px] h-[20px] md:w-[22px] md:h-[22px] xl:w-[24px] xl:h-[24px] 2xl:w-[26px] 2xl:h-[26px] flex-shrink-0"
                    />
                    <h3 className="font-body font-normal text-background text-[16px] leading-[22px] sm:text-[17px] sm:leading-[24px] md:text-[18px] md:leading-[26px] lg:text-[20px] lg:leading-[25px] xl:text-[22px] xl:leading-[28px] 2xl:text-[24px] 2xl:leading-[30px]">
                      {point.title}
                    </h3>
                  </div>
                  <p className="mt-3 pl-8 md:pl-9 xl:pl-10 font-body font-light text-background text-[15px] leading-[24px] sm:text-[16px] sm:leading-[26px] md:text-[18px] md:leading-[28px] lg:text-[18px] lg:leading-[25px] xl:text-[20px] xl:leading-[30px] 2xl:text-[22px] 2xl:leading-[32px] max-w-[660px] xl:max-w-[680px] 2xl:max-w-[780px]">
                    {point.description}
                  </p>
                </li>
              ))}
            </ul>
          </div>
        </div>
      </div>
    </section>
  );
}