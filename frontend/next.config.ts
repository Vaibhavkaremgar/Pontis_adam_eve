import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  allowedDevOrigins: ["127.0.0.1"],

  experimental: {
    cpus: 1,
    workerThreads: true,
  },

  webpack(config) {
    return config;
  },
};

export default nextConfig;
