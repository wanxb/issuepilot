# ============================================================================
# agent-sandbox-python — Agent B Python 沙箱镜像（2.1 重构）
#
# 与 sandbox/{node,go,rust,java}/Dockerfile 共享 sandbox/shared/ 下的
# entrypoint.sh 与 mcp_report_server.py，构建上下文 = sandbox/。
#
# 通用基线（5 个语言镜像共有）：
#   - git / curl / ca-certificates / jq / build-essential
#   - Node.js 20（Claude Code CLI 必需）
#   - Python 3 + mcp + pydantic（MCP report server 用）
#   - agent 非 root 用户 + /cache/{pip,npm,go,cargo,maven} 缓存挂载点
# ============================================================================

FROM python:3.11-slim-bookworm

# 通用工具 + Node.js 20 + Claude Code CLI
RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl ca-certificates build-essential jq \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g @anthropic-ai/claude-code@latest \
    && npm cache clean --force \
    && rm -rf /var/lib/apt/lists/*

# MCP server 依赖（系统 site-packages，agent 用户可读）
RUN pip install --no-cache-dir 'mcp>=1.0' 'pydantic>=2.0'

# Python 沙箱无须额外语言工具链（用基础镜像自带的 python3 / pip）

# 沙箱用户
RUN useradd --create-home --shell /bin/bash agent

# 共享脚本（从 sandbox/shared/ 复制）
COPY --chown=agent:agent shared/mcp_report_server.py /opt/issuepilot/mcp_report_server.py
COPY --chown=agent:agent shared/entrypoint.sh /opt/issuepilot/entrypoint.sh
RUN chmod +x /opt/issuepilot/entrypoint.sh /opt/issuepilot/mcp_report_server.py

# 工作区 + 缓存目录（runtime 由 host volume 挂载覆盖）
RUN mkdir -p /workspace /cache/pip /cache/npm \
    && chown -R agent:agent /workspace /cache

USER agent
WORKDIR /workspace

ENV PIP_CACHE_DIR=/cache/pip
ENV NPM_CONFIG_CACHE=/cache/npm
ENV DISABLE_AUTOUPDATER=1

CMD ["/bin/bash"]
