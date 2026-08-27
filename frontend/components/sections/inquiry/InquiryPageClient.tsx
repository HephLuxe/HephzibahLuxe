"use client";

import { useState } from "react";
import Image from "next/image";
import Link from "next/link";
import InquiryHero from "@/components/sections/inquiry/InquiryHero";
import InquiryForm from "@/components/sections/inquiry/InquiryForm";
import FAQSection from "@/components/sections/inquiry/FAQSection";
import Instagram from "@/components/sections/Instagram";

export default function InquiryPageClient() {
  const [submitted, setSubmitted] = useState(false);
  const [submittedName, setSubmittedName] = useState("");
  const [submittedEmail, setSubmittedEmail] = useState("");

  function handleSubmitted(firstName: string, email: string) {
    setSubmittedName(firstName);
    setSubmittedEmail(email);
    setSubmitted(true);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  if (submitted) {
    return (
      <section className="bg-background text-primary min-h-screen flex items-center">
        <div className="max-w-2xl xl:max-w-3xl 2xl:max-w-4xl mx-auto text-center px-6 sm:px-8 md:px-10 lg:px-12 xl:px-16 2xl:px-20 py-16 sm:py-20 md:py-24 lg:py-24 xl:py-28 2xl:py-32">
          <div className="flex justify-center">
            <Image
              src="/icons/success.svg"
              alt=""
              width={446}
              height={325}
              className="w-full max-w-[320px] sm:max-w-[380px] md:max-w-[420px] lg:max-w-[446px] xl:max-w-[500px] 2xl:max-w-[560px] h-auto"
              priority
            />
          </div>

          <h2 className="mt-6 sm:mt-7 md:mt-8 font-display font-thin text-primary text-center text-[30px] leading-[38px] sm:text-[34px] sm:leading-[42px] md:text-[40px] md:leading-[48px] lg:text-[44px] lg:leading-[52px] xl:text-[52px] xl:leading-[60px] 2xl:text-[60px] 2xl:leading-[68px]">
            Hi <span className="italic">{submittedName}</span>, We have received your inquiry!
          </h2>

          <p className="mt-8 sm:mt-9 md:mt-10 font-sans font-light text-primary text-center text-[16px] leading-[25px] sm:text-[17px] sm:leading-[27px] md:text-[18px] md:leading-[28px] lg:text-[18px] lg:leading-[29px] xl:text-[20px] xl:leading-[32px] 2xl:text-[22px] 2xl:leading-[34px] max-w-[640px] xl:max-w-[760px] 2xl:max-w-[860px] mx-auto">
            Thank you for reaching out to Hephzibah Luxe. We've received your inquiry, and our team is thoughtfully reviewing the details. We'll be in touch within{" "}
            <span className="font-bold">2-3 business days</span> to continue the conversation. We look forward to learning more about your event and exploring how we can bring your vision to life.
          </p>

          {/* <p className="mt-6 sm:mt-7 font-sans font-light text-primary text-center text-[16px] leading-[25px] sm:text-[17px] sm:leading-[27px] md:text-[18px] md:leading-[28px] lg:text-[18px] lg:leading-[29px] xl:text-[20px] xl:leading-[32px] 2xl:text-[22px] 2xl:leading-[34px]">
            A confirmation email has been sent to - {submittedEmail}.
          </p> */}

          <div className="mt-10 sm:mt-11 md:mt-12 xl:mt-14 flex justify-center">
            <Link
              href="/portfolio"
              className="inline-flex items-center justify-center bg-secondary text-background px-10 py-4 xl:px-12 xl:py-5 2xl:px-14 2xl:py-6 font-sans font-light tracking-[0.15em] uppercase hover:opacity-90 transition-opacity text-[14px] sm:text-[15px] md:text-[16px] lg:text-[14px] xl:text-[16px] 2xl:text-[18px]"
            >
              View Our Past Projects
            </Link>
          </div>
        </div>
      </section>
    );
  }

  return (
    <>
      <InquiryHero />
      <InquiryForm onSubmitted={handleSubmitted} />
      <FAQSection />
      <Instagram />
    </>
  );
}