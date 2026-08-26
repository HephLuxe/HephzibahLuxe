// import ComparisonSlider from "@/components/ui/ComparisonSlider";

// const transformations = [
//   {
//     location: "LAGOS, NIGERIA — 2022",
//     subtitle: "A Celebration Across Generations",
//     title: "LEGACY\nHONOURED",
//     description:
//       "What began as a familiar family compound was transformed into a regal setting worthy of eighty-five remarkable years. Rich shades of royal purple, complemented by warm touches of gold, elegant florals, and carefully layered details brought warmth and grandeur to the outdoor space, creating a welcoming atmosphere for family and friends gathered from near and far. The transformation honoured a life well lived with a celebration that felt vibrant, dignified, and beautifully composed.",
//     beforeImg: "/images/beforeafterpage/beforeone.jpg",
//     afterImg: "/images/beforeafterpage/afterone.jpg",
//     sliderOnRight: true,
//   },
//   {
//     location: "LAGOS, NIGERIA — 2022",
//     subtitle: "An Evening Inspired by Purpose",
//     title: "IMPACT\nELEVATED",
//     description:
//       "What began as a simple event space was thoughtfully transformed into an elegant setting for an evening of giving and impact. Through intentional design, layered lighting, refined tablescapes, and carefully considered details, the atmosphere encouraged meaningful conversation, genuine connection, and generosity. Every element was designed to support the purpose of the evening—creating a warm, welcoming environment that inspired guests to gather, engage, and give.",
//     beforeImg: "/images/beforeafterpage/beforetwo.jpg",
//     afterImg: "/images/beforeafterpage/aftertwo.jpg",
//     sliderOnRight: false,
//   },
//   {
//     location: "LAGOS, NIGERIA — 2021",
//     subtitle: "A Milestone Celebration in Elegance",
//     title: "GRACE\nCELEBRATED",
//     description:
//       "Rather than seeking a grand venue, we reimagined the place that meant the most—home. Set within the family compound, the celebration embraced familiar surroundings and transformed them through warm gold tones, regal purple accents, and a visual story centred on the celebrant herself. Layered portraits, thoughtful styling, and carefully composed details created an atmosphere that felt both intimate and magnificent, proving that meaningful design is never defined by location.",
//     beforeImg: "/images/beforeafterpage/beforethree.jpg",
//     afterImg: "/images/beforeafterpage/afterthree.jpg",
//     sliderOnRight: true,
//   },
//   {
//     location: "LAGOS, NIGERIA — 2026",
//     subtitle: "A Forum for Meaningful Change",
//     title: "CONNECTION\nCULTIVATED",
//     description:
//       "What began as a conventional conference venue became an inviting setting for meaningful conversation. Clean layouts, thoughtful styling, and carefully coordinated details softened the space while maintaining a polished corporate aesthetic. Completed from concept to execution in just five days, the transformation created an environment that felt warm, professional, and purposefully designed for connection.",
//     beforeImg: "/images/beforeafterpage/beforefour.jpg",
//     afterImg: "/images/beforeafterpage/afterfour.jpg",
//     sliderOnRight: false,
//   },
// ];

// export default function Transformations() {
//   return (
//     <>
//       {transformations.map((item, index) => (
//         <section key={index} className="bg-[#EEEEEE] py-10 sm:py-12 md:py-14 lg:py-12 xl:py-16 2xl:py-20">
//           <div className="max-w-7xl 2xl:max-w-[1600px] mx-auto px-6 sm:px-8 md:px-10 lg:px-10 xl:px-14 2xl:px-20">
//             <div className="grid grid-cols-1 lg:grid-cols-10 gap-12 lg:gap-16 xl:gap-20 2xl:gap-24 items-start">
//               {/* Text column */}
//               <div
//                 className={`lg:col-span-6 ${
//                   item.sliderOnRight ? "lg:order-1" : "lg:order-2"
//                 }`}
//               >
//                 {/* Location */}
//                 <p
//                   className={`font-sans font-light text-primary tracking-wider mb-2 text-[12px] sm:text-[13px] md:text-[14px] lg:text-[15px] xl:text-[16px] 2xl:text-[17px] ${
//                     item.sliderOnRight ? "text-left" : "lg:text-right text-left"
//                   }`}
//                 >
//                   {item.location}
//                 </p>

//                 {/* Subtitle */}
//                 <h3
//                   className={`font-display font-thin text-primary mb-8 sm:mb-10 md:mb-12 lg:mb-12 xl:mb-14 text-[24px] leading-[1.2] sm:text-[28px] sm:leading-[1.2] md:text-[34px] md:leading-[1.2] lg:text-[40px] lg:leading-[48px] xl:text-[46px] xl:leading-[56px] 2xl:text-[52px] 2xl:leading-[62px] ${
//                     item.sliderOnRight ? "text-left" : "lg:text-right text-left"
//                   }`}
//                 >
//                   {item.subtitle}
//                 </h3>

//                 {/* Mobile/landscape/iPad slider, sits between subtitle and big title */}
//                 <div className="lg:hidden mb-10 sm:mb-12 md:mb-14">
//                   <ComparisonSlider
//                     beforeImg={item.beforeImg}
//                     afterImg={item.afterImg}
//                   />
//                 </div>

//                 {/* Big title */}
//                 <h2 className="font-display font-thin text-primary text-center mb-8 sm:mb-10 md:mb-10 lg:mb-10 xl:mb-12 text-[44px] leading-[1.05] sm:text-[56px] sm:leading-[1.05] md:text-[72px] md:leading-[1.05] lg:text-[92px] lg:leading-[92px] xl:text-[108px] xl:leading-[108px] 2xl:text-[124px] 2xl:leading-[124px]">
//                   {item.title.split("\n").map((line, i) => (
//                     <span key={i} className="block">
//                       {line}
//                     </span>
//                   ))}
//                 </h2>

//                 {/* Description */}
//                 <p className="font-body font-light text-primary text-center text-[14px] leading-[24px] sm:text-[15px] sm:leading-[26px] md:text-[17px] md:leading-[28px] lg:text-[20px] lg:leading-[28.8px] xl:text-[22px] xl:leading-[32px] 2xl:text-[24px] 2xl:leading-[34px] max-w-[800px] xl:max-w-[920px] 2xl:max-w-[1040px] mx-auto">
//                   {item.description}
//                 </p>
//               </div>

//               {/* Desktop slider column */}
//               <div
//                 className={`hidden lg:block lg:col-span-4 ${
//                   item.sliderOnRight ? "lg:order-2" : "lg:order-1"
//                 }`}
//               >
//                 <ComparisonSlider
//                   beforeImg={item.beforeImg}
//                   afterImg={item.afterImg}
//                 />
//               </div>
//             </div>
//           </div>
//         </section>
//       ))}
//     </>
//   );
// }




"use client";

import ScrollReveal from "@/components/ui/ScrollReveal";

const transformations = [
    {
        location: "LAGOS, NIGERIA — 2022",
        subtitle: "A Celebration Across Generations",
        title: "LEGACY\nHONOURED",
        description: "What began as a familiar family compound was transformed into a regal setting worthy of eighty-five remarkable years. Rich shades of royal purple, complemented by warm touches of gold, elegant florals, and carefully layered details brought warmth and grandeur to the outdoor space, creating a welcoming atmosphere for family and friends gathered from near and far. The transformation honoured a life well lived with a celebration that felt vibrant, dignified, and beautifully composed.",
        beforeImg: "/images/beforeafterpage/beforeone.jpg",
        afterImg: "/images/beforeafterpage/afterone.jpg",
        sliderOnRight: true,
    },
    {
        location: "LAGOS, NIGERIA — 2022",
        subtitle: "An Evening Inspired by Purpose",
        title: "IMPACT\nELEVATED",
        description: "What began as a simple event space was thoughtfully transformed into an elegant setting for an evening of giving and impact. Through intentional design, layered lighting, refined tablescapes, and carefully considered details, the atmosphere encouraged meaningful conversation, genuine connection, and generosity. Every element was designed to support the purpose of the evening—creating a warm, welcoming environment that inspired guests to gather, engage, and give.",
        beforeImg: "/images/beforeafterpage/beforetwo.jpg",
        afterImg: "/images/beforeafterpage/aftertwo.jpg",
        sliderOnRight: false,
    },
    {
        location: "LAGOS, NIGERIA — 2021",
        subtitle: "A Milestone Celebration in Elegance",
        title: "GRACE\nCELEBRATED",
        description: "Rather than seeking a grand venue, we reimagined the place that meant the most—home. Set within the family compound, the celebration embraced familiar surroundings and transformed them through warm gold tones, regal purple accents, and a visual story centred on the celebrant herself. Layered portraits, thoughtful styling, and carefully composed details created an atmosphere that felt both intimate and magnificent, proving that meaningful design is never defined by location.",
        beforeImg: "/images/beforeafterpage/beforethree.jpg",
        afterImg: "/images/beforeafterpage/afterthree.jpg",
        sliderOnRight: true,
    },
    {
        location: "LAGOS, NIGERIA — 2026",
        subtitle: "A Forum for Meaningful Change",
        title: "CONNECTION\nCULTIVATED",
        description: "What began as a conventional conference venue became an inviting setting for meaningful conversation. Clean layouts, thoughtful styling, and carefully coordinated details softened the space while maintaining a polished corporate aesthetic. Completed from concept to execution in just five days, the transformation created an environment that felt warm, professional, and purposefully designed for connection.",
        beforeImg: "/images/beforeafterpage/beforefour.jpg",
        afterImg: "/images/beforeafterpage/afterfour.jpg",
        sliderOnRight: false,
    },
];

interface TransformationProps {
    item: typeof transformations[0];
}

function Transformation({ item }: TransformationProps) {
    return (
        <div data-scroll-pin className="relative h-[200vh]">
            <section className="sticky top-[-50px] bg-[#EEEEEE] flex items-center z-10">
                <div className="w-full max-w-7xl 2xl:max-w-[1600px] mx-auto px-6 sm:px-8 md:px-10 lg:px-10 xl:px-14 2xl:px-20 py-10 sm:py-12 md:py-14 lg:py-12 xl:py-16 2xl:py-20">
                    <div className="grid grid-cols-1 lg:grid-cols-10 gap-12 lg:gap-16 xl:gap-20 2xl:gap-24 items-start">
                        <div className={`lg:col-span-6 ${item.sliderOnRight ? "lg:order-1" : "lg:order-2"}`}>
                            <p className={`font-sans font-light text-primary tracking-wider mb-2 text-[12px] sm:text-[13px] md:text-[14px] lg:text-[15px] xl:text-[16px] 2xl:text-[17px] ${item.sliderOnRight ? "text-left" : "lg:text-right text-left"}`}>
                                {item.location}
                            </p>
                            <h3 className={`font-display font-thin text-primary mb-8 sm:mb-10 md:mb-12 lg:mb-12 xl:mb-14 text-[24px] leading-[1.2] sm:text-[28px] sm:leading-[1.2] md:text-[34px] md:leading-[1.2] lg:text-[40px] lg:leading-[48px] xl:text-[46px] xl:leading-[56px] 2xl:text-[52px] 2xl:leading-[62px] ${item.sliderOnRight ? "text-left" : "lg:text-right text-left"}`}>
                                {item.subtitle}
                            </h3>
                            <div className="lg:hidden mb-10 sm:mb-12 md:mb-14">
                                <ScrollReveal beforeImg={item.beforeImg} afterImg={item.afterImg} />
                            </div>
                            <h2 className="font-display font-thin text-primary text-center mb-8 sm:mb-10 md:mb-10 lg:mb-10 xl:mb-12 text-[44px] leading-[1.05] sm:text-[56px] sm:leading-[1.05] md:text-[72px] md:leading-[1.05] lg:text-[92px] lg:leading-[92px] xl:text-[108px] xl:leading-[108px] 2xl:text-[124px] 2xl:leading-[124px]">
                                {item.title.split("\n").map((line, i) => (
                                    <span key={i} className="block">
                                        {line}
                                    </span>
                                ))}
                            </h2>
                            <p className="font-body font-light text-primary text-center text-[14px] leading-[24px] sm:text-[15px] sm:leading-[26px] md:text-[17px] md:leading-[28px] lg:text-[20px] lg:leading-[28.8px] xl:text-[22px] xl:leading-[32px] 2xl:text-[24px] 2xl:leading-[34px] max-w-[800px] xl:max-w-[920px] 2xl:max-w-[1040px] mx-auto">
                                {item.description}
                            </p>
                        </div>
                        <div className={`hidden lg:block lg:col-span-4 ${item.sliderOnRight ? "lg:order-2" : "lg:order-1"}`}>
                            <ScrollReveal beforeImg={item.beforeImg} afterImg={item.afterImg} />
                        </div>
                    </div>
                </div>
            </section>
        </div>
    );
}

export default function Transformations() {
    return (
        <>
            {transformations.map((item, index) => (
                <Transformation key={index} item={item} />
            ))}
        </>
    );
}