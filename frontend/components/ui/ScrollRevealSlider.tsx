"use client";

import { useEffect, useRef, useState } from "react";
import { useLenis } from "lenis/react";

interface ScrollRevealSliderProps {
  beforeImg: string;
  afterImg: string;
}

export default function ScrollRevealSlider({ beforeImg, afterImg }: ScrollRevealSliderProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const isDragging = useRef(false);
  const [scrollProgress, setScrollProgress] = useState(0);
  const [dragPosition, setDragPosition] = useState<number | null>(null);
  const lenis = useLenis();

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
      setScrollProgress(p);
    };

    handleScroll();
    window.addEventListener("scroll", handleScroll, { passive: true });
    window.addEventListener("resize", handleScroll, { passive: true });
    return () => {
      window.removeEventListener("scroll", handleScroll);
      window.removeEventListener("resize", handleScroll);
    };
  }, []);

  // While dragging, follow the pointer directly; otherwise follow scroll.
  const position = dragPosition !== null ? dragPosition : scrollProgress * 100;

  // Moves the real page scroll to wherever the given handle percentage
  // corresponds to, so scrolling afterwards continues from that point
  // instead of jumping back to the pre-drag scroll position.
  const syncScrollToPosition = (percentage: number) => {
    const pinContainer = containerRef.current?.closest("[data-scroll-pin]") as HTMLElement | null;
    if (!pinContainer) return;

    const pinRect = pinContainer.getBoundingClientRect();
    const pinHeight = pinContainer.offsetHeight;
    const viewportHeight = window.innerHeight;
    const scrollableDistance = pinHeight - viewportHeight;
    if (scrollableDistance <= 0) return;

    const absoluteTop = window.scrollY + pinRect.top;
    const targetScrollY = absoluteTop + (percentage / 100) * scrollableDistance;

    if (lenis) {
      lenis.scrollTo(targetScrollY, { immediate: true });
    } else {
      window.scrollTo({ top: targetScrollY });
    }
  };

  const handleMove = (clientX: number) => {
    if (!containerRef.current || !isDragging.current) return;
    const rect = containerRef.current.getBoundingClientRect();
    const x = clientX - rect.left;
    const percentage = Math.max(0, Math.min(100, (x / rect.width) * 100));
    setDragPosition(percentage);
    syncScrollToPosition(percentage);
  };

  const handleDragStart = (e: React.MouseEvent | React.TouchEvent) => {
    e.preventDefault();
    isDragging.current = true;
    document.body.style.cursor = "grabbing";
  };

  const handleDragEnd = () => {
    if (!isDragging.current) return;
    isDragging.current = false;
    document.body.style.cursor = "default";
    setDragPosition(null);
  };

  const handleMouseMove = (e: React.MouseEvent) => {
    if (!isDragging.current) return;
    handleMove(e.clientX);
  };

  const handleTouchMove = (e: React.TouchEvent) => {
    if (!isDragging.current) return;
    handleMove(e.touches[0].clientX);
  };

  return (
    <div
      ref={containerRef}
      className="relative w-full h-full min-h-[400px] lg:min-h-[600px] overflow-hidden select-none"
      onMouseMove={handleMouseMove}
      onMouseUp={handleDragEnd}
      onMouseLeave={handleDragEnd}
      onTouchMove={handleTouchMove}
      onTouchEnd={handleDragEnd}
    >
      {/* Before image — base layer, always fully visible underneath */}
      <div
        className="absolute inset-0 bg-cover bg-center"
        style={{ backgroundImage: `url(${beforeImg})` }}
      >
        <span
          className="absolute bottom-6 left-6 font-sans font-light text-background text-[16px] lg:text-[20px] tracking-wider transition-opacity duration-300 z-10"
          style={{ opacity: 1 - position / 100 }}
        >
          BEFORE
        </span>
      </div>

      {/* After image — clipped from the right, revealed as position grows */}
      <div
        className="absolute inset-0 bg-cover bg-center"
        style={{
          backgroundImage: `url(${afterImg})`,
          clipPath: `inset(0 ${100 - position}% 0 0)`,
        }}
      >
        <span
          className="absolute bottom-6 right-6 font-sans font-light text-background text-[16px] lg:text-[20px] tracking-wider transition-opacity duration-300 z-10"
          style={{ opacity: position / 100 }}
        >
          AFTER
        </span>
      </div>

      {/* Draggable handle */}
      <div
        className="absolute top-0 bottom-0 w-[2px] bg-background pointer-events-none"
        style={{ left: `${position}%`, transform: "translateX(-50%)" }}
      >
        <div
          onMouseDown={handleDragStart}
          onTouchStart={handleDragStart}
          className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-14 h-14 lg:w-16 lg:h-16 bg-background rounded-full flex items-center justify-center shadow-lg cursor-grab active:cursor-grabbing pointer-events-auto"
        >
          <svg width="10" height="16" viewBox="0 0 10 16" fill="none" className="pointer-events-none">
            <path d="M9 1L2 8L9 15" stroke="#062025" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          <div className="w-3" />
          <svg width="10" height="16" viewBox="0 0 10 16" fill="none" className="pointer-events-none">
            <path d="M1 1L8 8L1 15" stroke="#062025" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </div>
      </div>
    </div>
  );
}
