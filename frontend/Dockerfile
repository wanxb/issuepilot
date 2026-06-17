# ============================================================================
# IssuePilot Frontend — 开发用镜像（Next.js 14 dev mode + hot reload）
# ============================================================================

FROM node:20-bookworm-slim

# 系统依赖：libc6 升级 + curl（健康检查）
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 启用 pnpm（更快、且与主机 pnpm 9.13 对齐）
ENV PNPM_HOME=/pnpm
ENV PATH="${PNPM_HOME}:${PATH}"
RUN corepack enable && corepack prepare pnpm@9.13.2 --activate

WORKDIR /app

# 仅拷依赖描述文件做 layer 缓存
COPY package.json ./
# pnpm-lock.yaml 由首次本地 install 生成；缺失时 pnpm 仍可工作
COPY pnpm-lock.yaml* ./

RUN --mount=type=cache,id=pnpm,target=/pnpm/store \
    pnpm install --prefer-offline

COPY . .

EXPOSE 3000

# Windows 主机挂载时 inotify 不可靠 → 启用 polling
ENV WATCHPACK_POLLING=true \
    CHOKIDAR_USEPOLLING=true

CMD ["pnpm", "dev"]
