"use client";

import { useRef, useCallback } from "react";

const steps = [
    {
        number: "No. 01",
        title: "CONNECT",
        lead: "Understanding the Meaning Behind the Moment",
        description:
            "Every celebration begins with connection. Through a private conversation, we listen deeply — to your vision, your priorities, and what this occasion truly represents. It’s how we ensure every choice that follows feels personal, considered, and true to you.",
    },
    {
        number: "No. 02",
        title: "ALIGN",
        lead: "Clarity Before Creativity, So Every Detail Is Made with Intention",
        description:
            "With your goals defined, we establish clarity around scope, priorities, and financial parameters, allowing every decision to feel informed, balanced, and aligned with your expectations.",
    },
    {
        number: "No. 03",
        title: "CURATE",
        lead: "Curated with Purpose—The Right Setting, the Right Partners",
        description:
            "From venues to creative collaborators, every element is thoughtfully chosen. We curate spaces and partners that align with your vision and elevate the experience, ensuring nothing feels incidental or out of place.",
    },
    {
        number: "No. 04",
        title: "ENVISION",
        lead: "Shaping the Experience, from First Impression to Final Moment",
        description:
            "This is where your celebration begins to take form. Drawing from your story and inspiration, we shape the look, feel, and flow of the event—imagining it as your guests will experience it, from first arrival to the final farewell.",
    },
    {
        number: "No. 05",
        title: "ORCHESTRATE",
        lead: "Precision Behind the Scenes, as the Day Unfolds",
        description:
            "As plans come together, we quietly manage the details — logistics, timelines, coordination — with care and exactness. Much of this work is unseen, but it's what allows the experience to feel seamless and unburdened.",
    },
    {
        number: "No. 06",
        title: "EXECUTE",
        lead: "An Experience Fully Realised—Every Detail Felt, Nothing Left to Chance",
        description:
            "On the day itself, our presence is steady and composed. We guide the celebration with quiet confidence, allowing you to remain fully present while everything unfolds exactly as envisioned.",
    },
    {
        number: "No. 07",
        title: "COMPLETION",
        lead: "Care That Extends Beyond Your Day of Celebration",
        description:
            "As the celebration comes to a close, we remain attentive, ensuring every detail is completed and the experience settles with the same intention with which it began. It's a quiet final touch, allowing the celebration to end feeling whole and well held.",
    },
];

export default function Process() {
    const scrollRef = useRef<HTMLDivElement>(null);
    const isDraggingRef = useRef(false);
    const startXRef = useRef(0);
    const startScrollLeftRef = useRef(0);
    const movedRef = useRef(false);

    const handlePointerDown = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
        if (e.pointerType === "touch") return;
        const target = e.target as HTMLElement;
        if (target.closest("a, button")) return;

        const container = scrollRef.current;
        if (!container) return;

        isDraggingRef.current = true;
        movedRef.current = false;
        startXRef.current = e.clientX;
        startScrollLeftRef.current = container.scrollLeft;
        container.setPointerCapture(e.pointerId);
    }, []);

    const handlePointerMove = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
        if (!isDraggingRef.current) return;
        const container = scrollRef.current;
        if (!container) return;

        const delta = e.clientX - startXRef.current;
        if (Math.abs(delta) > 4) movedRef.current = true;
        container.scrollLeft = startScrollLeftRef.current - delta;
    }, []);

    const handlePointerUp = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
        if (!isDraggingRef.current) return;
        const container = scrollRef.current;
        if (container) {
            try {
                container.releasePointerCapture(e.pointerId);
            } catch {
                // capture may have been released already
            }
        }
        isDraggingRef.current = false;
    }, []);

    const handleClickCapture = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
        if (movedRef.current) {
            e.preventDefault();
            e.stopPropagation();
            movedRef.current = false;
        }
    }, []);

    return (
        <section className="relative z-40 bg-background py-20 sm:py-24 md:py-24 lg:py-28 xl:py-32 2xl:py-36 px-6 sm:px-8 md:px-10 lg:px-10 xl:px-14 2xl:px-20 overflow-hidden">
            <div className="max-w-7xl 2xl:max-w-[1600px] mx-auto">
                {/* Header */}
                <h2 className="text-center mb-16 sm:mb-20 md:mb-20 lg:mb-24 xl:mb-28 text-primary lg:whitespace-nowrap">
                    <span className="font-display font-thin leading-[100%] text-[30px] sm:text-[36px] md:text-[44px] lg:text-[60px] xl:text-[70px] 2xl:text-[80px]">
                        A GLIMPSE INTO OUR{" "}
                    </span>
                    <span className="font-display font-thin italic leading-[100%] text-[30px] sm:text-[36px] md:text-[44px] lg:text-[60px] xl:text-[70px] 2xl:text-[80px]">
                        Signature
                    </span>
                    <span className="font-display font-thin leading-[100%] text-[30px] sm:text-[36px] md:text-[44px] lg:text-[60px] xl:text-[70px] 2xl:text-[80px]">
                        {" "}
                        PROCESS
                    </span>
                </h2>

                {/* Timeline */}
                <div
                    ref={scrollRef}
                    className="overflow-x-auto scrollbar-hide select-none cursor-grab active:cursor-grabbing"
                    onPointerDown={handlePointerDown}
                    onPointerMove={handlePointerMove}
                    onPointerUp={handlePointerUp}
                    onPointerCancel={handlePointerUp}
                    onClickCapture={handleClickCapture}
                >
                    <div className="relative min-w-max">
                        {/* Continuous horizontal line */}
                        <div className="absolute left-0 right-0 h-[2px] bg-primary top-[12px]" />

                        {/* Steps */}
                        <div className="flex gap-8 xl:gap-10 2xl:gap-12 pb-8">
                            {steps.map((step, i) => (
                                <div
                                    key={i}
                                    className={`flex-none w-[280px] sm:w-[300px] md:w-[320px] lg:w-[350px] xl:w-[400px] 2xl:w-[440px] ${
                                        i === steps.length - 1 ? "pr-6" : ""
                                    }`}
                                >
                                    {/* Dot */}
                                    <div className="w-6 h-6 xl:w-7 xl:h-7 2xl:w-8 2xl:h-8 rounded-full bg-primary relative z-10 mb-8" />

                                    {/* Title */}
                                    <h3 className="font-display font-thin text-primary leading-[100%] mb-3 text-[26px] sm:text-[28px] md:text-[30px] lg:text-[30px] xl:text-[36px] 2xl:text-[40px]">
                                        {step.number}: {step.title}
                                    </h3>

                                    {/* Lead */}
                                    <p className="font-body font-light italic text-primary leading-[100%] mb-4 text-[18px] sm:text-[19px] md:text-[20px] lg:text-[20px] xl:text-[22px] 2xl:text-[24px]">
                                        {step.lead}
                                    </p>

                                    {/* Description */}
                                    <p className="font-body font-light text-primary text-[16px] leading-[24px] sm:text-[17px] sm:leading-[26px] md:text-[18px] md:leading-[28px] lg:text-[18px] lg:leading-[24px] xl:text-[20px] xl:leading-[30px] 2xl:text-[22px] 2xl:leading-[32px]">
                                        {step.description}
                                    </p>
                                </div>
                            ))}
                        </div>
                    </div>
                </div>
            </div>
        </section>
    );
}