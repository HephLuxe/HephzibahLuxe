// import Image from "next/image";
// import type { ReactNode } from "react";

// interface ClientPortalLayoutProps {
//   children: ReactNode;
//   imageSide?: "left" | "right";
// }

// export default function ClientPortalLayout({
//   children,
//   imageSide = "left",
// }: ClientPortalLayoutProps) {
//   const imageOrder = imageSide === "left" ? "lg:order-1" : "lg:order-2";
//   const formOrder = imageSide === "left" ? "lg:order-2" : "lg:order-1";

//   return (
//     <section className="bg-background text-primary">
//       <div className="grid grid-cols-1 lg:grid-cols-2 items-stretch">
//         <div className={`relative w-full h-[40vh] sm:h-[45vh] md:h-[50vh] lg:h-auto lg:min-h-[600px] xl:min-h-[680px] 2xl:min-h-[760px] overflow-hidden order-1 ${imageOrder}`}>
//           <Image
//             src="/images/portfoliopage/portfoliotwo.jpg"
//             alt=""
//             fill
//             priority
//             className="object-cover"
//             sizes="(max-width: 1024px) 100vw, 50vw"
//           />
//         </div>

//         <div className={`order-2 ${formOrder} flex items-start justify-start px-4 sm:px-6 md:px-10 lg:px-10 xl:px-14 2xl:px-16 py-8 sm:py-10 md:py-14 lg:py-16 xl:py-20 2xl:py-24`}>
//           <div className="w-full max-w-[520px] md:max-w-[680px] lg:max-w-[520px] xl:max-w-[600px] 2xl:max-w-[680px]">
//             {children}
//           </div>
//         </div>
//       </div>
//     </section>
//   );
// }


"use client";

import { useState, useEffect } from "react";
import Image from "next/image";
import type { ReactNode } from "react";

interface ClientPortalLayoutProps {
  children: ReactNode;
  imageSide?: "left" | "right";
}

const portalImages = [
  "/images/portfoliopage/portfoliotwo.jpg",
  "/images/portfoliopage/portfolioeight.jpg",
  "/images/intro/introfour.jpg",
  "/images/intro/introfives.jpg",
];

export default function ClientPortalLayout({
  children,
  imageSide = "left",
}: ClientPortalLayoutProps) {
  const [activeIndex, setActiveIndex] = useState(0);

  useEffect(() => {
    const interval = setInterval(() => {
      setActiveIndex((prev) => (prev + 1) % portalImages.length);
    }, 7000);
    return () => clearInterval(interval);
  }, []);

  const imageOrder = imageSide === "left" ? "lg:order-1" : "lg:order-2";
  const formOrder = imageSide === "left" ? "lg:order-2" : "lg:order-1";

  return (
    <section className="bg-background text-primary">
      <div className="grid grid-cols-1 lg:grid-cols-2 items-stretch">
        <div className={`relative w-full h-[40vh] sm:h-[45vh] md:h-[50vh] lg:h-auto lg:min-h-[600px] xl:min-h-[680px] 2xl:min-h-[760px] overflow-hidden order-1 ${imageOrder}`}>
          {portalImages.map((src, i) => (
            <Image
              key={src}
              src={src}
              alt=""
              fill
              priority={i === 0}
              className={`object-cover transition-opacity duration-1000 ease-in-out ${
                i === activeIndex ? "opacity-100" : "opacity-0"
              }`}
              sizes="(max-width: 1024px) 100vw, 50vw"
            />
          ))}
        </div>

        <div className={`order-2 ${formOrder} flex items-start justify-start px-4 sm:px-6 md:px-10 lg:px-10 xl:px-14 2xl:px-16 py-8 sm:py-10 md:py-14 lg:py-16 xl:py-20 2xl:py-24`}>
          <div className="w-full max-w-[520px] md:max-w-[680px] lg:max-w-[520px] xl:max-w-[600px] 2xl:max-w-[680px]">
            {children}
          </div>
        </div>
      </div>
    </section>
  );
}