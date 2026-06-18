#!/bin/bash
# Agent B 容器入口脚本
# 由 SandboxManager 通过 docker run ... /bin/bash /opt/issuepilot/entrypoint.sh 调用
# 所需环境变量（由调度器注入）：
#   AGENT_PROMPT_B64  - base64 编码的 prompt（避免 shell 特殊字符问题）
#   REPO_FULL_NAME    - e.g. owner/repo
#   BRANCH_NAME       - e.g. issuepilot/issue-42-fix-subtract
#   GITHUB_TOKEN      - 只读 / 读写 token（无 GITHUB_DEV_TOKEN 时使用）
#   GITHUB_DEV_TOKEN  - (可选) 开发专用 token，用于 fork 后的 repo
#   CLAUDE_MODEL      - (可选) 默认 claude-sonnet-4-6
#   MAX_TURNS         - (可选) 默认 25
#   ANTHROPIC_API_KEY / ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN - 认证
set -euo pipefail

# git 全局配置（避免 "please tell me who you are" 报错）
git config --global user.email "agent@issuepilot.local"
git config --global user.name "IssuePilot Agent"
git config --global init.defaultBranch main

# 选择 clone token：优先 DEV_TOKEN，降级到普通 TOKEN
TOKEN="${GITHUB_DEV_TOKEN:-${GITHUB_TOKEN:-}}"
if [ -z "$TOKEN" ]; then
  echo "=== ERROR: no GitHub token provided ===" >&2
  exit 1
fi

echo "=== SETUP: cloning ${REPO_FULL_NAME} ==="
git clone --depth=1 "https://x-token:${TOKEN}@github.com/${REPO_FULL_NAME}.git" /workspace
cd /workspace

# 记录 clone 后的 base SHA（用于稍后产出 git diff base..HEAD）
export BASE_SHA="$(git rev-parse HEAD)"
echo "=== SETUP: base SHA = ${BASE_SHA} ==="

echo "=== SETUP: creating branch ${BRANCH_NAME} ==="
git checkout -b "${BRANCH_NAME}"

# 写 MCP 配置（单引号 heredoc 避免变量展开）
cat > /workspace/.mcp.json << 'MCPEOF'
{
  "mcpServers": {
    "issuepilot-agent-b-terminator": {
      "command": "python",
      "args": ["/opt/issuepilot/mcp_report_server.py"]
    }
  }
}
MCPEOF

echo "=== SYSTEM: starting Agent B (model=${CLAUDE_MODEL:-claude-sonnet-4-6}, max_turns=${MAX_TURNS:-25}) ==="

# base64 -d 解码 prompt，通过 stdin 传给 claude（避免命令行特殊字符问题）
echo "${AGENT_PROMPT_B64}" | base64 -d | claude -p - \
  --model "${CLAUDE_MODEL:-claude-sonnet-4-6}" \
  --max-turns "${MAX_TURNS:-25}" \
  --output-format stream-json \
  --allowedTools "Read,Write,Edit,Bash,Glob,Grep,mcp__issuepilot-agent-b-terminator__report_completion,mcp__issuepilot-agent-b-terminator__report_failure" \
  --permission-mode acceptEdits \
  --mcp-config /workspace/.mcp.json

EXIT_CODE=$?
echo "=== SYSTEM: Agent B finished (exit=${EXIT_CODE}) ==="

# 产出 git diff 供 Agent C 评审采集（best-effort，不影响 exit code）
echo "=== SYSTEM: capturing diff vs base SHA ${BASE_SHA} ==="
git diff "${BASE_SHA}..HEAD" > /workspace/.agent_diff.patch 2>/dev/null || true
DIFF_BYTES=$(wc -c < /workspace/.agent_diff.patch 2>/dev/null || echo 0)
echo "=== SYSTEM: diff captured (${DIFF_BYTES} bytes) ==="

exit $EXIT_CODE
