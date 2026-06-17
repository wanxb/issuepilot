"""Prompt 全文。

约定：
    - 每个 Agent 一个 module
    - 版本号在 module 顶部常量 PROMPT_VERSION 中维护
    - 修改 prompt 必须同步 bump 版本，以便 evaluations.prompt_version /
      llm_call_logs.prompt_version 做 A/B 归因
"""
