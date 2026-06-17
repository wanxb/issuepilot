"""SandboxManager —— Docker sandbox 的薄封装。

设计：
    - 单进程内单实例，懒初始化 client
    - 所有公开方法对应 Agent B 调度流程：ping / start / stream_logs / collect_report / stop
    - 所有方法为同步（Docker SDK 不支持 async），在 dev_worker 中通过
      asyncio.to_thread() 或 run_in_executor() 调用

错误：所有 Docker 异常包成 SandboxError，避免上层依赖 docker 包。
"""
from __future__ import annotations

import io
import json
import tarfile
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any
from uuid import UUID

import structlog

if TYPE_CHECKING:
    pass

log = structlog.get_logger(__name__)


class SandboxError(Exception):
    """Docker 操作通用错误。"""


class SandboxManager:
    def __init__(self) -> None:
        self._client = None  # 懒初始化避免 import 阶段触发连接

    # ---- 懒初始化 ----

    def _ensure_client(self) -> Any:
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

    def start(
        self,
        *,
        image: str,
        dev_task_id: UUID,
        env: dict[str, str],
    ) -> Any:
        """创建并启动沙箱容器（detach=True），返回 Container 对象。

        容器运行 /opt/issuepilot/entrypoint.sh，该脚本负责 clone + claude 调用。
        调用方需在容器完成后调用 stop() 清理。
        """
        client = self._ensure_client()
        try:
            container = client.containers.run(  # type: ignore[attr-defined]
                image=image,
                command=["/bin/bash", "/opt/issuepilot/entrypoint.sh"],
                environment=env,
                detach=True,
                auto_remove=False,
                labels={"issuepilot.dev_task_id": str(dev_task_id)},
                mem_limit="2g",
                pids_limit=512,
                network_mode="bridge",
            )
        except Exception as e:
            raise SandboxError(f"failed to start container: {e}") from e
        log.info(
            "sandbox.started",
            container_id=container.short_id,
            image=image,
            dev_task_id=str(dev_task_id),
        )
        return container

    def stream_logs(self, container: Any) -> Iterator[str]:
        """阻塞迭代器：跟随容器 stdout/stderr，yield 每行已解码的文本。

        此方法在线程中运行（Docker SDK 同步 API）。
        当容器退出时迭代自然结束。
        """
        try:
            for raw in container.logs(stream=True, follow=True):
                line = raw.decode("utf-8", errors="replace").rstrip()
                if line:
                    yield line
        except Exception as e:
            log.warning("sandbox.stream_logs_error", error=str(e))

    def collect_report(self, container: Any) -> dict[str, Any] | None:
        """从容器内 /workspace/.agent_report.json 读取 JSON 报告。

        使用 Docker get_archive API 提取文件（容器已停止时仍可读）。
        读取失败（文件不存在、JSON 无效）时返回 None。
        """
        try:
            bits, _ = container.get_archive("/workspace/.agent_report.json")
            buf = io.BytesIO(b"".join(bits))
            with tarfile.open(fileobj=buf) as tf:
                members = tf.getmembers()
                if not members:
                    return None
                f = tf.extractfile(members[0])
                if f is None:
                    return None
                data = f.read()
            return json.loads(data)
        except Exception as e:
            log.debug("sandbox.collect_report_failed", error=str(e))
            return None

    def wait(self, container: Any, *, timeout: int | None = None) -> int:
        """等待容器退出，返回 exit code。

        timeout: 秒数，None = 无限等待。
        超时时抛 SandboxError。
        """
        try:
            result = container.wait(timeout=timeout)
            return result.get("StatusCode", 1)
        except Exception as e:
            raise SandboxError(f"container wait failed: {e}") from e

    def stop(self, container: Any, *, timeout: int = 10) -> None:
        """强制停止并删除容器，忽略 not-found / already-stopped 错误。"""
        try:
            container.stop(t=timeout)
        except Exception:
            pass
        try:
            container.remove(force=True)
        except Exception:
            pass
        log.debug("sandbox.stopped", container_id=container.short_id)
