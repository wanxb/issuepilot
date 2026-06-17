"""Agent harness 层。

每个 Agent 一个文件 + prompt 单独维护。harness 负责：
    - 把领域输入装配为 LLM messages
    - 调用 LLMClient
    - 校验 Tool Use 输出 schema（pydantic）
    - 返回结构化结果 + 全部 LLMResponse（让上层写 llm_call_logs）

Prompt 全文在 prompts/ 子包，作为代码（Python 字符串常量）维护，
不在文档里复制。
"""
