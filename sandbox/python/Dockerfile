# ============================================================================
# agent-sandbox-python — Agent B Python 沙箱镜像（PoC 版）
#
# 目标：在容器内运行 Claude Code CLI headless，处理 Python 仓库的 Issue。
#
# 约束：
#   - 镜像内不存任何凭证；ANTHROPIC_API_KEY 通过运行时环境变量注入
#   - 不预装项目依赖；测试运行时由 Agent B 通过 Bash 安装
#   - 共享缓存挂载点：/cache/pip、/cache/npm（运行时由调度器挂载）
# ============================================================================

FROM python:3.11-slim-bookworm

# 系统依赖：git（克隆仓库）、curl（Node 安装）、ca-certificates、构建基础
RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        curl \
        ca-certificates \
        build-essential \
        jq \
    && rm -rf /var/lib/apt/lists/*

# Node.js 20 (NodeSource 官方仓库)
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Claude Code CLI（全局安装）
RUN npm install -g @anthropic-ai/claude-code@latest \
    && npm cache clean --force

# Python 端 MCP SDK + Pydantic（供 mcp_report_server.py 用）
# 安装到系统 site-packages，agent 用户可读
RUN pip install --no-cache-dir 'mcp>=1.0' 'pydantic>=2.0'

# 沙箱用户（非 root，降低逃逸风险）
RUN useradd --create-home --shell /bin/bash agent

# MCP server 脚本（暴露 report_completion / report_failure 工具）
COPY --chown=agent:agent mcp_report_server.py /opt/issuepilot/mcp_report_server.py
RUN chmod +x /opt/issuepilot/mcp_report_server.py

# 容器入口脚本（clone + branch + claude 调用）
COPY --chown=agent:agent entrypoint.sh /opt/issuepilot/entrypoint.sh
RUN chmod +x /opt/issuepilot/entrypoint.sh

# 工作区 + 缓存目录（缓存目录由 host volume 挂载覆盖）
RUN mkdir -p /workspace /cache/pip /cache/npm \
    && chown -R agent:agent /workspace /cache

USER agent
WORKDIR /workspace

# 让 pip 使用挂载的缓存
ENV PIP_CACHE_DIR=/cache/pip
ENV NPM_CONFIG_CACHE=/cache/npm

# 显式禁用 Claude Code 自动更新（沙箱内不需要联网更新）
ENV DISABLE_AUTOUPDATER=1

# 默认入口：bash，便于 docker run --rm -it 调试
# 实际生产由 dev_worker 通过 docker exec 或 -c 传命令
CMD ["/bin/bash"]
