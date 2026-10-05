"use client";

import { useState } from "react";
import Image from "next/image";
import FloatingInput from "@/components/ui/FloatingInput";
import FloatingPasswordInput from "@/components/ui/FloatingPasswordInput";
import ClientPortalLayout from "./ClientPortalLayout";
import { checkPasswordRules } from "@/lib/password-rules";

export default function ClientPortalSignUp() {
    const [email, setEmail] = useState("");
    const [password, setPassword] = useState("");
    const [confirmPassword, setConfirmPassword] = useState("");

    const { results: ruleResults, allMet: allRulesMet } = checkPasswordRules(password);
    const passwordsMatch = password.length > 0 && password === confirmPassword;
    const canSubmit = email && allRulesMet && passwordsMatch;

    function handleSubmit(e: React.FormEvent) {
        e.preventDefault();
        if (!canSubmit) return;
    }

    return (
        <ClientPortalLayout>
            <form onSubmit={handleSubmit}>
                <h1 className="text-left font-display font-thin uppercase tracking-[0.03em] leading-[100%] text-primary text-[40px] sm:text-[44px] md:text-[52px] lg:text-[64px] xl:text-[72px] 2xl:text-[80px]">
                    Client Portal
                </h1>

                <p className="mt-8 sm:mt-9 md:mt-10 font-body font-light text-primary text-[15px] leading-[24px] sm:text-[16px] sm:leading-[26px] md:text-[18px] md:leading-[28px] lg:text-[16px] lg:leading-[25px] xl:text-[18px] xl:leading-[28px] 2xl:text-[20px] 2xl:leading-[30px]">
                    Welcome to your Hephzibah Luxe Client Portal. This curated space gives you effortless access to your planning documents, timelines, proposals, and shared updates — ensuring a beautifully coordinated journey from start to finish.
                </p>

                <p className="mt-4 font-body font-normal italic text-primary text-[15px] leading-[24px] sm:text-[16px] sm:leading-[26px] md:text-[18px] md:leading-[28px] lg:text-[16px] lg:leading-[25px] xl:text-[18px] xl:leading-[28px] 2xl:text-[20px] 2xl:leading-[30px]">
                    <span className="font-medium not-italic">Please note:</span>{" "}
                    <span className="italic">New accounts cannot be created on this page. After your booking is confirmed, our team will create your portal account and email you instructions to set your password.</span>
                </p>

                <div className="mt-8 space-y-5">
                    <FloatingInput
                        label="Your Email Address"
                        type="email"
                        required
                        value={email}
                        onChange={(e) => setEmail(e.target.value)}
                    />
                    <FloatingPasswordInput
                        label="Enter Your Password"
                        required
                        value={password}
                        onChange={(e) => setPassword(e.target.value)}
                    />
                    <FloatingPasswordInput
                        label="Re-enter Your Password"
                        required
                        value={confirmPassword}
                        onChange={(e) => setConfirmPassword(e.target.value)}
                    />
                </div>

                <ul className="mt-5 space-y-2 sm:space-y-2.5 md:space-y-3">
                    {ruleResults.map(({ rule, met }) => (
                        <Rule key={rule.id} met={met} label={rule.label} />
                    ))}
                </ul>

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
                            Sign Up
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

function Rule({ met, label }: { met: boolean; label: string }) {
    return (
        <li className="flex items-center gap-3 font-sans font-light text-primary text-[14px] sm:text-[15px] md:text-[16px] lg:text-[14px] xl:text-[15px] 2xl:text-[16px]">
            <span
                className={`relative inline-block flex-shrink-0 border border-[#778472] w-[18px] h-[18px] md:w-[20px] md:h-[20px] xl:w-[20px] xl:h-[20px] 2xl:w-[22px] 2xl:h-[22px] ${met ? "bg-primary border-primary" : ""
                    }`}
            >
                {met && (
                    <svg
                        className="absolute inset-0 m-auto text-background w-[12px] h-[12px] md:w-[14px] md:h-[14px] 2xl:w-[15px] 2xl:h-[15px]"
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="3"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                    >
                        <polyline points="20 6 9 17 4 12" />
                    </svg>
                )}
            </span>
            {label}
        </li>
    );
}