import Image from "next/image";

export default function BehindTheBrand() {
    return (
        <section className="bg-background text-primary overflow-x-hidden">
            <div className="px-4 sm:px-6 md:px-8 lg:px-8 xl:px-12 2xl:px-16 py-16 sm:py-20 md:py-24 lg:py-24 xl:py-28 2xl:py-32">
                <div className="max-w-7xl 2xl:max-w-[1600px] mx-auto">
                    {/* Mobile / landscape / iPad heading */}
                    <div className="lg:hidden mb-10 sm:mb-12 md:mb-14">
                        <h2 className="font-display font-thin tracking-normal text-[56px] leading-[50px] sm:text-[68px] sm:leading-[60px] md:text-[84px] md:leading-[76px]">
                            BEHIND
                            <br />
                            <span className="italic">the</span> BRAND
                        </h2>
                        <p className="mt-6 sm:mt-7 md:mt-8 font-body font-light italic tracking-[0.02em] text-[15px] leading-[26px] sm:text-[16px] sm:leading-[28px] md:text-[18px] md:leading-[30px]">
                            Hephzibah Luxe is founded on the belief that celebrations are more than events—they are expressions of legacy, love, and intention. At the helm is a mother-daughter duo whose combined strengths bring warmth, precision, and unmistakable elegance to every experience.
                        </p>
                    </div>

                    {/* Founder row */}
                    <div className="grid grid-cols-1 lg:grid-cols-[45fr_55fr] gap-8 sm:gap-10 md:gap-12 lg:gap-14 xl:gap-16 2xl:gap-20">
                        <div className="relative w-full min-w-0 aspect-[4/5] sm:aspect-[3/4] md:aspect-[5/6] lg:aspect-auto lg:h-full lg:min-h-full">
                            <Image
                                src="/images/aboutpage/founder.jpg"
                                alt="Winnie O., Founder & CEO"
                                fill
                                className="object-cover"
                                sizes="(max-width: 1024px) 100vw, 45vw"
                            />
                        </div>

                        <div className="min-w-0">
                            {/* Desktop heading + intro */}
                            <div className="hidden lg:block mb-10 xl:mb-12 2xl:mb-14">
                                <h2 className="font-display font-thin tracking-normal text-[100px] leading-[90px] xl:text-[120px] xl:leading-[108px] 2xl:text-[140px] 2xl:leading-[126px]">
                                    BEHIND
                                    <br />
                                    <span className="italic">the</span> BRAND
                                </h2>
                                <p className="mt-8 xl:mt-10 lg:pl-16 xl:pl-20 2xl:pl-24 font-body font-light italic tracking-[0.02em] text-[16px] leading-[26px] xl:text-[18px] xl:leading-[30px] 2xl:text-[20px] 2xl:leading-[34px] max-w-[610px] xl:max-w-[700px] 2xl:max-w-[800px]">
                                    Hephzibah Luxe is founded on the belief that celebrations are more than events—they are expressions of legacy, love, and intention. At the helm is a mother-daughter duo whose combined strengths bring warmth, precision, and unmistakable elegance to every experience.
                                </p>
                            </div>

                            <div className="lg:pl-16 xl:pl-20 2xl:pl-24">
                                <h3 className="font-body font-medium tracking-[-0.01em] text-[16px] leading-[24px] sm:text-[17px] sm:leading-[26px] md:text-[18px] md:leading-[28px] lg:text-[18px] xl:text-[20px] xl:leading-[28px] 2xl:text-[22px] 2xl:leading-[30px]">
                                    Winnie O. | Founder &amp; CEO | Client Experience Curator &amp; On-Site Lead
                                </h3>
                                <div className="mt-4 sm:mt-5 md:mt-6 space-y-4 sm:space-y-5 font-body font-light text-[15px] leading-[26px] sm:text-[16px] sm:leading-[28px] md:text-[18px] md:leading-[30px] lg:text-[18px] lg:leading-[30px] xl:text-[20px] xl:leading-[32px] 2xl:text-[22px] 2xl:leading-[34px]">
                                    <p>
                                        Winnie is the heart of Hephzibah Luxe. With a calm presence and a deeply intuitive approach, she brings reassurance, wisdom, and grace to every celebration. Known for her thoughtful guidance and genuine warmth, Winnie has a gift for making clients feel at ease—even amid the most intricate planning moments. From venue walk-throughs to vendor relationships, she ensures every detail feels personal, considered, and beautifully aligned with your vision.
                                    </p>
                                    <p>
                                        Her love for event planning began long before Hephzibah Luxe. For years, Winnie was the trusted &ldquo;go-to&rdquo; for family celebrations, community gatherings, and milestone moments—naturally stepping into the role of organiser, guide, and calm anchor. What started as instinct has grown into a wealth of experience, shaped by years of hands-on involvement and a genuine joy in bringing people together. When she isn&apos;t planning, Winnie enjoys spending time with family and nurturing the spirit of hospitality that inspires everything she does.
                                    </p>
                                </div>
                            </div>
                        </div>
                    </div>

                    {/* Co-founder row */}
                    <div className="mt-16 sm:mt-20 md:mt-24 lg:mt-28 xl:mt-32 2xl:mt-36 grid grid-cols-1 lg:grid-cols-2 gap-8 sm:gap-10 md:gap-12 lg:gap-14 xl:gap-16 2xl:gap-20">
                        <div className="relative w-full min-w-0 aspect-[4/5] sm:aspect-[3/4] md:aspect-[5/6] lg:aspect-auto lg:h-full lg:min-h-full lg:order-2">
                            <Image
                                src="/images/aboutpage/cofounder.jpg"
                                alt="Tosin O., Co-Founder"
                                fill
                                className="object-cover"
                                sizes="(max-width: 1024px) 100vw, 50vw"
                            />
                        </div>

                        <div className="min-w-0 lg:order-1">
                            <h3 className="font-body font-medium tracking-[-0.01em] text-[16px] leading-[24px] sm:text-[17px] sm:leading-[26px] md:text-[18px] md:leading-[28px] lg:text-[18px] xl:text-[20px] xl:leading-[28px] 2xl:text-[22px] 2xl:leading-[30px]">
                                Tosin O. | Co-Founder | Strategy, Systems &amp; Creative Direction
                            </h3>
                            <div className="mt-4 sm:mt-5 md:mt-6 space-y-4 sm:space-y-5 font-body font-light text-[15px] leading-[26px] sm:text-[16px] sm:leading-[28px] md:text-[18px] md:leading-[30px] lg:text-[18px] lg:leading-[30px] xl:text-[20px] xl:leading-[32px] 2xl:text-[22px] 2xl:leading-[34px]">
                                <p>
                                    Tosin is the mind behind the seamlessness. Detail-driven and design-led, she brings structure, clarity, and strategic oversight to every celebration. With a background in digital management and a deep love for intentional design, Tosin ensures timelines flow, communication remains effortless, and nothing is left to chance. Her approach blends creative vision with operational excellence, allowing beauty and precision to coexist effortlessly.
                                </p>
                                <p>
                                    Her path to Hephzibah Luxe has been shaped by years of balancing multiple roles, disciplines, and responsibilities—each sharpening her eye for detail, structure, and foresight. Working alongside her mother allows Tosin to pair strategy with intuition and systems with presence. Their constant back-and-forth—refining, questioning, and perfecting—ensures every decision is thoughtful and every experience is held to the highest standard.
                                </p>
                            </div>

                            <div className="mt-8 sm:mt-9 md:mt-10 xl:mt-12">
                                <p className="font-body font-semibold italic text-[15px] leading-[26px] sm:text-[16px] sm:leading-[28px] md:text-[18px] md:leading-[30px] lg:text-[18px] lg:leading-[26px] xl:text-[20px] xl:leading-[30px] 2xl:text-[22px] 2xl:leading-[32px]">
                                   One Vision. Two Perspectives. Elegance in Every Element.
                                </p>
                                <p className="mt-4 sm:mt-5 font-body font-light italic tracking-[0.02em] text-[14px] leading-[24px] sm:text-[15px] sm:leading-[26px] md:text-[17px] md:leading-[28px] lg:text-[16px] lg:leading-[26px] xl:text-[18px] xl:leading-[30px] 2xl:text-[20px] 2xl:leading-[32px]">
                                    A partnership rooted in trust, perspective, and a shared commitment to excellence. Together, Winnie and Tosin create thoughtful, refined, and deeply meaningful celebrations—where every element is considered, every moment intentional, and every experience elevated.
                                </p>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </section>
    );
}