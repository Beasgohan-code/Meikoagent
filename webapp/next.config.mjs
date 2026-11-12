/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  output: "standalone",
  eslint: { ignoreDuringBuilds: true },
  // The browser only ever talks to this origin. `/api/backend/*` is handled by
  // the route handler in src/app/api/backend/[...path]/route.ts, which proxies
  // to the FastAPI control plane using MEIKO_API_URL resolved at request time.
};

export default nextConfig;
