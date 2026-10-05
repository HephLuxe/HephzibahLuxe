// Client testimonials shown inside portfolio galleries. These live on the
// frontend (not in the CMS) and are merged into the API-driven gallery rows by
// lib/portfolio.ts.
//
// eventSlug / subEventSlug are the public slugs from the portfolio API.
// Leave subEventSlug out for single-day events. afterRow is the number of
// image rows shown before the quote (0 = above the gallery); values past the
// end of the gallery put the quote at the end.

export interface PortfolioTestimonial {
  eventSlug: string;
  subEventSlug?: string;
  afterRow: number;
  quote: string;
  attribution: string;
}

export const portfolioTestimonials: PortfolioTestimonial[] = [
  {
    eventSlug: "golden-50th",
    subEventSlug: "pre-birthday-photoshoot",
    afterRow: 4,
    quote:
      "I wanted something simple but meaningful—and that's exactly what this was. Every detail felt intentional, and the photos captured me in such a beautiful, authentic, and timeless way. There was a quiet attention to detail that made me feel completely at ease, and it truly showed in the final images. What I loved most was that the focus never shifted away from me. It felt like the perfect way to step into fifty.",
    attribution: "Winnie, Celebrant",
  },
  {
    eventSlug: "golden-50th",
    subEventSlug: "thanksgiving-gathering",
    afterRow: 4,
    quote:
      "This moment meant so much more to me than I can fully express. To be surrounded by the people who have walked this journey with me, lifting me up in prayer and sharing in this time of thanksgiving, was truly special. It reminded me of how blessed I am—not just for the years, but for the love, support, and grace that have carried me through them. I'm so thankful to God for how far He has brought me and for the people He has placed in my life.",
    attribution: "Winnie, Celebrant",
  },
  {
    eventSlug: "golden-50th",
    subEventSlug: "celebration-night",
    afterRow: 4,
    quote:
      "I told myself that when the time came to celebrate, I was going to celebrate fully—and this night was exactly that. From the music and dancing to the laughter, every moment felt alive. To be surrounded by so much love and joy, all in one room, is something I'll never forget.",
    attribution: "Winnie, Celebrant",
  },
  {
    eventSlug: "intimate-85th",
    afterRow: 4,
    quote:
      "With very little time to plan, we weren't sure how everything would come together, but Hephzibah Luxe handled every detail with such care and calmness. Watching our mother celebrate alongside her church community, family, and close friends was incredibly special, and the atmosphere felt warm, graceful, and a true reflection of her life and faith.",
    attribution: "Olamipe, Daughter of Celebrant",
  },
  {
    eventSlug: "msme-forum",
    afterRow: 4,
    quote:
      "Working with Hephzibah Luxe was a wonderful experience. Despite the short timeline and limited budget, the team ensured the event was well organised and executed seamlessly from start to finish. Their coordination and attention to detail created a welcoming and professional atmosphere for our guests, and we truly appreciated their calm, thoughtful approach throughout the process.",
    attribution: "Mrs. Abimbola, OPL&CE Manager",
  },
];
