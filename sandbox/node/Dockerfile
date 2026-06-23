# ============================================================================
# agent-sandbox-node — Agent B JavaScript / TypeScript 沙箱
# Build context = sandbox/
# ============================================================================

FROM node:20-bookworm-slim

# 通用工具 + Python（MCP server）+ Claude Code CLI
RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl ca-certificates build-essential jq \
        python3 python3-pip python3-venv \
    && rm -rf /var/lib/apt/lists/* \
    && npm install -g @anthropic-ai/claude-code@latest pnpm yarn \
    && npm cache clean --force

# MCP server 依赖（用 venv 隔离 Debian Bookworm 的 PEP 668）
RUN python3 -m venv /opt/mcp-venv \
    && /opt/mcp-venv/bin/pip install --no-cache-dir 'mcp>=1.0' 'pydantic>=2.0'

# 让 entrypoint.sh 里的 `python` 指向 venv 内的（mcp_report_server.py 用）
ENV PATH="/opt/mcp-venv/bin:${PATH}"

RUN useradd --create-home --shell /bin/bash agent

COPY --chown=agent:agent shared/mcp_report_server.py /opt/issuepilot/mcp_report_server.py
COPY --chown=agent:agent shared/apply_devcontainer.py /opt/issuepilot/apply_devcontainer.py
COPY --chown=agent:agent shared/entrypoint.sh /opt/issuepilot/entrypoint.sh
RUN chmod +x /opt/issuepilot/entrypoint.sh \
        /opt/issuepilot/mcp_report_server.py \
        /opt/issuepilot/apply_devcontainer.py

# 工作区 + 缓存目录
RUN mkdir -p /workspace /cache/npm /cache/pnpm /cache/yarn \
    && chown -R agent:agent /workspace /cache

USER agent
WORKDIR /workspace

ENV NPM_CONFIG_CACHE=/cache/npm
ENV PNPM_HOME=/cache/pnpm
ENV YARN_CACHE_FOLDER=/cache/yarn
ENV DISABLE_AUTOUPDATER=1

CMD ["/bin/bash"]
