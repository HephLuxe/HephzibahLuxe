"use client";

import { useState } from "react";
import Image from "next/image";
import Link from "next/link";
import FloatingInput from "@/components/ui/FloatingInput";
import FloatingSelect from "@/components/ui/FloatingSelect";
import FloatingTextarea from "@/components/ui/FloatingTextarea";
import CountryPicker from "@/components/ui/CountryPicker";
import DateRangePicker from "@/components/ui/DateRangePicker";
import { ApiError, apiFetch, GENERIC_ERROR_MESSAGE } from "@/lib/client-api";

interface InquiryFormProps {
  onSubmitted: (firstName: string, email: string) => void;
}

// Display label -> backend value. Values must match the backend choices exactly:
// InquiryForm.CONTACT_MODE and events.Event.EVENT_TYPE (backend/apps/...).
const CONTACT_MODE_OPTIONS: Record<string, string> = {
  "Email Address": "Email",
  "Phone Number": "Phone Number",
};

const EVENT_TYPE_OPTIONS: Record<string, string> = {
  Wedding: "Wedding",
  Birthday: "Birthday",
  "Corporate Event": "Corporate",
  "Social Event (e.g., Proposals, Naming Ceremonies, Private Dinners, etc.)": "Social Events",
  Other: "Others",
};

// Labels used in the error summary, keyed by backend field name.
const FIELD_LABELS: Record<string, string> = {
  first_name: "First name",
  last_name: "Last name",
  email: "Email address",
  phone_number: "Phone number",
  contact_mode: "Preferred method of contact",
  event_type: "Event type",
  preferred_start_date: "Start date",
  preferred_end_date: "End date",
  desired_location: "Location",
  budget: "Budget",
  details: "Event details",
};

type SubmitError = { message: string; fieldErrors: { field: string; messages: string[] }[] };

// YYYY-MM-DD from the LOCAL calendar date. toISOString() converts to UTC first,
// which moves the date by a day for anyone east or west of UTC near midnight.
function toLocalISODate(date: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

// The backend stores phone_number in a CharField(max_length=20) as
// `${dialCode} ${phone}`, so the number gets whatever the dial code and the
// joining space leave over (15 for "+234").
const PHONE_MAX_TOTAL = 20;

function phoneMaxLength(dialCode: string): number {
  return PHONE_MAX_TOTAL - dialCode.length - 1;
}

// Digits and spaces only, cut to the room left beside the dial code. Not a
// native maxLength: that truncates a pasted "803-123-4567" before the dashes
// are stripped, losing digits.
function sanitizePhone(value: string, dialCode: string): string {
  return value.replace(/[^\d ]/g, "").slice(0, phoneMaxLength(dialCode));
}

// The backend stores budget as a single decimal (max_digits=14,
// decimal_places=2), so the field takes whole naira only: at most 12 digits.
const BUDGET_MAX_DIGITS = 12;

function formatBudget(digits: string): string {
  return digits.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

// Position in `formatted` just after its `digitCount`-th digit.
function caretAfterDigits(formatted: string, digitCount: number): number {
  if (digitCount <= 0) return 0;
  let seen = 0;
  for (let i = 0; i < formatted.length; i++) {
    if (/\d/.test(formatted[i]) && ++seen === digitCount) return i + 1;
  }
  return formatted.length;
}

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
  const [budget, setBudget] = useState(""); // digits only, e.g. "5000000"
  const [details, setDetails] = useState("");

  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<SubmitError | null>(null);

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

  // Keeps only digits, shows them with thousands separators, and puts the caret
  // back after the same digit it followed before reformatting.
  function handleBudgetChange(e: React.ChangeEvent<HTMLInputElement>) {
    const input = e.target;
    // Whole naira only: drop a trailing fraction (e.g. a pasted "1,250,000.00")
    // so its digits are not read as extra naira.
    const raw = input.value.replace(/\.\d{0,2}(?=\D*$)/, "");
    const caret = Math.min(input.selectionStart ?? raw.length, raw.length);
    let digits = raw.replace(/\D/g, "");
    let digitsBefore = raw.slice(0, caret).replace(/\D/g, "").length;

    // Backspace over a comma would otherwise do nothing: drop the digit before it.
    const inputType = (e.nativeEvent as InputEvent).inputType;
    if (inputType === "deleteContentBackward" && digits === budget && digitsBefore > 0) {
      digits = digits.slice(0, digitsBefore - 1) + digits.slice(digitsBefore);
      digitsBefore -= 1;
    }

    const trimmed = digits.replace(/^0+(?=\d)/, "");
    digitsBefore = Math.max(digitsBefore - (digits.length - trimmed.length), 0);
    let next = trimmed;
    if (next.length > BUDGET_MAX_DIGITS) {
      // Over the limit (typing or pasting): keep what was there, caret where it was.
      digitsBefore = Math.max(digitsBefore - (next.length - budget.length), 0);
      next = budget;
    }

    setBudget(next);
    const nextCaret = caretAfterDigits(formatBudget(next), digitsBefore);
    requestAnimationFrame(() => {
      if (document.activeElement === input) input.setSelectionRange(nextCaret, nextCaret);
    });
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!allFilled || submitting || !startDate) return;

    setSubmitting(true);
    setSubmitError(null);

    // A single-day event has no end date in the picker; the backend requires one.
    const start = toLocalISODate(startDate);
    const end = endDate ? toLocalISODate(endDate) : start;

    const payload = {
      first_name: firstName.trim(),
      last_name: lastName.trim(),
      email: email.trim(),
      phone_number: `${dialCode} ${phone.trim()}`,
      contact_mode: CONTACT_MODE_OPTIONS[contactMethod],
      event_type: EVENT_TYPE_OPTIONS[eventType],
      preferred_start_date: start,
      preferred_end_date: end,
      desired_location: location.trim(),
      ...(budget ? { budget } : {}),
      details,
    };

    try {
      await apiFetch("/api/v1/inquiries/", { method: "POST", json: payload });
      onSubmitted(firstName, email);
    } catch (err) {
      if (err instanceof ApiError) {
        const fieldErrors = Object.entries(err.fieldErrors).map(([field, messages]) => ({ field, messages }));
        setSubmitError({
          message: fieldErrors.length ? "Please check the following and try again:" : err.detail,
          fieldErrors,
        });
      } else {
        setSubmitError({ message: GENERIC_ERROR_MESSAGE, fieldErrors: [] });
      }
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
            We value every inquiry and would be delighted to learn more about your celebration. From weddings and milestone birthdays to elevated corporate gatherings, we create experiences that are intentional, refined, and unforgettable. Simply share your details below, and our team will be in touch within 2-3 business days.
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
                    setPhone((p) => sanitizePhone(p, dial));
                  }}
                />
                <div className="flex-1">
                  <FloatingInput
                    label="Your Phone Number"
                    type="tel"
                    required
                    inputMode="tel"
                    value={phone}
                    onChange={(e) => setPhone(sanitizePhone(e.target.value, dialCode))}
                  />
                </div>
              </div>
              <FloatingSelect
                label="Your Preferred Method of Contact"
                required
                options={Object.keys(CONTACT_MODE_OPTIONS)}
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
                options={Object.keys(EVENT_TYPE_OPTIONS)}
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
                minDate={new Date()}
              />
              <FloatingInput
                label="What Is Your Desired Location (State, Country)?"
                required
                value={location}
                onChange={(e) => setLocation(e.target.value)}
              />
              <FloatingInput
                label="What Is Your Budget?"
                required
                prefix="₦"
                inputMode="numeric"
                autoComplete="off"
                value={formatBudget(budget)}
                onChange={handleBudgetChange}
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
            <div role="alert" className={`${submitError ? "mt-3 mb-1" : ""} font-sans font-light text-primary text-[13px] leading-[20px] sm:text-[14px] sm:leading-[22px]`}>
              {submitError && (
                <>
                  <p>{submitError.message}</p>
                  {submitError.fieldErrors.length > 0 && (
                    <ul className="mt-1 list-disc pl-5">
                      {submitError.fieldErrors.map(({ field, messages }) => (
                        <li key={field}>
                          {FIELD_LABELS[field] ? `${FIELD_LABELS[field]}: ` : ""}
                          {messages.join(" ")}
                        </li>
                      ))}
                    </ul>
                  )}
                </>
              )}
            </div>

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