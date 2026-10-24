"""RAG 配置：从根目录 ``.env`` 读取 LLM / LangSmith / 应用层参数。

变量名兼容
----------
LLM 配置同时接受两种前缀（``.env`` 里两种都出现过）：

- ``RAG_LLM_API_KEY``（带 RAG_ 前缀）
- ``LLM_API_KEY``（不带前缀）

用 ``AliasChoices`` 兼容，避免因为改名导致 ``require_llm()`` 误报。

Milvus 与 Embedding 的配置不在这里
----------------------------------
它们各自有独立的 Settings：

- ``agent.milvus_connect.MilvusSettings``（``RAG_MILVUS_*``）
- ``agent.embedding.EmbeddingSettings``（``RAG_EMBEDDING_*``）
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def _llm_alias(name: str) -> AliasChoices:
    """同时接受 ``RAG_LLM_X`` 与 ``LLM_X``。"""
    return AliasChoices(f"RAG_LLM_{name}", f"LLM_{name}")


class Settings(BaseSettings):
    """进程级配置单例。"""

    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── LLM（DashScope 的 OpenAI 兼容接口）──────────────────────────
    llm_api_key: str = Field("", validation_alias=_llm_alias("API_KEY"))
    llm_base_url: str = Field(
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        validation_alias=_llm_alias("BASE_URL"),
    )
    llm_fast_model: str = Field(
        "qwen3.8-flash", validation_alias=_llm_alias("FAST_MODEL")
    )
    llm_strong_model: str = Field(
        "qwen3.8-max", validation_alias=_llm_alias("STRONG_MODEL")
    )
    llm_temperature: float = Field(0.1, validation_alias=_llm_alias("TEMPERATURE"))
    llm_max_tokens: int = Field(4096, validation_alias=_llm_alias("MAX_TOKENS"))

    # ── LangSmith（可选）───────────────────────────────────────────
    langsmith_api_key: str = Field("", validation_alias="LANGSMITH_API_KEY")
    langsmith_tracing: bool = Field(False, validation_alias="LANGSMITH_TRACING")
    langsmith_project: str = Field("project_api", validation_alias="LANGSMITH_PROJECT")
    langsmith_endpoint: str = Field(
        "https://api.smith.langchain.com", validation_alias="LANGSMITH_ENDPOINT"
    )

    # ── 应用层 ─────────────────────────────────────────────────────
    app_env: str = Field("development", validation_alias="APP_ENV")
    log_level: str = Field("INFO", validation_alias="LOG_LEVEL")
    rate_limit: str = Field("20/minute", validation_alias="RATE_LIMIT")
    cache_ttl_seconds: int = Field(300, validation_alias="CACHE_TTL_SECONDS")
    max_retries: int = Field(3, validation_alias="MAX_RETRIES")

    # ── 便捷属性 ───────────────────────────────────────────────────

    @property
    def is_production(self) -> bool:
        return self.app_env.strip().lower() == "production"

    def require_llm(self) -> None:
        """生成器调用前检查 key 是否配置，避免请求到远端才报 401。"""
        if not self.llm_api_key:
            raise RuntimeError(
                "LLM_API_KEY / RAG_LLM_API_KEY 为空：请在根目录 .env 中填写，然后重启进程。"
            )

    # ── 兼容旧代码的字段名（旧版 config 用的是大写属性）──────────────

    @property
    def LLM_API_KEY(self) -> str:
        return self.llm_api_key

    @property
    def LLM_BASE_URL(self) -> str:
        return self.llm_base_url

    @property
    def primary_model(self) -> str:
        return self.llm_fast_model

    @property
    def fallback_model(self) -> str:
        return self.llm_strong_model


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """单例：只读一次 ``.env``，后续复用。"""
    return Settings()
