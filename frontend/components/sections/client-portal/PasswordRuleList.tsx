import { checkPasswordRules } from "@/lib/password-rules";

export default function PasswordRuleList({ password }: { password: string }) {
    const { results } = checkPasswordRules(password);
    return (
        <ul className="mt-5 space-y-2 sm:space-y-2.5 md:space-y-3">
            {results.map(({ rule, met }) => (
                <Rule key={rule.id} met={met} label={rule.label} />
            ))}
        </ul>
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
