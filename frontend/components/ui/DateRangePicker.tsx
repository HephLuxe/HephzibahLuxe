"use client";

import { useState, useRef, useEffect } from "react";
import Image from "next/image";

interface DateRangePickerProps {
  label: string;
  required?: boolean;
  startDate: Date | null;
  endDate: Date | null;
  onStartDateChange: (date: Date | null) => void;
  onEndDateChange: (date: Date | null) => void;
  /** Earliest selectable day (compared by local calendar date). Earlier days are disabled. */
  minDate?: Date;
}

const monthNames = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
const dayNames = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

export default function DateRangePicker({
  label,
  required,
  startDate,
  endDate,
  onStartDateChange,
  onEndDateChange,
  minDate,
}: DateRangePickerProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [isFocused, setIsFocused] = useState(false);
  const [isMultiDay, setIsMultiDay] = useState(false);
  const [currentMonth, setCurrentMonth] = useState(new Date());
  const [error, setError] = useState("");
  const containerRef = useRef<HTMLDivElement>(null);

  const isFloating = isFocused || !!startDate || !!endDate;

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setIsOpen(false);
        setError("");
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  function getDaysInMonth(date: Date): (Date | null)[] {
    const year = date.getFullYear();
    const month = date.getMonth();
    const firstDay = new Date(year, month, 1);
    const lastDay = new Date(year, month + 1, 0);
    const days: (Date | null)[] = [];
    for (let i = 0; i < firstDay.getDay(); i++) days.push(null);
    for (let i = 1; i <= lastDay.getDate(); i++) days.push(new Date(year, month, i));
    return days;
  }

  const getNextMonth = (date: Date) => new Date(date.getFullYear(), date.getMonth() + 1, 1);

  function handleDateClick(date: Date | null) {
    if (!date) return;
    setError("");

    if (!isMultiDay) {
      onStartDateChange(date);
      onEndDateChange(null);
    } else {
      if (!startDate) {
        onStartDateChange(date);
        onEndDateChange(null);
      } else if (!endDate) {
        if (date < startDate) {
          setError("You can't select a date before the start date");
        } else if (date.toDateString() === startDate.toDateString()) {
          return;
        } else {
          onEndDateChange(date);
        }
      } else {
        onStartDateChange(date);
        onEndDateChange(null);
      }
    }
  }

  // Disabled: before minDate, or before the start while the end is being picked.
  // Days in the grid are local midnights, so minDate is floored to one too.
  const minDay = minDate ? new Date(minDate.getFullYear(), minDate.getMonth(), minDate.getDate()) : null;
  const isDateDisabled = (date: Date) =>
    (minDay !== null && date < minDay) || (isMultiDay && !!startDate && !endDate && date < startDate);

  const isDateSelected = (date: Date | null) => {
    if (!date) return false;
    if (startDate && date.toDateString() === startDate.toDateString()) return true;
    if (endDate && date.toDateString() === endDate.toDateString()) return true;
    return false;
  };

  const isDateInRange = (date: Date | null) => {
    if (!date || !startDate || !endDate) return false;
    return date > startDate && date < endDate;
  };

  function formatDisplayDate() {
    if (!startDate) return "";
    const options: Intl.DateTimeFormatOptions = { month: "long", day: "numeric", year: "numeric" };
    if (endDate && isMultiDay) {
      return `${startDate.toLocaleDateString("en-US", options)}  →  ${endDate.toLocaleDateString("en-US", options)}`;
    }
    return startDate.toLocaleDateString("en-US", options);
  }

  function handleToggleMultiDay() {
    const newValue = !isMultiDay;
    setIsMultiDay(newValue);
    setError("");
    if (!newValue) onEndDateChange(null);
  }

  function renderMonth(monthDate: Date) {
    const days = getDaysInMonth(monthDate);
    return (
      <div className="flex-1">
        <div className="text-center font-body font-semibold text-primary mb-5 sm:mb-6 md:mb-6 lg:mb-6 xl:mb-7 text-base sm:text-[17px] md:text-[18px] lg:text-base xl:text-[18px] 2xl:text-[20px]">
          {monthNames[monthDate.getMonth()]} {monthDate.getFullYear()}
        </div>
        <div className="grid grid-cols-7 gap-x-1 gap-y-2 sm:gap-x-2 mb-3 sm:mb-4">
          {dayNames.map((day) => (
            <div key={day} className="text-center font-sans font-medium text-primary text-[13px] sm:text-sm md:text-[15px] lg:text-sm xl:text-[15px] 2xl:text-[16px]">
              {day}
            </div>
          ))}
        </div>
        <div className="grid grid-cols-7 gap-x-1 gap-y-1 sm:gap-x-2 sm:gap-y-2">
          {days.map((date, i) => (
            <button
              key={i}
              type="button"
              onClick={() => handleDateClick(date)}
              disabled={!date || isDateDisabled(date)}
              className={`flex items-center justify-center font-sans font-medium rounded-full transition-colors w-9 h-9 sm:w-10 sm:h-10 md:w-11 md:h-11 lg:w-10 lg:h-10 xl:w-11 xl:h-11 2xl:w-12 2xl:h-12 text-[13px] sm:text-sm md:text-[15px] lg:text-sm xl:text-[15px] 2xl:text-[16px] ${
                !date ? "invisible" : ""
              } ${
                date && isDateDisabled(date)
                  ? "text-primary opacity-30 cursor-not-allowed"
                  : isDateSelected(date)
                  ? "bg-primary text-background"
                  : isDateInRange(date)
                  ? "bg-primary/10 text-primary"
                  : "text-primary hover:bg-primary/5"
              }`}
            >
              {date?.getDate()}
            </button>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="relative w-full" ref={containerRef}>
      <div
        onClick={() => {
          setIsOpen(!isOpen);
          setIsFocused(true);
        }}
        className="w-full h-[60px] px-4 border border-[#778472] bg-transparent font-sans font-normal text-[18px] text-primary cursor-pointer flex items-center justify-between"
      >
        <span>{formatDisplayDate()}</span>
        <Image
          src="/icons/calendar.svg"
          alt=""
          width={24}
          height={24}
          className="w-[20px] h-[20px] md:w-[22px] md:h-[22px] xl:w-[24px] xl:h-[24px] opacity-70"
        />
      </div>

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

      {isOpen && (
        <div className="absolute top-full right-0 mt-2 bg-background border border-primary/20 rounded-2xl shadow-xl z-50 p-4 sm:p-5 md:p-6 lg:p-8 xl:p-10 2xl:p-12 w-[95vw] sm:w-[95vw] md:w-[640px] lg:w-[720px] xl:w-[840px] 2xl:w-[960px] max-w-[95vw] md:max-w-[640px] lg:max-w-[720px] xl:max-w-[840px] 2xl:max-w-[960px]">
          <div className="flex items-center gap-3 mb-5 sm:mb-6 lg:mb-6 xl:mb-7 pb-4 border-b border-primary/10">
            <span className="font-sans font-normal text-primary text-[14px] sm:text-[15px] md:text-[16px] lg:text-[15px] xl:text-[16px] 2xl:text-[18px]">Start Date</span>
            <span className="text-primary">⟶</span>
            <span className="font-sans font-normal text-primary text-[14px] sm:text-[15px] md:text-[16px] lg:text-[15px] xl:text-[16px] 2xl:text-[18px]">End Date</span>
          </div>

          <div className="flex items-start">
            <button
              type="button"
              onClick={() => setCurrentMonth(new Date(currentMonth.getFullYear(), currentMonth.getMonth() - 1, 1))}
              className="p-2 xl:p-2.5 2xl:p-3 hover:bg-primary/5 rounded-full text-primary mr-1 sm:mr-2"
            >
              <svg className="w-[20px] h-[20px] xl:w-[22px] xl:h-[22px] 2xl:w-[24px] 2xl:h-[24px]" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M15 18l-6-6 6-6" />
              </svg>
            </button>

            <div className="flex gap-4 sm:gap-6 lg:gap-12 xl:gap-14 2xl:gap-16 flex-1 overflow-hidden">
              {renderMonth(currentMonth)}
              <div className="hidden lg:block flex-1">{renderMonth(getNextMonth(currentMonth))}</div>
            </div>

            <button
              type="button"
              onClick={() => setCurrentMonth(new Date(currentMonth.getFullYear(), currentMonth.getMonth() + 1, 1))}
              className="p-2 xl:p-2.5 2xl:p-3 hover:bg-primary/5 rounded-full text-primary ml-1 sm:ml-2"
            >
              <svg className="w-[20px] h-[20px] xl:w-[22px] xl:h-[22px] 2xl:w-[24px] 2xl:h-[24px]" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M9 18l6-6-6-6" />
              </svg>
            </button>
          </div>

          <div className="flex flex-col lg:flex-row items-start lg:items-center justify-between gap-4 mt-5 sm:mt-6 xl:mt-7 pt-4 border-t border-primary/10">
            <div className="flex items-center gap-3">
              <span className="font-sans font-normal text-primary text-[13px] sm:text-sm md:text-[15px] lg:text-sm xl:text-[15px] 2xl:text-[16px]">Multi-day Event</span>
              <button
                type="button"
                onClick={handleToggleMultiDay}
                className={`rounded-full transition-colors relative w-12 h-6 sm:w-14 sm:h-7 xl:w-16 xl:h-8 2xl:w-[68px] 2xl:h-[34px] ${isMultiDay ? "bg-primary" : "bg-gray-300"}`}
              >
                <span
                  className={`absolute top-1 bg-background rounded-full shadow-sm transition-all w-4 h-4 sm:w-5 sm:h-5 xl:w-6 xl:h-6 2xl:w-[26px] 2xl:h-[26px] ${
                    isMultiDay
                      ? "left-7 sm:left-8 xl:left-9 2xl:left-[38px]"
                      : "left-1"
                  }`}
                />
              </button>
            </div>

            <div className="flex items-center gap-4 ml-auto">
              {error && <span className="font-sans text-primary/60 text-[12px] sm:text-sm md:text-[13px]">{error}</span>}
              <button
                type="button"
                onClick={() => {
                  setIsOpen(false);
                  setError("");
                }}
                className="bg-primary text-background font-sans rounded-full hover:opacity-90 transition-opacity text-[13px] sm:text-sm md:text-[15px] lg:text-sm xl:text-[15px] 2xl:text-[16px] px-6 sm:px-8 xl:px-10 2xl:px-12 py-2 sm:py-2.5 xl:py-3 2xl:py-3.5"
              >
                Done
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}