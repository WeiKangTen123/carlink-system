import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  experimental: {
    serverActions: {
      // Server Actions default to a 1MB body limit -- easily exceeded by a
      // single real phone-camera photo (typically 2-8MB), let alone the
      // multiple photos a report can attach. Photo uploads to
      // analyzePhotosAction (see app/reports/new/actions.ts) were silently
      // hanging rather than erroring because of this.
      bodySizeLimit: "25mb",
    },
  },
};

export default nextConfig;
