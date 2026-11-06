"""LLM 工厂：按 agent.config 创建千问 / DeepSeek 的 ChatOpenAI。

两家都提供 OpenAI 兼容接口，所以共用 ``ChatOpenAI``，只换
``base_url`` / ``api_key`` / ``model``：

    from agent.llm import build_llm

    llm = build_llm()              # LLM_FAST_MODEL
    llm = build_llm(strong=True)   # LLM_STRONG_MODEL
"""
from __future__ import annotations

from langchain_openai import ChatOpenAI

from agent.config import LLMConfig, load_llm_config


def build_llm(
    config: LLMConfig | None = None,
    *,
    strong: bool = False,
    model: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> ChatOpenAI:
    """创建 ChatOpenAI；配置默认从项目根目录 .env 读取。"""
    cfg = config or load_llm_config()
    return ChatOpenAI(
        model=model or (cfg.strong_model if strong else cfg.fast_model),
        api_key=cfg.api_key,
        base_url=cfg.base_url,
        temperature=cfg.temperature if temperature is None else temperature,
        max_tokens=cfg.max_tokens if max_tokens is None else max_tokens,
        timeout=cfg.timeout,
        max_retries=cfg.max_retries,
    )


__all__ = ["build_llm"]
