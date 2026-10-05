"use client";

import { createContext, useContext, useState, useEffect, useCallback, useRef, ReactNode } from "react";
import Image from "next/image";
import type { GalleryImage } from "@/lib/portfolio";

interface GalleryContextValue {
    openAt: (image: string) => void;
}

const GalleryContext = createContext<GalleryContextValue | null>(null);

export function useGallery() {
    return useContext(GalleryContext);
}

interface GalleryProviderProps {
    children: ReactNode;
    images: GalleryImage[]; // Full flattened list, in display order
}

export function GalleryProvider({ children, images }: GalleryProviderProps) {
    const [lightboxIndex, setLightboxIndex] = useState<number | null>(null);

    const openAt = useCallback(
        (image: string) => {
            const idx = images.findIndex((img) => img.src === image);
            if (idx !== -1) setLightboxIndex(idx);
        },
        [images]
    );

    const close = useCallback(() => setLightboxIndex(null), []);

    const showPrev = useCallback(() => {
        setLightboxIndex((i) => (i === null ? null : (i - 1 + images.length) % images.length));
    }, [images.length]);

    const showNext = useCallback(() => {
        setLightboxIndex((i) => (i === null ? null : (i + 1) % images.length));
    }, [images.length]);

    useEffect(() => {
        if (lightboxIndex === null) return;

        const handleKey = (e: KeyboardEvent) => {
            if (e.key === "Escape") close();
            else if (e.key === "ArrowLeft") showPrev();
            else if (e.key === "ArrowRight") showNext();
        };
        window.addEventListener("keydown", handleKey);

        const originalOverflow = document.body.style.overflow;
        document.body.style.overflow = "hidden";

        return () => {
            window.removeEventListener("keydown", handleKey);
            document.body.style.overflow = originalOverflow;
        };
    }, [lightboxIndex, close, showPrev, showNext]);

    // ─── Swipe / drag detection ───
    // Works with touch (mobile) and mouse/trackpad drag (desktop).
    // Threshold: 50px horizontal, and horizontal delta must exceed vertical (so vertical scroll gestures don't trigger).
    const dragStartRef = useRef<{ x: number; y: number } | null>(null);

    const handleDragStart = (clientX: number, clientY: number) => {
        dragStartRef.current = { x: clientX, y: clientY };
    };

    const handleDragEnd = (clientX: number, clientY: number) => {
        if (!dragStartRef.current) return;
        const dx = clientX - dragStartRef.current.x;
        const dy = clientY - dragStartRef.current.y;
        dragStartRef.current = null;

        // Must be primarily horizontal, at least 50px
        if (Math.abs(dx) > 50 && Math.abs(dx) > Math.abs(dy)) {
            if (dx > 0) showPrev();
            else showNext();
        }
    };

    return (
        <GalleryContext.Provider value={{ openAt }}>
            {children}

            {lightboxIndex !== null && images[lightboxIndex] && (
                <div
                    className="fixed inset-0 z-[100] bg-black/90 flex items-center justify-center select-none"
                    onClick={close}
                    onTouchStart={(e) => handleDragStart(e.touches[0].clientX, e.touches[0].clientY)}
                    onTouchEnd={(e) => handleDragEnd(e.changedTouches[0].clientX, e.changedTouches[0].clientY)}
                    onMouseDown={(e) => handleDragStart(e.clientX, e.clientY)}
                    onMouseUp={(e) => handleDragEnd(e.clientX, e.clientY)}
                    role="dialog"
                    aria-modal="true"
                    aria-label="Image viewer"
                >
                    {/* Close */}
                    <button
                        type="button"
                        onClick={(e) => {
                            e.stopPropagation();
                            close();
                        }}
                        aria-label="Close"
                        className="absolute top-4 right-4 sm:top-6 sm:right-6 md:top-8 md:right-8 z-10 w-10 h-10 sm:w-12 sm:h-12 flex items-center justify-center text-white hover:opacity-70 transition-opacity"
                    >
                        <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                            <path d="M6 6l12 12M18 6L6 18" />
                        </svg>
                    </button>

                    {images.length > 1 && (
                        <>
                            {/* Prev — sits above the image on mobile/iPad (top-1/2 is centered vertically on the ENTIRE viewport, arrows on lg+ stay on the sides; mobile/iPad they sit near the top). Using top-4 on mobile puts them in the top-center-left/right area. */}
                            <button
                                type="button"
                                onClick={(e) => {
                                    e.stopPropagation();
                                    showPrev();
                                }}
                                aria-label="Previous image"
                                className="absolute z-10 w-10 h-10 sm:w-12 sm:h-12 flex items-center justify-center text-white hover:opacity-70 transition-opacity
        top-1/2 -translate-y-1/2 left-1 sm:left-2 md:left-4 lg:left-6"
                            >
                                <svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                                    <path d="M15 18l-6-6 6-6" />
                                </svg>
                            </button>

                            <button
                                type="button"
                                onClick={(e) => {
                                    e.stopPropagation();
                                    showNext();
                                }}
                                aria-label="Next image"
                                className="absolute z-10 w-10 h-10 sm:w-12 sm:h-12 flex items-center justify-center text-white hover:opacity-70 transition-opacity
        top-1/2 -translate-y-1/2 right-1 sm:right-2 md:right-4 lg:right-6"
                            >
                                <svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                                    <path d="M9 6l6 6-6 6" />
                                </svg>
                            </button>
                        </>
                    )}

                    {/* Image container — sized to viewport, image fills via object-contain */}
                    <div
                        className="relative w-[98vw] h-[92vh] sm:w-[95vw] sm:h-[90vh] md:w-[92vw] md:h-[88vh] lg:w-[85vw] lg:h-[85vh]"
                    >
                        <Image
                            src={images[lightboxIndex].src}
                            alt={images[lightboxIndex].alt}
                            fill
                            className="object-contain pointer-events-none select-none"
                            sizes="95vw"
                            priority
                            draggable={false}
                        />
                    </div>
                </div>
            )}
        </GalleryContext.Provider>
    );
}