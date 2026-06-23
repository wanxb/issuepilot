# ============================================================================
# agent-sandbox-java — Agent B Java 沙箱
# Build context = sandbox/
# ============================================================================

FROM eclipse-temurin:21-jdk-noble

# 通用工具 + Node.js 20 + Python（MCP server）+ Claude Code CLI + Maven + Gradle
RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl ca-certificates build-essential jq unzip \
        python3 python3-pip python3-venv \
        maven \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g @anthropic-ai/claude-code@latest \
    && npm cache clean --force \
    && rm -rf /var/lib/apt/lists/*

# Gradle 8.x（apt 版本通常偏旧；用官方 zip）
ENV GRADLE_VERSION=8.10.2
RUN curl -fsSL -o /tmp/gradle.zip \
        "https://services.gradle.org/distributions/gradle-${GRADLE_VERSION}-bin.zip" \
    && unzip -q /tmp/gradle.zip -d /opt \
    && ln -s "/opt/gradle-${GRADLE_VERSION}/bin/gradle" /usr/local/bin/gradle \
    && rm /tmp/gradle.zip

# MCP server 依赖
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
RUN mkdir -p /workspace /cache/maven /cache/gradle /cache/npm \
    && chown -R agent:agent /workspace /cache

USER agent
WORKDIR /workspace

ENV MAVEN_OPTS="-Dmaven.repo.local=/cache/maven"
ENV GRADLE_USER_HOME=/cache/gradle
ENV NPM_CONFIG_CACHE=/cache/npm
ENV DISABLE_AUTOUPDATER=1

CMD ["/bin/bash"]
