export type EventCategory = "Weddings" | "Birthdays" | "Corporate" | "Social Events";
export type EventType = "single-day" | "multi-day";

export type GalleryRow =
  | { type: "images"; images: string[]; ratios?: number[] }
  | { type: "testimonial"; quote: string; attribution: string };

export interface SubEvent {
  subtitle: string;
  title: string;
  image: string;
  slug: string;
  description?: string[];
  gallery?: GalleryRow[];
}

export interface PortfolioEvent {
  slug: string;
  type: EventType;
  category: EventCategory;
  location: string;
  year: number;
  title: string;
  coverImage: string;
  description?: string[];
  subEvents?: SubEvent[];
  gallery?: GalleryRow[];
}

export const portfolioEvents: PortfolioEvent[] = [
  {
    slug: "golden-50th",
    type: "multi-day",
    category: "Birthdays",
    location: "Lagos, Nigeria",
    year: 2021,
    title: "A Golden 50th: An Intimate Two-Day Celebration of Family, Faith & Joy",
    coverImage: "/images/portfoliopage/photoshootsixs.jpg",
    description: [
      "Turning fifty was not simply marked with a single event, but thoughtfully celebrated across two days that reflected a life rich in love, faith, and meaningful connection. Set within the comfort of home and the familiarity of community, the celebration unfolded gently—beginning with a heartfelt gathering of close family and friends before continuing into a joyful occasion filled with laughter, music, and shared moments.",
      "Rooted in gratitude and guided by intention, every element of the experience prioritised togetherness over grandeur. From the warmth of a thanksgiving gathering to the vibrancy of a celebratory party, the weekend felt deeply personal—a reflection of a life well lived and the people who helped shape it.",
      "The result was a celebration that felt less like an event and more like a beautiful continuation of a legacy—intimate, sincere, and filled with joy.",
    ],
    subEvents: [
      {
        subtitle: "Pre-Birthday Photoshoot",
        title: "A Moment Before Fifty — A Pre-Birthday Portrait Experience",
        image: "/images/portfoliopage/goldenone.jpg",
        slug: "pre-birthday-photoshoot",
        description: [
          "Before the celebrations began, there was a quiet moment to pause—to honour the woman at the heart of it all and the milestone she was about to embrace. This pre-birthday portrait session was designed to capture not simply an age, but a new season of life marked by grace, confidence, and quiet strength.",
          "Rather than relying on an elaborate setting, the experience remained intentionally understated. With a clean backdrop, every detail drew the focus back to what mattered most—her presence, her expression, and the story reflected in every frame.",
          "The result was a collection of portraits that felt timeless and deeply personal—a quiet yet powerful reflection of a life beautifully lived and the elegance of stepping into fifty.",
        ],
        gallery: [
          {
            type: "images",
            images: [
              "/images/portfoliopage/photoshootones.jpg",
              "/images/portfoliopage/photoshoottwos.jpg",
              "/images/portfoliopage/photoshootthrees.jpg",
            ],
          },
          {
            type: "images",
            images: ["/images/portfoliopage/photoshootfours.jpg"],
          },
          {
            type: "images",
            images: [
              "/images/portfoliopage/photoshootfives.jpg",
              "/images/portfoliopage/photoshootsixs.jpg",
            ],
          },
          {
            type: "images",
            images: ["/images/portfoliopage/photoshootsevens.jpg"],
          },
          {
            type: "testimonial",
            quote:
              "I wanted something simple but meaningful—and that's exactly what this was. Every detail felt intentional, and the photos captured me in such a beautiful, authentic, and timeless way. There was a quiet attention to detail that made me feel completely at ease, and it truly showed in the final images. What I loved most was that the focus never shifted away from me. It felt like the perfect way to step into fifty.",
            attribution: "Winnie, Celebrant",
          },
          {
            type: "images",
            images: [
              "/images/portfoliopage/photoshooteights.jpg",
              "/images/portfoliopage/photoshootnines.jpg",
            ],
            ratios: [3, 2],
          },
          {
            type: "images",
            images: [
              "/images/portfoliopage/photoshoottens.jpg",
              "/images/portfoliopage/photoshootelevens.jpg",
            ],
          },
          {
            type: "images",
            images: [
              "/images/portfoliopage/photoshoottwelves.jpg",
              "/images/portfoliopage/photoshootthirteens.jpg",
            ],
            ratios: [3, 2],
          },
          {
            type: "images",
            images: ["/images/portfoliopage/photoshootfourteens.jpg"],
          },
        ],
      },
      {
  subtitle: "Event No. 1",
  title: "Rooted in Gratitude — A Gathering of Thanksgiving & Reflection",
  image: "/images/portfoliopage/goldentwo.jpg",
  slug: "thanksgiving-gathering",
  description: [
    "The celebration began with a quiet and meaningful gathering centred on gratitude, faith, and reflection. Surrounded by close family, church members, and loved ones, the occasion unfolded with a sense of calm reverence—a gentle pause before the festivities to give thanks for a life richly lived.",
    "Led in prayer by the family pastor, the gathering created space for heartfelt words, blessings, and shared reflection. It was less about formality and more about presence—a coming together of community to honour the journey thus far and the years ahead.",
    "Following the prayers, guests shared light refreshments, warm conversation, and meaningful fellowship. The atmosphere remained simple yet deeply moving—a beautiful reminder that at the heart of every celebration are gratitude, faith, and the people who walk through life beside you.",
  ],
  gallery: [
    {
      type: "images",
      images: [
        "/images/portfoliopage/rootedones.jpg",
        "/images/portfoliopage/rootedtwos.jpg",
        "/images/portfoliopage/rootedthrees.jpg",
      ],
    },
    { type: "images", images: ["/images/portfoliopage/rootedfours.jpg"] },
    {
      type: "images",
      images: [
        "/images/portfoliopage/rootedfives.jpg",
        "/images/portfoliopage/rootedsixs.jpg",
      ],
    },
    { type: "images", images: ["/images/portfoliopage/rootedsevens.jpg"] },
    {
      type: "testimonial",
      quote:
        "This moment meant so much more to me than I can fully express. To be surrounded by the people who have walked this journey with me, lifting me up in prayer and sharing in this time of thanksgiving, was truly special. It reminded me of how blessed I am—not just for the years, but for the love, support, and grace that have carried me through them. I'm so thankful to God for how far He has brought me and for the people He has placed in my life.",
      attribution: "Winnie, Celebrant",
    },
    {
      type: "images",
      images: [
        "/images/portfoliopage/rootedeights.jpg",
        "/images/portfoliopage/rootednines.jpg",
      ],
      ratios: [3, 2],
    },
    {
      type: "images",
      images: [
        "/images/portfoliopage/rootedtens.jpg",
        "/images/portfoliopage/rootedelevens.jpg",
        "/images/portfoliopage/rootedtwelves.jpg",
      ],
    },
    {
      type: "images",
      images: [
        "/images/portfoliopage/rootedthirteens.jpg",
        "/images/portfoliopage/rootedfourteens.jpg",
      ],
      ratios: [3, 2],
    },
  ],
},
{
  subtitle: "Event No. 2",
  title: "Fifty, Unforgettable — An Evening of Music, Dance & Celebration",
  image: "/images/portfoliopage/goldenthree.jpg",
  slug: "celebration-night",
  description: [
    "If the earlier gathering was a quiet moment of reflection, this evening was its joyful release. As night fell, the atmosphere shifted into one of vibrant celebration—a space filled with music, movement, and the unmistakable energy of loved ones coming together to honour a life well lived.",
    "Friends and family gathered not simply to mark a milestone, but to celebrate it wholeheartedly. The room came alive with laughter, dancing, and shared joy as every guest became part of the experience. It was effortless, spirited, and full of life—the kind of celebration where time seems to soften and all that remains is the rhythm of the evening.",
    "At the heart of it all was the celebrant—radiant, joyful, and fully present—surrounded by love in its most expressive form. It was more than a party; it was an unforgettable evening filled with vibrancy, meaning, and celebration.",
  ],
  gallery: [
    {
      type: "images",
      images: [
        "/images/portfoliopage/pureones.jpg",
        "/images/portfoliopage/puretwos.jpg",
      ],
    },
    {
      type: "images",
      images: [
        "/images/portfoliopage/purethrees.jpg",
        "/images/portfoliopage/purefours.jpg",
        "/images/portfoliopage/purefives.jpg",
      ],
    },
    {
      type: "images",
      images: [
        "/images/portfoliopage/puresixs.jpg",
        "/images/portfoliopage/puresevens.jpg",
      ],
    },
    {
      type: "images",
      images: [
        "/images/portfoliopage/pureeights.jpg",
        "/images/portfoliopage/purenines.jpg",
      ],
      ratios: [2, 3],
    },
    {
      type: "testimonial",
      quote:
        "I told myself that when the time came to celebrate, I was going to celebrate fully—and this night was exactly that. From the music and dancing to the laughter, every moment felt alive. To be surrounded by so much love and joy, all in one room, is something I'll never forget.",
      attribution: "Winnie, Celebrant",
    },
    {
      type: "images",
      images: [
        "/images/portfoliopage/puretens.jpg",
        "/images/portfoliopage/pureelevens.jpg",
      ],
    },
    { type: "images", images: ["/images/portfoliopage/puretwelves.jpg"] },
    { type: "images", images: ["/images/portfoliopage/purethirteens.jpg"] },
    {
      type: "images",
      images: [
        "/images/portfoliopage/purefourteens.jpg",
        "/images/portfoliopage/purefifteens.jpg",
      ],
    },
    { type: "images", images: ["/images/portfoliopage/puresixteens.jpg"] },
    {
      type: "images",
      images: [
        "/images/portfoliopage/pureseventeens.jpg",
        "/images/portfoliopage/pureeighteens.jpg",
      ],
    },
    { type: "images", images: ["/images/portfoliopage/purenineteens.jpg"] },
  ],
},
    ],
  },
  {
  slug: "intimate-85th",
  type: "single-day",
  category: "Birthdays",
  location: "Lagos, Nigeria",
  year: 2022,
  title: "An Intimate 85th: A Celebration of Grace, Family & Legacy",
  coverImage: "/images/portfoliopage/portfoliothirteen.jpg",
  description: [
    "Eighty-five years of life, love, faith, and family were gently honoured through an intimate gathering centred on togetherness and gratitude. Surrounded by children, grandchildren, close friends, and loved ones, the celebration created space to reflect on a legacy shaped by wisdom, compassion, and enduring strength.",
    "Designed to feel warm, personal, and deeply meaningful, the experience prioritised connection over formality, allowing guests to celebrate not only a milestone birthday, but the remarkable woman at its heart. From heartfelt conversations to joyful moments of togetherness, every detail contributed to an atmosphere that felt sincere, graceful, and filled with love.",
    "The result was a quiet yet beautiful celebration of legacy—one that honoured a life well lived and the generations inspired by it.",
  ],
  gallery: [
    { type: "images", images: ["/images/portfoliopage/legacyones.jpg"] },
    {
      type: "images",
      images: [
        "/images/portfoliopage/legacytwos.jpg",
        "/images/portfoliopage/legacythrees.jpg",
      ],
    },
    {
      type: "images",
      images: [
        "/images/portfoliopage/legacyfours.jpg",
        "/images/portfoliopage/legacyfives.jpg",
      ],
      ratios: [2, 3],
    },
    {
      type: "images",
      images: [
        "/images/portfoliopage/legacysixs.jpg",
        "/images/portfoliopage/legacysevens.jpg",
        "/images/portfoliopage/legacyeights.jpg",
      ],
    },
    {
      type: "testimonial",
      quote:
        "With very little time to plan, we weren't sure how everything would come together, but Hephzibah Luxe handled every detail with such care and calmness. Watching our mother celebrate alongside her church community, family, and close friends was incredibly special, and the atmosphere felt warm, graceful, and a true reflection of her life and faith.",
      attribution: "Olamipe, Daughter of Celebrant",
    },
    {
      type: "images",
      images: [
        "/images/portfoliopage/legacynines.jpg",
        "/images/portfoliopage/legacytens.jpg",
      ],
      ratios: [3, 2],
    },
    {
      type: "images",
      images: [
        "/images/portfoliopage/legacyelevens.jpg",
        "/images/portfoliopage/legacytwelves.jpg",
      ],
    },
    {
      type: "images",
      images: [
        "/images/portfoliopage/legacythirteens.jpg",
        "/images/portfoliopage/legacyfourteens.jpg",
        "/images/portfoliopage/legacyfifteens.jpg",
      ],
    },
    {
      type: "images",
      images: [
        "/images/portfoliopage/legacysixteens.jpg",
        "/images/portfoliopage/legacyseventeens.jpg",
      ],
      ratios: [3, 2],
    },
    { type: "images", images: ["/images/portfoliopage/legacyeighteens.jpg"] },
  ],
},
  {
    slug: "msme-forum",
    type: "single-day",
    category: "Corporate",
    location: "Lagos, Nigeria",
    year: 2026,
    title: "Lagos State Government MSME Engagement Forum",
    coverImage: "/images/portfoliopage/portfoliofourteen.jpg",
    description: [
    "This corporate seminar was designed to bring together stakeholders for a meaningful conversation around social responsibility and community impact. Hosted in collaboration with the Lagos State Government, the event welcomed guests for an afternoon of discussion, knowledge sharing, and collective reflection.",
    "With just five days from planning to execution, Hephzibah Luxe supported the coordination of the experience with clarity and precision, ensuring a seamless guest journey from arrival through to the close of the programme. From registration and seating flow to hospitality and on-site coordination, every detail was carefully managed to create an organised, welcoming environment for both speakers and attendees.",
    "The result was a thoughtful and purposeful gathering that fostered meaningful dialogue, strengthened connections, and reflected a shared commitment to positive community impact.",
  ],
  gallery: [
    {
      type: "images",
      images: [
        "/images/portfoliopage/lagosones.jpg",
        "/images/portfoliopage/lagostwos.jpg",
        "/images/portfoliopage/lagosthrees.jpg",
      ],
    },
    {
      type: "images",
      images: [
        "/images/portfoliopage/lagosfours.jpg",
      ],
    },
    {
      type: "images",
      images: [
        "/images/portfoliopage/lagosfives.jpg",
        "/images/portfoliopage/lagossixs.jpg",
      ],
    },
    {
      type: "images",
      images: [
        "/images/portfoliopage/lagossevens.jpg",
      ],
    },
    {
      type: "testimonial",
      quote:
        "Working with Hephzibah Luxe was a wonderful experience. Despite the short timeline and limited budget, the team ensured the event was well organised and executed seamlessly from start to finish. Their coordination and attention to detail created a welcoming and professional atmosphere for our guests, and we truly appreciated their calm, thoughtful approach throughout the process.",
      attribution: "Mrs. Abimbola, OPL&CE Manager",
    },
    {
      type: "images",
      images: [
        "/images/portfoliopage/lagoseights.jpg",
      ],
    },
    {
      type: "images",
      images: [
        "/images/portfoliopage/lagosnines.jpg",
        "/images/portfoliopage/lagostens.jpg",
      ],
    },
    {
      type: "images",
      images: [
        "/images/portfoliopage/lagoselevens.jpg",
        "/images/portfoliopage/lagostwelves.jpg",
      ],
      ratios: [3, 2],
    },
    { type: "images", images: ["/images/portfoliopage/lagosthirteens.jpg"] },
  ],
  },
];

export function getEventBySlug(slug: string): PortfolioEvent | undefined {
  return portfolioEvents.find((e) => e.slug === slug);
}