"use client";

import { useRef, useEffect, useState } from "react";

interface ScrollRevealProps {
  beforeImg: string;
  afterImg: string;
}

export default function ScrollReveal({ beforeImg, afterImg }: ScrollRevealProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [progress, setProgress] = useState(0);

  useEffect(() => {
    const handleScroll = () => {
      const container = containerRef.current;
      if (!container) return;

      const pinContainer = container.closest("[data-scroll-pin]") as HTMLElement | null;
      if (!pinContainer) return;

      const pinRect = pinContainer.getBoundingClientRect();
      const pinHeight = pinContainer.offsetHeight;
      const viewportHeight = window.innerHeight;

      const scrolledPastTop = -pinRect.top;
      const scrollableDistance = pinHeight - viewportHeight;

      let p = scrolledPastTop / scrollableDistance;
      p = Math.max(0, Math.min(1, p));
      setProgress(p);
    };

    handleScroll();
    window.addEventListener("scroll", handleScroll, { passive: true });
    window.addEventListener("resize", handleScroll, { passive: true });
    return () => {
      window.removeEventListener("scroll", handleScroll);
      window.removeEventListener("resize", handleScroll);
    };
  }, []);

  return (
    <div
      ref={containerRef}
      className="relative w-full h-full min-h-[400px] lg:min-h-[600px] overflow-hidden select-none"
    >
      {/* Before image — base layer, always fully visible at the bottom */}
      <div
        className="absolute inset-0 bg-cover bg-center"
        style={{ backgroundImage: `url(${beforeImg})` }}
      >
        <span
          className="absolute bottom-6 left-6 font-sans font-light text-background text-[16px] lg:text-[20px] tracking-wider transition-opacity duration-300 z-10"
          style={{ opacity: 1 - progress }}
        >
          BEFORE
        </span>
      </div>

      {/* After image — clipped from the right, revealed as progress grows (left-to-right wipe).
          At progress=0: right inset = 100% (fully hidden). At progress=1: right inset = 0% (fully visible). */}
      <div
        className="absolute inset-0 bg-cover bg-center"
        style={{
          backgroundImage: `url(${afterImg})`,
          clipPath: `inset(0 ${(1 - progress) * 100}% 0 0)`,
        }}
      >
        <span
          className="absolute bottom-6 right-6 font-sans font-light text-background text-[16px] lg:text-[20px] tracking-wider transition-opacity duration-300 z-10"
          style={{ opacity: progress }}
        >
          AFTER
        </span>
      </div>
    </div>
  );
}