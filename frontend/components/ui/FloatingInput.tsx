"use client";

import { useState, InputHTMLAttributes } from "react";

interface FloatingInputProps extends InputHTMLAttributes<HTMLInputElement> {
  label: string;
  required?: boolean;
  /** Fixed text shown inside the field before the value (e.g. a currency sign). Appears once the label floats. */
  prefix?: string;
}

export default function FloatingInput({
  label,
  required,
  prefix,
  value,
  onChange,
  type = "text",
  ...rest
}: FloatingInputProps) {
  const [focused, setFocused] = useState(false);
  const isFloating = focused || (typeof value === "string" && value.length > 0);

  return (
    <div className="relative">
      <input
        type={type}
        value={value}
        onChange={onChange}
        onFocus={() => setFocused(true)}
        onBlur={() => setFocused(false)}
        className={`w-full h-[60px] border border-[#778472] bg-transparent pr-4 ${prefix ? "pl-8" : "pl-4"} pt-5 pb-2 font-sans font-normal text-[16px] leading-[24px] text-primary outline-none transition-colors focus:border-primary`}
        {...rest}
      />
      {prefix && (
        <span
          aria-hidden="true"
          className={`absolute left-4 top-px bottom-px pt-5 pb-2 flex items-center pointer-events-none font-sans font-normal text-[16px] leading-[24px] text-[#778472] transition-opacity duration-200 ${
            isFloating ? "opacity-100" : "opacity-0"
          }`}
        >
          {prefix}
        </span>
      )}
      <label
        className={`absolute left-4 pointer-events-none transition-all duration-200 font-sans font-light ${
          isFloating
            ? "top-0 -translate-y-1/2 text-[12px] bg-background px-1 text-[#778472]"
            : "top-1/2 -translate-y-1/2 text-[16px] text-[#778472]"
        }`}
      >
        {label}
        {required && <span className="text-red-500 ml-1">*</span>}
      </label>
    </div>
  );
}