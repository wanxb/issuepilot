# ============================================================================
# agent-sandbox-rust — Agent B Rust 沙箱
# Build context = sandbox/
# ============================================================================

FROM rust:1.83-bookworm

# 通用工具 + Node.js 20 + Python（MCP server）+ Claude Code CLI
# rust:bookworm 已自带 git + build-essential，少装一些
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates jq pkg-config libssl-dev \
        python3 python3-pip python3-venv \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g @anthropic-ai/claude-code@latest \
    && npm cache clean --force \
    && rm -rf /var/lib/apt/lists/*

# MCP server 依赖
RUN python3 -m venv /opt/mcp-venv \
    && /opt/mcp-venv/bin/pip install --no-cache-dir 'mcp>=1.0' 'pydantic>=2.0'

ENV PATH="/opt/mcp-venv/bin:${PATH}"

RUN useradd --create-home --shell /bin/bash agent

COPY --chown=agent:agent shared/mcp_report_server.py /opt/issuepilot/mcp_report_server.py
COPY --chown=agent:agent shared/entrypoint.sh /opt/issuepilot/entrypoint.sh
RUN chmod +x /opt/issuepilot/entrypoint.sh /opt/issuepilot/mcp_report_server.py

# 工作区 + 缓存目录
RUN mkdir -p /workspace /cache/cargo/registry /cache/cargo/git \
              /cache/target /cache/rustup /cache/npm \
    && chown -R agent:agent /workspace /cache

# 让 rust 工具链在 agent 用户下找得到（基础镜像把它装在 /usr/local/cargo）
ENV CARGO_HOME=/cache/cargo
ENV RUSTUP_HOME=/cache/rustup
ENV CARGO_TARGET_DIR=/cache/target
ENV PATH="/usr/local/cargo/bin:${PATH}"
ENV NPM_CONFIG_CACHE=/cache/npm
ENV DISABLE_AUTOUPDATER=1

USER agent
WORKDIR /workspace

CMD ["/bin/bash"]
