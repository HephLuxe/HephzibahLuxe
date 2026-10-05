"use client";

import { TextareaHTMLAttributes } from "react";

interface FloatingTextareaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  label: string;
  required?: boolean;
}

export default function FloatingTextarea({
  label,
  required,
  value,
  onChange,
  placeholder,
  rows = 6,
  ...rest
}: FloatingTextareaProps) {
  return (
    <div>
      <label className="block font-sans font-light text-[16px] lg:text-[19px] leading-[25px] text-primary mb-3">
        {label}
        {required && <span className="text-red-500 ml-1">*</span>}
      </label>
      <textarea
        value={value}
        onChange={onChange}
        rows={rows}
        placeholder={placeholder}
        className="w-full border border-[#778472] bg-transparent px-4 py-3 font-sans font-normal text-[16px] lg:text-[18px] leading-[24px] text-primary outline-none transition-colors focus:border-primary placeholder:text-[#778472] placeholder:font-light resize-y"
        {...rest}
      />
    </div>
  );
}