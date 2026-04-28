import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  experimental: {
    cpus: 1,
    workerThreads: true
  },
  typescript: {
    ignoreBuildErrors: true
  },
  webpack(config) {
    return config;
  }
};

export default nextConfig;
