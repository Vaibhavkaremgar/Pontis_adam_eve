import type { NextConfig } from "next";
import path from "node:path";

const nextConfig: NextConfig = {
  output: "standalone",
  outputFileTracingRoot: path.resolve(__dirname),
  allowedDevOrigins: ["127.0.0.1"],

  experimental: {
    cpus: 1,
    workerThreads: true,
  },

  async headers() {
    return [
      {
        source: "/(.*)",
        headers: [
          // Required for Vapi/Daily.co WebRTC — allows cross-origin postMessage
          { key: "Cross-Origin-Opener-Policy", value: "same-origin-allow-popups" },
          { key: "Cross-Origin-Embedder-Policy", value: "unsafe-none" },
        ],
      },
    ];
  },

  webpack(config) {
    return config;
  },
};

export default nextConfig;
