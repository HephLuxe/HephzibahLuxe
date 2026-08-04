import Image from "next/image";
import Link from "next/link";

export default function Celebration() {
    return (
        <section className="bg-secondary text-background">
            {/* Stacked (< lg): mobile portrait, landscape, iPad */}
            <div className="lg:hidden flex flex-col">
                <div className="relative w-full aspect-[4/3] sm:aspect-[16/9] md:aspect-[2/1]">
                    <Image
                        src="/images/celebration/shape.jpg"
                        alt="Elegant table setting"
                        fill
                        className="object-cover"
                        sizes="100vw"
                    />
                </div>

                <div className="flex flex-col items-center text-center px-6 py-16 sm:px-8 sm:py-20 md:px-10 md:py-24">
                    <h2 className="font-display font-thin tracking-[0.03em] text-background text-[28px] leading-[34px] sm:text-[32px] sm:leading-[40px] md:text-[40px] md:leading-[50px] max-w-[400px] sm:max-w-[520px] md:max-w-[680px]">
                        Where Your Celebration Takes Shape
                    </h2>

                    <p className="mt-8 sm:mt-9 md:mt-10 font-body font-extralight tracking-[0.03em] text-background text-[15px] leading-[26px] sm:text-[16px] sm:leading-[28px] md:text-[18px] md:leading-[30px] max-w-[420px] sm:max-w-[520px] md:max-w-[640px]">
                        Ready to start planning a celebration that feels thoughtful, meaningful, and beautifully considered? Whether you&apos;re at the early stages or refining the details, we&apos;re here to listen and guide you with care. We would love to hear more about your vision.
                    </p>

                    <Link
                        href="/inquiry"
                        className="group mt-10 sm:mt-11 md:mt-12 inline-flex items-center justify-center gap-3 border border-background px-6 py-3 md:px-7 md:py-3.5 transition-colors hover:bg-background"
                    >
                        <span className="font-body font-light italic tracking-[0.01em] text-background group-hover:text-secondary transition-colors text-[18px] sm:text-[19px] md:text-[20px]">
                            Share Your Vision
                        </span>
                        <span className="relative inline-block w-[16px] h-[16px] sm:w-[17px] sm:h-[17px] md:w-[18px] md:h-[18px]">
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

            {/* Two-column (lg+): laptop, monitor */}
            <div className="hidden lg:grid grid-cols-2 items-stretch">
                <div className="relative self-stretch min-h-[640px] xl:min-h-[720px] 2xl:min-h-[800px]">
                    <Image
                        src="/images/celebration/shape.jpg"
                        alt="Elegant table setting"
                        fill
                        className="object-cover"
                        sizes="50vw"
                    />
                </div>

                <div className="flex flex-col items-center justify-center text-center px-12 py-20 xl:px-16 xl:py-24 2xl:px-20 2xl:py-28">
                    <h2 className="font-display font-thin tracking-[0.01em] text-background whitespace-nowrap text-[35px] leading-[100%] xl:text-[42px] 2xl:text-[50px]">
                        Where Your Celebration Takes Shape
                    </h2>

                    <p className="mt-10 xl:mt-12 font-body font-extralight tracking-[0.03em] text-background text-[18px] leading-[30px] xl:text-[20px] xl:leading-[32px] 2xl:text-[22px] 2xl:leading-[34px] max-w-[480px] xl:max-w-[560px] 2xl:max-w-[640px]">
                        Ready to start planning a celebration that feels thoughtful, meaningful, and beautifully considered? Whether you&apos;re at the early stages or refining the details, we&apos;re here to listen and guide you with care. We would love to hear more about your vision.
                    </p>

                    <Link
                        href="/inquiry"
                        className="group mt-12 inline-flex items-center justify-center gap-3 border border-background px-6 py-3 xl:px-7 xl:py-3.5 2xl:px-8 2xl:py-4 transition-colors hover:bg-background"
                    >
                        <span className="font-body font-light italic tracking-[0.01em] text-background group-hover:text-secondary transition-colors text-[20px] leading-[35px] xl:text-[22px] 2xl:text-[24px]">
                            Share Your Vision
                        </span>
                        <span className="relative inline-block w-[20px] h-[20px] xl:w-[22px] xl:h-[22px] 2xl:w-[24px] 2xl:h-[24px]">
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
        </section>
    );
}