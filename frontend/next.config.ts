import type { NextConfig } from "next";

const apiOrigin = process.env.MULTEAGENT_API_ORIGIN || "http://127.0.0.1:8787";

const nextConfig: NextConfig = {
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${apiOrigin}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
