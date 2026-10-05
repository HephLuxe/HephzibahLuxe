"use client";

import { useState } from "react";
import Image from "next/image";
import Link from "next/link";
import { useRouter } from "next/navigation";
import FloatingInput from "@/components/ui/FloatingInput";
import FloatingPasswordInput from "@/components/ui/FloatingPasswordInput";
import ClientPortalLayout from "./ClientPortalLayout";
import PasswordRuleList from "./PasswordRuleList";
import { ApiError, GENERIC_ERROR_MESSAGE } from "@/lib/client-api";
import { forcePasswordChange, login } from "@/lib/auth";
import { checkPasswordRules } from "@/lib/password-rules";

const DASHBOARD_PATH = "/client-portal/dashboard";
const INVALID_CREDENTIALS_MESSAGE = "Invalid email or password.";

type LoginError = { message: string; resetRequired?: boolean };

function loginErrorFrom(err: unknown): LoginError {
    if (!(err instanceof ApiError)) return { message: GENERIC_ERROR_MESSAGE };
    // Account locked after repeated failures: the backend's message points at the
    // reset flow and is identical whether or not the account exists.
    if (err.code === "password_reset_required") return { message: err.detail, resetRequired: true };
    // Every other 401/400 gets one generic message, so the page never says which
    // of email or password was wrong.
    if (err.status === 401 || err.status === 400) return { message: INVALID_CREDENTIALS_MESSAGE };
    return { message: err.detail };
}

export default function ClientPortalLogIn() {
    const router = useRouter();
    const [step, setStep] = useState<"login" | "change">("login");

    const [email, setEmail] = useState("");
    const [password, setPassword] = useState("");
    const [error, setError] = useState<LoginError | null>(null);
    const [submitting, setSubmitting] = useState(false);
    const showError = error !== null;

    const canSubmit = email.length > 0 && password.length > 0 && !submitting;

    async function handleSubmit(e: React.FormEvent) {
        e.preventDefault();
        if (!canSubmit) return;
        setSubmitting(true);
        setError(null);
        try {
            const user = await login(email, password);
            if (user.force_password_change) {
                setStep("change");
            } else {
                router.push(DASHBOARD_PATH);
            }
        } catch (err) {
            setError(loginErrorFrom(err));
        } finally {
            setSubmitting(false);
        }
    }

    function handleEmailChange(e: React.ChangeEvent<HTMLInputElement>) {
        setEmail(e.target.value);
        if (showError) setError(null);
    }

    function handlePasswordChange(e: React.ChangeEvent<HTMLInputElement>) {
        setPassword(e.target.value);
        if (showError) setError(null);
    }

    if (step === "change") {
        return <ForcePasswordChange onDone={() => router.push(DASHBOARD_PATH)} />;
    }

    return (
        <ClientPortalLayout>
            <form onSubmit={handleSubmit}>
                <h1 className="text-left font-display font-thin uppercase tracking-[0.03em] leading-[100%] text-primary text-[40px] sm:text-[44px] md:text-[52px] lg:text-[64px] xl:text-[72px] 2xl:text-[80px]">
                    Client Portal
                </h1>

                <p className="mt-8 sm:mt-9 md:mt-10 font-body font-light text-primary text-[15px] leading-[24px] sm:text-[16px] sm:leading-[26px] md:text-[18px] md:leading-[28px] lg:text-[16px] lg:leading-[25px] xl:text-[18px] xl:leading-[28px] 2xl:text-[20px] 2xl:leading-[30px]">
                    Welcome to your Hephzibah Luxe Client Portal. This thoughtfully curated space gives you seamless access to your planning documents, timelines, proposals, contracts, and shared updates—keeping every stage of your planning journey organised, connected, and beautifully coordinated.
                </p>

                <p className="mt-4 font-body font-normal italic text-primary text-[15px] leading-[24px] sm:text-[16px] sm:leading-[26px] md:text-[18px] md:leading-[28px] lg:text-[16px] lg:leading-[25px] xl:text-[18px] xl:leading-[28px] 2xl:text-[20px] 2xl:leading-[30px]">
                    <span className="italic">Note: Portal accounts cannot be created through this page. Once your booking has been confirmed, our team will create your account and send you an email with instructions to set your password.</span>
                </p>

                {error && (
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
                        <p className="font-sans font-medium tracking-[0.0125em] text-primary text-[13px] leading-[24px] sm:text-[14px] sm:leading-[26px] md:text-[16px] md:leading-[28px] lg:text-[14px] lg:leading-[26px] xl:text-[14px] xl:leading-[28px] 2xl:text-[15px] 2xl:leading-[30px]">
                            {error.message}
                            {error.resetRequired && (
                                <>
                                    {" "}
                                    <Link href="/client-portal/forgot-password" className="underline underline-offset-2">
                                        Reset your password
                                    </Link>
                                </>
                            )}
                        </p>
                    </div>
                )}

                <div className={`${showError ? "mt-6" : "mt-8"} space-y-5`}>
                    <FloatingInput
                        label="Your Email Address"
                        type="email"
                        required
                        value={email}
                        onChange={handleEmailChange}
                    />
                    <FloatingPasswordInput
                        label="Enter Your Password"
                        required
                        value={password}
                        onChange={handlePasswordChange}
                    />
                </div>

                <div className="mt-8 flex flex-col items-end gap-3">
                    <button
                        type="submit"
                        disabled={!canSubmit}
                        className={`group inline-flex items-center gap-6 px-10 py-4 md:px-11 md:py-[18px] xl:px-12 xl:py-5 transition-colors ${canSubmit
                            ? "bg-secondary border border-secondary hover:bg-background hover:border-primary"
                            : "bg-[#A8A8A8] border border-[#A8A8A8] cursor-not-allowed"
                            }`}
                    >
                        <span
                            className={`font-sans font-normal tracking-[0.15em] uppercase transition-colors text-[14px] sm:text-[15px] md:text-[16px] lg:text-[14px] xl:text-[16px] 2xl:text-[18px] ${canSubmit ? "text-background group-hover:text-primary" : "text-background"
                                }`}
                        >
                            {submitting ? "Logging In…" : "Log In"}
                        </span>
                        <span className="relative inline-block w-[18px] h-[18px] md:w-[20px] md:h-[20px] xl:w-[22px] xl:h-[22px] 2xl:w-[24px] 2xl:h-[24px]">
                            <Image
                                src="/icons/whitebuttonarrow.svg"
                                alt=""
                                fill
                                className={`object-contain transition-opacity ${canSubmit ? "group-hover:opacity-0" : ""}`}
                            />
                            {canSubmit && (
                                <Image
                                    src="/icons/buttonarrow.svg"
                                    alt=""
                                    fill
                                    className="object-contain opacity-0 transition-opacity group-hover:opacity-100"
                                />
                            )}
                        </span>
                    </button>

                    <Link
                        href="/client-portal/forgot-password"
                        className="font-sans font-light leading-[100%] text-primary underline underline-offset-2 hover:opacity-70 transition-opacity text-[16px] sm:text-[17px] md:text-[18px] lg:text-[16px] xl:text-[18px] 2xl:text-[20px]"
                    >
                        Forgot Password?
                    </Link>
                </div>
            </form>
        </ClientPortalLayout>
    );
}

// First login with a temporary password. The backend blocks every other
// endpoint (403) until this succeeds, so the user cannot skip it.
function ForcePasswordChange({ onDone }: { onDone: () => void }) {
    const [newPassword, setNewPassword] = useState("");
    const [confirmPassword, setConfirmPassword] = useState("");
    const [error, setError] = useState<string | null>(null);
    const [submitting, setSubmitting] = useState(false);

    const { allMet } = checkPasswordRules(newPassword);
    const passwordsMatch = newPassword.length > 0 && newPassword === confirmPassword;
    const canSubmit = allMet && passwordsMatch && !submitting;

    async function handleSubmit(e: React.FormEvent) {
        e.preventDefault();
        if (!canSubmit) return;
        setSubmitting(true);
        setError(null);
        try {
            await forcePasswordChange(newPassword, confirmPassword);
            onDone();
        } catch (err) {
            if (err instanceof ApiError) {
                const fieldMessages = Object.values(err.fieldErrors).flat();
                setError(fieldMessages.length ? fieldMessages.join(" ") : err.detail);
            } else {
                setError(GENERIC_ERROR_MESSAGE);
            }
        } finally {
            setSubmitting(false);
        }
    }

    return (
        <ClientPortalLayout>
            <form onSubmit={handleSubmit}>
                <h1 className="text-left font-display font-thin uppercase tracking-[0.03em] leading-[100%] text-primary text-[40px] sm:text-[44px] md:text-[52px] lg:text-[64px] xl:text-[72px] 2xl:text-[80px]">
                    Set Your Password
                </h1>

                <p className="mt-8 sm:mt-9 md:mt-10 font-body font-light text-primary text-[15px] leading-[24px] sm:text-[16px] sm:leading-[26px] md:text-[18px] md:leading-[28px] lg:text-[16px] lg:leading-[25px] xl:text-[18px] xl:leading-[28px] 2xl:text-[20px] 2xl:leading-[30px]">
                    You signed in with a temporary password. Please choose a new password to continue to your Client Portal.
                </p>

                {error && (
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
                        <p className="font-sans font-medium tracking-[0.0125em] text-primary text-[13px] leading-[24px] sm:text-[14px] sm:leading-[26px] md:text-[16px] md:leading-[28px] lg:text-[14px] lg:leading-[26px] xl:text-[14px] xl:leading-[28px] 2xl:text-[15px] 2xl:leading-[30px]">
                            {error}
                        </p>
                    </div>
                )}

                <div className={`${error ? "mt-6" : "mt-8"} space-y-5`}>
                    <FloatingPasswordInput
                        label="Enter Your New Password"
                        required
                        value={newPassword}
                        onChange={(e) => setNewPassword(e.target.value)}
                    />
                    <FloatingPasswordInput
                        label="Re-enter Your New Password"
                        required
                        value={confirmPassword}
                        onChange={(e) => setConfirmPassword(e.target.value)}
                    />
                </div>

                <PasswordRuleList password={newPassword} />

                <div className="mt-8 flex justify-end">
                    <button
                        type="submit"
                        disabled={!canSubmit}
                        className={`group inline-flex items-center gap-6 px-10 py-4 md:px-11 md:py-[18px] xl:px-12 xl:py-5 transition-colors ${canSubmit
                            ? "bg-secondary border border-secondary hover:bg-background hover:border-primary"
                            : "bg-[#A8A8A8] border border-[#A8A8A8] cursor-not-allowed"
                            }`}
                    >
                        <span
                            className={`font-sans font-normal tracking-[0.15em] uppercase transition-colors text-[14px] sm:text-[15px] md:text-[16px] lg:text-[14px] xl:text-[16px] 2xl:text-[18px] ${canSubmit ? "text-background group-hover:text-primary" : "text-background"
                                }`}
                        >
                            {submitting ? "Saving…" : "Set Password"}
                        </span>
                        <span className="relative inline-block w-[18px] h-[18px] md:w-[20px] md:h-[20px] xl:w-[22px] xl:h-[22px] 2xl:w-[24px] 2xl:h-[24px]">
                            <Image
                                src="/icons/whitebuttonarrow.svg"
                                alt=""
                                fill
                                className={`object-contain transition-opacity ${canSubmit ? "group-hover:opacity-0" : ""}`}
                            />
                            {canSubmit && (
                                <Image
                                    src="/icons/buttonarrow.svg"
                                    alt=""
                                    fill
                                    className="object-contain opacity-0 transition-opacity group-hover:opacity-100"
                                />
                            )}
                        </span>
                    </button>
                </div>
            </form>
        </ClientPortalLayout>
    );
}
