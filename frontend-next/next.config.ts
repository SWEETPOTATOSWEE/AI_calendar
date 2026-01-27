import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // output: "export", // rewrites를 사용하려면 export 모드를 비활성화해야 함
  trailingSlash: true,
  images: {
    unoptimized: true,
  },
  async rewrites() {
    const backendBase = process.env.BACKEND_BASE_URL || "http://localhost:8000";
    return [
      {
        source: "/api/:path*",
        destination: `${backendBase}/api/:path*`,
      },
      {
        source: "/auth/:path*",
        destination: `${backendBase}/auth/:path*`,
      },
      {
        source: "/admin",
        destination: `${backendBase}/admin`,
      },
      {
        source: "/admin/exit",
        destination: `${backendBase}/admin/exit`,
      },
      {
        source: "/logout",
        destination: `${backendBase}/logout`,
      },
    ];
  },
};

export default nextConfig;

