"use client";

import Image from "next/image";
import type { GalleryRow } from "@/data/portfolio";
import { useGallery } from "./GalleryLightbox";

export default function GalleryRowRenderer({ row }: { row: GalleryRow }) {
  const gallery = useGallery();

  if (row.type === "testimonial") {
    return (
      <div className="my-8 sm:my-10 md:my-12 lg:my-16 xl:my-20 2xl:my-24 px-4 sm:px-6 md:px-8 lg:px-12 xl:px-16 2xl:px-20 max-w-[1100px] xl:max-w-[1280px] 2xl:max-w-[1440px] mx-auto text-center">
        <blockquote className="font-display font-thin tracking-[0.01em] text-primary text-[20px] leading-[32px] sm:text-[22px] sm:leading-[34px] md:text-[28px] md:leading-[42px] lg:text-[42px] lg:leading-[60px] xl:text-[48px] xl:leading-[66px] 2xl:text-[54px] 2xl:leading-[72px]">
          &ldquo;{row.quote}&rdquo;
        </blockquote>
        <p className="mt-6 sm:mt-7 md:mt-8 font-sans font-light tracking-[0.2em] uppercase text-primary text-[11px] sm:text-[12px] md:text-[13px] lg:text-[13px] xl:text-[14px] 2xl:text-[15px]">
          — {row.attribution}
        </p>
      </div>
    );
  }

  const images = row.images;
  const count = images.length;

  const renderImage = (src: string, key: number, extraClass = "", sizes = "100vw") => (
    <button
      key={key}
      type="button"
      onClick={() => gallery?.openAt(src)}
      className={`relative w-full overflow-hidden cursor-zoom-in ${extraClass}`}
      aria-label="View larger image"
    >
      <Image
        src={src}
        alt=""
        fill
        className="object-cover transition-transform duration-500 hover:scale-105"
        sizes={sizes}
      />
    </button>
  );

  if (count === 1) {
    return (
      <div className="relative w-full aspect-[3/2] lg:aspect-[5/3]">
        {renderImage(images[0], 0, "aspect-[3/2] lg:aspect-[5/3]", "100vw")}
      </div>
    );
  }

  if (count === 2) {
    const gridStyle = row.ratios
      ? { gridTemplateColumns: `${row.ratios[0]}fr ${row.ratios[1]}fr` }
      : undefined;
    const isRatioRow = !!row.ratios;

    return (
      <div
        className={`grid gap-2 lg:gap-3 items-stretch ${!gridStyle ? "grid-cols-2" : ""} ${
          isRatioRow
            ? "h-[260px] sm:h-[320px] md:h-[400px] lg:h-[440px] xl:h-[500px] 2xl:h-[560px]"
            : ""
        }`}
        style={gridStyle}
      >
        {images.map((src, i) =>
          renderImage(src, i, isRatioRow ? "h-full" : "aspect-[3/4] lg:aspect-[4/3]", "50vw")
        )}
      </div>
    );
  }

  return (
    <div className="grid grid-cols-2 lg:grid-cols-3 gap-2 lg:gap-3">
      {images.map((src, i) =>
        renderImage(
          src,
          i,
          `aspect-[3/4] ${count === 3 && i === 2 ? "col-span-2 lg:col-span-1" : ""}`,
          "(max-width: 1024px) 50vw, 33vw"
        )
      )}
    </div>
  );
}