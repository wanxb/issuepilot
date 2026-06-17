# ============================================================================
# IssuePilot Backend — 开发用镜像
#
# 使用 uv 作为包管理；运行时 volume-mount 源码以支持 hot reload。
# 生产镜像在后续里程碑（Phase 2）单独优化（多阶段、小体积）。
# ============================================================================

FROM python:3.11-slim-bookworm

# 系统依赖：构建工具（asyncpg / lxml 等需要）、curl（健康检查），ca-certificates
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        ca-certificates \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# uv（来自 Astral 官方镜像，体积小）
COPY --from=ghcr.io/astral-sh/uv:0.8.15 /uv /uvx /usr/local/bin/

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:${PATH}"

WORKDIR /app

# 先拷依赖文件单独 sync，让 layer 可缓存
COPY pyproject.toml ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-install-project --frozen 2>/dev/null \
    || uv sync --no-install-project

# 拷源码（dev 模式下被 volume 覆盖，但确保镜像本身可独立运行）
COPY . .

EXPOSE 8000

# 默认启动 API；docker-compose 中 worker service 覆盖 command
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
