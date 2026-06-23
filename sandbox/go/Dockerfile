# ============================================================================
# agent-sandbox-go — Agent B Go 沙箱
# Build context = sandbox/
# ============================================================================

FROM golang:1.23-bookworm

# 通用工具 + Node.js 20 + Python（MCP server）+ Claude Code CLI
RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl ca-certificates jq \
        python3 python3-pip python3-venv \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g @anthropic-ai/claude-code@latest \
    && npm cache clean --force \
    && rm -rf /var/lib/apt/lists/*

# MCP server 依赖（venv 避开 PEP 668）
RUN python3 -m venv /opt/mcp-venv \
    && /opt/mcp-venv/bin/pip install --no-cache-dir 'mcp>=1.0' 'pydantic>=2.0'

ENV PATH="/opt/mcp-venv/bin:${PATH}"

RUN useradd --create-home --shell /bin/bash agent

COPY --chown=agent:agent shared/mcp_report_server.py /opt/issuepilot/mcp_report_server.py
COPY --chown=agent:agent shared/apply_devcontainer.py /opt/issuepilot/apply_devcontainer.py
COPY --chown=agent:agent shared/entrypoint.sh /opt/issuepilot/entrypoint.sh
RUN chmod +x /opt/issuepilot/entrypoint.sh \
        /opt/issuepilot/mcp_report_server.py \
        /opt/issuepilot/apply_devcontainer.py

# 工作区 + 缓存目录
RUN mkdir -p /workspace /cache/go /cache/go-build /cache/npm \
    && chown -R agent:agent /workspace /cache

USER agent
WORKDIR /workspace

ENV GOPATH=/cache/go
ENV GOCACHE=/cache/go-build
ENV GOMODCACHE=/cache/go/pkg/mod
ENV NPM_CONFIG_CACHE=/cache/npm
ENV DISABLE_AUTOUPDATER=1

CMD ["/bin/bash"]
