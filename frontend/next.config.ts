import type { NextConfig } from "next";
import { PHASE_DEVELOPMENT_SERVER } from "next/constants";

export default function config(phase: string): NextConfig {
  const isDev = phase === PHASE_DEVELOPMENT_SERVER;

  return {
    images: {
      remotePatterns: [
        // Django media served by the API host
        { protocol: "https", hostname: "api.hephzibahluxe.com", pathname: "/media/**" },
        // Public R2 bucket(s), e.g. pub-669da6a43807482d902305db5be0872b.r2.dev
        { protocol: "https", hostname: "*.r2.dev", pathname: "/**" },
        // Public R2 bucket behind the custom domain (R2_PUBLIC_URL)
        { protocol: "https", hostname: "media.hephzibahluxe.com", pathname: "/**" },
        // Local Django dev server (dev only)
        ...(isDev
          ? [
              { protocol: "http" as const, hostname: "localhost", port: "8000", pathname: "/media/**" },
              { protocol: "http" as const, hostname: "127.0.0.1", port: "8000", pathname: "/media/**" },
            ]
          : []),
      ],
      // Next 16 refuses to optimize images that resolve to private/loopback IPs.
      // Allow it only for `next dev` so localhost:8000 media works locally.
      dangerouslyAllowLocalIP: isDev,
    },
  };
}
