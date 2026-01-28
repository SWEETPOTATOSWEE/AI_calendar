import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // output: "export", // rewrites를 사용하려면 export 모드를 비활성화해야 함
  trailingSlash: true,
  images: {
    unoptimized: true,
  },
  // rewrites 제거: app router의 프록시 라우트에서 처리
};

export default nextConfig;
