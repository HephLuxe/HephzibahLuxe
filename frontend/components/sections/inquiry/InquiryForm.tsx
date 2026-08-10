"use client";

import { useState } from "react";
import Image from "next/image";
import Link from "next/link";
import FloatingInput from "@/components/ui/FloatingInput";
import FloatingSelect from "@/components/ui/FloatingSelect";
import FloatingTextarea from "@/components/ui/FloatingTextarea";
import CountryPicker from "@/components/ui/CountryPicker";
import DateRangePicker from "@/components/ui/DateRangePicker";

interface InquiryFormProps {
  onSubmitted: (firstName: string, email: string) => void;
}

const FORMSPREE_ENDPOINT = process.env.NEXT_PUBLIC_FORMSPREE_ENDPOINT;

export default function InquiryForm({ onSubmitted }: InquiryFormProps) {
  const [firstName, setFirstName] = useState("");
  const [lastName, setLastName] = useState("");
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");
  const [contactMethod, setContactMethod] = useState("");
  const [countryCode, setCountryCode] = useState("NG");
  const [dialCode, setDialCode] = useState("+234");

  const [eventType, setEventType] = useState("");
  const [startDate, setStartDate] = useState<Date | null>(null);
  const [endDate, setEndDate] = useState<Date | null>(null);
  const [location, setLocation] = useState("");
  const [budget, setBudget] = useState("");
  const [details, setDetails] = useState("");

  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const allFilled =
    firstName &&
    lastName &&
    email &&
    phone &&
    contactMethod &&
    eventType &&
    startDate &&
    location &&
    budget &&
    details;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!allFilled || submitting) return;

    if (!FORMSPREE_ENDPOINT) {
      setSubmitError("Something went wrong submitting your inquiry. Please try again.");
      return;
    }

    setSubmitting(true);
    setSubmitError(null);

    // Payload sent to Formspree. Field names become the labels in your Formspree dashboard and email notifications.
    const payload = {
      "First Name": firstName,
      "Last Name": lastName,
      "Email": email,
      "Phone": `${dialCode} ${phone}`,
      "Country": countryCode,
      "Preferred Contact Method": contactMethod,
      "Event Type": eventType,
      "Preferred Start Date": startDate ? startDate.toISOString().split("T")[0] : "",
      "Preferred End Date": endDate ? endDate.toISOString().split("T")[0] : "",
      "Location": location,
      "Budget Range": budget,
      "Additional Details": details,
      // Formspree special fields
      _subject: `New inquiry from ${firstName} ${lastName} — ${eventType}`,
      _replyto: email,
    };

    try {
      const response = await fetch(FORMSPREE_ENDPOINT, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
        },
        body: JSON.stringify(payload),
      });

      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        const message =
          data?.errors?.[0]?.message ||
          "Something went wrong submitting your inquiry. Please try again.";
        throw new Error(message);
      }

      onSubmitted(firstName, email);
    } catch (err) {
      setSubmitError(
        err instanceof Error
          ? err.message
          : "Something went wrong submitting your inquiry. Please try again."
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section className="bg-background text-primary">
      <div className="px-4 sm:px-6 md:px-7 lg:px-8 xl:px-12 2xl:px-16 py-8 sm:py-10 md:py-10 lg:py-10 xl:py-12 2xl:py-32">
        <form onSubmit={handleSubmit} className="max-w-6xl xl:max-w-7xl 2xl:max-w-[1500px] mx-auto">
          <h2 className="font-display font-thin italic tracking-[0.03em] text-primary text-[48px] leading-[100%] sm:text-[60px] md:text-[76px] lg:text-[96px] xl:text-[112px] 2xl:text-[128px]">
            To Hephzibah Luxe:
          </h2>

          <p className="mt-8 sm:mt-9 md:mt-10 font-body font-light text-primary text-[16px] leading-[24px] sm:text-[16px] sm:leading-[26px] md:text-[18px] md:leading-[28px] lg:text-[16px] lg:leading-[25px] xl:text-[18px] xl:leading-[28px] 2xl:text-[20px] 2xl:leading-[30px] max-w-[1100px] xl:max-w-[1200px] 2xl:max-w-[1320px]">
            We value every inquiry and would be delighted to learn more about your celebration. From weddings and milestone birthdays to elevated corporate gatherings, we create experiences that are intentional, refined, and unforgettable. Simply share your details below, and our team will be in touch within 1–2 business days.
          </p>

          <p className="mt-4 font-body font-light text-primary text-[16px] leading-[24px] sm:text-[16px] sm:leading-[26px] md:text-[18px] md:leading-[28px] lg:text-[16px] lg:leading-[25px] xl:text-[18px] xl:leading-[28px] 2xl:text-[20px] 2xl:leading-[30px]">
            Have a quick question? View our{" "}
            <Link
              href="#faqs"
              className="font-semibold underline"
              onClick={(e) => {
                e.preventDefault();
                document.getElementById("faqs")?.scrollIntoView({ behavior: "smooth" });
              }}
            >
              FAQs
            </Link>{" "}
            below before submitting your inquiry.
          </p>

          {/* Section: Tell Us about Yourself */}
          <div className="mt-16 sm:mt-16 md:mt-20 lg:mt-20 xl:mt-24">
            <h3 className="font-body font-light italic tracking-[-0.01em] text-primary text-[22px] leading-[100%] sm:text-[24px] md:text-[26px] lg:text-[28px] xl:text-[32px] 2xl:text-[36px] pb-4 lg:pb-5 xl:pb-6 border-b border-primary/30">
              Tell Us About Yourself
            </h3>

            <p className="mt-6 sm:mt-7 md:mt-8 font-body font-light text-primary text-[16px] leading-[22px] sm:text-[15px] sm:leading-[24px] md:text-[18px] md:leading-[28px] lg:text-[16px] lg:leading-[25px] xl:text-[18px] xl:leading-[28px] 2xl:text-[20px] 2xl:leading-[30px] max-w-[900px] xl:max-w-[1040px] 2xl:max-w-[1160px]">
              Let&apos;s begin with the essentials—how we can best reach you. Your contact details allow us to begin the conversation and respond with the care, attention, and thoughtfulness your celebration deserves.
            </p>

            <div className="mt-8 sm:mt-9 md:mt-10 grid grid-cols-1 gap-5">
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
                <FloatingInput
                  label="First Name"
                  required
                  value={firstName}
                  onChange={(e) => setFirstName(e.target.value)}
                />
                <FloatingInput
                  label="Last Name"
                  required
                  value={lastName}
                  onChange={(e) => setLastName(e.target.value)}
                />
              </div>
              <FloatingInput
                label="Your Email Address"
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
              <div className="flex gap-4">
                <CountryPicker
                  value={countryCode}
                  onChange={(code, dial) => {
                    setCountryCode(code);
                    setDialCode(dial);
                  }}
                />
                <div className="flex-1">
                  <FloatingInput
                    label="Your Phone Number"
                    type="tel"
                    required
                    value={phone}
                    onChange={(e) => setPhone(e.target.value)}
                  />
                </div>
              </div>
              <FloatingSelect
                label="Your Preferred Method of Contact"
                required
                options={["Email Address", "Phone Number"]}
                value={contactMethod}
                onChange={setContactMethod}
              />
            </div>
          </div>

          {/* Section: Tell Us about Your Event */}
          <div className="mt-16 sm:mt-16 md:mt-20 lg:mt-20 xl:mt-24">
            <h3 className="font-body font-light italic tracking-[-0.01em] text-primary text-[22px] leading-[100%] sm:text-[24px] md:text-[26px] lg:text-[28px] xl:text-[32px] 2xl:text-[36px] pb-4 lg:pb-5 xl:pb-6 border-b border-primary/30">
              Tell Us About Your Event
            </h3>

            <p className="mt-6 sm:mt-7 md:mt-8 font-body font-light text-primary text-[16px] leading-[22px] sm:text-[15px] sm:leading-[24px] md:text-[18px] md:leading-[28px] lg:text-[16px] lg:leading-[25px] xl:text-[18px] xl:leading-[28px] 2xl:text-[20px] 2xl:leading-[30px] max-w-[900px] xl:max-w-[1040px] 2xl:max-w-[1160px]">
              Now, tell us a little about your event—whether it&apos;s a wedding, milestone birthday, corporate gathering, or private celebration. These details help us understand your vision and begin shaping an experience that feels intentional, refined, and uniquely yours.
            </p>

            <div className="mt-8 sm:mt-9 md:mt-10 grid grid-cols-1 gap-5">
              <FloatingSelect
                label="What Type of Event Are You Planning?"
                required
                options={[
                  "Wedding",
                  "Birthday",
                  "Corporate Event",
                  "Other Social Event (e.g., Proposals, Naming Ceremonies, Private Dinners, etc.)",
                ]}
                value={eventType}
                onChange={setEventType}
              />
              <DateRangePicker
                label="What Are Your Preferred Dates? "
                required
                startDate={startDate}
                endDate={endDate}
                onStartDateChange={setStartDate}
                onEndDateChange={setEndDate}
              />
              <FloatingInput
                label="What Is Your Desired Location (State, Country)?"
                required
                value={location}
                onChange={(e) => setLocation(e.target.value)}
              />
              <FloatingInput
                label="What Is Your Budget Range?"
                required
                value={budget}
                onChange={(e) => setBudget(e.target.value)}
              />
              <FloatingTextarea
                label="Share a few details about your event, or add anything else you'd like us to know."
                required
                value={details}
                onChange={(e) => setDetails(e.target.value)}
                placeholder="e.g. We're expecting around 120 guests and are looking for full planning, design, and coordination from start to finish. Or we already have our venue and vendors booked but need support with day-of coordination."
              />
            </div>

            {/* Error message */}
            {submitError && (
              <p className="mt-3 mb-1 font-sans font-light text-primary text-[13px] leading-[20px] sm:text-[14px] sm:leading-[22px]">
                Something went wrong submitting your inquiry. Please try again.
              </p>
            )}

            {/* Bottom row — row layout starts at md (iPad) instead of lg */}
            <div className="mt-8 sm:mt-10 md:mt-12 flex flex-col md:flex-row md:items-end md:justify-between gap-6">
              <p className="font-sans font-light tracking-[0.05em] text-primary text-[12px] leading-[20px] sm:text-[13px] sm:leading-[22px] md:text-[14px] md:leading-[24px] lg:text-[14px] lg:leading-[25px] xl:text-[15px] xl:leading-[26px] 2xl:text-[16px] 2xl:leading-[28px] max-w-[420px] md:max-w-[340px] lg:max-w-[420px] xl:max-w-[480px] 2xl:max-w-[540px]">
                This form is protected by reCAPTCHA and the Google{" "}
                <Link href="#" className="underline">
                  Privacy Policy
                </Link>{" "}
                and{" "}
                <Link href="#" className="underline">
                  Terms of Service
                </Link>{" "}
                apply.
              </p>

              {/* Wrapper right-aligns the button when the layout is stacked (mobile only now). On md+ the outer flex-row handles alignment. */}
              <div className="flex justify-end md:block">
                <button
                  type="submit"
                  disabled={!allFilled || submitting}
                  className={`group inline-flex items-center justify-between gap-6 px-8 py-4 xl:px-10 xl:py-5 2xl:px-12 2xl:py-6 transition-colors ${allFilled && !submitting ? "bg-secondary hover:opacity-90" : "bg-[#ABABAB]"
                    }`}
                >
                  <span className="font-sans font-light tracking-[0.15em] uppercase text-background text-[16px] sm:text-[17px] md:text-[18px] lg:text-[16px] xl:text-[18px] 2xl:text-[20px]">
                    {submitting ? "Sending…" : "Send us a message"}
                  </span>
                  <Image
                    src="/icons/whitebuttonarrow.svg"
                    alt=""
                    width={24}
                    height={24}
                    className="w-[20px] h-[20px] xl:w-[22px] xl:h-[22px] 2xl:w-[24px] 2xl:h-[24px] transition-transform group-hover:translate-x-1"
                  />
                </button>
              </div>
            </div>
          </div>
        </form>
      </div>
    </section>
  );
}




// {/* Bottom row */}
//             <div className="mt-8 sm:mt-10 md:mt-12 flex flex-col lg:flex-row lg:items-end lg:justify-between gap-6">
//               <p className="font-sans font-light tracking-[0.05em] text-primary text-[12px] leading-[20px] sm:text-[13px] sm:leading-[22px] md:text-[14px] md:leading-[24px] lg:text-[14px] lg:leading-[25px] xl:text-[15px] xl:leading-[26px] 2xl:text-[16px] 2xl:leading-[28px] max-w-[420px] xl:max-w-[480px] 2xl:max-w-[540px]">
//                 This form is protected by reCAPTCHA and the Google{" "}
//                 <Link href="#" className="underline">
//                   Privacy Policy
//                 </Link>{" "}
//                 and{" "}
//                 <Link href="#" className="underline">
//                   Terms of Service
//                 </Link>{" "}
//                 apply.
//               </p>

//               {/* Button wrapper — right-aligns the button on mobile/iPad (flex-col). On lg+ the outer flex-row handles alignment via justify-between, so this wrapper is transparent to layout. */}
//               <div className="flex justify-end lg:block">
//                 <button
//                   type="submit"
//                   disabled={!allFilled || submitting}
//                   className={`group inline-flex items-center justify-between gap-6 px-8 py-4 xl:px-10 xl:py-5 2xl:px-12 2xl:py-6 transition-colors ${allFilled && !submitting ? "bg-secondary hover:opacity-90" : "bg-[#778472]"
//                     }`}
//                 >
//                   <span className="font-sans font-light tracking-[0.15em] uppercase text-background text-[16px] sm:text-[17px] md:text-[18px] lg:text-[16px] xl:text-[18px] 2xl:text-[20px]">
//                     {submitting ? "Sending…" : "Send us a message"}
//                   </span>
//                   <Image
//                     src="/icons/whitebuttonarrow.svg"
//                     alt=""
//                     width={24}
//                     height={24}
//                     className="w-[20px] h-[20px] xl:w-[22px] xl:h-[22px] 2xl:w-[24px] 2xl:h-[24px] transition-transform group-hover:translate-x-1"
//                   />
//                 </button>
//               </div>
//             </div>