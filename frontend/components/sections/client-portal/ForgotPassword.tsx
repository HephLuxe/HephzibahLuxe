"use client";

import { useState, useEffect, useRef, KeyboardEvent, ClipboardEvent } from "react";
import Image from "next/image";
import Link from "next/link";
import FloatingInput from "@/components/ui/FloatingInput";
import FloatingPasswordInput from "@/components/ui/FloatingPasswordInput";
import ClientPortalLayout from "./ClientPortalLayout";
import PasswordRuleList from "./PasswordRuleList";
import { ApiError, apiFetch, GENERIC_ERROR_MESSAGE } from "@/lib/client-api";
import { checkPasswordRules } from "@/lib/password-rules";

// Matches the backend's 6-digit reset code (PasswordResetVerifySerializer).
const CODE_LENGTH = 6;
const RESEND_SECONDS = 10;
const emptyDigits = () => Array<string>(CODE_LENGTH).fill("");

type Step = "email" | "code" | "reset" | "success";

function messageFrom(err: unknown, field?: string): string {
    if (!(err instanceof ApiError)) return GENERIC_ERROR_MESSAGE;
    if (field && err.fieldErrors[field]?.length) return err.fieldErrors[field].join(" ");
    const fieldMessages = Object.values(err.fieldErrors).flat();
    return fieldMessages.length ? fieldMessages.join(" ") : err.detail;
}

function requestResetCode(email: string): Promise<unknown> {
    return apiFetch("/api/v1/auth/password-reset/request/", { method: "POST", json: { email: email.trim() } });
}

export default function ForgotPassword() {
    const [step, setStep] = useState<Step>("email");
    const [submitting, setSubmitting] = useState(false);

    const [email, setEmail] = useState("");
    const [emailError, setEmailError] = useState<string | null>(null);

    const [digits, setDigits] = useState<string[]>(emptyDigits);
    const [codeError, setCodeError] = useState<string | null>(null);
    const [secondsLeft, setSecondsLeft] = useState(RESEND_SECONDS);
    const inputRefs = useRef<Array<HTMLInputElement | null>>([]);

    const [newPassword, setNewPassword] = useState("");
    const [confirmPassword, setConfirmPassword] = useState("");
    const [resetError, setResetError] = useState<string | null>(null);

    const { allMet: allRulesMet } = checkPasswordRules(newPassword);
    const passwordsMatch = newPassword.length > 0 && newPassword === confirmPassword;
    const canReset = allRulesMet && passwordsMatch && !submitting;

    useEffect(() => {
        if (step !== "code") return;
        if (secondsLeft <= 0) return;
        const id = setInterval(() => setSecondsLeft((s) => Math.max(0, s - 1)), 1000);
        return () => clearInterval(id);
    }, [step, secondsLeft]);

    // The backend answers the same way whether or not the email has an account,
    // so this always moves on to the code step with a neutral message. Only a
    // network failure or rate limit keeps the user here.
    async function handleEmailSubmit(e: React.FormEvent) {
        e.preventDefault();
        if (!email || submitting) return;
        setSubmitting(true);
        setEmailError(null);
        try {
            await requestResetCode(email);
            setStep("code");
            setSecondsLeft(RESEND_SECONDS);
            setDigits(emptyDigits());
            setCodeError(null);
        } catch (err) {
            setEmailError(messageFrom(err, "email"));
        } finally {
            setSubmitting(false);
        }
    }

    function handleEmailChange(e: React.ChangeEvent<HTMLInputElement>) {
        setEmail(e.target.value);
        if (emailError) setEmailError(null);
    }

    const code = digits.join("");
    const canVerify = code.length === CODE_LENGTH && !submitting;

    function setDigitAt(i: number, value: string) {
        const next = [...digits];
        next[i] = value;
        setDigits(next);
        if (codeError) setCodeError(null);
    }

    function handleDigitChange(i: number, raw: string) {
        const cleaned = raw.replace(/\D/g, "").slice(-1);
        setDigitAt(i, cleaned);
        if (cleaned && i < CODE_LENGTH - 1) inputRefs.current[i + 1]?.focus();
    }

    function handleDigitKeyDown(i: number, e: KeyboardEvent<HTMLInputElement>) {
        if (e.key === "Backspace" && !digits[i] && i > 0) inputRefs.current[i - 1]?.focus();
        if (e.key === "ArrowLeft" && i > 0) inputRefs.current[i - 1]?.focus();
        if (e.key === "ArrowRight" && i < CODE_LENGTH - 1) inputRefs.current[i + 1]?.focus();
    }

    function handleDigitPaste(e: ClipboardEvent<HTMLInputElement>) {
        e.preventDefault();
        const pasted = e.clipboardData.getData("text").replace(/\D/g, "").slice(0, CODE_LENGTH);
        if (!pasted) return;
        const next = emptyDigits();
        for (let i = 0; i < pasted.length; i++) next[i] = pasted[i];
        setDigits(next);
        setCodeError(null);
        const lastIdx = Math.min(pasted.length, CODE_LENGTH) - 1;
        inputRefs.current[lastIdx]?.focus();
    }

    async function handleCodeSubmit(e: React.FormEvent) {
        e.preventDefault();
        if (!canVerify) return;
        setSubmitting(true);
        setCodeError(null);
        try {
            await apiFetch("/api/v1/auth/password-reset/verify/", {
                method: "POST",
                json: { email: email.trim(), code },
            });
            setStep("reset");
        } catch (err) {
            setCodeError(messageFrom(err, "code"));
        } finally {
            setSubmitting(false);
        }
    }

    async function handleResend() {
        if (secondsLeft > 0 || submitting) return;
        setSubmitting(true);
        setCodeError(null);
        try {
            await requestResetCode(email);
            setSecondsLeft(RESEND_SECONDS);
            setDigits(emptyDigits());
            inputRefs.current[0]?.focus();
        } catch (err) {
            setCodeError(messageFrom(err, "email"));
        } finally {
            setSubmitting(false);
        }
    }

    async function handleResetSubmit(e: React.FormEvent) {
        e.preventDefault();
        if (!canReset) return;
        setSubmitting(true);
        setResetError(null);
        try {
            await apiFetch("/api/v1/auth/password-reset/confirm/", {
                method: "POST",
                json: {
                    email: email.trim(),
                    code,
                    new_password: newPassword,
                    confirm_password: confirmPassword,
                },
            });
            setStep("success");
        } catch (err) {
            setResetError(messageFrom(err));
        } finally {
            setSubmitting(false);
        }
    }

    const timerLabel = `(0:${secondsLeft.toString().padStart(2, "0")})`;

    if (step === "success") return <SuccessScreen />;

    // Reusable typography classes
    const headingCls = "font-display font-thin uppercase tracking-[0.03em] leading-[100%] text-primary text-[40px] sm:text-[44px] md:text-[52px] lg:text-[52px] xl:text-[60px] 2xl:text-[68px] lg:whitespace-nowrap";
    const bodyCls = "font-body font-light text-primary text-[15px] leading-[24px] sm:text-[16px] sm:leading-[26px] md:text-[18px] md:leading-[28px] lg:text-[16px] lg:leading-[25px] xl:text-[18px] xl:leading-[28px] 2xl:text-[20px] 2xl:leading-[30px]";
    const buttonCls = "group inline-flex items-center gap-6 px-10 py-4 md:px-11 md:py-[18px] xl:px-12 xl:py-5 transition-colors";
    const buttonTextCls = "font-sans font-normal tracking-[0.15em] uppercase transition-colors text-[14px] sm:text-[15px] md:text-[16px] lg:text-[14px] xl:text-[16px] 2xl:text-[18px]";
    const buttonArrowWrapperCls = "relative inline-block w-[18px] h-[18px] md:w-[20px] md:h-[20px] xl:w-[22px] xl:h-[22px] 2xl:w-[24px] 2xl:h-[24px]";
    const footerLinkCls = "font-sans font-light leading-[100%] text-primary underline underline-offset-2 hover:opacity-70 transition-opacity text-[16px] sm:text-[17px] md:text-[18px] lg:text-[16px] xl:text-[18px] 2xl:text-[20px]";

    return (
        <ClientPortalLayout imageSide="right">
            {/* Step 1 — Email */}
            {step === "email" && (
                <form onSubmit={handleEmailSubmit}>
                    <h1 className={headingCls}>Forgot Password</h1>

                    <p className={`mt-6 sm:mt-7 md:mt-8 lg:mt-8 ${bodyCls}`}>
                        Please enter the email associated with your Hephzibah Luxe Client Portal. We will send a 6-digit code to your email.
                    </p>

                    {emailError && (
                        <div
                            className="mt-8 flex items-start gap-3 px-4 py-3 sm:py-3.5 md:py-4 lg:py-4 border border-[#D50000]"
                            style={{ backgroundColor: "rgba(213, 0, 0, 0.4)" }}
                            role="alert"
                        >
                            <Image
                                src="/icons/rederror.svg"
                                alt=""
                                width={24}
                                height={24}
                                className="w-[22px] h-[22px] md:w-[24px] md:h-[24px] flex-shrink-0 mt-0.5"
                            />
                            <p className="font-sans font-medium tracking-[0.0125em] text-primary text-[15px] leading-[24px] sm:text-[16px] sm:leading-[26px] md:text-[17px] md:leading-[28px] lg:text-[12px] lg:leading-[26px] xl:text-[15px] xl:leading-[28px] 2xl:text-[18px] 2xl:leading-[30px]">
                                {emailError}
                            </p>
                        </div>
                    )}

                    <div className={`${emailError ? "mt-6" : "mt-10"} space-y-5`}>
                        <FloatingInput
                            label="Your Email Address"
                            type="email"
                            required
                            value={email}
                            onChange={handleEmailChange}
                        />
                    </div>

                    <div className="mt-8 flex flex-col items-end gap-3">
                        <button
                            type="submit"
                            disabled={!email || submitting}
                            className={`${buttonCls} ${email && !submitting
                                ? "bg-secondary border border-secondary hover:bg-background hover:border-primary"
                                : "bg-[#A8A8A8] border border-[#A8A8A8] cursor-not-allowed"
                                }`}
                        >
                            <span className={`${buttonTextCls} ${email && !submitting ? "text-background group-hover:text-primary" : "text-background"}`}>
                                {submitting ? "Sending…" : "Send Code"}
                            </span>
                            <span className={buttonArrowWrapperCls}>
                                <Image src="/icons/whitebuttonarrow.svg" alt="" fill className={`object-contain transition-opacity ${email ? "group-hover:opacity-0" : ""}`} />
                                {email && <Image src="/icons/buttonarrow.svg" alt="" fill className="object-contain opacity-0 transition-opacity group-hover:opacity-100" />}
                            </span>
                        </button>

                        <Link href="/client-portal" className={footerLinkCls}>
                            Remember password? Login
                        </Link>
                    </div>
                </form>
            )}

            {/* Step 2 — Code */}
            {step === "code" && (
                <form onSubmit={handleCodeSubmit}>
                    <h1 className={headingCls}>Forgot Password</h1>

                    <p className={`mt-6 sm:mt-7 md:mt-8 lg:mt-8 ${bodyCls}`}>
                        If an account exists for{" "}
                        <span className="font-normal">{email}</span>, we have sent a 6-digit code to it. Please check your email.
                    </p>

                    <div className="mt-10 sm:mt-11 md:mt-12 flex justify-center gap-2 sm:gap-3 md:gap-3.5 lg:gap-3 xl:gap-4">
                        {digits.map((digit, i) => (
                            <input
                                key={i}
                                ref={(el) => {
                                    inputRefs.current[i] = el;
                                }}
                                type="text"
                                inputMode="numeric"
                                maxLength={1}
                                value={digit}
                                onChange={(e) => handleDigitChange(i, e.target.value)}
                                onKeyDown={(e) => handleDigitKeyDown(i, e)}
                                onPaste={handleDigitPaste}
                                aria-label={`Digit ${i + 1}`}
                                className={`rounded-2xl text-center font-sans font-medium outline-none transition-colors w-[42px] h-[52px] sm:w-[56px] sm:h-[64px] md:w-[64px] md:h-[72px] lg:w-[60px] lg:h-[72px] xl:w-[68px] xl:h-[80px] 2xl:w-[76px] 2xl:h-[88px] text-[22px] sm:text-[26px] md:text-[30px] lg:text-[30px] xl:text-[34px] 2xl:text-[38px] ${codeError
                                    ? "border border-[#D50000] bg-[rgba(213,0,0,0.25)] text-[#D50000]"
                                    : "border border-primary bg-background text-primary focus:border-secondary"
                                    }`}
                            />
                        ))}
                    </div>

                    {codeError && (
                        <div className="mt-4 flex items-center justify-center gap-2" role="alert">
                            <Image
                                src="/icons/errorcircle.svg"
                                alt=""
                                width={20}
                                height={20}
                                className="w-[16px] h-[16px] md:w-[18px] md:h-[18px] xl:w-[20px] xl:h-[20px]"
                            />
                            <p className="font-sans font-medium leading-[25px] text-[#D50000] text-[14px] sm:text-[15px] md:text-[16px] lg:text-[14px] xl:text-[15px] 2xl:text-[16px]">
                                {codeError}
                            </p>
                        </div>
                    )}

                    <p className="mt-4 text-center font-sans font-medium leading-[25px] text-primary text-[15px] sm:text-[16px] md:text-[17px] lg:text-[15px] xl:text-[17px] 2xl:text-[18px]">
                        I haven&apos;t received a code{" "}
                        {secondsLeft > 0 ? (
                            <span className="text-[#D50000]">{timerLabel}</span>
                        ) : (
                            <button type="button" onClick={handleResend} disabled={submitting} className="underline hover:opacity-70 transition-opacity">
                                Resend
                            </button>
                        )}
                    </p>

                    <div className="mt-10 sm:mt-11 md:mt-12 flex justify-end">
                        <button
                            type="submit"
                            disabled={!canVerify}
                            className={`${buttonCls} ${canVerify
                                ? "bg-secondary border border-secondary hover:bg-background hover:border-primary"
                                : "bg-[#A8A8A8] border border-[#A8A8A8] cursor-not-allowed"
                                }`}
                        >
                            <span className={`${buttonTextCls} ${canVerify ? "text-background group-hover:text-primary" : "text-background"}`}>
                                {submitting ? "Verifying…" : "Verify"}
                            </span>
                            <span className={buttonArrowWrapperCls}>
                                <Image src="/icons/whitebuttonarrow.svg" alt="" fill className={`object-contain transition-opacity ${canVerify ? "group-hover:opacity-0" : ""}`} />
                                {canVerify && <Image src="/icons/buttonarrow.svg" alt="" fill className="object-contain opacity-0 transition-opacity group-hover:opacity-100" />}
                            </span>
                        </button>
                    </div>
                </form>
            )}

            {/* Step 3 — Reset */}
            {step === "reset" && (
                <form onSubmit={handleResetSubmit}>
                    <h1 className={headingCls}>Reset Password</h1>

                    <p className={`mt-6 sm:mt-7 md:mt-8 lg:mt-8 ${bodyCls}`}>
                        Please set your new password. Ensure it is something you will remember
                    </p>

                    <div className="mt-8 space-y-5">
                        <FloatingPasswordInput
                            label="Enter Your Password"
                            required
                            value={newPassword}
                            onChange={(e) => setNewPassword(e.target.value)}
                        />
                        <FloatingPasswordInput
                            label="Re-enter Your Password"
                            required
                            value={confirmPassword}
                            onChange={(e) => setConfirmPassword(e.target.value)}
                        />
                    </div>

                    <PasswordRuleList password={newPassword} />

                    {resetError && (
                        <div className="mt-5 flex items-center gap-2" role="alert">
                            <Image
                                src="/icons/errorcircle.svg"
                                alt=""
                                width={20}
                                height={20}
                                className="w-[16px] h-[16px] md:w-[18px] md:h-[18px] xl:w-[20px] xl:h-[20px] flex-shrink-0"
                            />
                            <p className="font-sans font-medium leading-[25px] text-[#D50000] text-[14px] sm:text-[15px] md:text-[16px] lg:text-[14px] xl:text-[15px] 2xl:text-[16px]">
                                {resetError}
                            </p>
                        </div>
                    )}

                    <div className="mt-8 flex justify-end">
                        <button
                            type="submit"
                            disabled={!canReset}
                            className={`${buttonCls} ${canReset
                                ? "bg-secondary border border-secondary hover:bg-background hover:border-primary"
                                : "bg-[#A8A8A8] border border-[#A8A8A8] cursor-not-allowed"
                                }`}
                        >
                            <span className={`${buttonTextCls} ${canReset ? "text-background group-hover:text-primary" : "text-background"}`}>
                                {submitting ? "Resetting…" : "Reset Password"}
                            </span>
                            <span className={buttonArrowWrapperCls}>
                                <Image src="/icons/whitebuttonarrow.svg" alt="" fill className={`object-contain transition-opacity ${canReset ? "group-hover:opacity-0" : ""}`} />
                                {canReset && <Image src="/icons/buttonarrow.svg" alt="" fill className="object-contain opacity-0 transition-opacity group-hover:opacity-100" />}
                            </span>
                        </button>
                    </div>
                </form>
            )}
        </ClientPortalLayout>
    );
}

// Success — full-width, no image on mobile/landscape/iPad
function SuccessScreen() {
    return (
        <section className="bg-background text-primary">
            <div className="grid grid-cols-1 lg:grid-cols-2 lg:min-h-[calc(100vh-100px)]">
                {/* Content column */}
                <div className="flex items-center justify-center px-4 sm:px-6 md:px-10 lg:px-12 xl:px-16 2xl:px-20 py-12 sm:py-14 md:py-16 lg:py-16 xl:py-20 2xl:py-24">
                    <div className="w-full max-w-[520px] md:max-w-[640px] lg:max-w-[520px] xl:max-w-[600px] 2xl:max-w-[680px] flex flex-col items-center text-center">
                        <h1 className="font-display font-thin uppercase tracking-[0.03em] leading-[100%] text-primary text-[40px] sm:text-[44px] md:text-[52px] lg:text-[52px] xl:text-[60px] 2xl:text-[68px] lg:whitespace-nowrap">
                            Password Reset
                        </h1>

                        <p className="mt-4 sm:mt-5 md:mt-6 font-body font-light text-primary text-[15px] leading-[24px] sm:text-[16px] sm:leading-[26px] md:text-[18px] md:leading-[28px] lg:text-[16px] lg:leading-[25px] xl:text-[18px] xl:leading-[28px] 2xl:text-[20px] 2xl:leading-[30px]">
                            Password reset was successful. Please log in with new password.
                        </p>

                        <div className="mt-10 sm:mt-12 md:mt-14 lg:mt-14 xl:mt-16">
                            <Image
                                src="/icons/reset.svg"
                                alt=""
                                width={360}
                                height={360}
                                className="w-[220px] h-[220px] sm:w-[250px] sm:h-[250px] md:w-[280px] md:h-[280px] lg:w-[300px] lg:h-[300px] xl:w-[340px] xl:h-[340px] 2xl:w-[380px] 2xl:h-[380px]"
                                priority
                            />
                        </div>

                        <Link
                            href="/client-portal"
                            className="group mt-10 sm:mt-12 md:mt-14 lg:mt-14 xl:mt-16 inline-flex items-center gap-6 bg-secondary border border-secondary px-10 py-4 md:px-11 md:py-[18px] xl:px-12 xl:py-5 transition-colors hover:bg-background hover:border-primary"
                        >
                            <span className="font-sans font-normal tracking-[0.15em] uppercase text-background group-hover:text-primary transition-colors text-[14px] sm:text-[15px] md:text-[16px] lg:text-[14px] xl:text-[16px] 2xl:text-[18px]">
                                Back to Login
                            </span>
                            <span className="relative inline-block w-[18px] h-[18px] md:w-[20px] md:h-[20px] xl:w-[22px] xl:h-[22px] 2xl:w-[24px] 2xl:h-[24px]">
                                <Image src="/icons/whitebuttonarrow.svg" alt="" fill className="object-contain transition-opacity group-hover:opacity-0" />
                                <Image src="/icons/buttonarrow.svg" alt="" fill className="object-contain opacity-0 transition-opacity group-hover:opacity-100" />
                            </span>
                        </Link>
                    </div>
                </div>

                {/* Image column — laptop+ only */}
                <div className="hidden lg:block relative w-full lg:h-full overflow-hidden">
                    <Image
                        src="/images/portfoliopage/portfoliotwo.jpg"
                        alt=""
                        fill
                        className="object-cover"
                        sizes="50vw"
                    />
                </div>
            </div>
        </section>
    );
}
