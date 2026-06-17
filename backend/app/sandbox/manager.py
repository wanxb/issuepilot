"""SandboxManager —— Docker sandbox 的薄封装。

设计：
    - 单进程内单实例，懒初始化 client
    - 所有公开方法对应 Agent B 调度流程：ping / start / wait / stop / collect_report
    - 1.4a 仅实现 ping() + image_exists()，后续补全

错误：所有 Docker 异常包成 SandboxError，避免上层依赖 docker 包。
"""
from __future__ import annotations

import structlog

log = structlog.get_logger(__name__)


class SandboxError(Exception):
    """Docker 操作通用错误。"""


class SandboxManager:
    def __init__(self) -> None:
        self._client = None  # 懒初始化避免 import 阶段触发连接

    # ---- 懒初始化 ----

    def _ensure_client(self) -> object:
        if self._client is not None:
            return self._client
        try:
            import docker  # type: ignore[import-untyped]
        except ImportError as e:
            raise SandboxError("docker SDK not installed") from e
        try:
            self._client = docker.from_env()
        except Exception as e:
            raise SandboxError(f"cannot connect to docker daemon: {e}") from e
        return self._client

    # ---- 公开 API ----

    def ping(self) -> dict[str, object]:
        """验证 daemon 可达。返回版本元数据。"""
        client = self._ensure_client()
        try:
            version = client.version()  # type: ignore[attr-defined]
        except Exception as e:
            raise SandboxError(f"docker ping failed: {e}") from e
        return {
            "ok": True,
            "server_version": version.get("Version"),
            "api_version": version.get("ApiVersion"),
            "os": version.get("Os"),
            "arch": version.get("Arch"),
        }

    def image_exists(self, name: str) -> bool:
        """检查镜像是否本地可用。"""
        client = self._ensure_client()
        try:
            client.images.get(name)  # type: ignore[attr-defined]
            return True
        except Exception:
            return False
