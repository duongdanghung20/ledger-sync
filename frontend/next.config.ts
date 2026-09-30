import type { NextConfig } from "next";

// In production the Caddy proxy routes /api/* to the backend before a request
// ever reaches Next, so no rewrite is needed (and this stays unset). For a
// dev / e2e stack without Caddy, set API_PROXY_TARGET (e.g. http://127.0.0.1:8000)
// to forward same-origin /api/* calls to the FastAPI backend.
const apiTarget = process.env.API_PROXY_TARGET;

const nextConfig: NextConfig = {
  // Emit a self-contained server bundle so the Docker image stays small.
  output: "standalone",
  ...(apiTarget
    ? {
        async rewrites() {
          return [{ source: "/api/:path*", destination: `${apiTarget}/api/:path*` }];
        },
      }
    : {}),
};

export default nextConfig;
