import type { NextConfig } from "next";

const API_ORIGIN = process.env.API_ORIGIN ?? "http://localhost:8000";

const config: NextConfig = {
  // Standalone output keeps the runtime image small: only the traced server
  // files ship, not the whole node_modules tree.
  output: "standalone",
  reactStrictMode: true,
  poweredByHeader: false,

  async rewrites() {
    // The API is proxied under /api so the browser talks to a single origin.
    // That is what lets the refresh token live in an httpOnly cookie: a
    // cross-origin setup would need SameSite=None and a CORS credential dance.
    // In production Caddy does this; in development Next does it.
    return [{ source: "/api/:path*", destination: `${API_ORIGIN}/:path*` }];
  },
};

export default config;
