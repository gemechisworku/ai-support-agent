import type { NextConfig } from "next";

// The console (specs/11-console): server-rendered pages over the console API. `standalone` output is
// what docker/console.Dockerfile ships. Cache Components is on (create-next-app default): data
// components stream inside <Suspense> and are fetched per request, so builds never call the API.
const nextConfig: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
  cacheComponents: true,
  partialPrefetching: true,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
