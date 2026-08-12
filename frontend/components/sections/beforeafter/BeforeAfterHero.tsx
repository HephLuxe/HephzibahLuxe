import Image from "next/image";

export default function BeforeAfterHero() {
    return (
        <section className="relative w-full h-[40vh] landscape:h-[95vh] sm:h-[50vh] md:h-[50vh] lg:h-[100vh] xl:h-[100vh] overflow-hidden">
            {/* Split background images */}
            <div className="absolute inset-0 flex">
                <div
                    className="w-1/2 bg-cover bg-center"
                    style={{ backgroundImage: "url(/images/beforeafterpage/beforebgg.jpg)" }}
                />
                <div
                    className="w-1/2 bg-cover bg-center"
                    style={{ backgroundImage: "url(/images/beforeafterpage/afterbgg.jpg)" }}
                />
            </div>

            {/* Text overlay — & at dead center */}
            <div className="absolute inset-0 flex items-center justify-center">
                {/* BEFORE — ends at the & */}
                <h1
                    className="font-display font-thin italic absolute text-primary leading-[1] text-[42px] sm:text-[58px] md:text-[80px] lg:text-[140px] xl:text-[160px] 2xl:text-[180px] right-[50%] mr-7 sm:mr-10 md:mr-14 lg:mr-[100px] xl:mr-[100px] 2xl:mr-[150px]"
                >
                    BEFORE
                </h1>

                {/* & icon — dead center */}
                <Image
                    src="/icons/andsign.svg"
                    alt="and"
                    width={144}
                    height={144}
                    className="w-12 h-12 sm:w-16 sm:h-16 md:w-20 md:h-20 lg:w-28 lg:h-28 xl:w-32 xl:h-32 2xl:w-36 2xl:h-36 object-contain z-10"
                />

                {/* After — starts at the & */}
                <h1
                    className="font-display font-thin italic absolute text-background leading-[1] text-[52px] sm:text-[76px] md:text-[108px] lg:text-[190px] xl:text-[220px] 2xl:text-[250px] left-[50%] ml-7 sm:ml-10 md:ml-14 lg:ml-[100px] xl:ml-[100px] 2xl:ml-[150px]"
                >
                    After
                </h1>
            </div>
        </section>
    );
}