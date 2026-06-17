/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,

  // API 代理：前端 /api/* → 后端 /api/*
  // 在 docker-compose 内由 NEXT_PUBLIC_API_INTERNAL_URL 指向 api service
  async rewrites() {
    const apiBase =
      process.env.NEXT_PUBLIC_API_INTERNAL_URL || "http://localhost:8000";
    return [
      {
        source: "/api/:path*",
        destination: `${apiBase}/api/:path*`,
      },
      {
        source: "/healthz",
        destination: `${apiBase}/healthz`,
      },
      {
        source: "/readyz",
        destination: `${apiBase}/readyz`,
      },
    ];
  },

  // Windows + Docker volume mount 场景下，文件 inotify 不可靠，回退到 polling
  // 仅 dev 模式启用，由 WATCHPACK_POLLING=true 环境变量触发
  webpack: (config, { dev }) => {
    if (dev && process.env.WATCHPACK_POLLING === "true") {
      config.watchOptions = {
        poll: 1000,
        aggregateTimeout: 300,
      };
    }
    return config;
  },
};

export default nextConfig;
