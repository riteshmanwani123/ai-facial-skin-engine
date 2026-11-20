import type { NextConfig } from "next";

const API_BASE = process.env.CLARITY_API_BASE || "http://localhost:8010";

const nextConfig: NextConfig = {
  // The portal pages load plain scripts from /public/js. Force revalidation so a browser never pairs a new page
  // with a stale cached script (it showed up as "null is not an object" crashes after updates).
  async headers() {
    return [
      { source: "/js/:path*", headers: [{ key: "Cache-Control", value: "no-cache, must-revalidate" }] },
      { source: "/css/:path*", headers: [{ key: "Cache-Control", value: "no-cache, must-revalidate" }] },
    ];
  },
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${API_BASE}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
