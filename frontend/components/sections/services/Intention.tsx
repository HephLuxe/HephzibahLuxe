import Image from "next/image";
import Link from "next/link";

export default function Intention() {
    return (
        <section className="relative z-40">
            <div
                className="relative w-full min-h-[600px] sm:min-h-[640px] md:min-h-[720px] lg:min-h-screen flex items-center justify-center py-8 sm:py-12 md:py-16 lg:py-20 xl:py-24 2xl:py-28 px-6 sm:px-8 md:px-10 lg:px-12 xl:px-16 2xl:px-20 bg-cover bg-center bg-no-repeat"
                style={{ backgroundImage: "url(/images/servicespage/intentionbg.jpg)" }}
            >
                <div className="absolute inset-0 bg-primary/60" />

                <div className="relative z-10 max-w-5xl xl:max-w-6xl 2xl:max-w-[1400px] mx-auto text-center text-background">
                    <h2 className="font-display font-thin tracking-[0.01em] mb-10 lg:mb-12 xl:mb-14 text-[34px] leading-[100%] sm:text-[38px] sm:leading-[110%] md:text-[44px] md:leading-[110%] lg:text-[50px] lg:leading-[110%] xl:text-[58px] xl:leading-[110%] 2xl:text-[66px] 2xl:leading-[110%]">
                        Where intention leads, ease follows—thoughtfully planned, so every moment feels effortless.
                    </h2>

                    <div className="mb-10 lg:mb-12 xl:mb-14 max-w-4xl xl:max-w-5xl 2xl:max-w-[1200px] mx-auto">
                        <p className="font-body font-light tracking-[0] text-[16px] leading-[28px] sm:text-[17px] sm:leading-[30px] md:text-[19px] md:leading-[32px] lg:text-[24px] lg:leading-[35px] xl:text-[26px] xl:leading-[38px] 2xl:text-[28px] 2xl:leading-[40px]">
                            Luxury, to us, is intention. It&apos;s found in the quiet details, the seamless flow, and the confidence that comes from knowing every moment is thoughtfully handled. We create celebrations that feel beautiful, meaningful, and effortless, allowing you to be fully present and savour every moment.
                        </p>
                    </div>

                    <p className="mb-10 lg:mb-12 xl:mb-14 font-sans font-light tracking-[0.01em] uppercase text-[14px] leading-[24px] sm:text-[15px] sm:leading-[26px] md:text-[17px] md:leading-[28px] lg:text-[20px] lg:leading-[35px] xl:text-[22px] 2xl:text-[24px]">
                        — WINIFRED OJULARI, CEO, HEPHZIBAH LUXE
                    </p>

                    <div className="flex flex-row items-center justify-center gap-3 sm:gap-4 md:gap-5 lg:gap-6">
                        <Link
                            href="/portfolio"
                            className="group inline-flex items-center gap-2 lg:gap-3 border border-background px-4 py-2 sm:px-5 sm:py-2.5 md:px-6 md:py-3 lg:px-8 lg:py-3 xl:px-9 xl:py-3.5 2xl:px-10 2xl:py-4 transition-all duration-300 hover:bg-background"
                        >
                            <span className="font-body font-light italic text-background group-hover:text-primary transition-colors whitespace-nowrap text-[13px] leading-[24px] sm:text-[14px] sm:leading-[26px] md:text-[16px] md:leading-[28px] lg:text-[20px] lg:leading-[35px] xl:text-[22px] 2xl:text-[24px]">
                                View Our Portfolio
                            </span>
                            <span className="relative inline-block w-[16px] h-[12px] sm:w-[17px] sm:h-[13px] md:w-[18px] md:h-[14px] lg:w-[20px] lg:h-[14px] xl:w-[22px] xl:h-[16px] 2xl:w-[24px] 2xl:h-[18px]">
                                <Image
                                    src="/icons/whitebuttonarrow.svg"
                                    alt=""
                                    fill
                                    className="object-contain transition-opacity group-hover:opacity-0"
                                />
                                <Image
                                    src="/icons/buttonarrow.svg"
                                    alt=""
                                    fill
                                    className="object-contain opacity-0 transition-opacity group-hover:opacity-100"
                                />
                            </span>
                        </Link>

                        <Link
                            href="/inquiry"
                            className="group inline-flex items-center gap-2 lg:gap-3 border border-background px-4 py-2 sm:px-5 sm:py-2.5 md:px-6 md:py-3 lg:px-8 lg:py-3 xl:px-9 xl:py-3.5 2xl:px-10 2xl:py-4 transition-all duration-300 hover:bg-background"
                        >
                            <span className="font-body font-light italic text-background group-hover:text-primary transition-colors whitespace-nowrap text-[13px] leading-[24px] sm:text-[14px] sm:leading-[26px] md:text-[16px] md:leading-[28px] lg:text-[20px] lg:leading-[35px] xl:text-[22px] 2xl:text-[24px]">
                                Share Your vision
                            </span>
                            <span className="relative inline-block w-[16px] h-[12px] sm:w-[17px] sm:h-[13px] md:w-[18px] md:h-[14px] lg:w-[20px] lg:h-[14px] xl:w-[22px] xl:h-[16px] 2xl:w-[24px] 2xl:h-[18px]">
                                <Image
                                    src="/icons/whitebuttonarrow.svg"
                                    alt=""
                                    fill
                                    className="object-contain transition-opacity group-hover:opacity-0"
                                />
                                <Image
                                    src="/icons/buttonarrow.svg"
                                    alt=""
                                    fill
                                    className="object-contain opacity-0 transition-opacity group-hover:opacity-100"
                                />
                            </span>
                        </Link>
                    </div>
                </div>
            </div>
        </section>
    );
}