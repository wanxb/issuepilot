#!/usr/bin/env bash
# 2.1：一键构建（或重建）所有 Agent B 沙箱镜像
#
# 用法：
#   bash scripts/build_sandbox_images.sh                # 全部
#   bash scripts/build_sandbox_images.sh python node    # 只构建指定
#
# 镜像 tag：agent-sandbox-<lang>:latest（与 settings.sandbox_image_prefix 对齐）
set -euo pipefail

LANGS=("python" "node" "go" "rust" "java")
if [ "$#" -gt 0 ]; then
  LANGS=("$@")
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SANDBOX_DIR="${SCRIPT_DIR}/../sandbox"
PREFIX="${SANDBOX_IMAGE_PREFIX:-agent-sandbox}"

cd "${SANDBOX_DIR}"

failed=()
for lang in "${LANGS[@]}"; do
  if [ ! -d "${lang}" ] || [ ! -f "${lang}/Dockerfile" ]; then
    echo "!! skip ${lang}: no Dockerfile at ${SANDBOX_DIR}/${lang}/Dockerfile"
    failed+=("${lang}:missing")
    continue
  fi
  image="${PREFIX}-${lang}:latest"
  echo "=========================================================================="
  echo "Building ${image} (context=${SANDBOX_DIR}, dockerfile=${lang}/Dockerfile)"
  echo "=========================================================================="
  if docker build \
      -t "${image}" \
      -f "${lang}/Dockerfile" \
      "${SANDBOX_DIR}"; then
    echo "✓ ${image} built"
  else
    echo "✗ ${image} build FAILED"
    failed+=("${lang}:build_failed")
  fi
done

echo "=========================================================================="
echo "Summary:"
docker images --filter "reference=${PREFIX}-*:latest" --format "  {{.Repository}}:{{.Tag}} ({{.Size}}, created {{.CreatedSince}})"

if [ "${#failed[@]}" -gt 0 ]; then
  echo
  echo "Failed: ${failed[*]}"
  exit 1
fi
