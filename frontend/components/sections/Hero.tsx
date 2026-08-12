import Image from "next/image";

const heroImages = [
    { src: "/images/hero/heroones.jpg", alt: "Elegant dinner table setting" },
    { src: "/images/hero/herotwo.jpg", alt: "Couple celebrating with sparklers" },
    { src: "/images/hero/herothree.jpg", alt: "Reception table with floral details" },
    { src: "/images/hero/herofours.jpg", alt: "Couple in coordinated attire" },
    { src: "/images/hero/herosixs.jpg", alt: "Bride and groom on grand staircase" },
    { src: "/images/portfoliopage/portfoliotwo.jpg", alt: "Ballroom event with hanging decor" },
    { src: "/images/hero/heroseven.jpg", alt: "Reception tablescape" },
    { src: "/images/hero/heroeight.jpg", alt: "Couple in formal evening wear" },
];

export default function Hero() {
    return (
        <section className="relative w-full h-[55vh] min-h-[440px] lg:h-[100vh] lg:min-h-0 overflow-hidden bg-primary">
            {/* sm+ (640px+): 4 top, 4 bottom */}
            <div className="absolute inset-0 hidden sm:flex flex-col gap-2">
                <div className="flex gap-2 flex-1">
                    {heroImages.slice(0, 4).map((image, i) => (
                        <div key={i} className="relative flex-1 overflow-hidden">
                            <Image src={image.src} alt={image.alt} fill className="object-cover" priority sizes="25vw" />
                        </div>
                    ))}
                </div>
                <div className="flex gap-2 flex-1 -ml-[8%]">
                    {heroImages.slice(4, 8).map((image, i) => (
                        <div key={i} className="relative flex-1 overflow-hidden">
                            <Image src={image.src} alt={image.alt} fill className="object-cover" sizes="25vw" />
                        </div>
                    ))}
                </div>
            </div>

            {/* < sm (mobile portrait): 3 top, 4 bottom */}
            <div className="absolute inset-0 flex sm:hidden flex-col gap-1">
                <div className="flex gap-1 flex-1 justify-center">
                    {heroImages.slice(0, 3).map((image, i) => (
                        <div key={i} className="relative w-[145px] flex-shrink-0 overflow-hidden">
                            <Image src={image.src} alt={image.alt} fill className="object-cover" priority sizes="145px" />
                        </div>
                    ))}
                </div>
                <div className="flex gap-1 flex-1 -ml-[25%]">
                    {heroImages.slice(3, 7).map((image, i) => (
                        <div key={i} className="relative w-[145px] flex-shrink-0 overflow-hidden">
                            <Image src={image.src} alt={image.alt} fill className="object-cover" sizes="145px" />
                        </div>
                    ))}
                </div>
            </div>

            <div className="absolute inset-0 bg-primary/45" />

            <div className="relative z-10 flex h-full flex-col items-center justify-center px-4 sm:px-6 lg:px-8 pt-20 sm:pt-16 md:pt-20 lg:pt-32 xl:pt-40 text-background">
                <h1 className="font-display font-thin text-center tracking-[-0.02em] text-[28px] leading-[1.2] sm:text-[40px] sm:leading-[1.15] md:text-[54px] md:leading-[1.15] lg:text-[74px] lg:leading-[1.1] xl:text-[96px] xl:leading-[1.1] 2xl:text-[116px] 2xl:leading-[1.1]">
                    <span className="block whitespace-nowrap">
                        ARCHITECTS <em className="italic font-thin mx-1 sm:mx-2 lg:mx-3 xl:mx-4">of</em> THOUGHTFUL
                    </span>
                    <span className="block whitespace-nowrap">
                        CELEBRATIONS
                    </span>
                </h1>
                <p className="font-body font-normal text-center uppercase tracking-[-0.01em] text-[12.5px] leading-[19px] sm:text-[13px] sm:leading-[20px] md:text-[14px] md:leading-[22px] lg:text-[16px] lg:leading-[28px] xl:text-[18px] xl:leading-[32px] mt-4 sm:mt-6 lg:mt-10 max-w-[640px] xl:max-w-[740px]">
                    A design-led wedding and event planning studio shaping celebrations with intention and grace.
                </p>
            </div>
        </section>
    );
}