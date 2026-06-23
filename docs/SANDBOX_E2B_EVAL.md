# E2B 沙箱 vs 本地 Docker — 评估 + 决策（3.3）

> 日期：2026-06-23 · 决策：**3.x 阶段暂不切换**；保留本地 Docker。

---

## 背景

ROADMAP §3.3 列了 "E2B 沙箱切换（替代本地 Docker，提升启动速度）" 一项。
本文档评估 E2B vs 当前本地 Docker 方案，给出决策与触发条件。

当前 Agent B 沙箱（Phase 1.4 + 2.1）：
- 5 个本地 Docker 镜像（python/node/go/rust/java），每个 ~1-2GB
- `SandboxManager` 用 docker SDK 起容器，挂 docker.sock + workspace + cache volume
- 启动 ~3-8 秒（已构建镜像 + 缓存命中），冷启动可达 30+ 秒
- 安全：非 root 用户，no-new-privileges，资源限制 / 内存上限可配
- 部署：与 issuepilot worker 同机或 docker-in-docker

E2B（[e2b.dev](https://e2b.dev)）：
- 云端运行的轻量级 microVM（基于 Firecracker）
- 自定义模板（Custom Sandbox Template），近似自带 Dockerfile
- SDK：`@e2b/sdk`（Node）+ Python SDK；类似 SSH 接口暴露 exec / file ops
- 启动 ~250ms（官方宣称），实际 1-2 秒
- 按使用时长计费（vCPU/RAM/GB-hour）

---

## 对比矩阵

| 维度 | 本地 Docker | E2B |
|---|---|---|
| 冷启动延迟 | 3-30s | 0.5-2s（官方 250ms） |
| 热启动延迟 | <1s（容器复用） | 同上 |
| 单 task 成本 | 本机算力 | 按时长，约 $0.001-$0.01 / 任务（取决于 CPU/RAM） |
| 镜像构建 | 自己 docker build | E2B 模板（Dockerfile-like） |
| 缓存 volume | 主机本地 NVMe，无网络往返 | E2B persistent storage（有，但慢） |
| 网络可达性 | 直通主机网络 / 配置桥接 | 沙箱内可直出 internet（受 E2B 政策） |
| 工具链定制 | 完全自由（已有 5 镜像） | 通过模板自定义（需重新打 5 模板） |
| 调试 | docker exec / docker logs | E2B Dashboard + SDK |
| 多租户隔离 | 同一 docker daemon | 真正 microVM 级别 |
| 出口控制 | 自己写 firewall 规则 | E2B 政策 + 自定义 |
| 离线运行 | ✅ | ❌（需 E2B 在线） |
| Vendor lock-in | 无（docker 标准） | 中（特定 SDK） |
| **可观测性** | docker 日志 + 已有 DevLog 流 | E2B Dashboard，需另接 |

---

## 关键考虑

### 启动速度真是瓶颈吗？

当前观测（Phase 1 dry run + 2.3 多次实测）：
- Agent B 单 task ≈ **4-7 分钟**（含 clone / Agent B 推理 / test）
- 沙箱启动占比 ≈ 1-2%（3-8s vs 240-420s）
- 真瓶颈：Agent B 推理 turn 数（max_turns=25 时上限 ~10min）+ test runtime

**E2B 切换的速度收益 ≤ 1-2%**，对延迟感受可忽略。

### 成本怎么算？

- 本地 Docker：折算电费 + 主机折旧 ≈ 微不足道
- E2B：按 vCPU/RAM 时长计费。粗算 2 vCPU / 4GB / 5min ≈ $0.005-$0.02 / task

按当前 Phase 2 dry run 数据（21 次 Agent A 评估 + 0 次真 Agent B），切到 E2B
后 Agent B 全跑还需要先攒数据；预估 1000 task/月 ≈ $5-20，可接受。

### 安全收益

E2B 提供真正的 microVM 隔离，比 docker daemon 共享内核更强；对 Issue body
含 prompt injection / Bash 命令的场景，多一层保护。

但当前 sandbox 已是非 root + 拒提权 + 资源限制；剩余攻击面有限，不是
"非切不可" 的安全工程任务。

### Vendor lock-in

E2B 是单一厂商，切换需要重写 SandboxManager + 重写 5 个模板。回切到 Docker
也需逆向工程。本地 Docker 的"零厂商绑定"是当前明显优势。

---

## 决策

**3.x 阶段不切换到 E2B**，理由：
1. 启动速度不是瓶颈（占比 <2%）
2. 没有运维痛点（5 个本地镜像构建脚本已就位）
3. 引入 vendor lock-in + 月度成本（边际收益不大）
4. Phase 2.1 多语言沙箱才完成，重新打 E2B 模板等于推倒重来

**保留切换选项**，触发条件（任一）：
- 月 task 量超 1000 且本地资源吃紧（CPU / 内存不够并行）
- Agent B 增加 GUI / 浏览器自动化等场景，本地 Docker 难以处理
- 多人协作 / SaaS 模式上线，需要真正 microVM 隔离
- E2B 大幅降价（如 50% 以下）或推出 OSS 自托管

### 替代方案（暂行）

若本地 Docker 资源紧张，下一步先：
1. 把 worker 进程 + Docker daemon 分到独立机器
2. 实装 ROADMAP 里"共享缓存 volume 跨任务复用"（pip/npm/go mod/cargo）
3. 评估 podman / nerdctl 等更轻量替代

---

## 状态

- ROADMAP §3.3 标记本子项为 **"deferred"**（非 done，不是 skipped）
- 切换工作量评估（备查）：
  - SandboxManager 抽象 SandboxBackend 接口 — 1 天
  - E2BSandboxBackend 实现 — 2 天
  - 5 个 E2B 自定义模板（python/node/go/rust/java） — 2 天
  - DevLog 集成（E2B → 我们的 stream） — 1 天
  - 测试 + 文档 — 1 天
  - 合计 ≈ 1 周

---

*decision owner: 项目负责人；review cadence: 季度（Phase 3.x 末次回顾时复核）*
