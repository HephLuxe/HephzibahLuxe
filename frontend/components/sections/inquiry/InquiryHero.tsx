import Image from "next/image";

export default function InquiryHero() {
    return (
        <section className="relative w-full overflow-hidden h-[35vh] landscape:h-[75vh] sm:h-[35vh] md:h-[40vh] lg:h-[60vh] xl:h-[65vh] 2xl:h-[70vh]">
            {/* Background */}
            <div
                className="absolute inset-0 bg-cover bg-center bg-no-repeat"
                style={{ backgroundImage: "url(/images/inquirypage/inquirybg.jpg)" }}
            >
                <div className="absolute inset-0 bg-primary/10" />
            </div>

            {/* Rotating badge — moved further left on mobile/iPad so nothing can bleed past the viewport edge */}
            <div className="absolute bottom-2 sm:bottom-3 md:bottom-4 lg:bottom-6 xl:bottom-6 right-4 sm:right-6 md:right-10 lg:right-20 xl:right-18 2xl:right-32">
                <div className="relative w-40 h-40 sm:w-48 sm:h-48 md:w-56 md:h-56 lg:w-64 lg:h-64 xl:w-72 xl:h-72 2xl:w-80 2xl:h-80">
                    {/* Center logo */}
                    <div className="absolute inset-0 flex items-center justify-center z-10">
                        <Image
                            src="/brand/logo.svg"
                            alt="Hephzibah Luxe"
                            width={128}
                            height={128}
                            className="w-14 h-14 sm:w-20 sm:h-20 md:w-20 md:h-20 lg:w-24 lg:h-24 xl:w-28 xl:h-28 2xl:w-32 2xl:h-32"
                        />
                    </div>

                    {/* Rotating circular text */}
                    <svg
                        viewBox="0 0 200 200"
                        className="w-full h-full animate-rotate-slow"
                    >
                        <defs>
                            <path
                                id="circlePath"
                                d="M 100, 100 m -75, 0 a 75,75 0 1,1 150,0 a 75,75 0 1,1 -150,0"
                                fill="none"
                            />
                        </defs>
                        <text
                            fill="white"
                            fontSize="14"
                            fontFamily="var(--font-editors-note), serif"
                            fontWeight="400"
                        >
                            <textPath href="#circlePath" textLength="470" spacing="auto">
                                • WORK WITH US • WORK WITH US • WORK WITH US&#160;
                            </textPath>
                        </text>
                    </svg>
                </div>
            </div>
        </section>
    );
}