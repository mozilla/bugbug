/** @type {import('next').NextConfig} */
const nextConfig = {
  // Emit a self-contained server bundle so the Docker image stays small.
  output: "standalone",
  reactStrictMode: true,
};

export default nextConfig;
