import type { NextConfig } from "next";

// API calls go to /api/* and are proxied to the FastAPI backend.
const backend = process.env.CEP_API_URL ?? "http://localhost:8000";

const config: NextConfig = {
  output: "standalone",
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backend}/api/:path*` }];
  },
};

export default config;
