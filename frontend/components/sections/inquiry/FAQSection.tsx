"use client";

import { useState } from "react";

interface FAQ {
  question: string;
  answer: React.ReactNode;
}

const FAQS: FAQ[] = [
  {
    question: "What types of events do you plan?",
    answer: (
      <>
        <p>
          At Hephzibah Luxe, we plan a wide range of celebrations and events. From weddings and milestone birthdays to intimate dinners, naming ceremonies, private soirées, luxury picnics, and corporate gatherings, each experience is approached with the same level of care, intention, and refined execution.
        </p>
        <p>
          Whether your event is intimate or large in scale, our approach remains the same — creating thoughtful, beautifully considered experiences that reflect your vision with elegance and ease.
        </p>
      </>
    ),
  },
  {
    question: "What Planning Options Do You Offer, and How Do I Choose the One That Fits My Event Best?",
    answer: (
      <>
        <p>
          We offer three core levels of service across all event types — <strong className="font-normal">Full Planning, Partial Planning, and Coordination</strong>. Each provides a different level of support depending on where you are in your planning journey and how involved you&apos;d like us to be.
        </p>
        <p>
          If you&apos;re unsure which option is right for you, we&apos;ll guide you. During your discovery conversation, we&apos;ll learn more about your event, timeline, and planning needs before recommending the level of support best suited to your celebration.
        </p>
        <p>
          Because every event is unique, we also offer bespoke packages tailored to clients with specific requirements. Whether you&apos;re looking for comprehensive planning, selective guidance, or seamless event-day coordination, we&apos;ll create an experience that feels thoughtfully aligned with your vision.
        </p>
      </>
    ),
  },
  {
    question: "Do You Manage the Event Design Process, Including Décor, Styling, and Creative Direction?",
    answer: (
      <>
        <p>
          Yes — event design is an integral part of what we do. At Hephzibah Luxe, we lead the creative direction of your event through visual mood boards, colour palettes, style curation, and a cohesive design vision. Every decision is thoughtfully guided to ensure your celebration feels intentional, refined, and beautifully aligned.
        </p>
        <p>
          We believe exceptional events are brought to life by specialists. For décor, florals, rentals, stationery, lighting, and production, we collaborate with a trusted network of expert vendors, each selected for their craftsmanship and attention to detail. This allows us to deliver exceptional quality while ensuring every element is executed seamlessly.
        </p>
        <p>
          Throughout the process, we remain closely involved — overseeing design consistency, managing vendor collaboration, and ensuring every detail reflects the vision we&apos;ve created together. The result is a cohesive, beautifully executed celebration without the complexity of coordinating multiple creative partners yourself.
        </p>
      </>
    ),
  },
  {
    question: "How Far in Advance Should We Reach Out to Begin the Planning Process?",
    answer: (
      <>
        <p>
          We welcome inquiries at any stage of your planning journey — whether you&apos;re planning well in advance, just getting started, or working within a shorter timeline. For the most seamless experience, however, we encourage clients to reach out as early as possible. This gives us the time to secure trusted vendors, thoughtfully develop your vision, and create a well-paced planning experience from beginning to end.
        </p>
        <p className="font-normal">Our Recommendations:</p>
        <ul className="list-disc pl-6 space-y-1">
          <li>
            <strong className="font-normal">Weddings:</strong> 4–6 months in advance
          </li>
          <li>
            <strong className="font-normal">Social &amp; Private Events:</strong> 2–3 months in advance
          </li>
          <li>
            <strong className="font-normal">Corporate Events:</strong> Timelines vary, but earlier is always recommended
          </li>
        </ul>
        <p>
          Regardless of when you reach out, our role remains the same — to bring clarity, intention, and ease to every stage of your planning journey.
        </p>
      </>
    ),
  },
  {
    question: "How Does Budgeting Work with Hephzibah Luxe, and Do You Accept Events with Varying Investment Levels?",
    answer: (
      <>
        <p>
          Yes — we thoughtfully guide clients across a wide range of investment levels. Every event, whether intimate or grand, deserves intentional planning, refined design, and exceptional attention to detail. Our approach is to help you make the most of your investment, creating an experience that feels elevated while remaining aligned with your priorities.
        </p>
        <p>
          Throughout the planning process, we provide honest guidance, clear recommendations, and thoughtful solutions that maximise both beauty and value. During your discovery conversation, we&apos;ll explore what&apos;s most important to you, recommend the most appropriate level of support, and tailor our services to suit your vision and investment.
        </p>
      </>
    ),
  },
  {
    question: "What Does Day-of Presence and Support Look Like?",
    answer: (
      <>
        <p>
          From the first arrival to the final farewell, we&apos;re by your side. Whether it&apos;s a wedding, milestone birthday, naming ceremony, private dinner, or corporate gathering, your lead planner remains onsite throughout the celebration, overseeing every detail to ensure everything unfolds seamlessly from setup through to the final wrap-up.
        </p>
        <p>
          Unlike many planners, we don&apos;t work to fixed cut-off hours. Your lead planner remains present for as long as your celebration requires, providing uninterrupted support so you can be fully present and enjoy every moment with complete peace of mind.
        </p>
      </>
    ),
  },
];

export default function FAQSection() {
  const [openIndex, setOpenIndex] = useState<number | null>(null);

  const toggle = (i: number) => {
    setOpenIndex((current) => (current === i ? null : i));
  };

  return (
    <section id="faqs" className="bg-primary text-background scroll-mt-24">
      <div className="px-4 sm:px-6 md:px-7 lg:px-8 xl:px-12 2xl:px-16 py-16 sm:py-20 md:py-24 lg:py-28 xl:py-32">
        <div className="max-w-6xl xl:max-w-7xl 2xl:max-w-[1500px] mx-auto">
          {/* Title — Editor's Note Hairline (font-display + font-thin), 48px target */}
          <h2 className="font-display font-thin text-background text-center text-[28px] leading-[100%] sm:text-[32px] md:text-[36px] lg:text-[40px] xl:text-[44px] 2xl:text-[48px]">
            Frequently Asked Questions
          </h2>

          {/* Accordion list */}
          <div className="mt-10 sm:mt-12 md:mt-14 lg:mt-16 xl:mt-20 border-t border-background/30">
            {FAQS.map((faq, i) => {
              const isOpen = openIndex === i;
              return (
                <div key={i} className="border-b border-background/30">
                  <button
                    type="button"
                    onClick={() => toggle(i)}
                    aria-expanded={isOpen}
                    aria-controls={`faq-panel-${i}`}
                    className="w-full flex items-start justify-between gap-6 py-6 sm:py-7 md:py-8 text-left"
                  >
                    {/* Question — Newsreader regular (font-body + font-normal), 19px target */}
                    <span className="font-body font-normal text-background text-[15px] leading-[22px] sm:text-[16px] sm:leading-[24px] md:text-[17px] md:leading-[26px] lg:text-[17px] lg:leading-[26px] xl:text-[18px] xl:leading-[28px] 2xl:text-[19px] 2xl:leading-[28px]">
                      {faq.question}
                    </span>

                    {/* +/− icon — no border */}
                    <span
                      aria-hidden="true"
                      className="flex-shrink-0 mt-1 relative w-5 h-5 sm:w-6 sm:h-6 flex items-center justify-center"
                    >
                      <span className="absolute w-4 sm:w-5 h-px bg-background" />
                      <span
                        className={`absolute w-px h-4 sm:h-5 bg-background transition-opacity ${
                          isOpen ? "opacity-0" : "opacity-100"
                        }`}
                      />
                    </span>
                  </button>

                  {/* Answer panel */}
                  <div
                    id={`faq-panel-${i}`}
                    role="region"
                    className={`grid transition-[grid-template-rows] duration-300 ease-out ${
                      isOpen ? "grid-rows-[1fr]" : "grid-rows-[0fr]"
                    }`}
                  >
                    <div className="overflow-hidden">
                      {/* Answer — Lato light (font-sans + font-light), 16px target */}
                      <div className="pb-8 sm:pb-9 md:pb-10 pr-8 sm:pr-12 font-sans font-light text-background/90 text-[13px] leading-[22px] sm:text-[14px] sm:leading-[24px] md:text-[14px] md:leading-[24px] lg:text-[14px] lg:leading-[25px] xl:text-[15px] xl:leading-[26px] 2xl:text-[16px] 2xl:leading-[28px] space-y-4">
                        {faq.answer}
                      </div>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </section>
  );
}