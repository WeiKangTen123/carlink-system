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

  // Files under /public are served with `Cache-Control: max-age=0` by
  // default, because Next can't know whether their contents changed. That
  // meant the 16.7MB vehicle model was re-downloaded in full on EVERY case
  // page view (~6s each time). These are static assets that only change
  // when the file itself is replaced, so cache them hard.
  async headers() {
    return [
      {
        source: "/assets/:path*",
        headers: [
          { key: "Cache-Control", value: "public, max-age=31536000, immutable" },
        ],
      },
    ];
  },
};

export default nextConfig;
