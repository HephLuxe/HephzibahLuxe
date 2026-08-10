"use client";

import { useState } from "react";
import Image from "next/image";
import Link from "next/link";
import FloatingInput from "@/components/ui/FloatingInput";
import FloatingPasswordInput from "@/components/ui/FloatingPasswordInput";
import ClientPortalLayout from "./ClientPortalLayout";

export default function ClientPortalLogIn() {
    const [email, setEmail] = useState("");
    const [password, setPassword] = useState("");
    const [showError, setShowError] = useState(false);

    const canSubmit = email.length > 0 && password.length > 0;

    function handleSubmit(e: React.FormEvent) {
        e.preventDefault();
        if (!canSubmit) return;
        setShowError(true);
    }

    function handleEmailChange(e: React.ChangeEvent<HTMLInputElement>) {
        setEmail(e.target.value);
        if (showError) setShowError(false);
    }

    function handlePasswordChange(e: React.ChangeEvent<HTMLInputElement>) {
        setPassword(e.target.value);
        if (showError) setShowError(false);
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

                {showError && (
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
                            Client not found. Please{" "}
                            <Link href="/inquiry" className="underline underline-offset-2">
                                Send an Inquiry
                            </Link>{" "}
                            to become a client{" "}
                            <Image
                                src="/icons/send.svg"
                                alt=""
                                width={12}
                                height={12}
                                className="inline-block align-baseline translate-y-[1px] w-[11px] h-[11px] md:w-[11px] md:h-[11px] xl:w-[11px] xl:h-[11px]"
                            />
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
                            Log In
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