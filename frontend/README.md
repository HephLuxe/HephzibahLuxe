This is a [Next.js](https://nextjs.org) project bootstrapped with [`create-next-app`](https://nextjs.org/docs/app/api-reference/cli/create-next-app).

## Getting Started

First, run the development server:

```bash
npm run dev
# or
yarn dev
# or
pnpm dev
# or
bun dev
```

Open [http://localhost:3000](http://localhost:3000) with your browser to see the result.

You can start editing the page by modifying `app/page.tsx`. The page auto-updates as you edit the file.

This project uses [`next/font`](https://nextjs.org/docs/app/building-your-application/optimizing/fonts) to automatically optimize and load [Geist](https://vercel.com/font), a new font family for Vercel.

## Learn More

To learn more about Next.js, take a look at the following resources:

- [Next.js Documentation](https://nextjs.org/docs) - learn about Next.js features and API.
- [Learn Next.js](https://nextjs.org/learn) - an interactive Next.js tutorial.

You can check out [the Next.js GitHub repository](https://github.com/vercel/next.js) - your feedback and contributions are welcome!

## Deploy on Vercel

The easiest way to deploy your Next.js app is to use the [Vercel Platform](https://vercel.com/new?utm_medium=default-template&filter=next.js&utm_source=create-next-app&utm_campaign=create-next-app-readme) from the creators of Next.js.

Check out our [Next.js deployment documentation](https://nextjs.org/docs/app/building-your-application/deploying) for more details.

## Public API and homepage strip

Copy `.env.example` to `.env.local` and set `API_BASE_URL` to the Django origin
(no trailing slash). Server fetches prefer `API_BASE_URL`, then
`NEXT_PUBLIC_API_BASE_URL`; development defaults to `http://localhost:8000`.
Outside development, one of these variables is required. The public variable is
also exposed to browser code, so do not put secrets in it.

The homepage reads `GET /api/v1/public/home-strip/`, a bare ordered array of
`{ image: absoluteURL, alt_text: string, sort_order: number }`. Backend content
must contain the original six decorative photos in their original order. The
frontend preserves response order, duplicates the array for the seamless loop,
and does not substitute event covers or static image URLs. Photos have no event
links; the existing portfolio CTA remains. An empty response or API failure
hides this section, matching its existing failure behavior.

The strip uses the portfolio client's JSON Accept header, API base selection,
300-second Next.js revalidation, and `ApiError` handling. Media URLs must match
`images.remotePatterns` in `next.config.ts`. New backend content may remain
cached for five minutes; Next.js development HMR can also retain fetch responses.

Local checks: `npx tsc --noEmit --incremental false` and `npm run lint`.
Do not run `next build` alongside an active development server sharing `.next`.
